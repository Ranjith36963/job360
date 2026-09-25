"""Preference memory (owner decisions, 2026-09-25).

Four things, each pinned by VALUE (rule #21), never by key presence:

1. Notes — `preferences.assistant_notes`, the user's standing instructions to
   their assistant. Written on the web (preferences save) or by the agent
   (`update_profile`), read back by both, capped per note.
2. "Was X" + Take back — an assistant edit says what the field held before,
   and the human can take it back (a clearing row, append-only).
3. One history — a web preferences save appends a `set_by=web` row with the
   NEW value; the newest row still wins; web rows never show as assistant
   edits; `GET /profile/edits/history` reads both sides newest-first.
4. Per-user scope — history and take-back never reach another user (IDOR).

Helpers are copied from test_slice4_profile_edits.py, never imported — a
cross-module fixture import breaks per-test schema isolation.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

NOTES_PATH = "preferences.assistant_notes"


def _seed_profile(user_id: str) -> None:
    """A base profile the way extraction writes it — wholesale `save_profile`."""
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    profile = UserProfile(
        cv_data=CVData(
            raw_text="Python data engineer. dbt, Snowflake, Airflow.",
            name="Ada Lovelace",
            location="London",
            skills=["Python", "dbt"],
            job_titles=["Data Engineer"],
        ),
        preferences=UserPreferences(target_job_titles=["Data Engineer"]),
    )
    save_profile(profile, user_id, source_action="cv_upload")


async def _patch(client: AsyncClient, *edits: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits)})


async def _save_prefs(client: AsyncClient, prefs: dict[str, Any]):
    return await client.post("/api/profile/preferences", data={"preferences": json.dumps(prefs)})


async def _mint_token(client: AsyncClient, name: str = "claude") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _second_user_session_cookie(email: str) -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookie = sync_client.cookies.get("job360_session")
    assert cookie, "failed to capture second user's session cookie"
    sync_client.close()
    return str(cookie)


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


async def _history(client: AsyncClient, path: str) -> list[dict[str, Any]]:
    resp = await client.get("/api/profile/edits/history", params={"path": path})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == path
    return list(body["rows"])


# ═══════════════════════════════════════════════════════════════════════════
# 1. Notes
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_notes_round_trip_via_the_web(authenticated_async_context, fixture_user_id):
    notes = ["Never apply to recruitment agencies", "On holiday until 3 Oct"]
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _save_prefs(client, {"assistant_notes": notes})
        assert resp.status_code == 200, resp.text
        assert resp.json()["preferences"]["assistant_notes"] == notes
        # A later save that OMITS the key keeps the notes (partial-save shape).
        assert (await _save_prefs(client, {"target_job_titles": ["Analytics Engineer"]})).status_code == 200
        profile = (await client.get("/api/profile")).json()
        assert profile["preferences"]["assistant_notes"] == notes
        assert profile["preferences"]["target_job_titles"] == ["Analytics Engineer"]
        # An explicit [] removes them all.
        assert (await _save_prefs(client, {"assistant_notes": []})).status_code == 200
        assert (await client.get("/api/profile")).json()["preferences"]["assistant_notes"] == []


@pytest.mark.asyncio
async def test_notes_round_trip_via_mcp_with_the_same_list(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    web_notes = ["Never apply to recruitment agencies"]
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _save_prefs(client, {"assistant_notes": web_notes})).status_code == 200
        token = await _mint_token(client, name="agent")

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    agent_notes = [*web_notes, "Prefers hybrid in Manchester"]
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            before = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)
            # The web's notes reach the agent — at the top level AND in `fields`.
            assert before["assistant_notes"] == web_notes
            assert before["fields"][NOTES_PATH] == web_notes
            assert NOTES_PATH in before["editable_paths"]

            result = await mcp.call_tool("update_profile", {"edits": [{"path": NOTES_PATH, "value": agent_notes}]})
            assert not result.is_error, result.content[0].text

            after = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)
            assert after["assistant_notes"] == agent_notes

    # ... and the agent's list is what the web shows, marked as an assistant edit.
    async with authenticated_async_context() as client:
        profile = (await client.get("/api/profile")).json()
        assert profile["preferences"]["assistant_notes"] == agent_notes
        edits = {e["path"]: e for e in profile["agent_edits"]}
        assert edits[NOTES_PATH]["set_by"] == "token:agent"
        assert edits[NOTES_PATH]["previous_value"] == web_notes
        # export_history carries the notes as they read now.
        export = (await client.get("/api/applications/export")).json()
        assert export["assistant_notes"] == agent_notes


@pytest.mark.asyncio
async def test_empty_notes_stay_silent(authenticated_async_context, fixture_user_id):
    """Rule #29 — no notes is an empty list, never an invented default."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        profile = (await client.get("/api/profile")).json()
        assert profile["preferences"]["assistant_notes"] == []
        assert profile["agent_edits"] == []
        export = (await client.get("/api/applications/export")).json()
        assert export["assistant_notes"] == []


@pytest.mark.asyncio
async def test_note_length_cap_is_enforced_on_both_doors(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "PROFILE_NOTE_MAX_CHARS", 12)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        too_long = await _save_prefs(client, {"assistant_notes": ["x" * 13]})
        assert too_long.status_code == 422 and "PROFILE_NOTE_MAX_CHARS" in too_long.text
        # the web refusal changed nothing
        assert (await client.get("/api/profile")).json()["preferences"]["assistant_notes"] == []
    async with _bearer_client(token) as agent:
        resp = await _patch(agent, {"path": NOTES_PATH, "value": ["y" * 13]})
        assert resp.status_code == 422 and "PROFILE_NOTE_MAX_CHARS" in resp.text
        # Control characters are stripped, blanks dropped, repeats collapsed.
        ok = await _patch(agent, {"path": NOTES_PATH, "value": ["no\tagencies", "  ", "No agencies"]})
        assert ok.status_code == 200, ok.text
        assert ok.json()["applied"][0]["value"] == ["no agencies"]


@pytest.mark.asyncio
async def test_note_count_cap_uses_the_list_limit(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_LIST_ITEMS", 2)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _save_prefs(client, {"assistant_notes": ["a", "b", "c"]})
        assert resp.status_code == 422 and "PROFILE_EDIT_MAX_LIST_ITEMS" in resp.text


# ═══════════════════════════════════════════════════════════════════════════
# 2. "Was X" + Take back
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_was_value_is_what_take_back_restores(authenticated_async_context, fixture_user_id):
    """"was X" is the BASE value — exactly what Take back falls back to."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _save_prefs(client, {"salary_min": 45000})).status_code == 200
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        # salary_min: the human's web save wrote the base (45000)
        assert (await _patch(agent, {"path": "preferences.salary_min", "value": 50000})).status_code == 200
        # cv_data.location: the CV's own value
        assert (await _patch(agent, {"path": "cv_data.location", "value": "Manchester"})).status_code == 200
        # headline: agent twice — NOT the agent's first value (Take back would
        # not restore that), but the base: the CV has no headline.
        await _patch(agent, {"path": "cv_data.headline", "value": "Data Engineer"})
        await _patch(agent, {"path": "cv_data.headline", "value": "Senior Data Engineer"})
    async with authenticated_async_context() as client:
        edits = {e["path"]: e for e in (await client.get("/api/profile")).json()["agent_edits"]}
    assert edits["preferences.salary_min"]["value"] == 50000
    assert edits["preferences.salary_min"]["previous_value"] == 45000
    assert edits["cv_data.location"]["previous_value"] == "London"
    assert edits["cv_data.headline"]["previous_value"] == ""
    assert edits["cv_data.headline"]["value"] == "Senior Data Engineer"


def _full_form(prefs: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """What the web form's autosave posts: EVERY field, from the MERGED
    profile the page shows, with the human's one change applied."""
    keys = (
        "target_job_titles", "additional_skills", "excluded_skills", "preferred_locations", "industries",
        "salary_min", "salary_max", "work_arrangement", "experience_level", "negative_keywords",
        "about_me", "needs_visa", "work_authorization_countries", "assistant_notes",
    )
    doc = {k: prefs.get(k) for k in keys}
    doc.update(changes)
    return doc


@pytest.mark.asyncio
async def test_autosave_does_not_leak_an_assistant_value_into_the_base(authenticated_async_context, fixture_user_id):
    """Review P1: web 50000 → assistant 60000 → the human edits a location
    (autosave posts salary 60000 back) → Take back must give 50000."""
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _save_prefs(client, {"salary_min": 50000})).status_code == 200
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": "preferences.salary_min", "value": 60000})).status_code == 200
    async with authenticated_async_context() as client:
        shown = (await client.get("/api/profile")).json()["preferences"]
        assert shown["salary_min"] == 60000
        resp = await _save_prefs(client, _full_form(shown, preferred_locations=["Leeds"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["preferences"]["preferred_locations"] == ["Leeds"]
        assert body["preferences"]["salary_min"] == 60000, "the assistant's value still shows"
        base = load_profile(fixture_user_id, with_overlay=False)
        assert base is not None and base.preferences.salary_min == 50000, "the base keeps the human's value"
        edits = {e["path"]: e for e in body["agent_edits"]}
        assert edits["preferences.salary_min"]["previous_value"] == 50000, "the mark survives the autosave"
        # No web row was invented for the salary the human did not touch.
        assert [r["set_by"] for r in await _history(client, "preferences.salary_min")] == ["token:claude", "web"]

        taken = await client.post("/api/profile/edits/take-back", json={"path": "preferences.salary_min"})
        assert taken.status_code == 200, taken.text
        assert taken.json()["preferences"]["salary_min"] == 50000


@pytest.mark.asyncio
async def test_was_value_after_a_restore_is_what_take_back_gives(authenticated_async_context, fixture_user_id):
    """Review P2: base 40000 (a version) → web 50000 → assistant 60000 →
    restore the 40000 version. The mark must say "was 40000", because Take
    back now restores 40000."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _save_prefs(client, {"salary_min": 40000})).status_code == 200
        versions = (await client.get("/api/profile/versions")).json()["versions"]
        v40 = versions[0]["id"]  # newest first: the 40000 save
        assert (await _save_prefs(client, {"salary_min": 50000})).status_code == 200
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": "preferences.salary_min", "value": 60000})).status_code == 200
    async with authenticated_async_context() as client:
        restored = await client.post(f"/api/profile/versions/{v40}/restore")
        assert restored.status_code == 200, restored.text
        edits = {e["path"]: e for e in restored.json()["agent_edits"]}
        assert restored.json()["preferences"]["salary_min"] == 60000, "restore leaves the assistant's edit"
        assert edits["preferences.salary_min"]["previous_value"] == 40000
        taken = await client.post("/api/profile/edits/take-back", json={"path": "preferences.salary_min"})
        assert taken.json()["preferences"]["salary_min"] == 40000


_SEQUENCES = [
    ["web:40000", "agent:60000"],
    ["web:40000", "agent:60000", "autosave"],
    ["web:40000", "agent:60000", "web:45000", "agent:70000", "autosave"],
    ["agent:60000", "agent:65000"],
    ["web:40000", "web:50000", "agent:60000", "restore:1"],
    ["web:40000", "agent:60000", "autosave", "restore:0", "autosave"],
    ["web:40000", "agent:60000", "restore:0", "web:42000", "agent:61000", "autosave"],
]


@pytest.mark.asyncio
@pytest.mark.parametrize("steps", _SEQUENCES, ids=["-".join(s) for s in _SEQUENCES])
async def test_invariant_was_value_equals_the_value_after_take_back(
    authenticated_async_context, fixture_user_id, steps
):
    """For any mix of web saves, assistant edits, autosaves and restores: the
    "was X" shown is exactly the value the field has right after Take back."""
    path = "preferences.salary_min"
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        oldest_first: list[int] = []
        for step in steps:
            kind, _, arg = step.partition(":")
            if kind == "web":
                assert (await _save_prefs(client, {"salary_min": int(arg)})).status_code == 200
            elif kind == "agent":
                async with _bearer_client(token) as agent:
                    assert (await _patch(agent, {"path": path, "value": int(arg)})).status_code == 200
            elif kind == "autosave":
                shown = (await client.get("/api/profile")).json()["preferences"]
                assert (await _save_prefs(client, _full_form(shown, about_me=f"note {step}"))).status_code == 200
            elif kind == "restore":
                versions = (await client.get("/api/profile/versions", params={"limit": 100})).json()["versions"]
                oldest_first = [v["id"] for v in reversed(versions)]
                resp = await client.post(f"/api/profile/versions/{oldest_first[int(arg)]}/restore")
                assert resp.status_code == 200, resp.text
        profile = (await client.get("/api/profile")).json()
        edits = {e["path"]: e for e in profile["agent_edits"]}
        if path not in edits:
            # Every sequence here ends with an assistant value in force.
            pytest.fail(f"{steps}: expected a live assistant edit on {path}")
        promised = edits[path]["previous_value"]
        taken = await client.post("/api/profile/edits/take-back", json={"path": path})
        assert taken.status_code == 200, taken.text
        assert taken.json()["preferences"]["salary_min"] == promised, steps


@pytest.mark.asyncio
async def test_take_back_appends_a_clear_and_shows_the_base(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _save_prefs(client, {"salary_min": 45000})).status_code == 200
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": "preferences.salary_min", "value": 50000})).status_code == 200
    # NOT rate-limited: a spent assistant budget must not block the human.
    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_PER_HOUR", 1)
    async with authenticated_async_context() as client:
        assert (await client.get("/api/profile")).json()["preferences"]["salary_min"] == 50000
        resp = await client.post("/api/profile/edits/take-back", json={"path": "preferences.salary_min"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["preferences"]["salary_min"] == 45000, "falls back to the base value"
        assert resp.json()["agent_edits"] == []
        rows = await _history(client, "preferences.salary_min")
        assert rows[0]["value"] is None and rows[0]["set_by"] == "web", "take back is a clearing row by the human"
        assert [r["value"] for r in rows[1:]] == [50000, 45000], "nothing is deleted"
        # Nothing left to take back now.
        again = await client.post("/api/profile/edits/take-back", json={"path": "preferences.salary_min"})
        assert again.status_code == 404


@pytest.mark.asyncio
async def test_take_back_refuses_unknown_paths_and_extra_fields(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        bad = await client.post("/api/profile/edits/take-back", json={"path": "cv_data.raw_text"})
        assert bad.status_code == 422 and "cv_data.location" in bad.text
        forged = await client.post(
            "/api/profile/edits/take-back", json={"path": "cv_data.location", "user_id": "someone-else"}
        )
        assert forged.status_code == 422
        hist = await client.get("/api/profile/edits/history", params={"path": "user_id"})
        assert hist.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# 3. One history
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_web_save_appends_a_web_row_and_wins_without_a_mark(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        await _patch(
            agent,
            {"path": "preferences.preferred_locations", "value": ["Manchester"]},
            {"path": "preferences.about_me", "value": "hire me"},
        )
    async with authenticated_async_context() as client:
        # The human changes locations and re-submits about_me UNCHANGED.
        resp = await _save_prefs(client, {"preferred_locations": ["Leeds"], "about_me": "hire me"})
        assert resp.status_code == 200, resp.text
        profile = (await client.get("/api/profile")).json()
        assert profile["preferences"]["preferred_locations"] == ["Leeds"], "the human's newer save wins"
        edits = {e["path"]: e for e in profile["agent_edits"]}
        assert "preferences.preferred_locations" not in edits, "a web row never renders a mark"
        assert all(e["set_by"] != "web" for e in profile["agent_edits"])
        assert edits["preferences.about_me"]["set_by"] == "token:claude", "an untouched assistant edit stays"

        rows = await _history(client, "preferences.preferred_locations")
        assert (rows[0]["value"], rows[0]["set_by"]) == (["Leeds"], "web"), "the web row holds the NEW value"
        assert rows[1]["value"] == ["Manchester"] and rows[1]["set_by"] == "token:claude"
        # Unchanged field → no new row.
        assert [r["set_by"] for r in await _history(client, "preferences.about_me")] == ["token:claude"]


@pytest.mark.asyncio
async def test_history_lists_both_authors_newest_first(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _save_prefs(client, {"salary_min": 40000})).status_code == 200
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": "preferences.salary_min", "value": 50000})).status_code == 200
    async with authenticated_async_context() as client:
        assert (await _save_prefs(client, {"salary_min": 45000})).status_code == 200
        rows = await _history(client, "preferences.salary_min")
        assert [(r["value"], r["set_by"]) for r in rows] == [
            (45000, "web"), (50000, "token:claude"), (40000, "web"),
        ]
        assert all(r["set_at"] for r in rows)
        # And the human's latest save is what the page shows.
        assert (await client.get("/api/profile")).json()["preferences"]["salary_min"] == 45000


@pytest.mark.asyncio
async def test_restore_is_not_hidden_behind_an_old_web_row(authenticated_async_context, fixture_user_id):
    """A web save's row carries its value; a restore must still show the
    restored value (it is recorded as the human's change)."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        versions = (await client.get("/api/profile/versions")).json()["versions"]
        seeded_id = versions[0]["id"]
        assert (await _save_prefs(client, {"target_job_titles": ["Analytics Engineer"]})).status_code == 200
        resp = await client.post(f"/api/profile/versions/{seeded_id}/restore")
        assert resp.status_code == 200, resp.text
        assert resp.json()["preferences"]["target_job_titles"] == ["Data Engineer"]
        rows = await _history(client, "preferences.target_job_titles")
        assert [(r["value"], r["set_by"]) for r in rows] == [
            (["Data Engineer"], "web"), (["Analytics Engineer"], "web"),
        ]


# ═══════════════════════════════════════════════════════════════════════════
# 4. Per-user scope (rules #12/#25)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_history_and_take_back_never_reach_another_user(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": "cv_data.location", "value": "Manchester"})).status_code == 200

    cookie = await _second_user_session_cookie("memory-other@example.com")
    async with _session_client(cookie) as other:
        assert await _history(other, "cv_data.location") == [], "another user's history is invisible"
        taken = await other.post("/api/profile/edits/take-back", json={"path": "cv_data.location"})
        assert taken.status_code == 404, taken.text

    async with authenticated_async_context() as client:
        profile = (await client.get("/api/profile")).json()
        assert profile["cv_detail"]["location"] == "Manchester", "the owner's edit is untouched"
        assert [r["set_by"] for r in await _history(client, "cv_data.location")] == ["token:claude"]


@pytest.mark.asyncio
async def test_history_and_take_back_need_a_signed_in_user(authenticated_async_context):
    # The fixture points the app at this test's schema; the client below
    # carries no session cookie.
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/profile/edits/history", params={"path": "cv_data.location"})).status_code == 401
        assert (await anon.post("/api/profile/edits/take-back", json={"path": "cv_data.location"})).status_code == 401
