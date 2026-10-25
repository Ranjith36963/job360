"""Owner 2026-10-25 - no Apply run while every queued job waits on the user.

The hourly Apply task woke 10+ times and did nothing: every queued job waited on the owner. Now the batch
is due only while at least one queued job can move on without the user. A job waits on the user when it
has an open ask, an open block, or a newest CV the user has not seen. When all of them do, whats_due says
``batch_hold: waiting_on_you`` with the count, and ``nothing_due`` is true. The user's answer frees a job
(and Ignition starts the run). Helpers are defined here, never imported (per-test schema isolation).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile import schedule
from src.services.profile.models import CVData, UserPreferences, UserProfile

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": None, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": True,
        "queued": 2, "daily_left": 5, "weekly_left": None, "inside_hours": True, "stamps": {},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def test_all_queued_jobs_waiting_means_no_run_and_one_ready_means_a_run():
    held = schedule.due_now(_facts(waiting_on_you=2), NOW)
    assert (held["batch_hold"], held["nothing_due"], held["waiting_on_you"]) == ("waiting_on_you", True, 2)
    one = schedule.due_now(_facts(waiting_on_you=1), NOW)
    assert [d["duty"] for d in one["due"]] == ["batch"] and one["batch_hold"] is None


async def _queued(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote", "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200 and resp.json()["queued"] is True, resp.text
    return int(resp.json()["application_id"])


async def _due(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/schedule/due")
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_asks_blocks_and_unseen_cvs_hold_the_batch_until_the_user_answers(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.storage import save_profile

    async with authenticated_async_context() as client:
        save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                                 preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                     fixture_user_id, source_action="cv_upload")
        on = await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.submit_mode", "value": "auto_when_sure"}]})
        assert on.status_code == 200, on.text
        asked, blocked, cv = await _queued(client, 1), await _queued(client, 2), await _queued(client, 3)
        ask = await client.post("/api/asks", json={"question": "Notice period?", "application_id": asked})
        assert ask.status_code in (200, 201), ask.text
        stuck = await client.post(f"/api/applications/{blocked}/events", json={
            "event_type": "blocked", "payload": {"reason": "captcha", "page_host": "jobs.northwind.example"}})
        assert stuck.status_code == 201, stuck.text
        saved = await client.post(f"/api/applications/{cv}/artifacts", json={"kind": "cv", "text": "Ada, Python."})
        assert saved.status_code in (200, 201), saved.text
        held = await _due(client)
        assert (held["batch_hold"], held["waiting_on_you"], held["nothing_due"]) == ("waiting_on_you", 3, True)
        answered = await client.post(f"/api/asks/{ask.json()['id']}/answer", json={"answer": "One month"})
        assert answered.status_code == 200, answered.text
        freed = await _due(client)
        assert [d["duty"] for d in freed["due"]] == ["batch"] and freed["waiting_on_you"] == 2
