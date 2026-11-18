"""The user's assistant may START a duty's routine (owner 2026-11-18: "job360 and the assistant can trigger").

Fire only: ``POST /assistant-duty-triggers/{duty}/start`` (a token or OAuth, or the website) and its MCP door
``whats_due(start=<duty>)``. A token still never reads, sets, changes or deletes a start link. The start shares
the duty's cool-down and hourly cap with every other cause, and the user's Pause refuses it. The routine
endpoints are mocked (rule #4); helpers are defined here, never imported.
"""
from __future__ import annotations

import socket
from typing import Any

import pytest
from aioresponses import aioresponses
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

HUNT = "https://api.anthropic.com/v1/claude_code/routines/trig_HUNT/fire"
CV = "https://api.anthropic.com/v1/claude_code/routines/trig_CV/fire"


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


def _texts(mocked) -> list[str]:
    return [c.kwargs["json"]["text"] for _key, cs in mocked.requests.items() for c in cs]


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _tool(user_id: str, **args: Any):
    """whats_due, called the way the MCP server calls it (a route function, no HTTP in between)."""
    import asyncio

    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    async def run() -> Any:
        tool = m.build_server()._tool_manager.get_tool("whats_due")
        token = m._current_user.set(
            CurrentUser(id=user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc"))
        try:
            return await tool.fn(**args)
        finally:
            m._current_user.reset(token)

    return asyncio.ensure_future(run())


@pytest.mark.asyncio
async def test_an_assistant_can_start_a_duty_with_fixed_words_and_never_retry(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            assert (await agent.post("/api/assistant-duty-triggers/cv/start")).json() == {
                "fired": False, "reason": "not_set"}
            assert (await agent.post("/api/assistant-duty-triggers/apply/start")).status_code == 404
            assert (await client.put("/api/assistant-duty-triggers/cv", json={"url": CV, "token": "t"})).status_code == 200
            with aioresponses() as mocked:
                mocked.post(CV, status=200, payload={}, repeat=True)
                first = (await agent.post("/api/assistant-duty-triggers/cv/start")).json()
                again = (await agent.post("/api/assistant-duty-triggers/cv/start")).json()
                texts = _texts(mocked)
        assert first["fired"] is True and again["fired"] is False and again["reason"] == "cooldown"
        assert len(texts) == 1
        assert "your assistant asked Job360 to start it" in texts[0] and 'get_recipe("cv")' in texts[0]


@pytest.mark.asyncio
async def test_a_token_still_never_reads_sets_or_deletes_a_start_link(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            assert (await agent.get("/api/assistant-duty-triggers")).status_code == 403
            for duty in ("hunt", "cv", "own_browser"):
                assert (await agent.put(f"/api/assistant-duty-triggers/{duty}", json={"url": HUNT})).status_code == 403
                assert (await agent.delete(f"/api/assistant-duty-triggers/{duty}")).status_code == 403
                assert (await agent.post(f"/api/assistant-duty-triggers/{duty}/run")).status_code == 403
            assert (await agent.put("/api/assistant-trigger", json={"url": HUNT})).status_code == 403


@pytest.mark.asyncio
async def test_the_cool_down_and_hourly_cap_are_shared_with_the_website(authenticated_async_context, monkeypatch):
    from src.services.browser_agent import duty_trigger

    async with authenticated_async_context() as client:
        await client.put("/api/assistant-duty-triggers/hunt", json={"url": HUNT})
        async with await _agent(client) as agent:
            with aioresponses() as mocked:
                mocked.post(HUNT, status=200, payload={}, repeat=True)
                assert (await client.post("/api/assistant-duty-triggers/hunt/run")).json()["fired"] is True
                held = (await agent.post("/api/assistant-duty-triggers/hunt/start")).json()
                assert held == {**held, "fired": False, "reason": "cooldown"}  # the press a moment ago counts
                monkeypatch.setattr(duty_trigger, "COOLDOWN_S", 0)
                monkeypatch.setattr(duty_trigger, "FIRES_PER_HOUR", 2)
                assert (await agent.post("/api/assistant-duty-triggers/hunt/start")).json()["fired"] is True
                capped = (await client.post("/api/assistant-duty-triggers/hunt/run")).json()
                assert len(_texts(mocked)) == 2
        assert capped["fired"] is False and capped["reason"] == "hourly_cap"


@pytest.mark.asyncio
async def test_pause_refuses_an_assistants_start(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await client.put("/api/assistant-duty-triggers/hunt", json={"url": HUNT})
        assert (await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.paused_until", "value": "until_resumed"}]})).status_code == 200
        async with await _agent(client) as agent:
            with aioresponses() as mocked:
                mocked.post(HUNT, status=200, payload={}, repeat=True)
                out = (await agent.post("/api/assistant-duty-triggers/hunt/start")).json()
                tool = await _tool(fixture_user_id, start="hunt")
                assert _texts(mocked) == []
        assert out["fired"] is False and out["reason"] == "paused"
        assert tool["fired"] is False and tool["paused"] is True


@pytest.mark.asyncio
async def test_whats_due_start_fires_through_the_route(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        assert (await _tool(fixture_user_id, start="cv")) == {"fired": False, "reason": "not_set"}
        await client.put("/api/assistant-duty-triggers/cv", json={"url": CV})
        with aioresponses() as mocked:
            mocked.post(CV, status=200, payload={}, repeat=True)
            out = await _tool(fixture_user_id, start="cv")
            again = await _tool(fixture_user_id, start="cv")
            assert len(_texts(mocked)) == 1
        assert out["fired"] is True and again["reason"] == "cooldown"
        with pytest.raises(Exception, match="unknown duty"):
            await _tool(fixture_user_id, start="apply")
