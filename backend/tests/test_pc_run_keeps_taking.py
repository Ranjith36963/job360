"""PC run pc-0911 (2026-11-18) ended after ONE duty with 49 tasks waiting: the recipe never said "take again" after
``linkedin_search`` and the release reply gave no next step. The release now answers ``next`` while this session
could take more, and the owner-clicked sends come before the duties. Real rows through the real doors (rule #21)."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

HUNT = "assistant_settings.hunt_every_hours"
_AD = {"title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
       "description": "Build the pipelines."}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
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
    """A LinkedIn contact with a drafted message the owner pressed Send on; returns the contact id."""
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


async def _release(agent: AsyncClient, task_id: str, reason: str, session: str = "pc") -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/release",
                            json={"session": session, "task_id": task_id, "reason": reason})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_take_hands_out_the_owner_clicked_send_before_the_hunt_duty(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        contact_id = await _clicked_person(client, 1)
        kinds = [t["kind"] for t in (await client.get("/api/own-browser/queue")).json()["tasks"]]
        assert "linkedin_search" in kinds and "linkedin_outreach" in kinds
        async with await _agent(client) as agent:
            got = (await agent.post("/api/own-browser/take", json={"session": "pc"})).json()
            assert got["status"] == "task", got
            assert (got["task"]["kind"], got["task"]["contact_id"]) == ("linkedin_outreach", contact_id)
            assert got["task"]["owner_yes"] is True


@pytest.mark.asyncio
async def test_queue_lists_owner_clicked_sends_before_the_duties(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        await _clicked_person(client, 1)
        kinds = [t["kind"] for t in (await client.get("/api/own-browser/queue")).json()["tasks"]]
        assert kinds.index("linkedin_outreach") < kinds.index("linkedin_search")


@pytest.mark.asyncio
async def test_release_of_the_hunt_duty_says_take_again_while_tasks_wait(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        async with await _agent(client) as agent:
            first = (await agent.post("/api/own-browser/take", json={"session": "pc"})).json()
            assert first["task"]["kind"] == "linkedin_search"
            # a send the owner pressed after this session took the duty: still open, and takeable by this session
            second = await _clicked_person(client, 2)
            body = await _release(agent, "duty:linkedin_search", "done")
            # pc-2306: the release takes that send for this session, so the run cannot stop here
            assert body["next"] == f"Do this task now: contact:{second} (linkedin_outreach). Its claim is already yours. 40 of the run's 40 minutes are left: never stop early."


@pytest.mark.asyncio
async def test_release_next_is_null_when_nothing_is_left(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _hunt_on(client)
        async with await _agent(client) as agent:
            await agent.post("/api/own-browser/take", json={"session": "pc"})
            body = await _release(agent, "duty:linkedin_search", "done")
            assert body["outcome"] == "done"
            assert body["next"] == "Queue done: write the summary."
