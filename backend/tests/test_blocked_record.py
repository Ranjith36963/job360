"""S6 - the blocked record (owner decisions 2026-10-10).

`blocked` (closed payload, server-filled `assistant`) opens ONE Needs-you ask per
application + reason while it is open; `unblocked` closes it; check_submit answers
`ask` / `blocked` in between; the web "Mark resolved" is session-only; the audit
lines never carry the detail text.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import blocked as blocked_service
from src.services.profile import assistant_settings as rules

FORM = "https://careers.northwind.example/apply/7"
SECRET_DETAIL = "the page said hunter2-should-never-be-logged"

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}


# ── helpers (copied, never imported across test modules) ─────────────────────


async def _mint_token(client: AsyncClient, name: str = "claude-code") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any], **extra: Any):
    return await client.post(
        f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload, **extra}
    )


async def _open_asks(client: AsyncClient, app_id: int) -> list[dict[str, Any]]:
    resp = await client.get("/api/asks", params={"status": "open"})
    assert resp.status_code == 200, resp.text
    return [a for a in resp.json()["asks"] if a["application_id"] == app_id]


async def _check(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _blocked(**kw: Any) -> dict[str, Any]:
    return {"reason": "captcha", "step": "upload CV", "page_host": "jobs.northwind.example", **kw}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def log_capture():
    root = logging.getLogger()
    handler = _Capture()
    root.addHandler(handler)
    old = root.level
    root.setLevel(logging.DEBUG)
    audit = logging.getLogger("job360.audit")
    old_audit = audit.level
    audit.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(old)
    audit.setLevel(old_audit)


# ── pure: the gate ───────────────────────────────────────────────────────────


def test_gate_asks_blocked_and_a_yes_does_not_clear_it():
    cfg = rules.effective(rules.AssistantSettings(submit_mode="auto_when_sure"))
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=3)
    from datetime import datetime, timezone

    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    facts = rules.SubmitFacts(status="considering", has_receipt=False, submit_override=None,
                              cv_seen=True, approved=True, blocked=True)
    verdict = rules.may_submit(cfg, facts, FORM, counts, now=now)
    assert (verdict.decision, verdict.reason) == ("ask", "blocked")
    assert "blocked" in rules.REASONS


# ── payload validation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", sorted(blocked_service.BLOCKED_REASONS))
async def test_every_reason_is_accepted_and_stored_with_the_server_assistant(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _event(client, app_id, "blocked", _blocked(reason=reason, detail="a short note"))
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "considering"  # note-family: never moves the status
        controls = (await client.get(f"/api/applications/{app_id}/controls")).json()
        block = controls["blocked"]
        assert block["reason"] == reason and block["by"] == "web"
        assert block["page_host"] == "jobs.northwind.example" and block["step"] == "upload CV"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _blocked(assistant="someone-else"),            # server fills it; never trusted
        _blocked(password="hunter2"),                   # unknown key
        _blocked(reason="rate_limited"),                # not in the closed set
        {"step": "x"},                                  # reason missing
        _blocked(step="s" * 121),                       # oversize step
        _blocked(detail="d" * 301),                     # oversize detail
        _blocked(page_host="https://jobs.northwind.example/apply?id=7"),  # a full URL, not a host
        _blocked(page_host="jobs.northwind.example:8443"),              # a port
        _blocked(page_host="ada@jobs.northwind.example"),               # userinfo
        _blocked(step=["not", "text"]),
    ],
)
async def test_bad_blocked_payloads_are_422(authenticated_async_context, payload):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _event(client, app_id, "blocked", payload)
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_bad_unblocked_payloads_and_event_detail_are_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _event(client, app_id, "blocked", _blocked(), detail="page text")).status_code == 422
        assert (await _event(client, app_id, "blocked", _blocked())).status_code == 201
        assert (await _event(client, app_id, "unblocked", {"resolution": "magic"})).status_code == 422
        assert (await _event(client, app_id, "unblocked", {"resolution": "retried", "x": 1})).status_code == 422


# ── auto-ask, de-dup, unblocked closes it, the gate ──────────────────────────


@pytest.mark.asyncio
async def test_blocked_opens_one_ask_dedups_and_unblocked_closes_it(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _event(agent, app_id, "blocked", _blocked())).status_code == 201
        asks = await _open_asks(agent, app_id)
        assert len(asks) == 1
        assert asks[0]["question"] == (
            "Your assistant got stuck: a CAPTCHA at upload CV on jobs.northwind.example. What should it do?"
        )
        # Same reason again while the ask is open: no second ask.
        assert (await _event(agent, app_id, "blocked", _blocked(step="submit"))).status_code == 201
        assert len(await _open_asks(agent, app_id)) == 1
        # A different reason is a different ask.
        assert (await _event(agent, app_id, "blocked", _blocked(reason="login_needed"))).status_code == 201
        assert len(await _open_asks(agent, app_id)) == 2
        check = await _check(agent, app_id)
        assert (check["decision"], check["reason"]) == ("ask", "blocked")
        assert (await _event(agent, app_id, "unblocked", {"resolution": "skipped"})).status_code == 201
        assert await _open_asks(agent, app_id) == []
        assert (await _check(agent, app_id))["reason"] != "blocked"
        # Nothing blocked any more: a second unblocked is 409.
        assert (await _event(agent, app_id, "unblocked", {"resolution": "retried"})).status_code == 409
    async with authenticated_async_context() as client:
        assert (await client.get(f"/api/applications/{app_id}/controls")).json()["blocked"] is None


# ── the web "Mark resolved" ──────────────────────────────────────────────────


def test_resolve_route_is_session_only_with_auth_first():
    from src.api.auth_deps import require_session_user, require_user
    from src.api.routes.applications import router

    route = next(r for r in router.routes if getattr(r, "path", "") == "/applications/{application_id}/blocked/resolve")
    assert [d.dependency for d in route.dependencies] == [require_user]
    assert any(d.call is require_session_user for d in route.dependant.dependencies)


@pytest.mark.asyncio
async def test_mark_resolved_records_user_did_it_and_bearer_is_refused(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await client.post(f"/api/applications/{app_id}/blocked/resolve")).status_code == 409
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _event(agent, app_id, "blocked", _blocked(reason="bot_check"))).status_code == 201
        assert (await agent.post(f"/api/applications/{app_id}/blocked/resolve")).status_code in (401, 403)
    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/applications/{app_id}/blocked/resolve")
        assert resp.status_code == 201, resp.text
        assert resp.json()["blocked"] is None
        assert await _open_asks(client, app_id) == []
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        events = detail.get("events") or []
        unblocked = [e for e in events if e["event_type"] == "unblocked"]
        assert unblocked and unblocked[-1]["payload"] == {"resolution": "user_did_it", "assistant": "web"}


# ── audit ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_lines_carry_reason_and_actor_never_the_detail(authenticated_async_context, log_capture):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _event(client, app_id, "blocked", _blocked(detail=SECRET_DETAIL))).status_code == 201
        assert (await _event(client, app_id, "unblocked", {"resolution": "skipped"})).status_code == 201
    lines = [r for r in log_capture.records if r.get("event") in ("blocked", "unblocked")]
    assert {r["event"] for r in lines} == {"blocked", "unblocked"}
    blocked = next(r for r in lines if r["event"] == "blocked")
    assert blocked["reason"] == "captcha" and blocked["actor"] == "web" and blocked["result"] == "ok"
    assert blocked["application_id"] == app_id and blocked["user_id"]
    assert next(r for r in lines if r["event"] == "unblocked")["resolution"] == "skipped"
    for record in log_capture.records:
        assert "hunter2" not in repr(record)


# ── review follow-ups: atomicity, isolation, re-block, the ask cap ───────────


@pytest.mark.asyncio
async def test_a_failed_append_leaves_no_orphan_ask(authenticated_async_context, monkeypatch):
    from src.services.applications.spine import SpineError

    async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise SpineError(409, "x")

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        monkeypatch.setattr(blocked_service.spine, "append_event", boom)
        resp = await _event(client, app_id, "blocked", _blocked())
        monkeypatch.undo()
        assert resp.status_code == 409, resp.text
        assert await _open_asks(client, app_id) == []
        events = (await client.get(f"/api/applications/{app_id}")).json().get("events") or []
        assert not [e for e in events if e["event_type"] in ("asked", "blocked")]


@pytest.mark.asyncio
async def test_blocked_refuses_corrects_event_id_and_source(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _event(client, app_id, "blocked", _blocked(), corrects_event_id=1)).status_code == 422
        source = {"kind": "email", "message_id": "m-1", "sender": "a@b.example", "subject": "s",
                  "received_at": "2026-10-09T10:00:00Z"}
        assert (await _event(client, app_id, "blocked", _blocked(), source=source)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["captcha", "bot_check"])
async def test_a_captcha_or_bot_check_is_never_retried(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _event(client, app_id, "blocked", _blocked(reason=reason))).status_code == 201
        resp = await _event(client, app_id, "unblocked", {"resolution": "retried"})
        assert resp.status_code == 422 and "never retried" in resp.text
        assert (await _event(client, app_id, "unblocked", {"resolution": "skipped"})).status_code == 201


@pytest.mark.asyncio
async def test_retried_is_fine_for_other_reasons(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _event(client, app_id, "blocked", _blocked(reason="site_error"))).status_code == 201
        assert (await _event(client, app_id, "unblocked", {"resolution": "retried"})).status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["step", "detail"])
async def test_no_links_in_step_or_detail(authenticated_async_context, field):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _event(client, app_id, "blocked", _blocked(**{field: "see https://jobs.example.com/x"}))
        assert resp.status_code == 422 and "no links here" in resp.text


@pytest.mark.asyncio
async def test_another_users_application_is_404_and_opens_no_ask(authenticated_async_context):
    async with authenticated_async_context() as owner:
        app_id = await _bring(owner)
        token = await _mint_token(owner)
    # A second account's session cannot touch the owner's application.
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    sync = TestClient(app)
    email = "intruder-s6@example.com"
    assert sync.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"}).status_code == 201
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    login = sync.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    cookie = str(login.cookies.get("job360_session"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"job360_session": cookie}) as intruder:
        assert (await _event(intruder, app_id, "blocked", _blocked())).status_code == 404
        assert (await intruder.post(f"/api/applications/{app_id}/blocked/resolve")).status_code == 404
    async with _bearer_client(token) as agent:
        assert await _open_asks(agent, app_id) == []


@pytest.mark.asyncio
async def test_blocking_again_after_unblocked_opens_a_new_ask(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _event(client, app_id, "blocked", _blocked())).status_code == 201
        first = (await _open_asks(client, app_id))[0]["id"]
        assert (await _event(client, app_id, "unblocked", {"resolution": "user_did_it"})).status_code == 201
        assert (await _event(client, app_id, "blocked", _blocked())).status_code == 201
        asks = await _open_asks(client, app_id)
        assert len(asks) == 1 and asks[0]["id"] != first


@pytest.mark.asyncio
async def test_a_full_needs_you_queue_still_records_the_block(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "ASKS_MAX_OPEN_PER_USER", 0)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _event(client, app_id, "blocked", _blocked())).status_code == 201
        assert await _open_asks(client, app_id) == []
        assert (await client.get(f"/api/applications/{app_id}/controls")).json()["blocked"]["ask_id"] is None
        assert (await _check(client, app_id))["reason"] == "blocked"

