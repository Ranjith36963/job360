"""Owner 2026-10-26 - a block on the job board's page no longer stands once the job's link moves.

On production (apps 115, 116, 124) the run met a Cloudflare check on an aggregator page, found the
employer's own Ashby page, saved it with update_job apply_url and filled it - yet check_submit still
answered blocked. Now moving the link to ANOTHER site closes a block recorded on the old link's site
(unblocked, resolution skipped, a note naming both sites). A block on any other site, or a move within
one site, stays: only the user clears those. Helpers are copied here, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

BOARD = "https://www.arbeitnow.com/jobs/companies/51d/ai-engineer-123"
EMPLOYER = "https://jobs.ashbyhq.com/51d/abc"
_AD = {"title": "AI Engineer", "company": "51D", "location": "Berlin", "apply_url": BOARD,
       "description": "Build agents for customers. " * 6}


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _setup(client: AsyncClient, agent: AsyncClient, page_host: str) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    blocked = await agent.post(f"/api/applications/{app_id}/events", json={
        "event_type": "blocked", "payload": {"reason": "bot_check", "step": "open the form", "page_host": page_host}})
    assert blocked.status_code == 201, blocked.text
    return app_id


async def _block(client: AsyncClient, app_id: int) -> Any:
    resp = await client.get(f"/api/applications/{app_id}/controls")
    assert resp.status_code == 200, resp.text
    return resp.json()["blocked"]


async def _move(agent: AsyncClient, app_id: int, url: str) -> None:
    resp = await agent.patch(f"/api/applications/{app_id}/job", json={"apply_url": url})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_moving_to_the_employers_site_closes_the_boards_block(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            app_id = await _setup(client, agent, "www.arbeitnow.com")
            assert (await _block(client, app_id))["reason"] == "bot_check"
            await _move(agent, app_id, EMPLOYER)
            assert await _block(client, app_id) is None
            events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
            [freed] = [e for e in events if e["event_type"] == "unblocked"]
            assert freed["detail"] == "the job's link moved from arbeitnow.com to jobs.ashbyhq.com"
            asks = (await client.get("/api/asks")).json()["asks"]
            assert [a for a in asks if a["application_id"] == app_id] == []  # its Needs-you card closed too


@pytest.mark.asyncio
async def test_a_block_on_another_site_or_a_move_within_one_site_stays(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            other = await _setup(client, agent, "jobs.ashbyhq.com")  # the CAPTCHA is on the new site itself
            await _move(agent, other, EMPLOYER)
            assert (await _block(client, other))["page_host"] == "jobs.ashbyhq.com"
            await client.post(f"/api/applications/{other}/events", json={  # the user clears it
                "event_type": "unblocked", "payload": {"resolution": "user_did_it"}})
            same = await _setup(client, agent, "www.arbeitnow.com")
            await _move(agent, same, "https://arbeitnow.com/jobs/companies/51d/ai-engineer-999")
            assert (await _block(client, same))["page_host"] == "www.arbeitnow.com"
