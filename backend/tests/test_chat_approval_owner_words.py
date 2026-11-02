"""A chat approval carries the owner's own words (owner 2026-11-02).

An assistant with the owner's token could record ``cv_seen`` / ``submit_approved`` with where="chat" and
nothing showed the owner said yes. Now where=chat needs ``owner_words`` (his reply, verbatim) and ``asked``
(exactly what he was shown); both are stored on the event. where=web (his own click) is unchanged. A claim
on a duty does not name its caller, so a chat approval written while a scheduled run holds one is flagged
``during_duty_claim`` (server-computed), never refused by time window.

Real doors (HTTP + MCP), VALUES asserted (rule #21). Helpers copied, never imported across test modules.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import kit as kit_service
from src.services.profile import schedule

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines. Python, dbt.",
}
ASKED = "Here is the CV for Data Engineer at Northwind. OK to use it?"
MISSING = "quote the owner's own reply in owner_words"


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _mcp_client(token: str):
    import httpx as httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


async def _setup(client: AsyncClient) -> tuple[int, dict[str, Any], str]:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
    assert saved.status_code == 201, saved.text
    token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    cv = {"artifact_id": saved.json()["artifact_id"], "sha256": kit_service.sha256_text(CV_TEXT)}
    return app_id, cv, token


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _payloads(client: AsyncClient, app_id: int, event_type: str) -> list[dict[str, Any]]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return [e["payload"] for e in resp.json()["events"] if e["event_type"] == event_type]


def _claim_now(user_id: str) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute(
            "INSERT INTO assistant_duty_runs (user_id, duty, done_at) VALUES (?, ?, ?)",
            (user_id, schedule.RUNNING + "batch", datetime.now(timezone.utc).isoformat(timespec="microseconds")),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_chat_approval_without_owner_words_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cv, token = await _setup(client)
    async with _bearer_client(token) as agent:
        for event_type in ("cv_seen", "submit_approved"):
            bare = await _event(agent, app_id, event_type, {**cv, "where": "chat"})
            assert bare.status_code == 422, bare.text
            assert MISSING in bare.json()["detail"]
            for words in ("", "   ", "\x00\x07"):
                empty = await _event(agent, app_id, event_type, {**cv, "where": "chat", "owner_words": words, "asked": ASKED})
                assert empty.status_code == 422 and MISSING in empty.json()["detail"], empty.text
            no_ask = await _event(agent, app_id, event_type, {**cv, "where": "chat", "owner_words": "yes"})
            assert no_ask.status_code == 422 and "asked" in no_ask.json()["detail"], no_ask.text
            long_words = {**cv, "where": "chat", "owner_words": "y" * 501, "asked": ASKED}
            assert (await _event(agent, app_id, event_type, long_words)).status_code == 422
            long_ask = {**cv, "where": "chat", "owner_words": "yes", "asked": "a" * 2001}
            assert (await _event(agent, app_id, event_type, long_ask)).status_code == 422
        assert await _payloads(agent, app_id, "cv_seen") == []


@pytest.mark.asyncio
async def test_chat_approval_stores_the_owners_words_verbatim(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cv, token = await _setup(client)
    words = "Yes, that CV is fine.\nGo ahead\x07"
    async with _bearer_client(token) as agent:
        ok = await _event(agent, app_id, "cv_seen", {**cv, "where": "chat", "owner_words": words, "asked": ASKED})
        assert ok.status_code == 201, ok.text
        [stored] = await _payloads(agent, app_id, "cv_seen")
    assert stored["owner_words"] == "Yes, that CV is fine.\nGo ahead"
    assert stored["asked"] == ASKED
    assert stored["where"] == "chat" and stored["by"] == "token:claude-code"
    assert stored["during_duty_claim"] is False


@pytest.mark.asyncio
async def test_web_cv_seen_is_unchanged(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cv, _ = await _setup(client)
        assert (await client.post(f"/api/applications/{app_id}/cv-seen", json={})).status_code == 201
        [stored] = await _payloads(client, app_id, "cv_seen")
        assert set(stored) == {"artifact_id", "sha256", "where", "version", "by"} and stored["where"] == "web"
        extra = await _event(client, app_id, "cv_seen", {**cv, "where": "web", "owner_words": "x", "asked": "y"})
        assert extra.status_code == 422 and "unknown payload keys" in extra.json()["detail"], extra.text


@pytest.mark.asyncio
async def test_chat_approval_while_a_run_holds_a_claim_is_flagged_not_refused(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id, cv, token = await _setup(client)
    _claim_now(fixture_user_id)
    async with _bearer_client(token) as agent:
        yes = {**cv, "where": "chat", "owner_words": "yes, send it", "asked": "Shall I submit it?"}
        assert (await _event(agent, app_id, "submit_approved", yes)).status_code == 201
        [stored] = await _payloads(agent, app_id, "submit_approved")
        assert stored["during_duty_claim"] is True
        forged = {**yes, "during_duty_claim": False}
        assert (await _event(agent, app_id, "submit_approved", forged)).status_code == 422


@pytest.mark.asyncio
async def test_mcp_record_event_applies_the_same_rule(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id, cv, token = await _setup(client)
    async with mcp_runtime(), _mcp_client(token) as mcp:
        bare = await mcp.call_tool(
            "record_event", {"event_type": "cv_seen", "application_id": app_id, "payload": {**cv, "where": "chat"}}
        )
        assert bare.is_error and "422" in bare.content[0].text and MISSING in bare.content[0].text
        payload = {**cv, "where": "chat", "owner_words": "Looks good", "asked": ASKED}
        ok = await mcp.call_tool("record_event", {"event_type": "cv_seen", "application_id": app_id, "payload": payload})
        assert not ok.is_error, ok.content[0].text
        assert json.loads(ok.content[0].text)["event_type"] == "cv_seen"
    async with _bearer_client(token) as agent:
        [stored] = await _payloads(agent, app_id, "cv_seen")
    assert (stored["owner_words"], stored["asked"], stored["during_duty_claim"]) == ("Looks good", ASKED, False)


def test_the_record_event_description_names_the_chat_keys():
    from src.api.mcp_server import build_server

    tools = {t.name: t for t in build_server()._tool_manager.list_tools()}
    text = tools["record_event"].description or ""
    assert all(key in text for key in kit_service.CHAT_APPROVAL_KEYS)
