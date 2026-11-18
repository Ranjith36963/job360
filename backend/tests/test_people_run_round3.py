"""People hunt, 2026-11-18 13:56 run (Kits round 3): whats_due gave five applications, two of them already taken
off the queue as duplicates of other applications; the run worked one company, marked only that one, and Job360
judged one job. Reach no longer lists a duplicate, and mark_done(reach) refuses a call that leaves out an application
this run was given. Helpers are copied, never imported (the review seed is the shared test helper).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
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


async def _setup(client: AsyncClient, user_id: str) -> None:
    _seed_profile(user_id)
    resp = await client.patch("/api/profile", json={"edits": [{"path": PEOPLE, "value": True}]})
    assert resp.status_code == 200, resp.text


async def _bring(client: AsyncClient, n: int) -> int:
    ad = {"title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote",
          "apply_url": f"https://northwind{n}.example/careers/{n}",
          "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code in (200, 201), resp.text
    return int(resp.json()["application_id"])


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _unqueue_as_duplicate(user_id: str, app_id: int, of: int) -> None:
    """The row a batch run's unqueued {reason: duplicate} stores (rules.md: a duplicate of an earlier application)."""
    from src.repositories import pgsync

    now = datetime.now(timezone.utc).isoformat()
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO application_events (user_id, application_id, event_type, detail, payload, occurred_at, "
            "recorded_at, recorded_by) VALUES (?, ?, 'unqueued', ?, ?, ?, ?, 'agent:test')",
            (user_id, app_id, f"Duplicate of #{of}", json.dumps({"reason": "duplicate"}), now, now),
        )
        conn.commit()


def _reach(answer: dict[str, Any]) -> list[int]:
    items = [d for d in answer["due"] if d["duty"] == "reach"]
    return items[0]["application_ids"] if items else []


@pytest.mark.asyncio
async def test_reach_never_lists_a_duplicate(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await _setup(client, fixture_user_id)
        first, dup = await _bring(client, 1), await _bring(client, 2)
        assert set(_reach((await client.get("/api/schedule/due")).json())) == {first, dup}
        _unqueue_as_duplicate(fixture_user_id, dup, first)
        assert _reach((await client.get("/api/schedule/due")).json()) == [first]


@pytest.mark.asyncio
async def test_a_reach_mark_that_leaves_out_a_given_job_is_refused(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await _setup(client, fixture_user_id)
        one, two = await _bring(client, 1), await _bring(client, 2)
        async with await _agent(client) as agent:
            due = (await agent.get("/api/schedule/due", params={"duties": ["reach"]})).json()
            assert set(_reach(due)) == {one, two}
            partial = {"duty": "reach", "application_ids": [one], "outcome": "partial",
                       "note": "Sprouts: none. Clay: none."}
            refused = await agent.post("/api/schedule/done", json=partial)
            assert refused.status_code == 409, refused.text
            assert f"not in application_ids: {two}" in refused.json()["detail"]
            both = await agent.post("/api/schedule/done", json={**partial, "application_ids": [one, two]})
            assert both.status_code == 200, both.text
            assert both.json()["score"]["per_job"] == {str(one): 0, str(two): 0}
            assert both.json()["outcome"] == "partial"
