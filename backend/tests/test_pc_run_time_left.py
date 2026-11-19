"""PC run pc-0406 (2026-11-19, 04:05 UTC) stopped 3 minutes into its 40-minute budget ("the job-ad reading and Easy
Apply forms are slow"): the model misjudges its own time. Now Job360's clock tells it: the take slot keeps the run's
``started`` time, and a release's ``next`` says how many of the 40 minutes are left (or that they are up). Also an
application ``next_task`` is not claimed until its kit is read, so its ``next`` no longer says "claim is already
yours". Real rows through the real routes; helpers copied from test_pc_release_hands_next_task.py."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth_deps import CurrentUser
from src.api.routes import browser as browser_routes
from src.services.applications import blocked as blocked_service
from src.services.applications import own_browser
from src.services.profile.models import CVData, UserPreferences, UserProfile

HUNT = "assistant_settings.hunt_every_hours"
_AD = {"title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
       "description": "Build the pipelines."}


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _hunt_on(client: AsyncClient) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": HUNT, "value": 1}]})
    assert resp.status_code == 200, resp.text


async def _clicked_person(client: AsyncClient, n: int) -> int:
    brought = await client.post("/api/jobs/bring", json={**_AD, "title": f"Engineer {n}",
                                                          "apply_url": f"https://northwind.example/c/{n}"})
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    added = await client.post(f"/api/applications/{app_id}/contacts", json={
        "name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"})
    assert added.status_code == 201, added.text
    contact_id = int(added.json()["contact"]["id"])
    made = await client.post(f"/api/applications/{app_id}/artifacts", json={
        "kind": "outreach", "text": "Hi, a note", "contact_id": contact_id, "channel": "linkedin"})
    assert made.status_code == 201, made.text
    got = await client.get(f"/api/people?contact_id={contact_id}")
    message_id = int(got.json()["person"]["outreach"]["messages"][-1]["id"])
    click = await client.post(f"/api/contacts/{contact_id}/own-browser-send",
                              json={"message_id": message_id, "decision": "approve"})
    assert click.status_code == 200, click.text
    return contact_id


async def _bring_blocked(client: AsyncClient, ctx: Any, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://northwind{n}.example/careers/{n}", "description": "Build it. " * 8,
    })
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    user = CurrentUser(id=ctx.fixture_user_id, email="u@example.com")
    async with browser_routes._db() as db:  # noqa: SLF001
        await blocked_service.record_browser_block(
            db, user, app_id, {"reason": "captcha", "step": "Job360 browser", "page_host": "careers.example"}
        )
    return app_id


async def _take(agent: AsyncClient, session: str = "pc") -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/take", json={"session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _release(agent: AsyncClient, task_id: str, reason: str, session: str = "pc") -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/release", json={"session": session, "task_id": task_id, "reason": reason})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _kit_then_let_go(agent: AsyncClient, app_id: int, session: str) -> dict[str, Any]:
    kit = await agent.get(f"/api/applications/{app_id}/kit", params={"session": session, "compact": "true"})
    assert kit.status_code == 200, kit.text
    resp = await agent.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "hold_released", "payload": {"reason": "stopped"}, "detail": "check_submit said stop"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _age_run(session: str, minutes: int) -> None:
    """Make the take slot say the run began ``minutes`` ago (done_at stays fresh)."""
    started = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    async with browser_routes._db() as db:  # noqa: SLF001
        await db._db.execute(
            "UPDATE assistant_duty_runs SET state = ? WHERE duty = ?",
            (json.dumps({"session": session, "started": started}), own_browser.TAKE_SLOT),
        )
        await db._db.commit()


async def _slot_state() -> dict[str, Any]:
    async with browser_routes._db() as db:  # noqa: SLF001
        cur = await db._db.execute("SELECT state FROM assistant_duty_runs WHERE duty = ?", (own_browser.TAKE_SLOT,))
        return json.loads(dict(await cur.fetchone())["state"])


@pytest.mark.asyncio
async def test_a_duty_released_34_minutes_in_says_6_minutes_are_left(authenticated_async_context, fixture_user_id):
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        async with await _agent(client) as agent:
            assert (await _take(agent))["task"]["kind"] == "linkedin_search"
            contact_id = await _clicked_person(client, 1)
            await _age_run("pc", 34)
            body = await _release(agent, "duty:linkedin_search", "other")
            assert body["next"] == (f"Do this task now: contact:{contact_id} (linkedin_outreach). Its claim is "
                                    "already yours. 6 of the run's 40 minutes are left: never stop early.")


@pytest.mark.asyncio
async def test_an_application_handed_over_says_start_with_the_kit_and_the_minutes_left(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        apps = [await _bring_blocked(client, authenticated_async_context, n) for n in (1, 2, 3)]
        async with await _agent(client) as agent:
            taken = int((await _take(agent, "pc-0406"))["task"]["application_id"])
            await _age_run("pc-0406", 10)
            body = await _kit_then_let_go(agent, taken, "pc-0406")
            other = int(body["next_task"]["task"]["application_id"])
            assert other in set(apps) - {taken}
            assert body["next"] == (f"Do this task now: application:{other} (application): start it with "
                                    "get_application_kit (its next). 30 of the run's 40 minutes are left: "
                                    "never stop early.")


@pytest.mark.asyncio
async def test_released_41_minutes_in_says_the_run_is_up_and_takes_nothing(
    authenticated_async_context, fixture_user_id
):
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        async with await _agent(client) as agent:
            await _take(agent)
            contact_id = await _clicked_person(client, 1)
            await _age_run("pc", 41)
            body = await _release(agent, "duty:linkedin_search", "done")
            assert body["next"] == "The run's 40 minutes are up: write the summary."
            assert body.get("next_task") is None  # the route leaves a None out
            later = await _take(agent)  # the release claimed nothing: the person is still a fresh task
            assert (later["status"], later["task"]["contact_id"]) == ("task", contact_id)


@pytest.mark.asyncio
async def test_started_survives_a_second_take_and_resets_for_a_new_session(
    authenticated_async_context, fixture_user_id
):
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        async with await _agent(client) as agent:
            await _take(agent, "pc")
            await _age_run("pc", 20)
            first = (await _slot_state())["started"]
            await _take(agent, "pc")
            assert await _slot_state() == {"session": "pc", "started": first}
            await _take(agent, "pc-other")
            state = await _slot_state()
            assert state["session"] == "pc-other"
            assert datetime.fromisoformat(state["started"]) > datetime.fromisoformat(first) + timedelta(minutes=19)
