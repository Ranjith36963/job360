"""2026-11-18 (21:41 bulk apply run): 5 of 17 rows were blocked on a job board's sign-in or Apply button
(1061 and 1075 shine.com, 1062 and 1063 reed.co.uk, 1074 cutshort.io) without trying the employer's own apply
page, which batch.md step 3 requires. A ``blocked`` event whose ``page_host`` is a job board
(``job_checks.classify_host``) now carries a reply hint: find the employer's own page for the same role,
``update_job(apply_url)``, work that page. A hint, never a refusal: the block is still recorded."""
from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient


async def _job(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _blocked_as_agent(fixture_user_id: str, app_id: int, host: str) -> dict:
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        tool = m.build_server()._tool_manager.get_tool("record_event")
        return await tool.fn(application_id=app_id, event_type="blocked", payload={
            "reason": "login_needed", "step": "apply_click", "page_host": host, "browser": "manual",
            "detail": "Apply leads to a sign-in page."})
    finally:
        m._current_user.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["www.shine.com", "www.reed.co.uk", "uk.indeed.com"])
async def test_a_block_on_a_job_board_hints_the_employers_own_page(
    authenticated_async_context, fixture_user_id, host, caplog
):
    async with authenticated_async_context() as client:
        app_id = await _job(client, 1)
        with caplog.at_level(logging.INFO):
            out = await _blocked_as_agent(fixture_user_id, app_id, host)
    from src.services.applications import which_browser

    assert which_browser.BLOCKED_ON_JOB_BOARD in out.get("warnings", []), out
    assert out.get("event_id"), out  # recorded all the same: a hint, never a refusal
    logged = [r for r in caplog.records if getattr(r, "event", "") == "blocked_on_job_board"]
    assert logged and logged[0].application_id == app_id and logged[0].page_host == host


@pytest.mark.asyncio
async def test_a_block_on_the_employers_own_site_has_no_board_hint(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _job(client, 2)
        out = await _blocked_as_agent(fixture_user_id, app_id, "boards.greenhouse.io")
    from src.services.applications import which_browser

    assert which_browser.BLOCKED_ON_JOB_BOARD not in out.get("warnings", [])


def test_the_hint_names_the_recipe_step():
    from src.services.applications import which_browser

    text = which_browser.BLOCKED_ON_JOB_BOARD
    assert "update_job" in text and "apply_url" in text and "employer" in text
