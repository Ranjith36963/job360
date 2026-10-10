"""S5b - the morning check + the ready-to-send rule. Real HTTP doors, real rows, VALUE asserts (rule #21)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings

PAUSE, CAP = "assistant_settings.paused_until", "assistant_settings.daily_cap"
FORM = "https://careers.example/apply/7"
RECEIPT = {"channel": "company_site"}


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if statement.lstrip().upper().startswith("SELECT") else []
        conn.commit()
        return rows
    finally:
        conn.close()


def _age_everything(days: int) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    _sql("UPDATE application_events SET recorded_at = ?", (old,))
    _sql("UPDATE application_receipts SET sent_at = ?", (old,))


async def _bring(client: AsyncClient, n: int, company: str = "") -> int:
    ad = {
        "title": f"Engineer {n}", "company": company or f"Company{n}", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://co{n}.example/careers/{n}", "description": "Build things.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _fill(client: AsyncClient, app_id: int) -> None:
    assert (await _event(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 3})).status_code == 201


async def _check(client: AsyncClient, since: str | None = None) -> dict[str, Any]:
    resp = await client.get("/api/morning-check", params={"since": since} if since else None)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _counts(body: dict[str, Any]) -> dict[str, int]:
    return {k: v["count"] for k, v in body["tally"].items()}


async def _second_user_client(email: str) -> AsyncClient:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.repositories import pgsync

    sync_client = TestClient(app)
    assert sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"}).status_code == 201
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookies = {"job360_session": str(lr.cookies.get("job360_session"))}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies)


@pytest.mark.asyncio
async def test_each_bucket_counts_its_row_and_ignores_rows_before_since(authenticated_async_context):
    async with authenticated_async_context() as client:
        sent, blocked, waiting, failed, held = [await _bring(client, n) for n in range(1, 6)]
        assert (await client.post(f"/api/applications/{sent}/receipt", json=RECEIPT)).status_code == 201
        assert (await _event(client, blocked, "account_needed", {"host": "jobs.example.com"})).status_code == 201
        await _fill(client, waiting)
        assert (await _event(client, failed, "hold_released", {"reason": "failed"})).status_code == 201
        assert (await _event(client, held, "hold_released", {"reason": "blocked"})).status_code == 201
        assert (await _event(client, held, "hold_released", {"reason": "done"})).status_code == 201  # counts nowhere

        body = await _check(client)
        assert _counts(body) == {"sent": 1, "blocked": 2, "waiting": 1, "failed": 1}
        item = body["tally"]["sent"]["items"][0]
        assert (item["application_id"], item["company"], item["title"]) == (sent, "Company1", "Engineer 1")
        assert item["receipt_id"] is not None
        assert {i["application_id"] for i in body["tally"]["blocked"]["items"]} == {blocked, held}
        assert body["tally"]["waiting"]["items"][0]["application_id"] == waiting
        assert body["tally"]["failed"]["items"][0]["company"] == "Company4"

        _age_everything(3)  # all of it happened three days ago
        assert _counts(await _check(client)) == {"sent": 0, "blocked": 0, "waiting": 0, "failed": 0}
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        assert _counts(await _check(client, old)) == {"sent": 1, "blocked": 2, "waiting": 1, "failed": 1}


@pytest.mark.asyncio
async def test_ready_to_send_rules(authenticated_async_context):
    async def ids(client: AsyncClient) -> set[int]:
        return {i["application_id"] for i in (await _check(client))["tally"]["waiting"]["items"]}

    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        assert await ids(client) == set()
        await _fill(client, app_id)
        assert await ids(client) == {app_id}
        assert (await client.post(f"/api/applications/{app_id}/send/decline")).status_code == 201
        assert await ids(client) == set(), "a decline after the fill takes it out"
        await _fill(client, app_id)
        assert await ids(client) == {app_id}, "a NEW fill after the decline brings it back"
        cv = {"kind": "cv", "text": "Ada Lovelace\nSenior data engineer."}
        assert (await client.post(f"/api/applications/{app_id}/artifacts", json=cv)).status_code == 201
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        assert await ids(client) == set(), "an approval after the fill takes it out"
        await _fill(client, app_id)
        assert await ids(client) == {app_id}
        assert (await client.post(f"/api/applications/{app_id}/receipt", json=RECEIPT)).status_code == 201
        assert await ids(client) == set(), "a receipt takes it out"
        await _fill(client, await _bring(client, 2))
        async with await _second_user_client("ready-stranger@example.com") as stranger:
            assert _counts(await _check(stranger))["waiting"] == 0, "another user never sees it"


def test_since_is_clamped():
    from src.api.routes.morning_check import resolve_since

    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    hours = lambda raw: (now - resolve_since(raw, now)).total_seconds() / 3600  # noqa: E731
    assert [hours(None), hours("not-a-date"), hours("2026-10-12T12:00:00+00:00")] == [24, 24, 24]
    assert hours("2025-01-01T00:00:00+00:00") == 30 * 24
    assert hours("2026-10-10T07:00:00+00:00") == 5
    # Past the calendar edge once shifted to UTC: an OverflowError once made this a 500.
    assert [hours("0001-01-01T00:00:00+05:00"), hours("9999-12-31T23:59:59-05:00")] == [24, 24]


@pytest.mark.asyncio
async def test_state_running_then_paused_by_web_then_by_an_assistant(authenticated_async_context):
    async with authenticated_async_context() as client:
        running = (await _check(client))["state"]
        assert (running["paused"], running["paused_by"], running["paused_at"]) == (False, None, None)
        assert (running["apply_mode"], running["submit_mode"], running["daily_cap"]) == ("ask_each", "confirm", None)
        assert running["applied_today"] == 0

        edits = [{"path": PAUSE, "value": "until_resumed"}, {"path": CAP, "value": 10}]
        assert (await client.patch("/api/profile", json={"edits": edits})).status_code == 200
        state = (await _check(client))["state"]
        assert (state["paused"], state["paused_by"], state["daily_cap"]) == (True, "web", 10)
        assert state["paused_at"]

        app_id = await _bring(client, 1)
        assert (await client.post(f"/api/applications/{app_id}/receipt", json=RECEIPT)).status_code == 201
        assert (await _check(client))["state"]["applied_today"] == 1

        assert (await client.patch("/api/profile", json={"edits": [{"path": PAUSE, "value": ""}]})).status_code == 200
        assert (await _check(client))["state"]["paused"] is False

        from src.api.main import app

        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as agent:
            pause = await agent.patch("/api/profile", json={"edits": [{"path": PAUSE, "value": "until_resumed"}]})
            assert pause.status_code == 200, pause.text
            resume = await agent.patch("/api/profile", json={"edits": [{"path": PAUSE, "value": ""}]})
            assert resume.status_code == 200, resume.text
            assert [w["path"] for w in resume.json()["waiting"]] == [PAUSE], "an assistant's resume waits"
        assert (await _check(client))["state"]["paused"] is True
        stored = _sql("SELECT set_by FROM profile_edits WHERE path = ? ORDER BY id DESC LIMIT 1", (PAUSE,))[0][0]
        by = (await _check(client))["state"]["paused_by"]
        assert by == stored and by != "web"


@pytest.mark.asyncio
async def test_unauthenticated_is_401(authenticated_async_context):
    async with authenticated_async_context():
        pass
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/morning-check")).status_code == 401


@pytest.mark.asyncio
async def test_hold_released_accepts_failed_and_refuses_unknown(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        assert (await _event(client, app_id, "hold_released", {"reason": "failed"})).status_code == 201
        assert (await _event(client, app_id, "hold_released", {"reason": "exploded"})).status_code == 422


@pytest.mark.asyncio
async def test_the_log_carries_counts_never_values(authenticated_async_context):
    records: list[dict[str, Any]] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(dict(record.__dict__))

    root, audit = logging.getLogger(), logging.getLogger("job360.audit")
    handler, old, old_audit = Capture(), root.level, audit.level
    # job360.audit is propagate=False once the app's logging is set up (full suite), so
    # a root handler misses it; attach to the audit logger only (also avoids double capture).
    audit.addHandler(handler)
    root.setLevel(logging.DEBUG)
    audit.setLevel(logging.INFO)
    try:
        async with authenticated_async_context() as client:
            await _fill(client, await _bring(client, 1, company="Zebracorpsecret"))
            await _check(client)
    finally:
        audit.removeHandler(handler)
        root.setLevel(old)
        audit.setLevel(old_audit)
    mine = [r for r in records if r.get("event") == "morning_check_read"]
    assert len(mine) == 1 and (mine[0]["waiting"], mine[0]["sent"], mine[0]["result"]) == (1, 0, "ok")
    assert not any("Zebracorpsecret" in str(v) for r in mine for v in r.values())


_BLOCKED = {"reason": "captcha", "step": "upload CV", "page_host": "jobs.example.com"}


@pytest.mark.asyncio
async def test_a_blocked_record_counts_in_the_blocked_bucket(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        assert (await _event(client, app_id, "blocked", _BLOCKED)).status_code == 201
        body = await _check(client)
        assert _counts(body)["blocked"] == 1 and _counts(body)["failed"] == 0
        assert body["tally"]["blocked"]["items"][0]["application_id"] == app_id


@pytest.mark.asyncio
async def test_a_blocked_application_is_never_ready_to_send(authenticated_async_context):
    async def ids(client: AsyncClient) -> set[int]:
        return {i["application_id"] for i in (await _check(client))["tally"]["waiting"]["items"]}

    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        await _fill(client, app_id)
        assert await ids(client) == {app_id}
        assert (await _event(client, app_id, "blocked", _BLOCKED)).status_code == 201
        assert await ids(client) == set(), "an open block takes it out"
        assert (await _event(client, app_id, "unblocked", {"resolution": "user_did_it"})).status_code == 201
        assert await ids(client) == {app_id}, "unblocked brings it back"
