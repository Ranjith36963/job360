"""Owner 2026-10-25 - the Home status card: one row per duty.

``GET /api/schedule/status`` says, for Hunt, Gmail check, People hunt and Apply: is it on, when it last
ran and how that ended, and when it runs next. ``state`` is the first that applies: off, running, due,
failing, held, waiting. Read-only: it never takes a duty. Helpers are defined here, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

HUNT = "assistant_settings.hunt_every_hours"


async def _status(client: AsyncClient) -> dict[str, dict[str, Any]]:
    resp = await client.get("/api/schedule/status")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["duties"]
    assert [r["duty"] for r in rows] == ["hunt", "inbox", "reach", "batch"]
    return {r["duty"]: r for r in rows}


@pytest.mark.asyncio
async def test_every_duty_is_off_until_the_user_turns_it_on(authenticated_async_context):
    async with authenticated_async_context() as client:
        rows = await _status(client)
        assert {r["state"] for r in rows.values()} == {"off"}
        assert rows["hunt"]["label"] == "Hunt" and rows["hunt"]["next_due_at"] is None


@pytest.mark.asyncio
async def test_a_hunt_goes_due_then_waiting_then_failing(authenticated_async_context):
    async with authenticated_async_context() as client:
        assert (await client.patch("/api/profile", json={"edits": [{"path": HUNT, "value": 6}]})).status_code == 200
        assert (await _status(client))["hunt"]["state"] == "due"
        assert (await _status(client))["hunt"]["state"] == "due"  # the card only looks: it takes nothing
        await client.post("/api/schedule/done", json={"duty": "hunt"})
        done = (await _status(client))["hunt"]
        assert (done["state"], done["last_outcome"], done["failures_in_row"]) == ("waiting", "done", 0)
        assert done["last_run_at"] and done["next_due_at"]
        await client.post("/api/schedule/done", json={"duty": "hunt", "outcome": "failed", "reason": "sign_in_needed"})
        failing = (await _status(client))["hunt"]
        assert (failing["state"], failing["last_outcome"], failing["last_reason"]) == ("failing", "failed", "sign_in_needed")


@pytest.mark.asyncio
async def test_the_status_is_the_users_own(authenticated_async_context):
    from fastapi.testclient import TestClient
    from httpx import ASGITransport

    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        assert (await client.patch("/api/profile", json={"edits": [{"path": HUNT, "value": 6}]})).status_code == 200
        await client.post("/api/schedule/done", json={"duty": "hunt"})
        sync_client, email = TestClient(app), "status-other@example.com"
        assert sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"}).status_code == 201
        conn = pgsync.connect(str(settings.DB_PATH))
        conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
        conn.commit()
        conn.close()
        cookie = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"}).cookies
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               cookies={"job360_session": str(cookie.get("job360_session"))}) as other:
            row = (await _status(other))["hunt"]
            assert (row["state"], row["last_run_at"]) == ("off", None)
