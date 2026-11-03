"""Owner 2026-11-03 - "5 people for one job": a worked application comes back until it has 5 people.

A run that worked an application holds it back REACH_RETRY_HOURS; after REACH_MAX_ATTEMPTS runs it is left
alone (nobody more could be proven), so the people hunt cannot loop on one job. Helpers are copied, never
imported from another test module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.profile.models import UserPreferences

PEOPLE = "assistant_settings.people_hunt"


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


async def _bring(client: AsyncClient) -> int:
    ad = {"title": "Data Engineer", "company": "Northwind", "location": "Remote",
          "apply_url": "https://northwind.example/careers/1",
          "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code in (200, 201), resp.text
    return int(resp.json()["application_id"])


async def _reach(client: AsyncClient) -> list[int]:
    resp = await client.get("/api/schedule/due")
    assert resp.status_code == 200, resp.text
    items = [d for d in resp.json()["due"] if d["duty"] == "reach"]
    return items[0]["application_ids"] if items else []


def _age_stamp(app_id: int, hours: float) -> None:
    """Move this application's reach stamp ``hours`` into the past (the retry clock)."""
    from src.repositories import pgsync

    when = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute("UPDATE assistant_duty_runs SET done_at = ? WHERE duty = ?", (when, f"reach:{app_id}"))
        conn.commit()


@pytest.mark.asyncio
async def test_a_worked_job_short_of_five_comes_back_after_the_retry_and_stops_after_the_tries(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, PEOPLE, True)
        app_id = await _bring(client)
        assert await _reach(client) == [app_id]
        for attempt in range(1, settings.REACH_MAX_ATTEMPTS + 1):
            done = await client.post("/api/schedule/done", json={"duty": "reach", "application_ids": [app_id]})
            assert done.status_code == 200, done.text
            assert await _reach(client) == []  # just worked: it waits
            _age_stamp(app_id, settings.REACH_RETRY_HOURS + 1)
            expected = [app_id] if attempt < settings.REACH_MAX_ATTEMPTS else []
            assert await _reach(client) == expected, attempt
        assert (settings.REACH_RETRY_HOURS, settings.REACH_MAX_ATTEMPTS) == (24, 3)
