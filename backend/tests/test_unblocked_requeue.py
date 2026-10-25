"""Owner 2026-10-25 - a stuck job the user cleared goes back on the queue.

The batch takes a job off the queue after its second block (``unqueued`` {reason: blocked}). When the
user presses Mark resolved on a block the assistant can try again (a sign-in page, a sign-up wall, a
site error, the site's own daily limit), the job goes back on the queue and Apply starts at once. Its
block count starts again from 0, so the batch does not take it straight off again. A block the user
finishes on the form (a CAPTCHA) or a job that left for another reason stays off. Helpers are defined
here, never imported (per-test schema isolation); the start link's endpoint is mocked (rule #4).
"""
from __future__ import annotations

import socket
from typing import Any

import pytest
from aioresponses import aioresponses
from cryptography.fernet import Fernet
from httpx import AsyncClient

SEND = "https://api.anthropic.com/v1/claude_code/routines/trig_SEND/fire"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("BROWSER_SIGN_IN_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    from src.services.fetch import fetcher

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        return [{"hostname": host, "host": "93.184.216.34", "port": port, "family": socket.AF_INET, "proto": 0,
                 "flags": 0}]

    monkeypatch.setattr(fetcher, "DEFAULT_RESOLVE", resolve)


async def _queued_job(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": "Northwind", "location": "London", "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200 and resp.json()["queued"] is True, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text


async def _block_twice_and_unqueue(client: AsyncClient, app_id: int, reason: str) -> None:
    for _ in range(2):
        await _event(client, app_id, "blocked", {"reason": reason, "page_host": "jobs.northwind.example"})
        if _ == 0:
            assert (await client.post(f"/api/applications/{app_id}/blocked/resolve")).status_code == 201
    await _event(client, app_id, "unqueued", {"reason": "blocked"})


async def _queue(client: AsyncClient) -> dict[int, dict[str, Any]]:
    resp = await client.get("/api/applications", params={"queued": "true"})
    assert resp.status_code == 200, resp.text
    return {int(r["id"]): r for r in resp.json()["applications"]}


async def _resolve(client: AsyncClient, app_id: int) -> None:
    resp = await client.post(f"/api/applications/{app_id}/blocked/resolve")
    assert resp.status_code == 201, resp.text
    from src.services.browser_agent import duty_trigger

    await duty_trigger.drain()


@pytest.mark.asyncio
async def test_a_cleared_sign_in_puts_the_job_back_and_starts_apply(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, 1)
        await _block_twice_and_unqueue(client, app_id, "login_needed")
        assert app_id not in await _queue(client)
        await _event(client, app_id, "blocked", {"reason": "login_needed", "page_host": "jobs.northwind.example"})
        # The link is saved only now: the earlier Mark resolved (job still queued) must not use up the cooldown.
        saved = await client.put("/api/assistant-duty-triggers/batch", json={"url": SEND, "token": "tok"})
        assert saved.status_code == 200, saved.text
        with aioresponses() as mocked:
            mocked.post(SEND, status=200, payload={}, repeat=True)
            await _resolve(client, app_id)
            [call] = [c for calls in mocked.requests.values() for c in calls]
        row = (await _queue(client))[app_id]
        assert row["preflight"]["blocked_count"] == 0  # starts again: the batch keeps it this time
        assert "back on the queue" in call.kwargs["json"]["text"] and f"Applications: {app_id}." in call.kwargs["json"]["text"]


@pytest.mark.asyncio
async def test_a_captcha_or_another_exit_stays_off_the_queue(authenticated_async_context):
    async with authenticated_async_context() as client:
        captcha = await _queued_job(client, 2)
        await _block_twice_and_unqueue(client, captcha, "captcha")
        await _event(client, captcha, "blocked", {"reason": "captcha", "page_host": "jobs.northwind.example"})
        await _resolve(client, captcha)  # the user pressed Submit themselves: never applied twice
        removed = await _queued_job(client, 3)
        await _event(client, removed, "unqueued", {"reason": "user_removed"})
        await _event(client, removed, "blocked", {"reason": "site_error", "page_host": "jobs.northwind.example"})
        await _resolve(client, removed)
        assert set(await _queue(client)).isdisjoint({captcha, removed})


@pytest.mark.asyncio
async def test_a_job_still_on_the_queue_stays_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _queued_job(client, 4)
        await _event(client, app_id, "blocked", {"reason": "site_error", "page_host": "jobs.northwind.example"})
        await _resolve(client, app_id)
        assert app_id in await _queue(client)
        timeline = (await client.get(f"/api/applications/{app_id}")).json()
        queued = [e for e in timeline.get("events", []) if e.get("event_type") == "queued"]
        assert len(queued) == 1, queued
