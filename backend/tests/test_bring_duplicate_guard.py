"""Prod 2026-10-30: the hourly hunt brought "AI Engineer I - Zonda - Glasgow, ..." as application 328
while application 126 "AI Engineer I - Zonda" was still open. The guard is code, not prompt: an
AGENT bring of the same company + same role (location suffix trimmed) as an OPEN application returns
that application (`duplicate_of`) and creates nothing. A closed one never blocks; a human on the web
form can still re-bring on purpose. Real doors (HTTP + MCP) on the real Postgres.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

from src.api import dependencies as api_deps
from src.services.applications.reposts import role_core

_AD: dict[str, Any] = {
    "title": "AI Engineer I - Zonda", "company": "CoStar Group", "location": "Austin, TX",
    "apply_url": "https://costar.example/careers/126",
    "description": "Build AI features. Python, LLMs.",
}
_GLASGOW: dict[str, Any] = {
    **_AD, "title": "AI Engineer I - Zonda - Glasgow, Scotland, United Kingdom",
    "location": "Glasgow, Scotland, United Kingdom", "apply_url": "https://costar.example/careers/328",
}


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


async def _count_apps() -> int:
    db = await api_deps.get_db()
    cur = await db._conn.execute("SELECT COUNT(*) FROM applications")
    return int((await cur.fetchone())[0])


async def _token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def test_role_core_trims_location_noise_only() -> None:
    assert role_core("AI Engineer I - Zonda - Glasgow, Scotland, United Kingdom") == "ai engineer i - zonda"
    assert role_core("  AI  Engineer I - Zonda ") == "ai engineer i - zonda"
    assert role_core("Data Engineer (Glasgow)") == "data engineer"
    assert role_core("Data Engineer - Glasgow", "glasgow") == "data engineer"
    assert role_core("Data Engineer - Platform") == "data engineer - platform"
    assert role_core("Data Engineer") != role_core("Senior Data Engineer")


@pytest.mark.asyncio
async def test_agent_bring_of_an_open_role_returns_the_existing_application(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_AD)).json()
        token = await _token(client)
    before = await _count_apps()
    async with mcp_runtime(), _mcp_client(token) as mcp:
        out = _payload(await mcp.call_tool("bring_job", _GLASGOW))
        other = _payload(await mcp.call_tool("bring_job", {**_GLASGOW, "title": "Senior AI Engineer - Glasgow, UK"}))
    assert out["duplicate_of"] == first["application_id"]
    assert out["application_id"] == first["application_id"]
    assert out["title"] == _AD["title"], "the reply shows the EXISTING application, not the new paste"
    assert await _count_apps() == before + 1, "only the genuinely different title created a row"
    assert other["duplicate_of"] is None and other["application_id"] != first["application_id"]


@pytest.mark.asyncio
async def test_a_closed_application_does_not_block(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_AD)).json()
        rej = await client.post(f"/api/applications/{first['application_id']}/events", json={"event_type": "rejected"})
        assert rej.json()["status"] == "rejected", rej.text
        token = await _token(client)
    async with mcp_runtime(), _mcp_client(token) as mcp:
        out = _payload(await mcp.call_tool("bring_job", _GLASGOW))
    assert out["duplicate_of"] is None
    assert out["application_id"] != first["application_id"]


@pytest.mark.asyncio
async def test_the_web_form_can_still_rebring_on_purpose(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_AD)).json()
        again = await client.post("/api/jobs/bring", json=_GLASGOW)
        assert again.status_code == 200, again.text
        body = again.json()
    assert body["duplicate_of"] is None
    assert body["application_id"] != first["application_id"]
