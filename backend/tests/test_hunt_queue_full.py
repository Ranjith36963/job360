"""Owner 2026-10-25 - back-pressure: no hunt while the batch queue is full.

While the queue already holds ``HUNT_QUEUE_MAX`` open jobs the hunt is not due (``hunt_hold``
``queue_full``), even when its time came; it is due again as the queue drains. The batch and the
inbox are not held by it. Helpers are defined here, never imported.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.profile import schedule

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": 1, "inbox_mode": "auto", "check_every": "", "paused": False, "apply_on_own": True,
        "queued": 0, "daily_left": 5, "weekly_left": None, "inside_hours": True,
        "stamps": {"hunt": NOW - timedelta(hours=2)},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def test_a_full_queue_holds_the_hunt_only():
    full = schedule.due_now(_facts(queued=settings.HUNT_QUEUE_MAX), NOW)
    assert [d["duty"] for d in full["due"]] == ["inbox", "batch"] and full["hunt_hold"] == "queue_full"
    room = schedule.due_now(_facts(queued=settings.HUNT_QUEUE_MAX - 1), NOW)
    assert [d["duty"] for d in room["due"]] == ["inbox", "hunt", "batch"] and room["hunt_hold"] is None
    off = schedule.due_now(_facts(hunt_every_hours=None, queued=99), NOW)
    assert off["hunt_hold"] is None  # no hunt set: nothing to hold


@pytest.mark.asyncio
async def test_the_route_holds_the_hunt_when_the_queue_is_full(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "HUNT_QUEUE_MAX", 1)
    async with authenticated_async_context() as client:
        on = await client.patch("/api/profile", json={"edits": [{"path": "assistant_settings.hunt_every_hours", "value": 1}]})
        assert on.status_code == 200, on.text
        assert [d["duty"] for d in (await _due(client))["due"]] == ["hunt"]
        brought = await client.post("/api/jobs/bring", json={
            "title": "Data Engineer", "company": "Northwind", "location": "London", "queue": True,
            "apply_url": "https://northwind.example/careers/1", "description": "Build the pipelines. " * 8,
        })
        assert brought.status_code == 200 and brought.json()["queued"] is True, brought.text
        held = await _due(client)
        assert held["hunt_hold"] == "queue_full" and "hunt" not in [d["duty"] for d in held["due"]]


async def _due(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/schedule/due")
    assert resp.status_code == 200, resp.text
    return resp.json()
