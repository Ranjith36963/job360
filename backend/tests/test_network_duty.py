"""Owner 2026-10-31 - the NETWORK hunt (duty ``network``): build the user's network from their profile.

While the user's own ``people_hunt`` switch is on, the duty is due once every ``NETWORK_EVERY_HOURS``
and lists ``max_people`` (``NETWORK_MAX_PER_RUN``). Job360 only says it is due; the assistant finds the
people and stores them as cold contacts (VISION rule 4). Helpers are copied, never imported.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.profile import schedule
from src.services.profile.models import UserPreferences

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
PEOPLE = "assistant_settings.people_hunt"


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": None, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": False,
        "queued": 0, "daily_left": None, "weekly_left": None, "inside_hours": True, "stamps": {},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def _network(answer: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in answer["due"] if d["duty"] == "network"]


def test_network_is_off_until_the_people_hunt_is_on():
    """Rule #29: a user who never turned the people hunt on is never woken to build a network."""
    answer = schedule.due_now(_facts(), NOW)
    assert _network(answer) == [] and "network" not in answer["next_due_at"]


def test_never_done_is_due_with_the_daily_cap():
    assert _network(schedule.due_now(_facts(people_hunt=True), NOW)) == [
        {"duty": "network", "recipe": "reach", "max_people": settings.NETWORK_MAX_PER_RUN, "reason": "never_done"}
    ]
    assert settings.NETWORK_MAX_PER_RUN == 10 and settings.NETWORK_EVERY_HOURS == 24


def test_once_a_day_elapsed_time_not_slots():
    ran = NOW - timedelta(hours=2)
    answer = schedule.due_now(_facts(people_hunt=True, stamps={"network": ran}), NOW)
    assert _network(answer) == []
    assert answer["next_due_at"]["network"] == (ran + timedelta(hours=24)).isoformat(timespec="seconds")
    late = schedule.due_now(_facts(people_hunt=True, stamps={"network": NOW - timedelta(hours=25)}), NOW)
    assert [d["reason"] for d in _network(late)] == ["interval_elapsed"]


def test_run_now_ignores_the_day_and_a_failure_backs_off():
    ran = NOW - timedelta(hours=1)
    pressed = _facts(people_hunt=True, stamps={"network": ran}, forced={"network": NOW - timedelta(minutes=1)})
    assert [d["reason"] for d in _network(schedule.due_now(pressed, NOW))] == ["run_now"]
    failed = _facts(people_hunt=True, stamps={"network": ran}, failures={"network": 1})
    answer = schedule.due_now(failed, NOW)
    assert _network(answer) == []  # one failure waits 2h, not the full day
    assert answer["next_due_at"]["network"] == (ran + timedelta(hours=2)).isoformat(timespec="seconds")


def test_it_runs_after_reach_and_before_the_batch():
    f = _facts(people_hunt=True, reach_ids=[7], apply_on_own=True, queued=2)
    assert [d["duty"] for d in schedule.due_now(f, NOW)["due"]] == ["reach", "network", "batch"]


# ── Through the real doors ───────────────────────────────────────────────────


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


@pytest.mark.asyncio
async def test_due_then_marked_done_then_waits_a_day(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert not [d for d in (await client.get("/api/schedule/due")).json()["due"] if d["duty"] == "network"]
        await _patch(client, PEOPLE, True)
        due = (await client.get("/api/schedule/due", params={"duties": ["network"]})).json()
        assert [(d["duty"], d["max_people"]) for d in due["due"]] == [("network", settings.NETWORK_MAX_PER_RUN)]
        bad = await client.post("/api/schedule/done", json={"duty": "network", "application_ids": [1]})
        assert bad.status_code == 422, bad.text  # application_ids is only for reach
        done = await client.post("/api/schedule/done", json={"duty": "network"})
        assert done.status_code == 200 and done.json()["duty"] == "network", done.text
        after = (await client.get("/api/schedule/due", params={"duties": ["network"]})).json()
        assert after["nothing_due"] is True and after["last_done_at"]["network"] is not None
        rows = (await client.get("/api/schedule/status")).json()["duties"]
        net = next(r for r in rows if r["duty"] == "network")
        assert net["label"] == "Network" and net["state"] == "waiting"
