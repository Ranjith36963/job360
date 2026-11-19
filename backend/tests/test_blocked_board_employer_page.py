"""2026-11-19 (01:41 bulk apply run): 7 of 10 rows were blocked on a job board's sign-in, CAPTCHA or
search page (Instahyre, Shine, WhatJobs). The kit's `apply_host` check (ok: false, "the employer's own
posting is the better source"), batch.md step 3 and #1681's reply hint all said: find the employer's
own apply page first. No run did. A block an assistant records on a job-board host now has to say, in
`employer_page`, what its search for the employer's own page found. Claude still judges whether such a
page exists; Job360 only refuses a board block that does not say."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _job(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _blocked(fixture_user_id: str, app_id: int, payload: dict):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        tool = m.build_server()._tool_manager.get_tool("record_event")
        return await tool.fn(application_id=app_id, event_type="blocked", payload=payload)
    finally:
        m._current_user.reset(token)


_BASE = {"reason": "login_needed", "step": "Apply Now opens sign-in", "browser": "manual",
         "detail": "Apply flow requires signing in."}


@pytest.mark.asyncio
async def test_a_board_block_without_the_employer_search_is_refused(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _job(client, 1)
        with pytest.raises(Exception) as err:
            await _blocked(fixture_user_id, app_id, {**_BASE, "page_host": "www.shine.com"})
    assert "employer_page" in str(err.value) and "update_job" in str(err.value)


@pytest.mark.asyncio
async def test_a_board_block_that_says_what_the_search_found_is_recorded(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _job(client, 2)
        out = await _blocked(fixture_user_id, app_id, {
            **_BASE, "page_host": "www.shine.com",
            "employer_page": "Searched Northwind 2 careers and the web for the role: the employer posts only on Shine."})
        assert out.get("event_id"), out
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    stored = [e for e in events if e["event_type"] == "blocked"][-1]["payload"]
    assert stored["employer_page"].startswith("Searched Northwind 2 careers")


@pytest.mark.asyncio
async def test_a_block_on_the_employers_own_site_needs_no_employer_page(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _job(client, 3)
        out = await _blocked(fixture_user_id, app_id, {**_BASE, "page_host": "boards.greenhouse.io"})
    assert out.get("event_id"), out


@pytest.mark.asyncio
async def test_a_closed_posting_on_a_board_needs_no_employer_page(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _job(client, 4)
        out = await _blocked(fixture_user_id, app_id, {
            "reason": "posting_closed", "evidence": "page_says_closed", "page_host": "www.reed.co.uk",
            "browser": "manual", "step": "ad page", "detail": "The ad says it is no longer accepting applications."})
    assert out.get("event_id"), out
