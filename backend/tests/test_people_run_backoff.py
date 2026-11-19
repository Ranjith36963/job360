"""People hunt overnight, 2026-11-18/19 (Kits orders, 01:10): the 22:56 run's reach and network were marked failed,
stamped 22:57:41 (the claim's start). The 2h back-off ended at 00:57:41, but the hourly task wakes at :56, so the
00:56 run read "not due" and the jobs waited a third hour: the back-off check had no DUE_GRACE, which every
interval check has. And a people run that could not reach one site or tool is recorded partial (no back-off),
never failed. Helpers are copied, never imported.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

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


async def _setup(client: AsyncClient, user_id: str) -> int:
    _seed_profile(user_id)
    resp = await client.patch("/api/profile", json={"edits": [{"path": PEOPLE, "value": True}]})
    assert resp.status_code == 200, resp.text
    ad = {"title": "Data Engineer", "company": "Northwind", "location": "Remote",
          "apply_url": "https://northwind.example/careers/1",
          "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote."}
    made = await client.post("/api/jobs/bring", json=ad)
    assert made.status_code in (200, 201), made.text
    return int(made.json()["application_id"])


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _failed_at(user_id: str, duty: str, when: datetime) -> None:
    """The slot a failed mark_done leaves (schedule.mark: outcome failed, one failure in a row)."""
    from src.repositories import pgsync

    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO assistant_duty_runs (user_id, duty, done_at, outcome, reason, failures) "
            "VALUES (?, ?, ?, 'failed', 'error', 1) ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at",
            (user_id, duty, when.isoformat()))
        conn.commit()


def _reach(answer: dict[str, Any]) -> list[int]:
    items = [d for d in answer["due"] if d["duty"] == "reach"]
    return items[0]["application_ids"] if items else []


@pytest.mark.asyncio
async def test_a_backoff_ending_minutes_after_the_wake_is_due_at_that_wake(authenticated_async_context,
                                                                          fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _setup(client, fixture_user_id)
        now = datetime.now(timezone.utc)
        _failed_at(fixture_user_id, "reach", now - timedelta(minutes=30))  # mid back-off: still waits
        assert _reach((await client.get("/api/schedule/due")).json()) == []
        # 22:57:41 failed, woken at 00:56:42: the 2h back-off ends 59 s after the wake
        _failed_at(fixture_user_id, "reach", now - timedelta(hours=2) + timedelta(seconds=59))
        assert _reach((await client.get("/api/schedule/due")).json()) == [app_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["network_blocked", "rate_limited"])
async def test_a_people_run_that_could_not_reach_a_tool_is_partial_not_failed(authenticated_async_context,
                                                                             fixture_user_id, reason):
    async with authenticated_async_context() as client:
        await _setup(client, fixture_user_id)
        async with await _agent(client) as agent:
            said = "Sprouts: timed out (blocked). Clay: 429 too many requests. Web search: no names."
            resp = await agent.post("/api/schedule/done", json={
                "duty": "network", "outcome": "failed", "reason": reason, "note": said})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["outcome"] == "partial" and body["failures_in_row"] == 0, body
            assert f"partial ({reason})" in body["note"] and "never failed" in body["note"], body["note"]
        rows = (await client.get("/api/schedule/status")).json()["duties"]
        row = next(r for r in rows if r["duty"] == "network")
        assert (row["last_outcome"], row["last_reason"]) == ("partial", reason), row
        web = await client.post("/api/schedule/done", json={"duty": "network", "outcome": "failed", "reason": reason})
        assert web.status_code == 200 and web.json()["outcome"] == "failed", web.text  # the website: as before
