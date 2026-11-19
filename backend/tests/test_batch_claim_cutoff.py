"""2026-11-17: closing a batch run only demands the rows queued before its claim.

Prod: ``mark_done(duty="batch", outcome="partial")`` was refused 409 while any Job360-browser queued row
needed nothing from the owner. A hunt adding rows while the batch ran kept that 409 alive (each fix brought a
new row), until the run gave up with the batch unmarked. Now the 409 counts only rows queued at or before
this run's claim (the ``running:batch`` stamp ``whats_due(duties=["batch"])`` writes); rows queued after it
wait for the next batch, and the reply says so. The website never claims, so its own stop keeps the old rule.
Helpers are defined here, never imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile.models import CVData, UserPreferences, UserProfile


async def _agent(client: AsyncClient, user_id: str) -> AsyncClient:
    from src.api.main import app
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")
    on = await client.patch("/api/profile", json={
        "edits": [{"path": "assistant_settings.submit_mode", "value": "auto_when_sure"}]})
    assert on.status_code == 200, on.text
    token = (await client.post("/api/tokens", json={"name": "apply-routine"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _queued_job(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote", "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200 and resp.json()["queued"] is True, resp.text
    return int(resp.json()["application_id"])


async def _has_cv(client: AsyncClient, app_id: int) -> None:
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, engineer."})
    assert saved.status_code in (200, 201), saved.text


async def _waits_on_owner(client: AsyncClient, app_id: int) -> None:
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, engineer."})
    assert saved.status_code in (200, 201), saved.text
    ask = await client.post("/api/asks", json={"question": "Notice period?", "application_id": app_id})
    assert ask.status_code in (200, 201), ask.text


async def _claim(agent: AsyncClient) -> None:
    mine = (await agent.get("/api/schedule/due", params={"duties": ["batch"]})).json()
    assert [d["duty"] for d in mine["due"]] == ["batch"], mine


async def _stop(agent: AsyncClient, **body: Any):
    return await agent.post("/api/schedule/done", json={
        "duty": "batch", "outcome": "partial", "reason": "needs_user", **body})


@pytest.mark.asyncio
async def test_a_row_queued_after_the_claim_waits_for_the_next_batch(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        async with await _agent(client, fixture_user_id) as agent:
            before = await _queued_job(client, 1)
            await _has_cv(client, before)  # owner 2026-11-19: batch work makes the batch due (claimable)
            await _claim(agent)
            await _waits_on_owner(client, before)
            later = await _queued_job(client, 2)  # a hunt brought it while the batch ran: no CV, no ask
            done = await _stop(agent)
            assert done.status_code == 200, done.text
            body = done.json()
            assert (body["duty"], body["outcome"]) == ("batch", "partial")
            assert body["next_batch"] == [later]
            assert f"{later}" in body["note"] and "next batch" in body["note"]


@pytest.mark.asyncio
async def test_a_row_queued_before_the_claim_still_refuses_and_names_the_later_ones(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        async with await _agent(client, fixture_user_id) as agent:
            before = await _queued_job(client, 3)
            await _has_cv(client, before)  # 2026-11-18: a CV-less row is the CV run's, never refuses the stop
            await _claim(agent)
            later = await _queued_job(client, 4)
            refused = await _stop(agent)
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert "1 queued jobs still need no owner input" in detail
            assert f"next: application {before} (Northwind 3)" in detail
            assert f"Ids: {before}." in detail
            assert f"queued after this run claimed the batch wait for the next batch: {later}" in detail
            await _waits_on_owner(client, before)
            assert (await _stop(agent)).status_code == 200


@pytest.mark.asyncio
async def test_the_website_stop_keeps_counting_every_queued_row(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await _agent(client, fixture_user_id)
        app_id = await _queued_job(client, 5)
        await _has_cv(client, app_id)
        refused = await client.post("/api/schedule/done", json={
            "duty": "batch", "outcome": "partial", "reason": "needs_user"})
        assert refused.status_code == 409 and f"next: application {app_id}" in refused.json()["detail"]
