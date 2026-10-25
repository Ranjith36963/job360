"""Ignition part 2 (owner 2026-10-24): which press starts which duty, at once.

A finished hunt (mark_done hunt) and a hunted job queued start the people hunt and the batch; the
user's own answer to an ask starts the batch; a schedule change the user saved starts that duty once.
An assistant's own answer or setting write starts nothing, and a user with no start link spawns no
background work. The routine endpoints are mocked (rule #4); helpers are defined here, never imported.
"""
from __future__ import annotations

import socket
from typing import Any

import pytest
from aioresponses import aioresponses
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

LINKS = {
    "hunt": "https://api.anthropic.com/v1/claude_code/routines/trig_HUNT/fire",
    "batch": "https://api.anthropic.com/v1/claude_code/routines/trig_SEND/fire",
    "inbox": "https://api.anthropic.com/v1/claude_code/routines/trig_INBOX/fire",
    "reach": "https://api.anthropic.com/v1/claude_code/routines/trig_REACH/fire",
}


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


async def _link_all(client: AsyncClient) -> None:
    for duty, url in LINKS.items():
        resp = await client.put(f"/api/assistant-duty-triggers/{duty}", json={"url": url, "token": "tok"})
        assert resp.status_code == 200, resp.text


def _mock(mocked) -> None:
    for url in LINKS.values():
        mocked.post(url, status=200, payload={}, repeat=True)


def _started(mocked) -> dict[str, str]:
    """{duty: text} for every start the mock received."""
    by_url = {v: k for k, v in LINKS.items()}
    out: dict[str, str] = {}
    for (_method, url), calls in mocked.requests.items():
        for call in calls:
            out[by_url[str(url)]] = call.kwargs["json"]["text"]
    return out


async def _settle() -> None:
    from src.services.browser_agent import duty_trigger

    await duty_trigger.drain()


def _ad(n: int) -> dict[str, Any]:
    return {"title": f"Data Engineer {n}", "company": "Northwind", "location": "London",
            "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8}


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_a_finished_hunt_starts_reach_and_the_batch(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link_all(client)
        with aioresponses() as mocked:
            _mock(mocked)
            assert (await client.post("/api/schedule/done", json={"duty": "hunt"})).status_code == 200
            await _settle()
            started = _started(mocked)
        assert set(started) == {"reach", "batch"}
        assert "a job hunt finished" in started["reach"] and "duty `reach`" in started["reach"]


@pytest.mark.asyncio
async def test_other_marks_start_nothing(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link_all(client)
        with aioresponses() as mocked:
            _mock(mocked)
            assert (await client.post("/api/schedule/done", json={"duty": "inbox"})).status_code == 200
            await _settle()
            assert _started(mocked) == {}


@pytest.mark.asyncio
async def test_a_hunted_job_queued_starts_reach_and_the_batch_with_its_id(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link_all(client)
        with aioresponses() as mocked:
            _mock(mocked)
            own = await client.post("/api/jobs/bring", json={**_ad(1), "queue": True, "origin": "user_link"})
            assert own.status_code == 200, own.text
            await _settle()
            assert _started(mocked) == {}  # the user's own link starts nothing
            hunted = await client.post("/api/jobs/bring", json={**_ad(2), "queue": True, "origin": "ai_hunt"})
            assert hunted.status_code == 200 and hunted.json()["queued"] is True, hunted.text
            await _settle()
            started = _started(mocked)
        app_id = hunted.json()["application_id"]
        assert set(started) == {"reach", "batch"}
        assert f"Applications: {app_id}." in started["batch"] and "a new job was queued" in started["batch"]


@pytest.mark.asyncio
async def test_the_users_answer_starts_the_batch_an_assistants_does_not(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link_all(client)
        app_id = (await client.post("/api/jobs/bring", json=_ad(3))).json()["application_id"]
        first = (await client.post("/api/asks", json={"question": "Notice period?", "application_id": app_id})).json()
        second = (await client.post("/api/asks", json={"question": "Salary?", "application_id": app_id})).json()
        async with await _agent(client) as agent:
            with aioresponses() as mocked:
                _mock(mocked)
                relayed = await agent.post(f"/api/asks/{first['id']}/answer", json={"answer": "One month"})
                assert relayed.status_code == 200, relayed.text
                await _settle()
                assert _started(mocked) == {}
        with aioresponses() as mocked:
            _mock(mocked)
            own = await client.post(f"/api/asks/{second['id']}/answer", json={"answer": "60k"})
            assert own.status_code == 200, own.text
            await _settle()
            started = _started(mocked)
        assert set(started) == {"batch"} and "the user answered a question" in started["batch"]


@pytest.mark.asyncio
async def test_a_schedule_change_the_user_saved_starts_that_duty_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link_all(client)
        with aioresponses() as mocked:
            _mock(mocked)
            resp = await client.patch("/api/profile", json={"edits": [
                {"path": "assistant_settings.hunt_every_hours", "value": 6},
                {"path": "preferences.check_every", "value": "6h"},
            ]})
            assert resp.status_code == 200, resp.text
            await _settle()
            started = _started(mocked)
        assert set(started) == {"hunt", "inbox"} and "changed a schedule setting" in started["hunt"]


@pytest.mark.asyncio
async def test_no_start_link_means_no_background_work(authenticated_async_context):
    from src.services.browser_agent import duty_trigger

    async with authenticated_async_context() as client:
        with aioresponses() as mocked:
            assert (await client.post("/api/schedule/done", json={"duty": "hunt"})).status_code == 200
            assert not duty_trigger._PENDING
            await _settle()
            assert _started(mocked) == {}


@pytest.mark.asyncio
async def test_a_failed_hunt_starts_nothing(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link_all(client)
        with aioresponses() as mocked:
            _mock(mocked)
            failed = await client.post("/api/schedule/done", json={"duty": "hunt", "outcome": "failed",
                                                                   "reason": "tool_unavailable"})
            assert failed.status_code == 200, failed.text
            await _settle()
            assert _started(mocked) == {}
