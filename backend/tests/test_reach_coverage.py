"""People hunt coverage audit (Kits, 2026-11-18): every application above the line gets its first people search
within a day. Reach took the 5 NEWEST each run, so with more than 5 arrivals between runs the older ones waited
behind every newer one and could age out of REACH_WINDOW_DAYS never searched. Helpers are copied, never imported.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings

PEOPLE = "assistant_settings.people_hunt"


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer: dbt, Snowflake.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _bring(client: AsyncClient, n: int) -> int:
    ad = {"title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote",
          "apply_url": f"https://northwind{n}.example/careers/{n}",
          "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code in (200, 201), resp.text
    return int(resp.json()["application_id"])


def _age(app_id: int, hours: float) -> None:
    from src.repositories import pgsync

    when = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute("UPDATE applications SET created_at = ? WHERE id = ?", (when, app_id))
        conn.commit()


def _stamp(user_id: str, app_id: int, hours_ago: float) -> None:
    """The slot a worked application gets (schedule.mark stamps reach:<id>)."""
    from src.repositories import pgsync

    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute("INSERT INTO assistant_duty_runs (user_id, duty, done_at) VALUES (?, ?, ?)",
                     (user_id, f"reach:{app_id}", when))
        conn.commit()


def _reach(answer: dict[str, Any]) -> list[int]:
    items = [d for d in answer["due"] if d["duty"] == "reach"]
    return items[0]["application_ids"] if items else []


@pytest.mark.asyncio
async def test_a_job_waiting_over_a_day_is_searched_before_newer_ones(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": PEOPLE, "value": True}]})
        assert resp.status_code == 200, resp.text
        ids = [await _bring(client, n) for n in range(settings.REACH_MAX_PER_RUN + 3)]
        oldest, older = ids[0], ids[1]
        _age(oldest, settings.REACH_FIRST_TRY_HOURS + 6)
        _age(older, settings.REACH_FIRST_TRY_HOURS + 1)
        listed = _reach((await client.get("/api/schedule/due")).json())
        assert len(listed) == settings.REACH_MAX_PER_RUN
        assert listed[:2] == [oldest, older], listed  # overdue first, oldest first
        assert listed[2] == ids[-1], listed  # then the newest never searched


@pytest.mark.asyncio
async def test_a_job_worked_before_waits_behind_ones_never_searched(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": PEOPLE, "value": True}]})
        assert resp.status_code == 200, resp.text
        tried = await _bring(client, 0)
        _stamp(fixture_user_id, tried, settings.REACH_RETRY_HOURS + 1)  # its retry is due again
        fresh = [await _bring(client, n) for n in range(1, 3)]
        listed = _reach((await client.get("/api/schedule/due")).json())
        assert listed == [fresh[1], fresh[0], tried], listed
