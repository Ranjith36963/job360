"""A CAPTCHA / bot check Job360's cloud browser met goes to the PC queue (owner 2026-11-07, reason
``captcha_handoff``), decided at read time from the stored block: old rows route too, no migration. Only
``captcha`` and ``bot_check`` do; ``login_needed`` and ``site_error`` do not. A handed-off block stops
counting in ``blocked_count``. Real doors, real Postgres."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.api.auth_deps import CurrentUser
from src.api.routes import browser as browser_routes
from src.services.applications import blocked as blocked_service
from src.services.applications import own_browser, which_browser
from src.services.browser_agent import routing

URL = "https://boards.greenhouse.io/acme/jobs/7"


async def _queued_job(client: AsyncClient, slug: str, url: str = URL) -> int:
    ad = {"title": f"Engineer {slug}", "company": f"Co {slug}", "location": "Remote", "apply_url": url,
          "description": "Build the pipelines. Python, dbt. Fully remote."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    assert (await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued"})).status_code == 201
    return app_id


async def _cloud_block(client: AsyncClient, app_id: int, reason: str) -> None:
    """An assistant records a block it met in Job360's cloud browser (``browser: job360``, no ``by`` marker)."""
    said = which_browser.SERVER_SAYS.set(which_browser.JOB360)
    try:
        resp = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "blocked",
            "payload": {"reason": reason, "step": "submit", "page_host": "boards.greenhouse.io", "detail": "x"},
        })
    finally:
        which_browser.SERVER_SAYS.reset(said)
    assert resp.status_code == 201, resp.text


async def _rows(client: AsyncClient) -> dict[int, dict[str, Any]]:
    resp = await client.get("/api/applications", params={"queued": True})
    assert resp.status_code == 200, resp.text
    return {r["id"]: r for r in resp.json()["applications"]}


async def _pc_tasks(ctx) -> dict[int, dict[str, Any]]:
    user = CurrentUser(id=ctx.fixture_user_id, email="u@example.com")
    async with browser_routes._db() as db:  # noqa: SLF001
        tasks = await own_browser.queue_tasks(db, user, datetime.now(timezone.utc))
    return {int(t["application_id"]): t for t in tasks if t["kind"] == "application"}


def test_the_rule_is_closed_to_captcha_and_bot_check():
    for reason in ("captcha", "bot_check"):
        assert routing.browser_for(job360_failed=reason, url=URL, handoff=True) == routing.Route(
            "own_browser", "captcha_handoff")
        assert routing.ask_route("captcha", block_reason=reason, handoff=True).reason == "captcha_handoff"
    for reason in ("login_needed", "site_error"):
        assert routing.browser_for(job360_failed=reason, url=URL, handoff=True).reason != "captcha_handoff"
        assert blocked_service.captcha_handoff({"reason": reason, "browser": "job360"}) is False
    assert "captcha_handoff" in routing.ROUTE_REASONS and "captcha_handoff" in routing.ROUTE_LABELS
    assert blocked_service.captcha_handoff({"reason": "captcha", "browser": "job360"}) is True
    assert blocked_service.captcha_handoff({"reason": "captcha", "browser": "own_browser"}) is False
    # the browser's own marker keeps its older reason code
    assert blocked_service.captcha_handoff({"reason": "captcha", "by": "browser"}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["captcha", "bot_check"])
async def test_a_cloud_captcha_or_bot_check_routes_to_the_pc_queue(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, reason)
        assert (await _rows(client))[app_id]["browser"]["kind"] == "job360"
        await _cloud_block(client, app_id, reason)  # an old blocked row routes with no data change
        chip = (await _rows(client))[app_id]["browser"]
        assert chip["kind"] == "own_browser" and chip["reason"] == "captcha_handoff"
        task = (await _pc_tasks(authenticated_async_context))[app_id]
        assert task["browser"]["reason"] == "captcha_handoff"
        assert task["why_own_browser"] == own_browser.WHY_WORDS["captcha_handoff"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["login_needed", "site_error"])
async def test_other_cloud_blocks_do_not_hand_off(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, reason)
        await _cloud_block(client, app_id, reason)
        assert (await _rows(client))[app_id]["browser"]["kind"] == "job360"
        assert app_id not in await _pc_tasks(authenticated_async_context)


@pytest.mark.asyncio
async def test_a_handed_off_captcha_stops_counting_as_a_failure(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, "count")
        await _cloud_block(client, app_id, "captcha")
        assert (await _rows(client))[app_id]["preflight"]["blocked_count"] == 0  # handed off from the start (2026-11-12)
        # the user's own browser takes over: a later event stamped own_browser
        resp = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "form_checked", "payload": {"questions_count": 3, "answered": 3, "missing": 0,
                                                      "browser": "own_browser"},
        })
        assert resp.status_code == 201, resp.text
        assert (await _rows(client))[app_id]["preflight"]["blocked_count"] == 0
        # a login block the cloud browser met is a real failure and still counts
        await _cloud_block(client, app_id, "login_needed")
        assert (await _rows(client))[app_id]["preflight"]["blocked_count"] == 1


async def _pc_unblocks(client: AsyncClient, app_id: int) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={
        "event_type": "unblocked", "payload": {"resolution": "own_browser"}})
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_the_pc_run_clears_the_cloud_captcha_so_check_submit_is_not_blocked(authenticated_async_context):
    """The real flow: cloud captcha -> PC queue -> the PC records unblocked {own_browser} -> no `blocked` gate."""
    form = "https://boards.greenhouse.io/acme/jobs/7/apply"
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, "flow")
        await _cloud_block(client, app_id, "captcha")
        assert app_id in await _pc_tasks(authenticated_async_context)

        async def check() -> dict[str, Any]:
            for et, payload in (("form_filled", {"form_url": form, "fields_count": 1, "browser": "own_browser"}),
                                ("form_swept", {"fields_checked": 1, "fixed": 0, "browser": "own_browser"})):
                assert (await client.post(f"/api/applications/{app_id}/events",
                                          json={"event_type": et, "payload": payload})).status_code == 201
            resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
            assert resp.status_code == 200, resp.text
            return resp.json()

        assert (await check())["reason"] == "blocked"
        await _pc_unblocks(client, app_id)
        assert (await check())["reason"] != "blocked"
        assert app_id not in await _pc_tasks(authenticated_async_context)


async def _own_event(client: AsyncClient, app_id: int) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={
        "event_type": "form_checked",
        "payload": {"questions_count": 3, "answered": 3, "missing": 0, "browser": "own_browser"}})
    assert resp.status_code == 201, resp.text


# 2026-11-12: CAPTCHA and bot check are never a failure, however often the cloud meets them
# (was: a returning captcha counted; bulk apply then unqueued 13 CAPTCHA jobs as "failed twice").
@pytest.mark.asyncio
async def test_a_handed_off_captcha_that_returns_never_counts(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, "loop")
        await _cloud_block(client, app_id, "captcha")
        await _own_event(client, app_id)
        await _pc_unblocks(client, app_id)
        assert (await _rows(client))[app_id]["preflight"]["blocked_count"] == 0
        await _cloud_block(client, app_id, "captcha")  # the cloud meets it again
        await _cloud_block(client, app_id, "bot_check")
        assert (await _rows(client))[app_id]["preflight"]["blocked_count"] == 0


@pytest.mark.asyncio
async def test_handed_off_hosts_are_learned_so_the_cloud_stops_taking_the_site(authenticated_async_context):
    from src.services.browser_agent import learned

    async with authenticated_async_context() as client:
        first = await _queued_job(client, "h1", "https://boards.greenhouse.io/acme/jobs/11")
        second = await _queued_job(client, "h2", "https://boards.greenhouse.io/acme/jobs/12")
        third = await _queued_job(client, "h3", "https://boards.greenhouse.io/acme/jobs/13")
        await _cloud_block(client, first, "captcha")
        async with browser_routes._db() as db:  # noqa: SLF001
            assert await learned.blocked_hosts(db, authenticated_async_context.fixture_user_id) == frozenset()
        await _cloud_block(client, second, "bot_check")
        async with browser_routes._db() as db:  # noqa: SLF001
            assert await learned.blocked_hosts(db, authenticated_async_context.fixture_user_id) == frozenset(
                {"boards.greenhouse.io"})
        assert (await _rows(client))[third]["browser"]["reason"] == "learned_bot_check"
