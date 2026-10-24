"""Feature 10 - the outreach SEND gate (``check_outreach``).

Owner intent: the assistant may SEND an outreach email itself, but only after a
server check, so the user does not have to watch. ``may_send_outreach`` is a
pure function beside ``may_submit``; ``GET /api/contacts/{id}/outreach-check``
and the MCP ``check_outreach`` tool both answer through it. Pure tests need no
database; the API tests go through the real doors. Helpers are copied, never
imported from another test module (schema isolation).
"""
from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import assistant_settings as rules
from src.services.profile import edits
from src.services.profile.models import AssistantSettings, UserPreferences

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
INBOX = "preferences.daily_check"
SEND = "assistant_settings.outreach_send"
PAUSE = "assistant_settings.paused_until"
_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


# ═══════════════════════════════════════════════════════════════════════════
# The pure gate - every reason, in order
# ═══════════════════════════════════════════════════════════════════════════


def _cfg(**kw: Any) -> rules.EffectiveSettings:
    kw.setdefault("outreach_send", "auto")
    return rules.effective(AssistantSettings(**kw))


def _ok_facts(**kw: Any) -> rules.OutreachFacts:
    """Facts that pass every rule: the latest email draft to a linked person
    with an address and a found_via, never sent to, no reply, job open."""
    base: dict[str, Any] = {
        "message_found": True, "latest_draft": True, "channel": "email", "has_email": True,
        "already_sent": False, "replied": False, "linked": True, "application_closed": False,
        "found_via": "company_site", "company": "northwind",
    }
    base.update(kw)
    return rules.OutreachFacts(**base)


def _counts(today: int = 0, company: int = 0) -> rules.OutreachCounts:
    return rules.OutreachCounts(sent_today=today, company_sent_in_window=company)


def _gate(cfg=None, facts=None, counts=None) -> rules.OutreachDecision:
    return rules.may_send_outreach(cfg or _cfg(), facts or _ok_facts(), counts or _counts(), NOW)


# One way to trip each reason, in the order the gate checks them. Each case
# trips ONLY its own rule on top of otherwise-passing inputs.
_PAUSED = {"cfg": {"paused_until": "until_resumed"}}
_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("stop", "paused", _PAUSED),
    ("stop", "message_not_found", {"facts": {"message_found": False}}),
    ("stop", "not_latest_draft", {"facts": {"latest_draft": False}}),
    ("stop", "not_email_channel", {"facts": {"channel": "linkedin"}}),
    ("stop", "no_email", {"facts": {"has_email": False}}),
    ("stop", "email_bounced", {"facts": {"email_bounced": True}}),
    ("stop", "already_sent", {"facts": {"already_sent": True}}),
    ("stop", "replied", {"facts": {"replied": True}}),
    ("stop", "application_closed", {"facts": {"application_closed": True}}),
    ("stop", "daily_outreach_cap_reached", {"counts": {"today": 10}}),
    ("stop", "company_cap_reached", {"counts": {"company": 2}}),
    ("ask", "email_guessed", {"facts": {"email_source": "pattern_guess"}}),
    ("ask", "mode_not_auto", {"cfg": {"outreach_send": "draft_only"}}),
    ("ask", "no_found_via", {"facts": {"found_via": None}}),
    ("ask", "unlinked_contact", {"facts": {"linked": False}}),
    ("send", "auto_mode", {}),
]


def _run(trips: list[dict[str, Any]]) -> rules.OutreachDecision:
    cfg: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    counts: dict[str, Any] = {}
    for t in trips:
        cfg.update(t.get("cfg", {}))
        facts.update(t.get("facts", {}))
        counts.update(t.get("counts", {}))
    return _gate(_cfg(**cfg), _ok_facts(**facts), _counts(**counts))


def test_the_reason_table_is_the_gates_own_order():
    assert [r for _d, r, _t in _CASES] == list(rules.OUTREACH_REASONS)


@pytest.mark.parametrize(("decision", "reason", "trip"), _CASES, ids=[c[1] for c in _CASES])
def test_may_send_outreach_each_reason_alone(decision, reason, trip):
    verdict = _run([trip])
    assert (verdict.decision, verdict.reason) == (decision, reason)
    assert verdict.detail and "\n" not in verdict.detail


@pytest.mark.parametrize("index", range(len(_CASES) - 1), ids=[c[1] for c in _CASES[:-1]])
def test_may_send_outreach_an_earlier_reason_beats_every_later_one(index):
    """Trip this rule AND every rule after it: the earlier one must win."""
    decision, reason, _ = _CASES[index]
    verdict = _run([trip for _d, _r, trip in _CASES[index:]])
    assert (verdict.decision, verdict.reason) == (decision, reason)


def test_outreach_check_stop_paused_pure_until_a_time_and_expired():
    later = (NOW + timedelta(hours=2)).isoformat()
    assert _gate(_cfg(paused_until=later)).reason == "paused"
    earlier = (NOW - timedelta(hours=2)).isoformat()
    assert _gate(_cfg(paused_until=earlier)).reason == "auto_mode"


@pytest.mark.parametrize("value", ["", "draft_only", "DRAFT", "scheduled", "yes"])
def test_only_outreach_send_auto_sends(value):
    assert rules.effective(AssistantSettings(outreach_send=value)).outreach_send == "draft_only"
    assert _gate(_cfg(outreach_send=value)).reason == "mode_not_auto"
    assert _gate(_cfg(outreach_send="auto")).decision == "send"


def test_the_gmail_mode_no_longer_grants_sending():
    assert "inbox_mode" not in inspect.signature(rules.may_send_outreach).parameters


def test_outreach_send_is_a_gated_riskier_setting():
    assert SEND in rules.SETTING_PATHS and SEND in rules.GATED_PATHS
    assert rules.classify_change(SEND, "draft_only", "auto") == "riskier"
    assert rules.classify_change(SEND, "auto", "draft_only") == "safer"
    assert rules.classify_change(SEND, "auto", "auto") == "safer"
    assert rules.effective_path_value(SEND, "") == "draft_only"
    assert rules.effective_path_value(SEND, "bogus") == "draft_only"
    assert edits.validate_edit(SEND, "AUTO") == "auto"
    for bad in ("yes", True):
        with pytest.raises(edits.ProfileEditError) as info:
            edits.validate_edit(SEND, bad)
        assert info.value.status_code == 422 and SEND in str(info.value.detail)


def test_caps_are_operator_settings(monkeypatch):
    monkeypatch.setattr(settings, "OUTREACH_AUTO_SENDS_MAX_PER_DAY", 3)
    monkeypatch.setattr(settings, "OUTREACH_COMPANY_MAX", 5)
    assert _gate(counts=_counts(today=2, company=4)).decision == "send"
    assert _gate(counts=_counts(today=3)).reason == "daily_outreach_cap_reached"
    assert _gate(counts=_counts(company=5)).reason == "company_cap_reached"
    # No company (an unlinked contact) is never company-capped; it asks instead.
    assert _gate(facts=_ok_facts(linked=False, company=""), counts=_counts(company=99)).reason == "unlinked_contact"


# ═══════════════════════════════════════════════════════════════════════════
# API helpers
# ═══════════════════════════════════════════════════════════════════════════


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _patch(client: AsyncClient, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _contact(
    client: AsyncClient, app_id: int | None, *, email: str = "rita@northwind.example",
    found_via: str | None = "company_site", name: str = "Rita Recruiter",
) -> int:
    body: dict[str, Any] = {"name": name, "role": "Recruiter", "email": email}
    if app_id is not None:
        body["application_id"] = app_id
    if app_id is None:
        body["company"] = "Contoso"
    if found_via:
        body["found_via"] = found_via
    resp = await client.post("/api/contacts", json=body)
    assert resp.status_code == 201, resp.text
    return int(resp.json()["contact"]["id"])


async def _draft(client: AsyncClient, app_id: int, contact_id: int, channel: str = "email") -> int:
    resp = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "contact_id": contact_id, "channel": channel, "text": "Hi Rita, a short note."},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _sent(client: AsyncClient, contact_id: int) -> None:
    resp = await client.post(f"/api/contacts/{contact_id}/outreach", json={"entry": "sent", "channel": "email"})
    assert resp.status_code == 201, resp.text


async def _check(client: AsyncClient, contact_id: int, message_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/contacts/{contact_id}/outreach-check", params={"message_id": message_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _auto_ready(client: AsyncClient, user_id: str) -> tuple[int, int, int]:
    """A user in auto mode with one job, one linked contact and one email draft."""
    _seed_profile(user_id)
    await _patch(client, INBOX, "auto")
    await _patch(client, SEND, "auto")  # the web user's own click applies at once
    app_id = await _bring(client)
    cid = await _contact(client, app_id)
    mid = await _draft(client, app_id, cid)
    return app_id, cid, mid


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
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
    return str(lr.cookies.get("job360_session"))


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


def _row_counts() -> dict[str, int]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        return {
            t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])  # noqa: S608 — fixed names
            for t in ("contact_outreach", "application_events", "application_contacts", "contact_edits",
                      "profile_edits", "application_asks")
        }
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# The route
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_outreach_check_send_in_auto_mode(authenticated_async_context, fixture_user_id, audit_capture):
    async with authenticated_async_context() as client:
        _app, cid, mid = await _auto_ready(client, fixture_user_id)
        verdict = await _check(client, cid, mid)
    assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")
    assert verdict["sent_today"] == 0 and verdict["daily_max"] == settings.OUTREACH_AUTO_SENDS_MAX_PER_DAY
    logged = [r for r in audit_capture.records if r.get("event") == "outreach_check"]
    assert len(logged) == 1
    line = logged[0]
    assert (line["decision"], line["reason"], line["contact_id"]) == ("send", "auto_mode", cid)
    assert line["user_id"] == fixture_user_id and line["actor"] == "web"
    # Never the address, the message text or the company in any audit line.
    blob = json.dumps(audit_capture.records, default=str)
    assert "rita@northwind.example" not in blob and "a short note" not in blob


@pytest.mark.asyncio
async def test_daily_check_auto_alone_never_sends(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, INBOX, "auto")
        app_id = await _bring(client)
        cid = await _contact(client, app_id)
        mid = await _draft(client, app_id, cid)
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("ask", "mode_not_auto")
        view = (await client.get("/api/assistant-settings")).json()
        assert view["outreach_send"] == {"value": "", "effective": "draft_only"}
        await _patch(client, SEND, "draft_only")
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("ask", "mode_not_auto")
        await _patch(client, SEND, "auto")
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")
        view = (await client.get("/api/assistant-settings")).json()
        assert view["outreach_send"] == {"value": "auto", "effective": "auto"}


@pytest.mark.asyncio
async def test_an_assistant_cannot_turn_outreach_send_on_until_the_user_confirms(
    authenticated_async_context, fixture_user_id
):
    from src.api.main import app

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        app_id = await _bring(client)
        cid = await _contact(client, app_id)
        mid = await _draft(client, app_id, cid)
        agent_headers = {"Authorization": f"Bearer {token}"}

        async def agent_set(value: str) -> dict[str, Any]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test", headers=agent_headers
            ) as agent:
                resp = await agent.patch("/api/profile", json={"edits": [{"path": SEND, "value": value}]})
            assert resp.status_code == 200, resp.text
            return resp.json()

        body = await agent_set("auto")
        assert body["applied"] == [] and [w["path"] for w in body["waiting"]] == [SEND]
        assert body["profile"]["assistant_settings"]["outreach_send"]["effective"] == "draft_only"
        assert (await _check(client, cid, mid))["reason"] == "mode_not_auto"
        confirmed = await client.post(f"/api/assistant-settings/requests/{body['waiting'][0]['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")
        off = await agent_set("draft_only")
        assert off["waiting"] == [] and [a["path"] for a in off["applied"]] == [SEND]
        assert (await _check(client, cid, mid))["reason"] == "mode_not_auto"


@pytest.mark.asyncio
async def test_outreach_check_ask_when_mode_not_auto(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _app, cid, mid = await _auto_ready(client, fixture_user_id)
        await _patch(client, SEND, "draft_only")
        verdict = await _check(client, cid, mid)
        assert (verdict["decision"], verdict["reason"]) == ("ask", "mode_not_auto")
        # no found_via -> ask; a cold (unlinked) contact -> ask
        await _patch(client, SEND, "auto")
        app2 = await _bring(client, {**_AD, "title": "Platform Engineer", "apply_url": "https://n.example/2"})
        bare = await _contact(client, app2, email="ann@northwind.example", found_via=None, name="Ann")
        assert (await _check(client, bare, await _draft(client, app2, bare)))["reason"] == "no_found_via"
        cold = await _contact(client, None, email="cole@elsewhere.example", name="Cole")
        msg = await client.post(
            f"/api/contacts/{cold}/outreach", json={"entry": "message", "channel": "email", "text": "Hello Cole"},
        )
        assert msg.status_code == 201, msg.text
        verdict = await _check(client, cold, msg.json()["outreach"]["id"])
        assert (verdict["decision"], verdict["reason"]) == ("ask", "unlinked_contact")


@pytest.mark.asyncio
async def test_outreach_check_stop_paused(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _app, cid, mid = await _auto_ready(client, fixture_user_id)
        await _patch(client, PAUSE, "until_resumed")
        verdict = await _check(client, cid, mid)
    assert (verdict["decision"], verdict["reason"]) == ("stop", "paused")


@pytest.mark.asyncio
async def test_stop_already_sent(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _app, cid, mid = await _auto_ready(client, fixture_user_id)
        await _sent(client, cid)
        verdict = await _check(client, cid, mid)
    assert (verdict["decision"], verdict["reason"]) == ("stop", "already_sent")
    assert verdict["sent_today"] == 1


@pytest.mark.asyncio
async def test_stop_not_latest_draft(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid, first = await _auto_ready(client, fixture_user_id)
        second = await _draft(client, app_id, cid)
        assert (await _check(client, cid, first))["reason"] == "not_latest_draft"
        assert (await _check(client, cid, second))["reason"] == "auto_mode"
        # an id that is not this contact's message reads as not found (no oracle)
        other = await _contact(client, app_id, email="bo@northwind.example", name="Bo")
        assert (await _check(client, other, second))["reason"] == "message_not_found"
        assert (await _check(client, cid, 987654321))["reason"] == "message_not_found"


@pytest.mark.asyncio
async def test_stop_linkedin_channel(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid, _mid = await _auto_ready(client, fixture_user_id)
        linkedin = await _draft(client, app_id, cid, channel="linkedin")
        verdict = await _check(client, cid, linkedin)
    assert (verdict["decision"], verdict["reason"]) == ("stop", "not_email_channel")


@pytest.mark.asyncio
async def test_stop_no_email(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, INBOX, "auto")
        await _patch(client, SEND, "auto")
        app_id = await _bring(client)
        cid = await _contact(client, app_id, email="")
        mid = await _draft(client, app_id, cid)
        assert (await _check(client, cid, mid))["reason"] == "no_email"
        # an edit that ADDS the address is read (the current value, not the base row)
        edited = await client.patch(f"/api/contacts/{cid}", json={"email": "new@northwind.example"})
        assert edited.status_code == 200, edited.text
        assert (await _check(client, cid, mid))["reason"] == "auto_mode"


@pytest.mark.asyncio
async def test_stop_replied_and_application_closed(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid, mid = await _auto_ready(client, fixture_user_id)
        closed = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "rejected"})
        assert closed.status_code == 201, closed.text
        assert (await _check(client, cid, mid))["reason"] == "application_closed"
        reply = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "reply", "channel": "email"})
        assert reply.status_code == 201, reply.text
        assert (await _check(client, cid, mid))["reason"] == "replied"


@pytest.mark.asyncio
async def test_daily_cap(authenticated_async_context, fixture_user_id, monkeypatch):
    monkeypatch.setattr(settings, "OUTREACH_AUTO_SENDS_MAX_PER_DAY", 2)
    monkeypatch.setattr(settings, "OUTREACH_COMPANY_MAX", 50)
    async with authenticated_async_context() as client:
        app_id, cid, mid = await _auto_ready(client, fixture_user_id)
        for n in range(2):
            other = await _contact(client, app_id, email=f"p{n}@else.example", name=f"P{n}")
            await _sent(client, other)
        verdict = await _check(client, cid, mid)
    assert (verdict["decision"], verdict["reason"]) == ("stop", "daily_outreach_cap_reached")
    assert (verdict["sent_today"], verdict["daily_max"]) == (2, 2)


@pytest.mark.asyncio
async def test_company_cap_window(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.repositories import pgsync

    monkeypatch.setattr(settings, "OUTREACH_COMPANY_MAX", 2)
    async with authenticated_async_context() as client:
        app_id, cid, mid = await _auto_ready(client, fixture_user_id)
        # Two sends to people at the same company (spelled differently) on another job.
        app2 = await _bring(client, {**_AD, "company": "  NORTHWIND ", "title": "Analyst", "apply_url": "https://n.example/9"})
        for n in range(2):
            other = await _contact(client, app2, email=f"q{n}@northwind.example", name=f"Q{n}")
            await _sent(client, other)
        assert (await _check(client, cid, mid))["reason"] == "company_cap_reached"
        # Move both sends outside the 14-day window: the cap no longer counts them.
        old = (datetime.now(timezone.utc) - timedelta(days=settings.OUTREACH_COMPANY_WINDOW_DAYS + 1)).isoformat()
        conn = pgsync.connect(str(settings.DB_PATH))
        conn.execute("UPDATE contact_outreach SET recorded_at = ? WHERE entry = 'sent'", (old,))
        conn.commit()
        conn.close()
        verdict = await _check(client, cid, mid)
    assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")
    assert verdict["sent_today"] == 0


@pytest.mark.asyncio
async def test_outreach_check_other_users_contact_404(authenticated_async_context, fixture_user_id):
    from src.api.main import app

    async with authenticated_async_context() as client:
        _app, cid, mid = await _auto_ready(client, fixture_user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            unauth = await anon.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid})
        assert unauth.status_code == 401
        cookie = await _second_user_session_cookie()
        client.cookies.set("job360_session", cookie)
        resp = await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid})
    assert resp.status_code == 404 and resp.json()["detail"] == "contact not found"


@pytest.mark.asyncio
async def test_outreach_check_is_read_only(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _app, cid, mid = await _auto_ready(client, fixture_user_id)
        before = _row_counts()
        for _ in range(3):
            await _check(client, cid, mid)
        await _check(client, cid, 987654321)
    assert _row_counts() == before


@pytest.mark.asyncio
async def test_mcp_check_outreach_parity(authenticated_async_context, fixture_user_id):
    """The tool answers exactly what the route answers, from the same function."""
    pytest.importorskip("mcp")
    from mcp.server.mcpserver.exceptions import ToolError

    from src.api import mcp_server

    tool = mcp_server.build_server()._tool_manager.get_tool("check_outreach")
    assert tool is not None
    user = mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )
    async with authenticated_async_context() as client:
        app_id, cid, mid = await _auto_ready(client, fixture_user_id)
        token = mcp_server._current_user.set(user)
        try:
            route_send = await _check(client, cid, mid)
            assert await tool.fn(contact_id=cid, message_id=mid) == route_send
            linkedin = await _draft(client, app_id, cid, channel="linkedin")
            route_stop = await _check(client, cid, linkedin)
            assert await tool.fn(contact_id=cid, message_id=linkedin) == route_stop
            with pytest.raises(ToolError) as info:
                await tool.fn(contact_id=987654321, message_id=mid)
            assert str(info.value).startswith("404")
            # Turning the send switch off through the tool applies at once and closes the gate on both doors.
            await mcp_server.build_server()._tool_manager.get_tool("update_profile").fn(
                edits=[{"path": SEND, "value": "draft_only"}]
            )
            latest = await _draft(client, app_id, cid)  # the linkedin draft above superseded `mid`
            route_ask = await _check(client, cid, latest)
            assert await tool.fn(contact_id=cid, message_id=latest) == route_ask
            assert route_ask["reason"] == "mode_not_auto"
        finally:
            mcp_server._current_user.reset(token)
    assert (route_send["decision"], route_send["reason"]) == ("send", "auto_mode")
    assert (route_stop["decision"], route_stop["reason"]) == ("stop", "not_email_channel")
    assert set(route_send) == {"decision", "reason", "detail", "sent_today", "daily_max"}


def test_instructions_and_recipes_carry_the_outreach_gate():
    from src.api.mcp_server import instructions
    from src.api.routes.recipes import load_recipe

    hard = " ".join(instructions().split("\n")[1].lower().split())
    assert "never send outreach unless check_outreach says send" in hard
    reach = " ".join(load_recipe("reach").text.lower().split())
    assert "check_outreach(contact_id, message_id)" in reach
    assert "settings.outreach_send" in reach
    assert "`send`: press send in gmail" in reach
    # Owner 2026-10-24: an outreach `ask` is no longer an ask_user - the saved message shows under
    # People to reach on that job's Needs-you card.
    assert "`ask`: save a gmail draft; the saved message shows under people to reach on needs you (no `ask_user`)" in reach
    assert "`stop`: do not send" in reach and "record `outreach_sent`" in reach
    rules_text = " ".join(load_recipe("rules").text.lower().split())
    assert "check_outreach(contact_id, message_id)" in rules_text
    assert "never on the gmail mode" in rules_text
