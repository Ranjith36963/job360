"""``preflight.blocked_count`` counts only blocks the assistant cannot route and the user has not resolved
(production 2026-11-09/10: 48 jobs left the queue as "failed twice", 13 of them CAPTCHA, 3 one-time-code).
CAPTCHA / bot check / one-time code are handed off, never failures; a later ``unblocked``, ``cv_seen`` or
successful form step retracts earlier blocks. Real doors, real Postgres."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

URL = "https://boards.greenhouse.io/acme/jobs/9"


async def _queued_job(client: AsyncClient, slug: str) -> int:
    ad = {"title": f"Engineer {slug}", "company": f"Co {slug}", "location": "Remote", "apply_url": URL + slug,
          "description": "Build the pipelines. Python, dbt. Fully remote."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    assert (await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued"})).status_code == 201
    return app_id


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    body: dict[str, Any] = {"event_type": event_type}
    if payload is not None:
        body["payload"] = payload
    resp = await client.post(f"/api/applications/{app_id}/events", json=body)
    assert resp.status_code == 201, resp.text


async def _block(client: AsyncClient, app_id: int, reason: str) -> None:
    await _event(client, app_id, "blocked", {"reason": reason, "step": "submit", "page_host": "boards.greenhouse.io",
                                             "detail": "x"})


async def _count(client: AsyncClient, app_id: int) -> int:
    resp = await client.get("/api/applications", params={"queued": True})
    assert resp.status_code == 200, resp.text
    return {r["id"]: r for r in resp.json()["applications"]}[app_id]["preflight"]["blocked_count"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["captcha", "bot_check", "otp_needed"])
async def test_two_handed_off_blocks_are_not_failed_twice(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, reason)
        await _block(client, app_id, reason)
        await _block(client, app_id, reason)
        assert await _count(client, app_id) == 0


@pytest.mark.asyncio
async def test_two_login_needed_blocks_still_count(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, "login")
        await _block(client, app_id, "login_needed")
        await _block(client, app_id, "login_needed")
        assert await _count(client, app_id) == 2


@pytest.mark.asyncio
async def test_captcha_between_real_failures_does_not_add(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, "mixed")
        await _block(client, app_id, "login_needed")
        await _block(client, app_id, "captcha")
        assert await _count(client, app_id) == 1


@pytest.mark.asyncio
async def test_a_block_then_unblocked_does_not_count(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, "unblocked")
        await _block(client, app_id, "login_needed")
        await _event(client, app_id, "unblocked", {"resolution": "user_did_it"})
        await _block(client, app_id, "login_needed")
        assert await _count(client, app_id) == 1


@pytest.mark.asyncio
async def test_a_newer_successful_form_step_retracts_earlier_blocks(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, "formstep")
        await _block(client, app_id, "site_error")
        await _event(client, app_id, "form_checked", {"questions_count": 3, "answered": 3, "missing": 0})
        assert await _count(client, app_id) == 0
        await _block(client, app_id, "site_error")
        assert await _count(client, app_id) == 1
