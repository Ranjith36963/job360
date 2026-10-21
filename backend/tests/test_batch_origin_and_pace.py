"""Owner asks 2026-10-18: every job carries where it came from (``origin``: ai_hunt / user_link /
user_paste), ``bring_job queue=true`` puts it straight into the batch, the wait between applications
takes seconds, and ``company_cap_days`` 0 = all time.

Real doors (HTTP and MCP) on the real Postgres. Helpers are copied, never imported from another
test module (schema isolation).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings


def _ad(n: int, company: str = "Northwind", **extra: Any) -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": company, "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.", **extra,
    }


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _rows(client: AsyncClient, **params: Any) -> dict[int, dict[str, Any]]:
    resp = await client.get("/api/applications", params=params)
    assert resp.status_code == 200, resp.text
    return {r["id"]: r for r in resp.json()["applications"]}


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


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


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_bring_with_queue_puts_it_in_the_batch_and_tags_the_origin(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        linked = await _bring(client, _ad(1, origin="user_link", queue=True))
        assert (linked["queued"], linked["queue_note"]) == (True, None)
        pasted = await _bring(client, _ad(2, company="Contoso", origin="user_paste"))
        assert pasted["queued"] is None, "no queue asked: nothing said"
        plain = await _bring(client, _ad(3, company="Initech"))
        again = await _bring(client, _ad(1, origin="ai_hunt", queue=True))
        assert again["existing"] is True and (again["queued"], again["queue_note"]) == (True, "already queued")
        queued = await _rows(client, queued=True)
        assert list(queued) == [linked["application_id"]]
        rows = await _rows(client)
        assert rows[linked["application_id"]]["origin"] == "user_link", "a re-bring never rewrites the origin"
        assert rows[pasted["application_id"]]["origin"] == "user_paste"
        assert rows[plain["application_id"]]["origin"] == "user", "no origin said: the web brought it"
        bad = await client.post("/api/jobs/bring", json=_ad(4, origin="scraped"))
        assert bad.status_code == 422


@pytest.mark.asyncio
async def test_a_closed_application_is_not_queued_by_a_bring(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        first = await _bring(client, _ad(1))
        sent = await client.post(
            f"/api/applications/{first['application_id']}/events", json={"event_type": "applied"}
        )
        assert sent.status_code == 201, sent.text
        again = await _bring(client, _ad(1, queue=True))
        assert (again["queued"], again["queue_note"]) == (False, "not open")
        assert await _rows(client, queued=True) == {}


@pytest.mark.asyncio
async def test_an_assistant_bring_is_tagged_ai_and_queues_through_mcp(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = await _mint_token(client)
    async with mcp_runtime(), _mcp_client(token) as mcp:
        hunted = _payload(await mcp.call_tool("bring_job", {**_ad(1), "origin": "ai_hunt", "queue": True}))
        assert hunted["queued"] is True
        unsaid = _payload(await mcp.call_tool("bring_job", _ad(2, company="Contoso")))
        assert unsaid["queued"] is None
    async with authenticated_async_context() as client:
        rows = await _rows(client)
        assert rows[hunted["application_id"]]["origin"] == "ai_hunt"
        assert rows[unsaid["application_id"]]["origin"] == "ai", "no origin said: an assistant brought it"
        assert list(await _rows(client, queued=True)) == [hunted["application_id"]]


# ═══════════════════════════════════════════════════════════════════════════
# The wait in seconds, and 0 days = all time
# ═══════════════════════════════════════════════════════════════════════════


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
FORM = "https://careers.northwind.example/apply/7"


def _gate(cfg: rules.EffectiveSettings, last: datetime | None) -> rules.SubmitDecision:
    facts = rules.SubmitFacts(
        status="considering", has_receipt=False, submit_override=None, cv_seen=True,
        form_filled=True, form_swept=True, fit_score=80, form_host="careers.northwind.example",
    )
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=1, last_applied_at=last)
    return rules.may_submit(cfg, facts, FORM, counts, now=NOW)


def _eff(**kw: Any) -> rules.EffectiveSettings:
    return rules.effective(AssistantSettings(
        apply_min_score=50, above_line="auto", below_line="auto", submit_mode="auto_when_sure", **kw,
    ))


def test_the_wait_takes_whole_seconds():
    path = rules.MIN_GAP_PATH
    assert rules.validate_setting(path, 1.5) == 1.5
    assert rules.validate_setting(path, 0.25) == 0.25
    assert rules.validate_setting(path, 2.0) == 2 and isinstance(rules.validate_setting(path, 2.0), int)
    assert rules.validate_setting(path, 0) == 0
    for bad in (0.001, -1, 241, True, "5"):
        with pytest.raises(Exception):
            rules.validate_setting(path, bad)
    assert rules.gap_words(1.5) == "1 min 30 s"
    assert rules.gap_words(0.25) == "15 seconds"
    assert rules.gap_words(10) == "10 minutes"
    # a shorter wait is still the riskier change
    assert rules.classify_change(path, 1.5, 1.25) == "riskier"
    assert rules.classify_change(path, 1.25, 1.5) == "safer"


def test_a_wait_in_seconds_is_enforced_by_the_gate():
    cfg = _eff(min_gap_minutes=1.5)
    stop = _gate(cfg, NOW - timedelta(seconds=60))
    assert (stop.decision, stop.reason) == ("stop", "pace_wait")
    assert "1 min 30 s" in stop.detail and "12:00:30" in stop.detail
    assert _gate(cfg, NOW - timedelta(seconds=90)).decision == "submit"


def test_zero_days_means_all_time_and_is_the_safest_window():
    path = rules.COMPANY_CAP_DAYS_PATH
    assert rules.validate_setting(path, 0) == 0
    assert rules.classify_change(path, 30, 0) == "safer"
    assert rules.classify_change(path, 0, 30) == "riskier"


@pytest.mark.asyncio
async def test_zero_days_counts_an_application_from_long_ago(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        target = (await _bring(client, _ad(1)))["application_id"]
        old = (await _bring(client, _ad(2)))["application_id"]
        sent = await client.post(
            f"/api/applications/{old}/events",
            json={"event_type": "applied", "occurred_at": (now - timedelta(days=900)).isoformat()},
        )
        assert sent.status_code == 201, sent.text
        db = await api_deps.get_db()
        assert await rules.company_applied_count(db, fixture_user_id, target, now, 180) == 0
        assert await rules.company_applied_count(db, fixture_user_id, target, now, 0) == 1
