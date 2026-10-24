"""Owner 2026-10-24 - the people hunt (duty ``reach``).

``whats_due`` lists new applications that still have no one to reach out to, so the user's own
scheduled task finds people for them; Job360 finds nobody itself. Pure tests pin the due rule;
API tests go through the real doors and pin the limits: at most REACH_MAX_PER_RUN applications a
run, REACH_PEOPLE_PER_ROLE people per company and role a week, and ``mark_done`` stamps only the
caller's own applications. Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import schedule
from src.services.profile.models import UserPreferences

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
HUNT = "assistant_settings.hunt_every_hours"


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": None, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": False,
        "queued": 0, "daily_left": None, "weekly_left": None, "inside_hours": True, "stamps": {},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def _ad(n: int, company: str = "Northwind", title: str = "Data Engineer") -> dict[str, Any]:
    return {
        "title": title, "company": company, "location": "Remote",
        "apply_url": f"https://{company.lower()}.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


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


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _reach(client: AsyncClient) -> list[int]:
    resp = await client.get("/api/schedule/due")
    assert resp.status_code == 200, resp.text
    items = [d for d in resp.json()["due"] if d["duty"] == "reach"]
    return items[0]["application_ids"] if items else []


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "scheduler"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _add_person(client: AsyncClient, app_id: int, name: str):
    return await client.post("/api/contacts", json={
        "name": name, "role": "Engineering Manager", "company": "Northwind", "application_id": app_id,
    })


# ── Pure rules ───────────────────────────────────────────────────────────────


def test_reach_runs_after_hunt_and_before_batch():
    answer = schedule.due_now(
        _facts(hunt_every_hours=1, inbox_mode="auto", check_every="1h", apply_on_own=True, queued=1,
               reach_ids=[7, 3]), NOW
    )
    assert [d["duty"] for d in answer["due"]] == ["inbox", "hunt", "reach", "batch"]
    assert answer["due"][2] == {"duty": "reach", "recipe": "reach", "reason": "new_applications",
                                "application_ids": [7, 3]}


def test_reach_rides_the_hunt_schedule_and_stays_silent_without_it():
    """Rule #29: a user who never scheduled a hunt is never woken to find people."""
    assert schedule.due_now(_facts(reach_ids=[7]), NOW)["nothing_due"] is True


# ── Through the real doors ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_application_is_due_until_marked_then_never_again(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        assert await _reach(client) == []
        await _patch(client, HUNT, 6)
        assert await _reach(client) == [app_id]
        resp = await client.post("/api/schedule/done", json={"duty": "reach", "application_ids": [app_id]})
        assert resp.status_code == 200, resp.text
        assert await _reach(client) == []


@pytest.mark.asyncio
async def test_mark_done_reach_takes_only_the_callers_own_ids(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        for body in ({"duty": "reach"}, {"duty": "hunt", "application_ids": [app_id]},
                     {"duty": "reach", "application_ids": list(range(1, 30))}):
            assert (await client.post("/api/schedule/done", json=body)).status_code == 422, body
        foreign = await client.post("/api/schedule/done", json={"duty": "reach", "application_ids": [app_id + 999]})
        assert foreign.status_code == 404, foreign.text


@pytest.mark.asyncio
async def test_a_run_gets_at_most_the_cap_newest_first(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, HUNT, 6)
        ids = [await _bring(client, _ad(n, company=f"Co{n}")) for n in range(settings.REACH_MAX_PER_RUN + 1)]
        assert await _reach(client) == list(reversed(ids))[: settings.REACH_MAX_PER_RUN]


@pytest.mark.asyncio
async def test_a_person_the_assistant_added_takes_the_application_off(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, HUNT, 6)
        app_id = await _bring(client, _ad(1))
        async with await _agent(client) as agent:
            assert (await _add_person(agent, app_id, "Priya Shah")).status_code in (200, 201)
        assert await _reach(client) == []


@pytest.mark.asyncio
async def test_two_people_per_company_and_role_a_week_for_the_assistant_only(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first = await _bring(client, _ad(1))
        other_role = await _bring(client, _ad(3, title="Analytics Engineer"))
        async with await _agent(client) as agent:
            assert (await _add_person(agent, first, "Priya Shah")).status_code in (200, 201)
            assert (await _add_person(agent, first, "Sam Lee")).status_code in (200, 201)
            third = await _add_person(agent, first, "Alex Kim")
            assert third.status_code == 422 and "wait for replies" in third.text
            assert (await _add_person(agent, other_role, "Alex Kim")).status_code in (200, 201)
        # The user's own add is never limited.
        assert (await _add_person(client, first, "Jo Park")).status_code in (200, 201)


@pytest.mark.asyncio
async def test_the_mcp_mark_done_stamps_reach(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context() as client:
            _seed_profile(fixture_user_id)
            await _patch(client, HUNT, 6)
            app_id = await _bring(client, _ad(1))
            due = await tools.get_tool("whats_due").fn()
            assert [d for d in due["due"] if d["duty"] == "reach"][0]["application_ids"] == [app_id]
            await tools.get_tool("mark_done").fn(duty="reach", application_ids=[app_id])
            assert await _reach(client) == []
            with pytest.raises(Exception):  # noqa: B017 - reach without ids is refused
                await tools.get_tool("mark_done").fn(duty="reach")
    finally:
        m._current_user.reset(token)
