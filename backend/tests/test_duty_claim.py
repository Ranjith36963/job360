"""Owner 2026-10-25 - one run per duty at a time.

When ``whats_due`` lists a duty to an assistant, that run has it for ``DUTY_CLAIM_MINUTES``: another run
(the next hourly tick, a Run now) is not given it and sees it under ``running`` instead. ``mark_done``
frees it. The website's own read only looks and takes nothing. Helpers are defined here, never imported.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import schedule
from src.services.profile.models import CVData, UserPreferences, UserProfile

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": 1, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": False,
        "queued": 0, "daily_left": None, "weekly_left": None, "inside_hours": True, "stamps": {},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


async def _setup(client: AsyncClient, user_id: str) -> AsyncClient:
    from src.api.main import app
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")
    on = await client.patch("/api/profile", json={"edits": [{"path": "assistant_settings.hunt_every_hours", "value": 1}]})
    assert on.status_code == 200, on.text
    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _due(client: AsyncClient, duties: tuple[str, ...] = ("hunt",)) -> dict[str, Any]:
    """A routine for one job names it (2026-10-25: a read without `duties` only looks)."""
    resp = await client.get("/api/schedule/due", params={"duties": list(duties)} if duties else None)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_duty_another_run_has_is_not_listed_until_its_time_runs_out():
    taken = _facts(running={"hunt": NOW - timedelta(minutes=10)})
    answer = schedule.due_now(taken, NOW)
    assert answer["due"] == [] and answer["running"] == {"hunt": (NOW - timedelta(minutes=10)).isoformat()}
    stale = _facts(running={"hunt": NOW - timedelta(minutes=settings.DUTY_CLAIM_MINUTES)})
    assert [d["duty"] for d in schedule.due_now(stale, NOW)["due"]] == ["hunt"]


@pytest.mark.asyncio
async def test_a_second_run_does_not_get_the_same_duty(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            first = await _due(agent)
            assert [d["duty"] for d in first["due"]] == ["hunt"] and first["running"] == {}
            second = await _due(agent)
            assert second["due"] == [] and second["nothing_due"] is True and set(second["running"]) == {"hunt"}
            done = await agent.post("/api/schedule/done", json={"duty": "hunt"})
            assert done.status_code == 200, done.text
            assert (await _due(agent))["running"] == {}  # mark_done freed it


@pytest.mark.asyncio
async def test_a_read_that_names_no_duty_only_looks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            for _ in range(2):
                plain = await _due(agent, duties=())
                assert [d["duty"] for d in plain["due"]] == ["hunt"] and plain["running"] == {}
            assert [d["duty"] for d in (await _due(agent))["due"]] == ["hunt"]


@pytest.mark.asyncio
async def test_the_website_only_looks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            for _ in range(2):
                assert [d["duty"] for d in (await _due(client))["due"]] == ["hunt"]
            assert [d["duty"] for d in (await _due(agent))["due"]] == ["hunt"]


@pytest.mark.asyncio
async def test_a_run_that_never_marks_done_frees_the_duty_in_time(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            assert [d["duty"] for d in (await _due(agent))["due"]] == ["hunt"]
            monkeypatch.setattr(settings, "DUTY_CLAIM_MINUTES", 0)
            assert [d["duty"] for d in (await _due(agent))["due"]] == ["hunt"]


@pytest.mark.asyncio
async def test_a_routine_for_one_job_takes_only_its_own(authenticated_async_context, fixture_user_id):
    """2026-10-25 on prod: an Apply routine's read took the Gmail check and the people hunt it never ran."""
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            other = await agent.get("/api/schedule/due", params={"duties": ["batch"]})
            assert other.status_code == 200 and other.json()["due"] == [] and other.json()["running"] == {}
            mine = (await agent.get("/api/schedule/due", params={"duties": ["hunt"]})).json()
            assert [d["duty"] for d in mine["due"]] == ["hunt"]
            again = (await agent.get("/api/schedule/due", params={"duties": ["hunt"]})).json()
            assert (again["nothing_due"], again["held_by_running"]) == (True, ["hunt"])  # never a silent nothing
            assert (await agent.get("/api/schedule/due", params={"duties": ["nap"]})).status_code == 422
