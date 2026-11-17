"""2026-11-17: an assistant's ``blocked`` without ``browser`` is recorded with a reply warning.

Bulk apply runs (prompt v15-v22) kept recording ``blocked`` with no ``browser``, so the block read
``unknown`` and could not be routed or counted per browser. A 422 would break the PC loop command and older
routines that record ``blocked`` without it, so the event is kept and the reply says exactly what to pass.
The website's own writes are never warned. Helpers are defined here, never imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import which_browser


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "apply-routine"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _job(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _block(client: AsyncClient, app_id: int, **extra: Any):
    resp = await client.post(f"/api/applications/{app_id}/events", json={
        "event_type": "blocked", "payload": {"reason": "login_needed", "page_host": "jobs.northwind.example", **extra}})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_an_assistant_blocked_without_browser_is_kept_and_warned(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            bare, named = await _job(client, 1), await _job(client, 2)
            warned = await _block(agent, bare)
            assert which_browser.BLOCKED_NO_BROWSER in warned["warnings"]
            assert 'pass browser (job360, own_browser or manual)' in which_browser.BLOCKED_NO_BROWSER
            quiet = await _block(agent, named, browser="own_browser")
            assert which_browser.BLOCKED_NO_BROWSER not in quiet.get("warnings", [])


@pytest.mark.asyncio
async def test_the_website_is_never_warned(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _job(client, 3)
        body = await _block(client, app_id)
        assert which_browser.BLOCKED_NO_BROWSER not in body.get("warnings", [])
