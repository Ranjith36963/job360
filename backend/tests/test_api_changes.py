"""GET /api/changes - the web page's live cursor (Needs-you redesign, PR 5).

Real Postgres, real HTTP doors, real MCP calls. Pinned here:
* every kind of write moves the cursor (an MCP tool call, an event, an answer, a pause,
  a waiting setting request, an unmatched mail) and moves the RIGHT area marker;
* web READS never move it - the trap: they write audit rows (``ready_to_send_read``), so
  only ``mcp_tool_call`` rows may count, or the page's own refetch loops forever;
* ``If-None-Match`` with the cursor answers 304 with no body;
* another user's writes never move my cursor (rules #12/#25);
* session only: no credentials 401, an agent token 403 ``session_required``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync

PAUSE = "assistant_settings.paused_until"


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


def _audit(event: str, user_id: str, **extra: Any) -> None:
    """One audit record through the real logger + DB tee, flushed so the row is there."""
    from src.utils import audit_trail
    from src.utils.logger import get_audit_logger, setup_audit_logger

    setup_audit_logger()
    get_audit_logger().info(event, extra={"event": event, "user_id": user_id, **extra})
    audit_trail.flush()


def _audit_count(user_id: str) -> int:
    from src.utils import audit_trail

    audit_trail.flush()
    return int(_sql("SELECT COUNT(*) FROM audit_log WHERE user_id = ?", (user_id,))[0][0])


async def _changes(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/changes")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert resp.headers["etag"] == f'"{body["cursor"]}"'
    assert resp.headers["cache-control"] == "no-store"
    return body


def _moved(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {k for k, v in after["areas"].items() if before["areas"][k] != v}


async def _bring(client: AsyncClient, n: int = 1) -> int:
    ad = {
        "title": f"Engineer {n}", "company": f"Company{n}", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://co{n}.example/careers/{n}", "description": "Build things.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _mint(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "agent"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def _bearer(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


async def _second_user_client(email: str) -> tuple[AsyncClient, str]:
    from fastapi.testclient import TestClient

    from src.api.main import app

    sync_client = TestClient(app)
    assert sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"}).status_code == 201
    _sql("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    user_id = str(_sql("SELECT id FROM users WHERE email = ?", (email,))[0][0])
    cookies = {"job360_session": str(lr.cookies.get("job360_session"))}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies), user_id


@pytest.mark.asyncio
async def test_a_real_mcp_ask_moves_the_cursor_and_stamps_the_assistant(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime
    from src.utils.logger import setup_audit_logger

    setup_audit_logger()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        token = await _mint(client)
        before = await _changes(client)
        assert before["assistant_last_active_at"] is None, "no assistant has called a tool yet"

        async with mcp_runtime():
            async with _mcp_client(token) as mcp:
                res = await mcp.call_tool("ask_user", {"question": "Notice period?", "application_id": app_id})
                assert not res.is_error, res.content
        after = await _changes(client)
        assert after["cursor"] != before["cursor"]
        assert after["assistant_last_active_at"], "the tool call is the assistant's last-active time"
        assert {"assistant", "asks"} <= _moved(before, after)
        assert "settings" not in _moved(before, after)


@pytest.mark.asyncio
async def test_an_mcp_tool_call_alone_moves_only_the_assistant_marker(authenticated_async_context):
    """The in-place ask merges have no timestamp: the mcp_tool_call row is what catches them."""
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        before = await _changes(client)
        _audit("mcp_tool_call", user_id, tool="get_profile", status="ok")
        after = await _changes(client)
        assert after["cursor"] != before["cursor"]
        assert _moved(before, after) == {"assistant"}
        newest = _sql(
            "SELECT occurred_at FROM audit_log WHERE user_id = ? AND event = 'mcp_tool_call' ORDER BY id DESC LIMIT 1",
            (user_id,),
        )[0][0]
        assert after["assistant_last_active_at"] == str(newest)


@pytest.mark.asyncio
async def test_web_reads_never_move_the_cursor_though_they_write_audit_rows(authenticated_async_context):
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        await _bring(client)
        first = await _changes(client)
        audit_rows = _audit_count(user_id)
        for path in ("/api/ready-to-send", "/api/asks", "/api/morning-check", "/api/profile",
                     "/api/applications", "/api/inbox-items", "/api/changes"):
            resp = await client.get(path)
            assert resp.status_code in (200, 404), (path, resp.text)  # /profile: 404 until a CV
        assert _audit_count(user_id) > audit_rows, "the trap is real: web reads DO write audit rows"
        assert _sql("SELECT 1 FROM audit_log WHERE user_id = ? AND event = 'ready_to_send_read'", (user_id,))
        _audit("login_ok", user_id)  # any non-assistant audit row: still no move
        assert (await _changes(client))["cursor"] == first["cursor"]


@pytest.mark.asyncio
async def test_each_write_moves_its_own_area(authenticated_async_context):
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        app_id = await _bring(client)

        # A new event (a web decision) moves applications, ready and other.
        before = await _changes(client)
        note = await client.post(f"/api/applications/{app_id}/events",
                                 json={"event_type": "note", "payload": {"detail": "x"}})
        assert note.status_code == 201, note.text
        after = await _changes(client)
        assert {"applications", "ready", "other"} <= _moved(before, after)
        assert "asks" not in _moved(before, after)

        # An ask the user answers on the web moves asks.
        made = await client.post("/api/asks", json={"question": "Salary?", "application_id": app_id})
        assert made.status_code == 201, made.text
        before = await _changes(client)
        answered = await client.post(f"/api/asks/{made.json()['id']}/answer", json={"answer": "60k"})
        assert answered.status_code == 200, answered.text
        after = await _changes(client)
        assert "asks" in _moved(before, after) and after["cursor"] != before["cursor"]

        # A pause is a profile_edits row (updated_at does not move): settings moves.
        before = await _changes(client)
        paused = await client.patch("/api/profile", json={"edits": [{"path": PAUSE, "value": "until_resumed"}]})
        assert paused.status_code == 200, paused.text
        after = await _changes(client)
        assert "settings" in _moved(before, after)
        assert "asks" not in _moved(before, after)

        # A setting request waiting for the user's OK moves settings.
        before = await _changes(client)
        now = datetime.now(timezone.utc).isoformat()
        _sql(
            "INSERT INTO assistant_setting_requests (user_id, path, value, requested_by, requested_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "assistant_settings.daily_cap", "20", "token:agent", now),
        )
        after = await _changes(client)
        assert "settings" in _moved(before, after)

        # Unmatched mail moves other.
        before = await _changes(client)
        _sql(
            "INSERT INTO inbox_items (user_id, message_id, recorded_by, created_at) VALUES (?, ?, ?, ?)",
            (user_id, "msg-1", "token:agent", now),
        )
        after = await _changes(client)
        assert _moved(before, after) == {"other"}


@pytest.mark.asyncio
async def test_if_none_match_with_the_cursor_is_304(authenticated_async_context):
    async with authenticated_async_context() as client:
        body = await _changes(client)
        tag = f'"{body["cursor"]}"'
        for header in (tag, f"W/{tag}", f'"stale", {tag}', body["cursor"]):
            resp = await client.get("/api/changes", headers={"If-None-Match": header})
            assert resp.status_code == 304, header
            assert resp.content == b""
            assert resp.headers["etag"] == tag
        stale = await client.get("/api/changes", headers={"If-None-Match": '"stale"'})
        assert stale.status_code == 200 and stale.json()["cursor"] == body["cursor"]

        await _bring(client)  # a write: the old tag no longer matches
        moved = await client.get("/api/changes", headers={"If-None-Match": tag})
        assert moved.status_code == 200 and moved.json()["cursor"] != body["cursor"]


@pytest.mark.asyncio
async def test_another_users_writes_never_move_my_cursor(authenticated_async_context):
    async with authenticated_async_context() as client:
        mine = await _changes(client)
        stranger, stranger_id = await _second_user_client("changes-stranger@example.com")
        async with stranger:
            theirs_before = await _changes(stranger)
            assert theirs_before["cursor"] != mine["cursor"], "the user id salts the cursor"
            app_id = await _bring(stranger, 2)
            assert (await stranger.post("/api/asks", json={"question": "x", "application_id": app_id})).status_code == 201
            paused = await stranger.patch("/api/profile", json={"edits": [{"path": PAUSE, "value": "until_resumed"}]})
            assert paused.status_code == 200, paused.text
            _audit("mcp_tool_call", stranger_id, tool="get_profile", status="ok")
            assert (await _changes(stranger))["cursor"] != theirs_before["cursor"]
        after = await _changes(client)
        assert after == mine


@pytest.mark.asyncio
async def test_session_only(authenticated_async_context):
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/changes")).status_code == 401
    async with authenticated_async_context() as client:
        token = await _mint(client)
    async with _bearer(token) as agent:
        resp = await agent.get("/api/changes")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "session_required"
