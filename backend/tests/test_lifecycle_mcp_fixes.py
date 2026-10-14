"""Full-lifecycle QA fixes: record_application takes application_id, outreach
needs a contact, a deduped message_id says so when the type differs, absolute
receipt url, absolute hunt-kit urls. Helpers are COPIED, never imported."""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}
_AD2 = {**_AD, "title": "Platform Engineer", "apply_url": "https://northwind.example/careers/8"}
_NEEDS_CONTACT = "outreach events need contact_id — call add_contact first and pass its contact_id"


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    out["job_id"] = out["job"]["id"]
    return out


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "agent"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _ok(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _err(result) -> str:
    assert result.is_error, "expected a tool error"
    return result.content[0].text


async def _setup(client: AsyncClient):
    a = await _bring(client)
    b = await _bring(client, _AD2)
    return a, b, await _mint_token(client)


# ── 1. record_application takes application_id ──────────────────────────────


@pytest.mark.asyncio
async def test_record_application_by_application_id_and_absolute_url(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime
    from src.core.settings import SITE_BASE_URL

    async with authenticated_async_context() as client:
        a, _b, token = await _setup(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            body = _ok(await mcp.call_tool("record_application", {"application_id": a["application_id"]}))
    assert body["job_id"] == a["job_id"]
    assert body["url"] == f"{SITE_BASE_URL}/applications/{a['application_id']}"  # fix 4


@pytest.mark.asyncio
async def test_record_application_job_id_back_compat(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        a, _b, token = await _setup(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            body = _ok(await mcp.call_tool("record_application", {"job_id": a["job_id"]}))
    assert body["job_id"] == a["job_id"]
    assert body["url"].startswith("http")


@pytest.mark.asyncio
async def test_record_application_mismatched_ids_422_and_unknown_application_404(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        a, b, token = await _setup(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            text = _err(await mcp.call_tool(
                "record_application", {"application_id": a["application_id"], "job_id": b["job_id"]}
            ))
            assert "do not belong together" in text
            text = _err(await mcp.call_tool("record_application", {"application_id": 987654}))
            assert "not found" in text
            assert "do not belong together" in _err(await mcp.call_tool(
                "record_application", {"application_id": a["application_id"], "job_id": 987654}
            ))
            assert "give application_id" in _err(await mcp.call_tool("record_application", {}))


# ── 2. outreach needs a contact ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outreach_without_contact_is_refused_on_both_surfaces(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        a, _b, token = await _setup(client)
        app_id = a["application_id"]
        for et in ("outreach_sent", "outreach_replied"):
            r = await client.post(f"/api/applications/{app_id}/events", json={"event_type": et})
            assert r.status_code == 422 and _NEEDS_CONTACT in r.text
        r = await client.post(
            f"/api/applications/{app_id}/artifacts", json={"kind": "outreach", "text": "hi"}
        )
        assert r.status_code == 422 and _NEEDS_CONTACT in r.text
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            for et in ("outreach_sent", "outreach_replied"):
                for args in ({"application_id": app_id}, {}):
                    assert _NEEDS_CONTACT in _err(await mcp.call_tool("record_event", {"event_type": et, **args}))
            for args in ({"application_id": app_id}, {}):
                assert _NEEDS_CONTACT in _err(
                    await mcp.call_tool("save_artifact", {"kind": "outreach", "text": "hi", **args})
                )
            # A normal event and a normal artifact are unaffected.
            _ok(await mcp.call_tool("record_event", {"event_type": "note", "application_id": app_id, "detail": "x"}))
            _ok(await mcp.call_tool("save_artifact", {"kind": "cv", "text": "CV", "application_id": app_id}))
            # And with a contact it still works.
            added = _ok(await mcp.call_tool("add_contact", {"name": "Priya", "application_id": app_id}))
            cid = added["contact_id"]
            sent = _ok(await mcp.call_tool(
                "record_event",
                {"event_type": "outreach_sent", "application_id": app_id, "contact_id": cid, "channel": "email"},
            ))
            assert sent["event_type"] == "outreach_sent"


@pytest.mark.asyncio
async def test_add_contact_reply_has_top_level_contact_id(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client)
        r = await client.post(f"/api/applications/{a['application_id']}/contacts", json={"name": "Priya"})
        assert r.status_code == 201, r.text
        assert r.json()["contact_id"] == r.json()["contact"]["id"]
        r = await client.post("/api/contacts", json={"name": "Cold One"})
        assert r.json()["contact_id"] == r.json()["contact"]["id"]


# ── 3. same message_id, different type ──────────────────────────────────────


@pytest.mark.asyncio
async def test_dedupe_with_different_type_warns(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client)
        url = f"/api/applications/{a['application_id']}/events"
        src = {"kind": "email", "message_id": "m-1", "sender": "a@b.c", "subject": "s"}
        # Applied first, so the reply-before-applied warning (#815) stays out of this dedupe test.
        applied = await client.post(url, json={"event_type": "applied"})
        assert applied.status_code == 201, applied.text
        first = await client.post(url, json={"event_type": "replied", "source": src})
        assert first.status_code == 201, first.text
        assert first.json()["warnings"] == []
        same = await client.post(url, json={"event_type": "replied", "source": src})
        assert same.json()["already_existed"] is True and same.json()["warnings"] == []
        other = await client.post(url, json={"event_type": "rejected", "source": src})
        body = other.json()
        assert body["already_existed"] is True and body["event_type"] == "replied"
        assert body["warnings"] == [
            f"message_id already recorded as replied; to reclassify, "
            f"record_event(..., corrects_event_id={first.json()['event_id']}) without `source` (the original keeps it)"
        ]


# ── 5. hunt-kit index urls are absolute ─────────────────────────────────────


@pytest.mark.asyncio
async def test_hunt_kit_index_urls_absolute(authenticated_async_context):
    from src.core.settings import SITE_BASE_URL

    async with authenticated_async_context() as client:
        r = await client.get("/api/hunt-kit")
        assert r.status_code == 200, r.text
        files = r.json()["files"]
        assert files
        for f in files:
            assert f["url"] == f"{SITE_BASE_URL}/api/hunt-kit/{f['name']}"
            assert len(f["sha256"]) == 64 and f["bytes"] > 0
        assert (await client.get(f"/api/hunt-kit/{files[0]['name']}")).status_code == 200
