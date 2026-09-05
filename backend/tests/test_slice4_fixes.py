"""Slice 4 review fix batch — one test per finding (docs/plans/2026-09-05-contacts-stats/REVIEW.md).

Each test names the finding it pins. Helpers copied (never imported) from the
frozen slice-4 files — a cross-module fixture import breaks per-test schema
isolation.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Manchester",
    "description": "Build the lakehouse. dbt, Snowflake, Airflow, Python. " * 4,
}


def _seed_profile(user_id: str, *, location: str = "London") -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    profile = UserProfile(
        cv_data=CVData(
            raw_text="Python data engineer. dbt, Snowflake, Airflow.",
            name="Ada Lovelace",
            headline="Data Engineer",
            location=location,
            skills=["Python", "dbt"],
        ),
        preferences=UserPreferences(target_job_titles=["Data Engineer"], preferred_locations=["London"]),
    )
    save_profile(profile, user_id, source_action="cv_upload")


async def _patch(client: AsyncClient, *edits: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits)})


async def _bring(client: AsyncClient, **over: Any) -> int:
    resp = await client.post("/api/jobs/bring", json={**_AD, **over})
    assert resp.status_code in (200, 201), resp.text
    return int(resp.json()["application_id"])


# ── P1: the web "Clear" must also clear the agent's edits ────────────────────


@pytest.mark.asyncio
async def test_p1_clear_section_appends_clearing_rows(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _patch(client, {"path": "cv_data.location", "value": "Manchester"})).status_code == 200
        assert (await _patch(client, {"path": "preferences.about_me", "value": "hire me"})).status_code == 200
        resp = await client.post("/api/profile/clear", data={"section": "cv"})
        assert resp.status_code == 200, resp.text
        # a FRESH read, not the clear response
        profile = (await client.get("/api/profile")).json()
        assert (profile["cv_detail"] or {}).get("location", "") == ""
        paths = {e["path"] for e in profile["agent_edits"]}
        assert "cv_data.location" not in paths, "the cleared scope's edits are gone"
        assert "preferences.about_me" in paths, "a section clear leaves the other section's edits alone"
        export = (await client.get("/api/applications/export")).json()
        clearing = [e for e in export["profile_edits"] if e["path"] == "cv_data.location" and e["value"] is None]
        assert clearing and clearing[-1]["set_by"] == "web", "the clear is an append-only row by the human"


@pytest.mark.asyncio
async def test_p1_clear_all_clears_both_sections(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, {"path": "cv_data.location", "value": "Manchester"})
        await _patch(client, {"path": "preferences.about_me", "value": "hire me"})
        assert (await client.post("/api/profile/clear", data={"section": "all"})).status_code == 200
        assert (await client.get("/api/profile")).json()["agent_edits"] == []


# ── P2: extraction writers read the BASE, so the overlay never bakes in ──────


@pytest.mark.asyncio
async def test_p2_load_profile_without_overlay_is_the_base(authenticated_async_context, fixture_user_id):
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, location="London")
        await _patch(client, {"path": "cv_data.location", "value": "Manchester"})
    assert load_profile(fixture_user_id).cv_data.location == "Manchester"
    base = load_profile(fixture_user_id, with_overlay=False)
    assert base is not None and base.cv_data.location == "London"


def test_p2_every_extraction_writer_reads_the_base():
    """The load→mutate→save routes must not start from the overlaid profile."""
    import inspect

    from src.api.routes import profile as profile_route

    for fn in (profile_route.upload_cv, profile_route.upsert_profile, profile_route.upload_linkedin,
               profile_route.upload_github, profile_route.upsert_preferences):
        src = inspect.getsource(fn)
        assert "with_overlay=False" in src, f"{fn.__name__} must read the base profile"
    import pathlib

    script = pathlib.Path(profile_route.__file__).parents[3] / "scripts" / "reextract_stale_profiles.py"
    assert "with_overlay=False" in script.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_p2_clear_after_reupload_reveals_the_new_extraction(authenticated_async_context, fixture_user_id):
    """The whole point of base-only writers: a later clear shows the CV, not the stale edit."""
    from src.services.profile.storage import load_profile, save_profile

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, location="London")
        await _patch(client, {"path": "cv_data.location", "value": "Manchester"})
        # what an upload route now does: mutate the BASE and save it
        base = load_profile(fixture_user_id, with_overlay=False)
        assert base is not None
        base.cv_data.location = "Birmingham"
        save_profile(base, fixture_user_id, source_action="cv_upload")
        assert (await client.get("/api/profile")).json()["cv_detail"]["location"] == "Manchester"
        await _patch(client, {"path": "cv_data.location", "value": None})
        assert (await client.get("/api/profile")).json()["cv_detail"]["location"] == "Birmingham"


@pytest.mark.asyncio
async def test_p2_web_preferences_change_of_an_edited_field_wins(authenticated_async_context, fixture_user_id):
    """The human's explicit change beats the agent's earlier edit; untouched overlay fields stay."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(
            client,
            {"path": "preferences.preferred_locations", "value": ["Manchester"]},
            {"path": "preferences.about_me", "value": "hire me"},
        )
        resp = await client.post(
            "/api/profile/preferences",
            data={"preferences": json.dumps({"preferred_locations": ["Leeds"]})},
        )
        assert resp.status_code == 200, resp.text
        profile = (await client.get("/api/profile")).json()
        assert profile["preferences"]["preferred_locations"] == ["Leeds"]
        assert profile["preferences"]["about_me"] == "hire me"
        paths = {e["path"] for e in profile["agent_edits"]}
        assert paths == {"preferences.about_me"}


# ── P2: contact + event are one transaction ─────────────────────────────────


@pytest.mark.asyncio
async def test_p2_contact_insert_rolls_back_when_the_event_fails(authenticated_async_context, monkeypatch):
    from src.services.applications import contacts as contacts_mod

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("event store down")

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        monkeypatch.setattr(contacts_mod, "append_event", _boom, raising=True)
        with pytest.raises(RuntimeError):
            await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Priya", "email": "p@x.example"})
        monkeypatch.undo()
        resp = await client.get(f"/api/applications/{app_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["contacts"] == [], "no half-written contact without its event"
        # and the retry is a real create, not an idempotent no-op
        resp = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Priya", "email": "p@x.example"})
        assert resp.status_code == 201 and resp.json()["event_id"]


# ── P2: record_edits is atomic ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p2_record_edits_is_all_or_nothing(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.repositories import pgsync
    from src.services.profile import edits

    async with authenticated_async_context():  # binds the per-test schema
        _seed_profile(fixture_user_id)
    real_execute = pgsync.Connection.execute
    calls = {"inserts": 0}

    def _flaky(self: Any, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO profile_edits" in sql:
            calls["inserts"] += 1
            if calls["inserts"] == 2:
                raise pgsync.OperationalError("connection dropped mid-batch")
        return real_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(pgsync.Connection, "execute", _flaky)
    with pytest.raises(pgsync.OperationalError):
        edits.record_edits(fixture_user_id, "web", [("cv_data.name", "A"), ("cv_data.headline", "B")])
    monkeypatch.undo()
    assert edits.current_overlay(fixture_user_id) == [], "the first insert must not survive the second's failure"


# ── P2: numbers must be finite ──────────────────────────────────────────────


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_p2_non_finite_numbers_are_422(bad: float):
    from src.services.profile.edits import ProfileEditError, validate_edit

    with pytest.raises(ProfileEditError) as exc:
        validate_edit("preferences.salary_min", bad)
    assert exc.value.status_code == 422 and "finite" in str(exc.value.detail)


# ── P3: `since` compares as a normalised UTC instant, not raw text ───────────


@pytest.mark.asyncio
async def test_p3_since_with_z_suffix_includes_a_row_created_after_it(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _bring(client)
        # created "now"; a since 5 minutes ago spelled with Z must include it
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = await client.get("/api/applications/stats", params={"since": since})
        assert resp.status_code == 200, resp.text
        assert resp.json()["overall"]["brought"] == 1
        # and an offset spelling of the same instant agrees
        plus2 = (datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone(timezone(timedelta(hours=2)))
        resp2 = await client.get("/api/applications/stats", params={"since": plus2.isoformat()})
        assert resp2.json()["overall"]["brought"] == 1


def test_p3_since_is_normalised_to_utc_isoformat():
    from src.services.applications.stats import _parse_since

    assert _parse_since("2026-09-05T10:00:00Z") == "2026-09-05T10:00:00+00:00"
    assert _parse_since("2026-09-05T12:00:00+02:00") == "2026-09-05T10:00:00+00:00"


# ── P3: deterministic group order among ties ────────────────────────────────


@pytest.mark.asyncio
async def test_p3_tied_groups_order_by_key(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "STATS_MAX_GROUPS", 2)
    async with authenticated_async_context() as client:
        for title in ("Zeta Role", "Alpha Role", "Mid Role"):
            await _bring(client, title=title)
        first = (await client.get("/api/applications/stats")).json()
        second = (await client.get("/api/applications/stats")).json()
        keys = [g["key"] for g in first["by_role"]]
        assert keys == ["alpha role", "mid role"], keys
        assert [g["key"] for g in second["by_role"]] == keys
        assert first["groups_truncated"] is True


# ── P3: a rejected update_profile leaves no profile row behind ──────────────


@pytest.mark.asyncio
async def test_p3_rejected_first_edit_creates_no_profile(authenticated_async_context):
    async with authenticated_async_context() as client:
        assert (await client.get("/api/profile")).status_code == 404
        resp = await _patch(client, {"path": "cv_data.locaton", "value": "London"})
        assert resp.status_code == 422
        assert (await client.get("/api/profile")).status_code == 404, "no blank profile from a typo"


# ── P3: rejected / no-op calls do not burn the hourly budget ────────────────


@pytest.mark.asyncio
async def test_p3_422s_do_not_consume_the_edit_budget(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_PER_HOUR", 1)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        for _ in range(3):
            assert (await _patch(client, {"path": "cv_data.nope", "value": "x"})).status_code == 422
        assert (await _patch(client, {"path": "cv_data.name", "value": "Ada"})).status_code == 200
        assert (await _patch(client, {"path": "cv_data.name", "value": "Bea"})).status_code == 429


@pytest.mark.asyncio
async def test_p3_bad_since_does_not_consume_the_stats_budget(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "STATS_MAX_PER_HOUR", 1)
    async with authenticated_async_context() as client:
        for _ in range(3):
            assert (await client.get("/api/applications/stats", params={"since": "yesterday"})).status_code == 422
        assert (await client.get("/api/applications/stats")).status_code == 200
        assert (await client.get("/api/applications/stats")).status_code == 429


@pytest.mark.asyncio
async def test_p3_idempotent_contact_replay_does_not_consume_the_budget(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "CONTACTS_MAX_PER_HOUR", 1)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        body = {"name": "Priya", "email": "p@x.example"}
        assert (await client.post(f"/api/applications/{app_id}/contacts", json=body)).status_code == 201
        for _ in range(3):
            assert (await client.post(f"/api/applications/{app_id}/contacts", json=body)).status_code == 200
        assert (await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Q"})).status_code == 429


# ── P3: the OpenAPI schema tells the truth (typed PATCH response, 201) ───────


def test_p3_openapi_declares_patch_profile_response_and_contact_201():
    from src.api.main import app

    schema = app.openapi()
    patch = schema["paths"]["/api/profile"]["patch"]
    ok = patch["responses"]["200"]["content"]["application/json"]["schema"]
    assert "$ref" in ok and ok["$ref"].endswith("UpdateProfileResponse"), ok
    post = schema["paths"]["/api/applications/{application_id}/contacts"]["post"]
    assert "201" in post["responses"], list(post["responses"])


# ── P3: S9 encoded-size bound holds for lists too ───────────────────────────


def test_p3_list_edit_is_bounded_by_encoded_size(monkeypatch):
    from src.core import settings
    from src.services.profile.edits import ProfileEditError, validate_edit

    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_CHARS", 40)
    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_LIST_ITEMS", 100)
    with pytest.raises(ProfileEditError) as exc:
        validate_edit("cv_data.skills", [f"skill-{i:02d}" for i in range(10)])
    assert exc.value.status_code == 422 and "40" in str(exc.value.detail)


# ── P3: one connection per profile read ─────────────────────────────────────


@pytest.mark.asyncio
async def test_p3_load_profile_opens_one_connection(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.repositories import pgsync
    from src.services.profile import storage

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, {"path": "cv_data.location", "value": "Manchester"})
    real_connect = pgsync.connect
    count = {"n": 0}

    def _counting(*a: Any, **k: Any) -> Any:
        count["n"] += 1
        return real_connect(*a, **k)

    monkeypatch.setattr(pgsync, "connect", _counting)
    profile = storage.load_profile(fixture_user_id)
    assert profile is not None and profile.cv_data.location == "Manchester"
    assert count["n"] == 1, f"overlay must ride the existing connection, opened {count['n']}"


# ── conventions S3: export's profile_edits is bounded and counted ────────────


@pytest.mark.asyncio
async def test_s3_export_profile_edits_is_capped_newest_first(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "EXPORT_HISTORY_MAX_PROFILE_EDITS", 2)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        for name in ("A", "B", "C"):
            assert (await _patch(client, {"path": "cv_data.name", "value": name})).status_code == 200
        export = (await client.get("/api/applications/export")).json()
        assert [e["value"] for e in export["profile_edits"]] == ["B", "C"], "newest two, oldest first"
        assert export["profile_edits_truncated"] is True
        # the byte figure describes the whole payload, edits included
        assert export["bytes"] >= len(json.dumps(export["profile_edits"]))


# ── conventions S4: event-type vocabulary has ONE home ──────────────────────


def test_s4_stats_event_types_derive_from_the_status_vocabulary():
    import inspect

    from src.core import settings
    from src.services.applications import stats

    assert set(settings.STATS_INTERVIEW_EVENT_TYPES) <= set(settings.APPLICATION_STATUS_EVENT_TYPES)
    src = inspect.getsource(stats)
    for literal in ("'applied'", "'replied'", "'offer'", "'rejected'", "'interview_"):
        assert literal not in src, f"{literal} re-typed in stats.py — use the settings tuple"


# ── conventions S6: the stats fetch itself is bounded ───────────────────────


@pytest.mark.asyncio
async def test_s6_stats_fetch_is_bounded_by_a_parameter(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "STATS_MAX_APPLICATIONS", 2)
    async with authenticated_async_context() as client:
        for title in ("One", "Two", "Three"):
            await _bring(client, title=title)
        body = (await client.get("/api/applications/stats")).json()
        assert body["overall"]["brought"] == 2, "only the newest N applications are counted"
        assert body["applications_truncated"] is True
        monkeypatch.setattr(settings, "STATS_MAX_APPLICATIONS", 50)
        body = (await client.get("/api/applications/stats")).json()
        assert body["overall"]["brought"] == 3 and body["applications_truncated"] is False
