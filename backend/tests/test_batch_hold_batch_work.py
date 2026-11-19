"""Owner 2026-11-19 (yes in chat, reversing the 2026-11-01 rule): the batch is due only while it has batch work.

Overnight 18-19 Nov the batch came due at 22:41, 00:41 and 03:41 with nothing it may do: every queued row waited
on the owner or had no CV, and since #1646 (owner, 18 Nov) the batch never writes a CV (the CV run does). The
2026-11-01 rule ("a row without a CV keeps the batch due") kept it due anyway. Now batch work is exactly what
mark_done's stop counts (``queue_preflight.fillable_rows(with_cv=True)``) plus the rows the user pressed Send on
(``queue_preflight.submit_now_ids``); with none, ``batch_hold`` is ``waiting_on_you``. CV-less rows stay the CV run's.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile import schedule
from src.services.profile.models import CVData, UserPreferences, UserProfile

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": None, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": True,
        "queued": 3, "daily_left": 5, "weekly_left": None, "inside_hours": True, "stamps": {},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def _batch_due(answer: dict[str, Any]) -> bool:
    return "batch" in [d["duty"] for d in answer["due"]]


def test_no_batch_work_holds_the_batch_even_with_cv_less_rows():
    held = schedule.due_now(_facts(waiting_on_you=1, cv_missing=2, batch_work=0), NOW)
    assert not _batch_due(held) and held["batch_hold"] == "waiting_on_you"


def test_one_row_of_batch_work_makes_the_batch_due():
    due = schedule.due_now(_facts(waiting_on_you=2, batch_work=1), NOW)
    assert _batch_due(due) and due["batch_hold"] is None


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")


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


async def _auto(client: AsyncClient) -> None:
    on = await client.patch("/api/profile", json={"edits": [
        {"path": "assistant_settings.submit_mode", "value": "auto_when_sure"}]})
    assert on.status_code == 200, on.text


@pytest.mark.asyncio
async def test_a_queue_of_only_cv_less_rows_is_the_cv_runs_not_the_batchs(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _auto(client)
        await _queued(client, 1)  # no CV: the CV run's work
        answer = await _due(client)
    assert not _batch_due(answer) and answer["batch_hold"] == "waiting_on_you"


@pytest.mark.asyncio
async def test_a_row_with_its_cv_and_nothing_open_is_batch_work(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _auto(client)
        app_id = await _queued(client, 2)
        saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, Python."})
        assert saved.status_code in (200, 201), saved.text
        answer = await _due(client)
    assert _batch_due(answer) and answer["batch_hold"] is None


@pytest.mark.asyncio
async def test_a_row_the_user_pressed_send_on_keeps_the_batch_due(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _auto(client)
        app_id = await _queued(client, 3)
        saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, Python."})
        assert saved.status_code in (200, 201), saved.text
        fill = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": {
            "form_url": f"https://northwind.example/careers/{app_id}/apply", "fields_count": 1}})
        assert fill.status_code == 201, fill.text
        yes = await client.post(f"/api/applications/{app_id}/send/approve",
                                params={"form_filled_event_id": fill.json()["event_id"]})
        assert yes.status_code == 201, yes.text
        answer = await _due(client)
    assert _batch_due(answer) and answer["batch_hold"] is None
