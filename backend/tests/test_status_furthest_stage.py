"""Owner decision 2026-10-14 — "furthest stage wins" for the status cache, the
no-``applied`` warning, stats rates that never exceed 1, and the cache rebuild
script. Helpers are copied, never imported from another test module (a
cross-module fixture import breaks schema isolation)."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications.status import replay_status

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}
_AD_2 = {**_AD, "title": "Platform Engineer", "company": "Southwind", "apply_url": "https://southwind.example/c/3"}

NO_APPLIED = "no applied event recorded for this application — ask the user when they applied, then record_application"


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _detail(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.mark.asyncio
async def test_applied_after_interview_does_not_regress_status(authenticated_async_context):
    """The reproduced bug: replied + interview_requested, THEN the late applied."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "replied", occurred_at=_ago(3))
        await _event(client, app_id, "interview_requested", occurred_at=_ago(2))
        late = await _event(client, app_id, "applied", occurred_at=_ago(10))
        assert late["status"] == "interview_requested"
        detail = await _detail(client, app_id)
        assert detail["status"] == "interview_requested"
        assert detail["next_step"]["code"] != "wait", detail["next_step"]
        assert replay_status(detail["events"]) == "interview_requested"


@pytest.mark.asyncio
async def test_offer_then_replied_keeps_offer(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        await _event(client, app_id, "offer")
        assert (await _event(client, app_id, "replied"))["status"] == "offer"
        listed = (await client.get("/api/applications", params={"status": "offer"})).json()["applications"]
        assert [a["id"] for a in listed] == [app_id]


@pytest.mark.asyncio
async def test_ghosted_then_replied_moves_forward(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        assert (await _event(client, app_id, "ghosted"))["status"] == "ghosted"
        assert (await _event(client, app_id, "replied"))["status"] == "replied"


def test_ghosted_never_overrides_an_offer_or_an_ending():
    def ev(i: int, t: str) -> dict[str, Any]:
        return {"id": i, "event_type": t, "recorded_at": f"2026-10-14T10:00:0{i}+00:00", "occurred_at": ""}

    assert replay_status([ev(1, "brought"), ev(2, "applied"), ev(3, "offer"), ev(4, "ghosted")]) == "offer"
    assert replay_status([ev(1, "applied"), ev(2, "rejected"), ev(3, "ghosted")]) == "rejected"
    assert replay_status([ev(1, "applied"), ev(2, "interview_done"), ev(3, "ghosted")]) == "ghosted"


@pytest.mark.asyncio
async def test_rejected_after_offer_applies(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        await _event(client, app_id, "offer")
        assert (await _event(client, app_id, "rejected"))["status"] == "rejected"
        assert (await _event(client, app_id, "withdrawn"))["status"] == "withdrawn", "the latest ending wins"


@pytest.mark.asyncio
async def test_reopen_after_rejected_needs_later_occurred_at(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied", occurred_at=_ago(20))
        await _event(client, app_id, "rejected", occurred_at=_ago(5))
        # a late record of something that happened BEFORE the rejection: no reopen
        stale = await _event(client, app_id, "interview_requested", occurred_at=_ago(10))
        assert stale["status"] == "rejected"
        # news that happened AFTER the rejection: reopens
        fresh = await _event(client, app_id, "interview_requested", occurred_at=_ago(1))
        assert fresh["status"] == "interview_requested"
        # and progress continues from the reopened rung
        assert (await _event(client, app_id, "interview_scheduled"))["status"] == "interview_scheduled"


@pytest.mark.asyncio
async def test_regress_only_via_corrects_event_id(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        replied = await _event(client, app_id, "replied")
        wrong = await _event(client, app_id, "interview_requested")
        # a plain lower event never moves it back
        assert (await _event(client, app_id, "applied"))["status"] == "interview_requested"
        # correcting the mis-recorded event drops it from the replay
        fixed = await _event(client, app_id, "note", detail="no interview yet", corrects_event_id=wrong["event_id"])
        assert fixed["status"] == "replied"
        # a STATUS event that corrects another is an explicit "the status is this"
        back = await _event(client, app_id, "applied", corrects_event_id=replied["event_id"])
        assert back["status"] == "applied"
        detail = await _detail(client, app_id)
        assert detail["status"] == "applied" and replay_status(detail["events"]) == "applied"


@pytest.mark.asyncio
async def test_replied_without_applied_warns(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        replied = await _event(client, app_id, "replied")
        assert replied["status"] == "replied", "the fact is still accepted"
        assert replied["warnings"] == [NO_APPLIED]
        detail = await _detail(client, app_id)
        assert [e["event_type"] for e in detail["events"]] == ["brought", "replied"], "no synthetic applied"
        assert (await _event(client, app_id, "note", detail="x"))["warnings"] == []
        await _event(client, app_id, "applied")
        assert (await _event(client, app_id, "interview_requested"))["warnings"] == []
        other = await _bring(client, _AD_2)
        token = (await client.post("/api/tokens", json={"name": "cli"})).json()["token"]
    # the MCP tool returns the route's reply, warnings included
    pytest.importorskip("mcp")
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime(), Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
        result = await mcp.call_tool("record_event", {"event_type": "offer", "application_id": other})
        assert not result.is_error, result.content[0].text
        body = json.loads(result.content[0].text)
        assert body["status"] == "offer" and body["warnings"] == [NO_APPLIED]


@pytest.mark.asyncio
async def test_stats_rates_never_exceed_one(authenticated_async_context):
    async with authenticated_async_context() as client:
        orphan = await _bring(client)
        await _event(client, orphan, "replied")
        await _event(client, orphan, "interview_requested")
        normal = await _bring(client, _AD_2)
        await _event(client, normal, "applied")
        resp = await client.get("/api/applications/stats")
        assert resp.status_code == 200, resp.text
        overall = resp.json()["overall"]
        assert (overall["applied"], overall["replied"], overall["interview"]) == (1, 0, 0)
        assert overall["replies_without_applied"] == 1
        for group_key in ("by_role", "by_cv_version", "by_country", "by_job_source", "by_channel"):
            for g in resp.json()[group_key]:
                for rate in ("reply_rate", "interview_rate", "offer_rate"):
                    assert g[rate] is None or g[rate] <= 1, (group_key, g)
        assert all(overall[r] is None or overall[r] <= 1 for r in ("reply_rate", "interview_rate", "offer_rate"))


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "rebuild_status_cache.py"
    spec = importlib.util.spec_from_file_location("rebuild_status_cache_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_rebuild_status_cache_dry_run(authenticated_async_context, capsys):
    from src.core import settings
    from src.repositories import pgsync

    script = _load_script()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        await _event(client, app_id, "interview_requested")
        other = await _bring(client, _AD_2)
        await _event(client, other, "applied")

    with pgsync.connect(str(settings.DB_PATH)) as conn:
        # a row written under the OLD rule ("last status event wins")
        conn.execute("UPDATE applications SET status = 'applied', stage = 'applied' WHERE id = ?", (app_id,))
        events_before = conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]

        dry = script.rebuild(conn, apply=False)
        assert (dry["changed"], dry["checked"]) == (1, 2)
        assert dry["changes"][0]["status"] == ("applied", "interview_requested")
        assert conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()[0] == "applied"

    assert script.main([]) == 0  # default is a dry run
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "2 applications checked, 1 differ" in out

    with pgsync.connect(str(settings.DB_PATH)) as conn:
        assert conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()[0] == "applied"
        applied = script.rebuild(conn, apply=True)
        assert applied["changed"] == 1
        row = conn.execute("SELECT status, stage FROM applications WHERE id = ?", (app_id,)).fetchone()
        assert tuple(row) == ("interview_requested", "interview")
        assert script.rebuild(conn, apply=True)["changed"] == 0, "idempotent"
        assert conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0] == events_before
