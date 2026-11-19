"""PC run pc-2306 (2026-11-18, 23:05 UTC) stopped after two tasks with 50 waiting, though both release replies said
``"next": "Keep going: 50 tasks still wait; call take again."`` (#1677). The model ignored the sentence at the one
point it had to choose. Now a release (or ``hold_released``) in a live PC run takes the next task for the SAME
session and returns it as ``next_task``: there is no choice left to get wrong. Real rows through the real routes
(rule #21); helpers are copied from test_pc_run_keeps_taking.py and test_pc_application_close_keeps_going.py."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth_deps import CurrentUser
from src.api.routes import browser as browser_routes
from src.services.applications import blocked as blocked_service
from src.services.profile.models import CVData, UserPreferences, UserProfile
from tests._pc_ready import pc_ready

HUNT = "assistant_settings.hunt_every_hours"
DONE = "Queue done: write the summary."
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
    await pc_ready(client, app_id)  # 2026-11-19: only a row check_submit can pass is a PC task
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


@pytest.mark.asyncio
async def test_a_duty_released_in_a_pc_run_hands_back_the_next_task_already_claimed(
    authenticated_async_context, fixture_user_id
):
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        async with await _agent(client) as agent:
            assert (await _take(agent))["task"]["kind"] == "linkedin_search"
            contact_id = await _clicked_person(client, 1)
            body = await _release(agent, "duty:linkedin_search", "done")
            nxt = body["next_task"]
            assert nxt["status"] == "task", nxt
            assert (nxt["task"]["kind"], nxt["task"]["contact_id"]) == ("linkedin_outreach", contact_id)
            assert body["next"] == (f"Do this task now: contact:{contact_id} (linkedin_outreach). "
                                    "Its claim is already yours. 40 of the run's 40 minutes are left: never stop early.")
            again = await _take(agent)  # the same session asking again gets that claim back: it is its own
            assert (again["status"], again["task"]["contact_id"]) == ("already_yours", contact_id)


@pytest.mark.asyncio
async def test_an_application_let_go_in_a_pc_run_hands_back_a_different_application(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        apps = [await _bring_blocked(client, authenticated_async_context, n) for n in (1, 2, 3)]
        async with await _agent(client) as agent:
            taken = int((await _take(agent, "pc-2306"))["task"]["application_id"])
            body = await _kit_then_let_go(agent, taken, "pc-2306")
            nxt = body["next_task"]
            assert nxt["status"] == "task", nxt
            other = int(nxt["task"]["application_id"])
            assert other in set(apps) - {taken}
            assert body["next"] == (f"Do this task now: application:{other} (application): start it with get_application_kit "
                                    "(its next). 40 of the run's 40 minutes are left: never stop early.")


@pytest.mark.asyncio
async def test_the_last_task_closed_says_the_queue_is_done(authenticated_async_context, fixture_user_id, monkeypatch):
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        async with await _agent(client) as agent:
            await _take(agent)
            body = await _release(agent, "duty:linkedin_search", "done")
            assert (body["next_task"]["status"], body["next"]) == ("empty", DONE)
            only = await _bring_blocked(client, authenticated_async_context, 1)
            assert int((await _take(agent))["task"]["application_id"]) == only
            let_go = await _kit_then_let_go(agent, only, "pc")
            assert (let_go["next_task"]["status"], let_go["next"]) == ("empty", DONE)


@pytest.mark.asyncio
async def test_a_session_that_is_not_the_pc_run_gets_no_next_task(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        apps = [await _bring_blocked(client, authenticated_async_context, n) for n in (1, 2)]
        async with await _agent(client) as agent:
            await _take(agent, "pc-2306")  # the PC run is live, under ANOTHER label
            body = await _kit_then_let_go(agent, apps[1], "batch-apply")
            assert body.get("next_task") is None and body["next"] is None


@pytest.mark.asyncio
async def test_the_task_just_released_is_never_the_next_task(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.services.applications import own_browser

    monkeypatch.setattr(own_browser, "RELEASE_BACKOFF_MINUTES", 0)  # not even with no backoff to keep it out
    monkeypatch.setattr(own_browser, "LINKEDIN_MIN_GAP_SECONDS", 0)
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        first = await _clicked_person(client, 1)
        second = await _clicked_person(client, 2)
        async with await _agent(client) as agent:
            assert (await _take(agent))["task"]["contact_id"] == first
            nxt = (await _release(agent, f"contact:{first}", "other"))["next_task"]
            assert (nxt["status"], nxt["task"]["contact_id"]) == ("task", second), nxt
            last = (await _release(agent, f"contact:{second}", "other"))["next_task"]
            assert last["status"] == "task" and last["task"]["contact_id"] == first  # the OTHER one, never second
