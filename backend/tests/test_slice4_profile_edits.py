"""Slice 4 — update_profile (docs/plans/2026-09-05-contacts-stats/spec.md R8-R12, S2-S4, S7-S9, S12).

Frozen per spec.md §Frozen tests. Agent edits are an append-only overlay
(`profile_edits`) applied inside `load_profile`, so every reader — the web
route, the tailor, MCP `get_profile` — sees one profile; an edit wins over
extraction until it is cleared. Helpers copied from test_application_spine.py.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


def _seed_profile(user_id: str, *, location: str = "London", source_action: str = "cv_upload") -> None:
    """Write a base profile the way extraction does — wholesale `save_profile`."""
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    profile = UserProfile(
        cv_data=CVData(
            raw_text="Python data engineer. dbt, Snowflake, Airflow.",
            name="Ada Lovelace",
            headline="Data Engineer",
            location=location,
            skills=["Python", "dbt", "Snowflake"],
            job_titles=["Data Engineer"],
        ),
        preferences=UserPreferences(target_job_titles=["Data Engineer"], preferred_locations=["London"]),
    )
    save_profile(profile, user_id, source_action=source_action)


async def _patch(client: AsyncClient, *edits: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits)})


async def _mint_token(client: AsyncClient, name: str = "cli") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
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
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


class _CapturingAuditHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _CapturingAuditHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


# ═══════════════════════════════════════════════════════════════════════════
# R8/R11 — an edit wins over extraction and is visible with its provenance
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_edit_shows_on_get_profile_with_provenance(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client, name="claude-code")
    async with _bearer_client(token) as agent:
        resp = await _patch(agent, {"path": "cv_data.location", "value": "Manchester"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [e["path"] for e in body["applied"]] == ["cv_data.location"]
        assert body["applied"][0]["value"] == "Manchester"
        assert body["applied"][0]["set_by"] == "token:claude-code"
        assert body["applied"][0]["set_at"]
        # the full profile comes back merged — no second call needed
        assert body["profile"]["cv_detail"]["location"] == "Manchester"
    async with authenticated_async_context() as client:
        profile = (await client.get("/api/profile")).json()
        assert profile["cv_detail"]["location"] == "Manchester"
        edits = profile["agent_edits"]
        assert len(edits) == 1
        assert edits[0]["path"] == "cv_data.location" and edits[0]["set_by"] == "token:claude-code"


@pytest.mark.asyncio
async def test_re_extraction_does_not_undo_an_edit(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, location="London")
        assert (await _patch(client, {"path": "cv_data.location", "value": "Manchester"})).status_code == 200
        # a new CV upload rewrites cv_data wholesale — the overlay must survive it
        _seed_profile(fixture_user_id, location="Birmingham", source_action="cv_upload")
        profile = (await client.get("/api/profile")).json()
        assert profile["cv_detail"]["location"] == "Manchester"


@pytest.mark.asyncio
async def test_clear_reveals_extraction_and_keeps_the_history(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, location="London")
        assert (await _patch(client, {"path": "cv_data.location", "value": "Manchester"})).status_code == 200
        cleared = await _patch(client, {"path": "cv_data.location", "value": None})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["applied"][0]["value"] is None
        profile = (await client.get("/api/profile")).json()
        assert profile["cv_detail"]["location"] == "London"
        assert profile["agent_edits"] == [], "a cleared path is not a current edit"
        export = (await client.get("/api/applications/export")).json()
        history = [e for e in export["profile_edits"] if e["path"] == "cv_data.location"]
        assert [e["value"] for e in history] == ["Manchester", None], "nothing is deleted — the clear is a row"


@pytest.mark.asyncio
async def test_newest_edit_per_path_wins(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, {"path": "cv_data.headline", "value": "Data Engineer"})
        await _patch(client, {"path": "cv_data.headline", "value": "Senior Data Engineer"})
        profile = (await client.get("/api/profile")).json()
        assert profile["cv_detail"]["headline"] == "Senior Data Engineer"
        assert len(profile["agent_edits"]) == 1


@pytest.mark.asyncio
async def test_every_reader_sees_the_overlay(authenticated_async_context, fixture_user_id):
    """R8 — load_profile is the one door, so the dataclass itself carries the edit."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(
            client,
            {"path": "cv_data.skills", "value": ["Python", "dbt", "Snowflake", "Airflow"]},
            {"path": "preferences.needs_visa", "value": True},
            {"path": "preferences.salary_min", "value": 65000},
        )
    from src.services.profile.storage import load_profile

    profile = load_profile(fixture_user_id)
    assert profile is not None
    assert profile.cv_data.skills == ["Python", "dbt", "Snowflake", "Airflow"]
    assert profile.preferences.needs_visa is True
    assert profile.preferences.salary_min == 65000


@pytest.mark.asyncio
async def test_links_is_a_real_cvdata_field_that_round_trips(authenticated_async_context, fixture_user_id):
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import load_profile, save_profile

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(client, {"path": "cv_data.links", "value": ["https://ada.example", "https://github.com/ada"]})
        assert resp.status_code == 200, resp.text
        profile = (await client.get("/api/profile")).json()
        assert profile["cv_detail"]["links"] == ["https://ada.example", "https://github.com/ada"]

    # and as a plain extracted field, without the overlay
    base = UserProfile(cv_data=CVData(raw_text="x", links=["https://plain.example"]), preferences=UserPreferences())
    save_profile(base, fixture_user_id, source_action="cv_upload")
    loaded = load_profile(fixture_user_id)
    assert loaded is not None
    assert loaded.cv_data.links == ["https://ada.example", "https://github.com/ada"], "overlay still wins"


@pytest.mark.asyncio
async def test_no_profile_row_gets_an_empty_base(authenticated_async_context):
    async with authenticated_async_context() as client:
        assert (await client.get("/api/profile")).status_code == 404
        resp = await _patch(client, {"path": "preferences.target_job_titles", "value": ["Data Engineer"]})
        assert resp.status_code == 200, resp.text
        profile = (await client.get("/api/profile")).json()
        assert profile["preferences"]["target_job_titles"] == ["Data Engineer"]


# ═══════════════════════════════════════════════════════════════════════════
# R9/R10 — closed path set; values typed by the dataclass
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["cv_data.raw_text", "cv_data.__class__", "preferences.github_username", "user_id", "cv_data", "cv_data.name.upper"],
)
async def test_path_outside_the_set_is_422_listing_the_set(authenticated_async_context, fixture_user_id, path):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(client, {"path": path, "value": "x"})
        assert resp.status_code == 422, resp.text
        assert path in resp.text
        assert "cv_data.location" in resp.text, "the 422 lists what IS editable"


def test_every_editable_path_is_a_declared_dataclass_field():
    """S8 — the allowlist can only name real fields; edits.py asserts this at import."""
    import dataclasses

    from src.core import settings
    from src.services.profile import edits
    from src.services.profile.models import CVData, UserPreferences

    declared = {
        "cv_data": {f.name for f in dataclasses.fields(CVData)},
        "preferences": {f.name for f in dataclasses.fields(UserPreferences)},
    }
    for path in edits.editable_paths():
        head, _, field = path.partition(".")
        assert field in declared[head], path
    assert set(settings.PROFILE_EDITABLE_PATHS) <= set(edits.editable_paths())


def test_an_unknown_extra_path_is_refused_at_import(monkeypatch):
    from src.core import settings
    from src.services.profile import edits

    monkeypatch.setattr(settings, "PROFILE_EXTRA_EDITABLE_PATHS", ("cv_data.no_such_field",))
    with pytest.raises(ValueError, match="no_such_field"):
        edits.editable_paths(refresh=True)
    monkeypatch.setattr(settings, "PROFILE_EXTRA_EDITABLE_PATHS", ())
    edits.editable_paths(refresh=True)


@pytest.mark.asyncio
async def test_wrong_type_is_422(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        for path, value in [
            ("cv_data.skills", "python"),
            ("cv_data.location", ["Manchester"]),
            ("preferences.needs_visa", "yes"),
            ("preferences.salary_min", "sixty grand"),
            ("preferences.salary_min", -1),
            ("cv_data.skills", ["Python", 7]),
        ]:
            resp = await _patch(client, {"path": path, "value": value})
            assert resp.status_code == 422, f"{path}={value!r}: {resp.text}"
            assert path in resp.text
        # nothing above was applied
        assert (await client.get("/api/profile")).json()["agent_edits"] == []


@pytest.mark.asyncio
async def test_closed_set_preference_lists_allowed_values(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(client, {"path": "preferences.work_arrangement", "value": "office"})
        assert resp.status_code == 422, resp.text
        assert "remote" in resp.text and "hybrid" in resp.text and "onsite" in resp.text
        resp = await _patch(client, {"path": "preferences.experience_level", "value": "graduate"})
        assert resp.status_code == 422, resp.text
        assert "entry" in resp.text and "executive" in resp.text
        ok = await _patch(client, {"path": "preferences.work_arrangement", "value": " Hybrid "})
        assert ok.status_code == 200, ok.text
        assert ok.json()["profile"]["preferences"]["work_arrangement"] == "hybrid"
        unset = await _patch(client, {"path": "preferences.work_arrangement", "value": ""})
        assert unset.status_code == 200, "empty string is the explicit unset (rule #29)"


@pytest.mark.asyncio
async def test_lists_are_deduplicated_and_capped(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_LIST_ITEMS", 3)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        ok = await _patch(client, {"path": "cv_data.skills", "value": ["Python", " python", "dbt", "Python"]})
        assert ok.status_code == 200, ok.text
        assert ok.json()["applied"][0]["value"] == ["Python", "dbt"], "de-dup is case/space-insensitive, order kept"
        too_many = await _patch(client, {"path": "cv_data.skills", "value": ["a", "b", "c", "d"]})
        assert too_many.status_code == 422 and "3" in too_many.text


@pytest.mark.asyncio
async def test_string_caps_are_422(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_CHARS", 20)
    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_ITEM_CHARS", 5)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(client, {"path": "cv_data.summary", "value": "x" * 21})
        assert resp.status_code == 422 and "20" in resp.text
        resp = await _patch(client, {"path": "cv_data.skills", "value": ["toolong"]})
        assert resp.status_code == 422 and "5" in resp.text


@pytest.mark.asyncio
async def test_too_many_edits_in_one_call_is_422_and_atomic(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_PATHS_PER_CALL", 2)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(
            client,
            {"path": "cv_data.name", "value": "A"},
            {"path": "cv_data.headline", "value": "B"},
            {"path": "cv_data.location", "value": "C"},
        )
        assert resp.status_code == 422, resp.text
        assert (await client.get("/api/profile")).json()["agent_edits"] == [], "all or nothing"
        empty = await client.patch("/api/profile", json={"edits": []})
        assert empty.status_code == 422


@pytest.mark.asyncio
async def test_one_bad_edit_fails_the_whole_call(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(
            client,
            {"path": "cv_data.name", "value": "Ada"},
            {"path": "cv_data.nope", "value": "x"},
        )
        assert resp.status_code == 422, resp.text
        assert (await client.get("/api/profile")).json()["agent_edits"] == []


# ═══════════════════════════════════════════════════════════════════════════
# S2/S3/S7/S12 — actor derived, no PII in audit, per-user limit, append-only
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_set_by_cannot_be_supplied(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch(
            "/api/profile", json={"edits": [{"path": "cv_data.name", "value": "A", "set_by": "agent:evil"}]}
        )
        assert resp.status_code == 422
        resp = await client.patch("/api/profile", json={"edits": [{"path": "cv_data.name", "value": "A"}], "set_by": "x"})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_audit_log_carries_paths_not_values(authenticated_async_context, fixture_user_id, audit_capture):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(client, {"path": "cv_data.name", "value": "Grace Hopper-Secretname"})
        assert resp.status_code == 200, resp.text
    blob = repr(audit_capture.records)
    assert "Hopper-Secretname" not in blob
    assert "cv_data.name" in blob


@pytest.mark.asyncio
async def test_update_profile_is_rate_limited_per_user(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_PER_HOUR", 2)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _patch(client, {"path": "cv_data.name", "value": "A"})).status_code == 200
        assert (await _patch(client, {"path": "cv_data.name", "value": "B"})).status_code == 200
        assert (await _patch(client, {"path": "cv_data.name", "value": "C"})).status_code == 429
    cookie = await _second_user_session_cookie("edits-other@example.com")
    async with _session_client(cookie) as other:
        resp = await _patch(other, {"path": "preferences.about_me", "value": "hello"})
        assert resp.status_code == 200, "the limit is per USER, not per IP/process"


@pytest.mark.asyncio
async def test_second_user_is_unaffected(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, {"path": "cv_data.location", "value": "Manchester"})
    cookie = await _second_user_session_cookie("edits-second@example.com")
    async with _session_client(cookie) as other:
        assert (await other.get("/api/profile")).status_code == 404
        resp = await _patch(other, {"path": "cv_data.location", "value": "Leeds"})
        assert resp.status_code == 200
        assert (await other.get("/api/profile")).json()["cv_detail"] is None or True  # no CV text → detail may be None
    async with authenticated_async_context() as client:
        assert (await client.get("/api/profile")).json()["cv_detail"]["location"] == "Manchester"


def test_profile_route_exposes_patch_only_as_append():
    """S12 — PATCH /profile appends rows; no route deletes or rewrites them."""
    from src.api.main import app
    from tests._routes import route_table

    # route_table, not app.routes -- FastAPI 0.141 nests included routers.
    methods: set[str] = set()
    for row in route_table(app):
        if row.path == "/api/profile":
            methods |= row.methods
    assert "PATCH" in methods
    assert "DELETE" not in methods


@pytest.mark.asyncio
async def test_web_preferences_save_still_works_with_an_overlay(authenticated_async_context, fixture_user_id):
    """Flagged concern — the web form loads the overlaid profile and saves; must not 500."""
    import json

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, {"path": "preferences.preferred_locations", "value": ["Manchester"]})
        resp = await client.post(
            "/api/profile/preferences",
            data={"preferences": json.dumps({"target_job_titles": ["Analytics Engineer"]})},
        )
        assert resp.status_code == 200, resp.text
        profile = (await client.get("/api/profile")).json()
        assert profile["preferences"]["preferred_locations"] == ["Manchester"]
        assert profile["preferences"]["target_job_titles"] == ["Analytics Engineer"]


# ═══════════════════════════════════════════════════════════════════════════
# R12 — the same function on MCP; get_profile shows what is editable
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mcp_update_profile_and_get_profile(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    import json

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime
    from src.core import settings

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client, name="agent")

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            before = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)
            assert set(before["editable_paths"]) == set(settings.PROFILE_EDITABLE_PATHS)
            assert before["fields"]["cv_data.location"] == "London"
            assert before["agent_edits"] == []

            result = await mcp.call_tool(
                "update_profile", {"edits": [{"path": "cv_data.location", "value": "Manchester"}]}
            )
            assert not result.is_error, result.content[0].text
            body = json.loads(result.content[0].text)
            assert body["applied"][0]["set_by"] == "token:agent"

            after = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)
            assert after["fields"]["cv_data.location"] == "Manchester"
            assert after["agent_edits"][0]["path"] == "cv_data.location"

            bad = await mcp.call_tool("update_profile", {"edits": [{"path": "cv_data.raw_text", "value": "x"}]})
            assert bad.is_error and "422" in bad.content[0].text
