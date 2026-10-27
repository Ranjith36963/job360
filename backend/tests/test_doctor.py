"""Feature 7 - the agent doctor (``GET /api/doctor``, MCP ``doctor``) and
``report_missing_tool`` (``POST /api/missing-tools``).

Doctor is one defensive read of the account's state with ONE closed
``next_step``; report_missing_tool only stores. Helpers are copied, never
imported from another test module (schema isolation).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile.models import UserPreferences

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


def _seed_profile(user_id: str, *, titles: bool = True) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"] if titles else []),
        ),
        user_id,
        source_action="cv_upload",
    )


def _sql(query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(query, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


async def _doctor(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/doctor")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mint_token(client: AsyncClient, name: str = "claude-code") -> str:
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
    from tests._signin import sign_in

    sync_client = TestClient(app)
    cookie, _ = sign_in(sync_client, email)
    return cookie


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _Capture()
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


# ═══════════════════════════════════════════════════════════════════════════
# doctor
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_doctor_new_user_without_profile(authenticated_async_context, audit_capture):
    async with authenticated_async_context() as client:
        body = await _doctor(client)
    assert body["profile_exists"] is False
    assert body["next_step"] == "upload_cv"
    assert body["setup"] == {"done": 0, "total": 6, "next": "targets"}
    keys = [m["key"] for m in body["missing"]]
    assert keys[:2] == ["cv", "preferences.target_job_titles"]
    assert body["paused"] is False and body["paused_until"] == ""
    assert (body["open_asks"], body["waiting_settings"]) == (0, 0)
    assert body["latest_event_recorded_at"] is None and "whats_new(since=" in body["whats_new_hint"]
    assert body["outreach"] == {
        "sent_today": 0, "daily_max": settings.OUTREACH_AUTO_SENDS_MAX_PER_DAY, "blocked_today": 0,
    }
    assert body["auth"] == {"kind": "session", "actor": "web"}
    assert "unconfirmed" not in body  # feature 9 adds it
    logged = [r for r in audit_capture.records if r.get("event") == "doctor_read"]
    assert len(logged) == 1 and logged[0]["next_step"] == "upload_cv"


@pytest.mark.asyncio
async def test_doctor_verify_email_comes_first(authenticated_async_context, fixture_user_id, monkeypatch):
    monkeypatch.delenv("REQUIRE_EMAIL_VERIFICATION", raising=False)
    _sql("UPDATE users SET email_verified_at = NULL WHERE id = ?", (fixture_user_id,))
    async with authenticated_async_context() as client:
        body = await _doctor(client)  # NOT refused: an unverified user is told to verify
    assert body["email_verified"] is False and body["next_step"] == "verify_email"


@pytest.mark.asyncio
async def test_doctor_reports_token_vs_oauth_kind(authenticated_async_context, fixture_user_id):
    from src.api.auth_deps import CurrentUser
    from src.api.routes import doctor as doctor_route
    from src.repositories.database import JobDatabase

    async with authenticated_async_context() as client:
        token = await _mint_token(client, name="claude-code")
    async with _bearer_client(token) as agent:
        body = await _doctor(agent)
    assert body["auth"] == {"kind": "token", "actor": "token:claude-code"}
    # An OAuth-connected assistant: the same route function, as an oauth user.
    db = JobDatabase(str(settings.DB_PATH))
    await db.init_db()
    try:
        oauth = CurrentUser(
            id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="oauth", actor_name="Claude",
        )
        resp = await doctor_route.doctor(db, oauth)
    finally:
        await db.close()
    assert resp["auth"] == {"kind": "oauth", "actor": "agent:Claude"}


@pytest.mark.asyncio
async def test_doctor_missing_lists_contact_gaps_and_brief_gaps(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit
    from src.services.profile.search_brief import build_search_brief
    from src.services.profile.storage import load_profile_with_overlay

    _seed_profile(fixture_user_id, titles=False)
    async with authenticated_async_context() as client:
        body = await _doctor(client)
    keys = [m["key"] for m in body["missing"]]
    assert "cv" not in keys and "preferences.target_job_titles" in keys
    # The contact gaps are the kit's own (job-country rows dropped), never a copy.
    profile, overlay = load_profile_with_overlay(fixture_user_id)
    _blocks, kit_missing = kit.compute_answers(profile, overlay, None)
    contact = [m["key"] for m in kit_missing if m["key"].startswith("contact.")]
    assert contact and [k for k in keys if k.startswith("contact.")] == contact
    assert not [k for k in keys if k.startswith(("right_to_work.", "salary", "job_country"))]
    # The search-brief gaps (no place to search yet) ride along by path + ask.
    gaps = build_search_brief(profile.user_info.right_to_work)["gaps"]
    assert gaps and all(
        {"key": g["path"], "why": g["ask"]} in body["missing"] for g in gaps
    )
    assert body["next_step"] == "setup:targets"


@pytest.mark.asyncio
async def test_doctor_counts_asks_and_waiting(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    done = {r: {"done_at": "2026-10-01T09:00:00+00:00"} for r in
            ("targets", "you", "visa", "logistics", "equality", "settings")}
    async with authenticated_async_context() as client:
        token = await _mint_token(client)
        # all six setup rounds done (the web's own write applies at once)
        r = await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.apply_min_score", "value": 50},
            {"path": "preferences.daily_check", "value": "ask"},
            {"path": "user_info.right_to_work", "value": {"countries": [
                {"country": "GB", "remote_ok": True, "onsite": "yes", "needs_sponsorship": False}]}},
        ]})
        assert r.status_code == 200, r.text
        r = await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.setup_progress", "value": done}]})
        assert r.status_code == 200, r.text
        assert (await _doctor(client))["next_step"] == "ok"
        async with _bearer_client(token) as agent:
            # an assistant's RISKIER change waits for the user
            held = await agent.patch("/api/profile", json={"edits": [
                {"path": "assistant_settings.submit_mode", "value": "auto_when_sure"}]})
            assert held.status_code == 200, held.text
            body = await _doctor(agent)
            assert (body["waiting_settings"], body["next_step"]) == (1, "confirm_settings")
            for q in ("Which CV?", "Notice period?"):
                assert (await agent.post("/api/asks", json={"question": q})).status_code == 201
            body = await _doctor(agent)
    assert (body["open_asks"], body["waiting_settings"], body["next_step"]) == (2, 1, "answer_asks")


@pytest.mark.asyncio
async def test_doctor_shows_outreach_sent_and_blocked_today(authenticated_async_context, fixture_user_id):
    from src.utils.audit_trail import _DBAuditHandler

    # The production tee (audit_log rows) is installed by the app lifespan, which
    # the test fixture swaps out; attach the same DB handler, synchronously.
    audit = logging.getLogger("job360.audit")
    tee = _DBAuditHandler()
    audit.addHandler(tee)
    old_level = audit.level
    audit.setLevel(logging.INFO)
    try:
        await _outreach_today_flow(authenticated_async_context, fixture_user_id)
    finally:
        audit.removeHandler(tee)
        audit.setLevel(old_level)


async def _outreach_today_flow(authenticated_async_context, fixture_user_id) -> None:
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        bring = await client.post("/api/jobs/bring", json=_AD)
        app_id = bring.json()["application_id"]
        c = await client.post("/api/contacts", json={
            "name": "Rita", "email": "rita@northwind.example", "application_id": app_id})
        cid = c.json()["contact"]["id"]
        d = await client.post(f"/api/applications/{app_id}/artifacts", json={
            "kind": "outreach", "contact_id": cid, "channel": "linkedin", "text": "Hi"})
        mid = d.json()["artifact_id"]
        stop = await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid})
        assert stop.json()["decision"] == "stop"
        sent = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "sent", "channel": "linkedin"})
        assert sent.status_code == 201, sent.text
        body = await _doctor(client)
    assert body["outreach"]["sent_today"] == 1 and body["outreach"]["blocked_today"] == 1
    assert body["latest_event_recorded_at"]


@pytest.mark.asyncio
async def test_doctor_scoped_to_caller(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        assert (await client.post("/api/asks", json={"question": "Mine?"})).status_code == 201
        assert (await _doctor(client))["open_asks"] == 1
        client.cookies.set("job360_session", await _second_user_session_cookie())
        other = await _doctor(client)
    assert other["open_asks"] == 0 and other["profile_exists"] is False
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/doctor")).status_code == 401
        assert (await anon.post("/api/missing-tools", json={"name": "list_x"})).status_code == 401


@pytest.mark.asyncio
async def test_mcp_doctor_parity(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    _seed_profile(fixture_user_id)
    tools = mcp_server.build_server()._tool_manager
    user = mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )
    async with authenticated_async_context() as client:
        token = await _mint_token(client, name="cc")
        async with _bearer_client(token) as agent:
            route = await _doctor(agent)
        ctx = mcp_server._current_user.set(user)
        try:
            via_tool = await tools.get_tool("doctor").fn()
            stored = await tools.get_tool("report_missing_tool").fn(name="list_interviews", why="need dates")
        finally:
            mcp_server._current_user.reset(ctx)
    assert via_tool == route
    assert _sql("SELECT name, reported_by FROM missing_tool_reports WHERE id = ?", (stored["id"],)) == [
        ("list_interviews", "token:cc")
    ]


# ═══════════════════════════════════════════════════════════════════════════
# report_missing_tool
# ═══════════════════════════════════════════════════════════════════════════


def _table_counts() -> dict[str, int]:
    return {
        t: int(_sql(f"SELECT COUNT(*) FROM {t}")[0][0])  # noqa: S608 — fixed names
        for t in ("missing_tool_reports", "application_events", "application_asks", "profile_edits")
    }


@pytest.mark.asyncio
async def test_report_missing_tool_stores_only(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        token = await _mint_token(client, name="cc")
    before = _table_counts()
    async with _bearer_client(token) as agent:
        resp = await agent.post("/api/missing-tools", json={"name": "list_interviews", "why": "to plan the week"})
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    after = _table_counts()
    assert after["missing_tool_reports"] == before["missing_tool_reports"] + 1
    assert {k: v for k, v in after.items() if k != "missing_tool_reports"} == {
        k: v for k, v in before.items() if k != "missing_tool_reports"
    }
    assert _sql("SELECT user_id, name, why, reported_by FROM missing_tool_reports WHERE id = ?", (rid,)) == [
        (fixture_user_id, "list_interviews", "to plan the week", "token:cc")
    ]
    # Erased with the account and exported with it.
    from src.repositories.database import JobDatabase

    assert "missing_tool_reports" in JobDatabase._PER_USER_TABLES
    assert "missing_tool_reports" in JobDatabase._EXPORT_TABLES


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "x", "List_x", "1tool", "has-dash", "has space", "a" * 61, "ünï"])
async def test_report_missing_tool_rejects_bad_name(authenticated_async_context, name):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/missing-tools", json={"name": name})
        too_long = await client.post("/api/missing-tools", json={"name": "ok_name", "why": "w" * 281})
        extra = await client.post("/api/missing-tools", json={"name": "ok_name", "reported_by": "me"})
    assert resp.status_code == 422, resp.text
    assert too_long.status_code == 422 and "280" in too_long.text and "www" not in too_long.text
    assert extra.status_code == 422
    assert _table_counts()["missing_tool_reports"] == 0


@pytest.mark.asyncio
async def test_report_missing_tool_rate_limited(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "MISSING_TOOL_REPORTS_MAX_PER_DAY", 2)
    async with authenticated_async_context() as client:
        for _ in range(2):
            assert (await client.post("/api/missing-tools", json={"name": "tool_a"})).status_code == 201
        refused = await client.post("/api/missing-tools", json={"name": "tool_a"})
    assert refused.status_code == 429 and "MISSING_TOOL_REPORTS_MAX_PER_DAY" in refused.text
    assert _table_counts()["missing_tool_reports"] == 2


@pytest.mark.asyncio
async def test_missing_tool_why_never_logged(authenticated_async_context, audit_capture, caplog):
    secret = "SECRET-WHY-TEXT a@b.example"
    caplog.set_level(logging.DEBUG)
    async with authenticated_async_context() as client:
        ok = await client.post("/api/missing-tools", json={"name": "tool_b", "why": secret})
        assert ok.status_code == 201
        bad = await client.post("/api/missing-tools", json={"name": "Bad Name", "why": secret})
        assert bad.status_code == 422 and secret not in bad.text
    logged = [r for r in audit_capture.records if r.get("event") == "missing_tool_reported"]
    assert len(logged) == 1 and logged[0]["tool_name"] == "tool_b" and logged[0]["report_id"] == ok.json()["id"]
    blob = json.dumps(audit_capture.records, default=str) + caplog.text
    assert secret not in blob
