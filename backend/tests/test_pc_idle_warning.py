"""Owner issue #12 (2026-11-05): Job360 says when the user's own browser has not worked the PC queue.

``open_now`` (from ``pc_on_hours``) only says the queue MAY run. When own-browser tasks wait and no PC session took,
released or claimed one for more than ``PC_IDLE_WARN_HOURS`` (or ever), the queue list, the Needs-you count and
``whats_due`` carry ``pc_idle``. Real rows through the real HTTP doors, value asserts (rule #21). The clock is the
fixture's own ``NOW``, frozen in the route modules: never a second read of the wall clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes import own_browser as own_browser_route
from src.api.routes import schedule as schedule_route
from src.core import settings
from src.services.applications import needs_you_count as needs_you_module

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)  # a Wednesday, inside 09:00-17:00 London (= UTC)
LIMIT = timedelta(hours=settings.PC_IDLE_WARN_HOURS)
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}


# ── helpers (copied, never imported from another test module) ────────────────


class _Clock:
    """Freezes ``datetime.now`` in the route modules at a time the test sets."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.at = NOW
        clock = self

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
                return clock.at if tz is None else clock.at.astimezone(tz)

        for module in (own_browser_route, schedule_route, needs_you_module):
            monkeypatch.setattr(module, "datetime", _Frozen)


async def _person(client: AsyncClient, applied: bool = False) -> tuple[int, int]:
    """One application with one LinkedIn draft; returns (contact_id, message_id)."""
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    added = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": "Priya", "role": "Recruiter", "linkedin_url": "https://www.linkedin.com/in/priya"},
    )
    assert added.status_code == 201, added.text
    contact_id = int(added.json()["contact"]["id"])
    draft = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "text": "Hi, a note", "contact_id": contact_id, "channel": "linkedin"},
    )
    assert draft.status_code == 201, draft.text
    if applied:  # a note waits on Needs you only once the application is sent (owner 2026-10-31)
        done = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "applied"})
        assert done.status_code == 201, done.text
    person = await client.get(f"/api/people?contact_id={contact_id}")
    return contact_id, int(person.json()["person"]["outreach"]["messages"][-1]["id"])


async def _waiting(client: AsyncClient, applied: bool = False) -> int:
    """A LinkedIn send the user pressed Send on: one task waits for the user's own browser."""
    contact_id, message_id = await _person(client, applied)
    click = await client.post(
        f"/api/contacts/{contact_id}/own-browser-send", json={"message_id": message_id, "decision": "approve"}
    )
    assert click.status_code == 200 and click.json()["state"] == "queued", click.text
    return contact_id


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "pc"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _pc_takes_at(client: AsyncClient, clock: _Clock, at: datetime) -> None:
    """The user's PC session takes the task at ``at`` (its claim is the activity), then the clock returns to NOW."""
    clock.at = at
    async with await _agent(client) as agent:
        got = await agent.post("/api/own-browser/take", json={"session": "pc"})
        assert got.status_code == 200 and got.json()["status"] == "task", got.text
    clock.at = NOW


async def _three_reads(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    queue = await client.get("/api/own-browser/queue")
    due = await client.get("/api/schedule/due")
    count = await client.get("/api/needs-you-count")
    for resp in (queue, due, count):
        assert resp.status_code == 200, resp.text
    return queue.json(), due.json(), count.json()


def _idle(since: datetime | None, waiting: int) -> dict[str, Any]:
    when = f"since {since:%Y-%m-%d %H:%M} UTC" if since is not None else "yet"
    tasks = f"{waiting} task waits" if waiting == 1 else f"{waiting} tasks wait"
    return {
        "since": since.isoformat() if since is not None else None, "waiting": waiting,
        "hours": settings.PC_IDLE_WARN_HOURS,
        "text": f"Nothing has worked your PC queue {when}; {tasks}. Start your PC browser session.",
    }


# ── the four cases ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tasks_waiting_and_no_pc_activity_ever_says_pc_idle_everywhere(authenticated_async_context, monkeypatch):
    _Clock(monkeypatch)
    async with authenticated_async_context() as client:
        await _waiting(client, applied=True)
        window = {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "from": "09:00", "to": "17:00"}
        edit = await client.patch(
            "/api/profile", json={"edits": [{"path": "assistant_settings.pc_on_hours", "value": window}]}
        )
        assert edit.status_code == 200, edit.text
        queue, due, count = await _three_reads(client)
    # Inside pc_on_hours the queue is open, and still never reads as "working": pc_idle says nobody is.
    assert queue["open_now"] is True and queue["why"] == "pc_on_hours" and queue["count"] == 1
    assert queue["pc_idle"] == _idle(None, 1)
    assert due["pc_idle"] == _idle(None, 1)
    assert count["by_actor"]["your_pc"] == 1 and count["pc_idle"] == _idle(None, 1)


@pytest.mark.asyncio
async def test_recent_pc_activity_means_no_pc_idle(authenticated_async_context, monkeypatch):
    clock = _Clock(monkeypatch)
    async with authenticated_async_context() as client:
        await _waiting(client, applied=True)
        await _pc_takes_at(client, clock, NOW - timedelta(hours=1))
        queue, due, count = await _three_reads(client)
    assert queue["count"] == 1 and queue["tasks"][0]["claim"] is None  # the claim lapsed: the task waits again
    assert queue["pc_idle"] is None and due["pc_idle"] is None and count["pc_idle"] is None
    assert count["by_actor"]["your_pc"] == 1


@pytest.mark.asyncio
async def test_no_waiting_tasks_means_no_warning(authenticated_async_context, monkeypatch):
    _Clock(monkeypatch)
    async with authenticated_async_context() as client:
        await _person(client, applied=True)  # a draft nobody pressed Send on: nothing waits for the PC
        queue, due, count = await _three_reads(client)
    assert queue["count"] == 0 and count["by_actor"]["your_pc"] == 0
    assert queue["pc_idle"] is None and due["pc_idle"] is None and count["pc_idle"] is None


@pytest.mark.asyncio
async def test_the_boundary_is_exactly_the_limit(authenticated_async_context, monkeypatch):
    clock = _Clock(monkeypatch)
    async with authenticated_async_context() as client:
        await _waiting(client, applied=True)
        await _pc_takes_at(client, clock, NOW - LIMIT)  # exactly the limit ago: not yet idle
        queue, due, count = await _three_reads(client)
        assert queue["pc_idle"] is None and due["pc_idle"] is None and count["pc_idle"] is None

        clock.at = NOW + timedelta(seconds=1)  # one second past the limit: idle since that take
        queue, due, count = await _three_reads(client)
    assert queue["pc_idle"] == _idle(NOW - LIMIT, 1)
    assert due["pc_idle"] == _idle(NOW - LIMIT, 1)
    assert count["pc_idle"] == _idle(NOW - LIMIT, 1)
