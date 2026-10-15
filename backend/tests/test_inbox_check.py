"""Gmail check slice (owner decision 2026-10-15).

The user's assistant reads Gmail; Job360 stores two facts per application:
``email_senders`` (the ATS domains to search, so mail that never names the company
is found) and ``inbox_checked_at`` (how far the mail was read). Real doors (HTTP +
MCP), VALUES asserted (rule #21). Helpers are copied, never imported from another
test module.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications.inbox import email_senders_for

ASHBY = "https://jobs.ashbyhq.com/bjakcareer/e8406b78-3cb3-436c-8a22-6e1d44cf8816"
AD = {"title": "Applied AI Engineer", "company": "BJAK", "location": "India",
      "description": "Agents, RAG and evals. " * 10, "apply_url": ASHBY}


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


async def _bring(client: AsyncClient, ad: dict[str, Any] = AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _row(client: AsyncClient, app_id: int) -> dict[str, Any]:
    rows = (await client.get("/api/applications", params={"limit": 200})).json()["applications"]
    return next(r for r in rows if r["id"] == app_id)


async def _mark(client: AsyncClient, ids: list[int], checked_at: str = ""):
    return await client.post("/api/applications/inbox-checked", json={"application_ids": ids, "checked_at": checked_at})


@pytest.mark.parametrize(
    "url, senders",
    [
        (ASHBY, ["ashbyhq.com"]),
        ("https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4739199005",
         ["greenhouse.io", "greenhouse-mail.io"]),
        ("https://jobs.lever.co/mactores/07c51b46", ["lever.co"]),
        ("https://apply.workable.com/j/C344AE73AD", ["workable.com", "workablemail.com"]),
        ("https://evil-greenhouse.io/jobs/1", []),        # look-alike host: not the vendor
        ("https://greenhouse.io.evil.com/jobs/1", []),
        ("https://careers.example.com/jobs/7", []),       # own careers site: domain unknown, stay silent
        ("", []),
        ("mailto:hr@ashbyhq.com", []),
    ],
)
def test_senders_come_from_the_apply_links_ats(url: str, senders: list[str]) -> None:
    assert email_senders_for(url) == senders


@pytest.mark.asyncio
async def test_a_row_carries_its_senders_and_starts_unchecked(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        row = await _row(client, app_id)
        assert row["email_senders"] == ["ashbyhq.com"]
        assert row["inbox_checked_at"] is None
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert detail["email_senders"] == ["ashbyhq.com"] and detail["inbox_checked_at"] is None


@pytest.mark.asyncio
async def test_marking_moves_the_cursor_forward_only_and_writes_no_event(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client)
        b = await _bring(client, {**AD, "company": "Razorpay"})
        before = await _row(client, a)
        events_before = len((await client.get(f"/api/applications/{a}")).json()["events"])

        when = _iso(1)
        resp = await _mark(client, [a, b], when)
        assert resp.status_code == 200, resp.text
        assert resp.json()["application_ids"] == sorted([a, b])
        stored = resp.json()["inbox_checked_at"]
        assert (await _row(client, a))["inbox_checked_at"] == stored
        assert (await _row(client, b))["inbox_checked_at"] == stored

        # an older time never rewinds it
        assert (await _mark(client, [a], _iso(5))).status_code == 200
        assert (await _row(client, a))["inbox_checked_at"] == stored
        # a newer one moves it
        assert (await _mark(client, [a])).status_code == 200
        assert (await _row(client, a))["inbox_checked_at"] > stored

        # a cursor, not history: no timeline event, the job does not look active
        after = await _row(client, a)
        assert len((await client.get(f"/api/applications/{a}")).json()["events"]) == events_before
        assert after["last_event_at"] == before["last_event_at"]


@pytest.mark.asyncio
async def test_bad_input_is_refused_and_writes_nothing(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        a = await _bring(client)
        foreign = await _mark(client, [a, 987654321])
        assert foreign.status_code == 404
        assert (await _row(client, a))["inbox_checked_at"] is None  # all or nothing
        assert (await _mark(client, [])).status_code == 422
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        assert (await _mark(client, [a], future)).status_code == 422
        assert (await _mark(client, [a], "yesterday")).status_code == 422
        assert (await _mark(client, [a], "2026-10-15T10:00:00")).status_code == 422  # no timezone
        monkeypatch.setattr(settings, "INBOX_CHECK_MAX_APPLICATIONS", 1)
        too_many = await _mark(client, [a, a + 1])
        assert too_many.status_code == 422 and "INBOX_CHECK_MAX_APPLICATIONS" in too_many.text
        assert (await _row(client, a))["inbox_checked_at"] is None


@pytest.mark.asyncio
async def test_signed_out_is_refused():
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await _mark(anon, [1])).status_code == 401


@pytest.mark.asyncio
async def test_the_mcp_tool_uses_the_same_route(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                              headers={"Authorization": f"Bearer {token}"})
    async with mcp_runtime(), Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
        def load(r):
            assert not r.is_error, r.content[0].text
            return json.loads(r.content[0].text)

        row = load(await mcp.call_tool("list_applications", {}))["applications"][0]
        assert row["email_senders"] == ["ashbyhq.com"] and row["inbox_checked_at"] is None
        marked = load(await mcp.call_tool("mark_inbox_checked", {"application_ids": [app_id]}))
        assert marked["application_ids"] == [app_id]
        row = load(await mcp.call_tool("list_applications", {}))["applications"][0]
        assert row["inbox_checked_at"] == marked["inbox_checked_at"]
        denied = await mcp.call_tool("mark_inbox_checked", {"application_ids": [987654321]})
        assert denied.is_error


def test_the_daily_recipe_teaches_the_check() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "src" / "recipes" / "daily.md").read_text(encoding="utf-8")
    for must in ("inbox_checked_at", "email_senders", "-in:sent -in:drafts", "WHOLE email",
                 "mark_inbox_checked", "record_application", "ask_user"):
        assert must in text, must
