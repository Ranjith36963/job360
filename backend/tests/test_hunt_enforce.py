"""Job360 enforces the hunt rules the 2026-11-17 run drifted from (recipe hunt.md, PR #1628 follow-up).

The 11-17 run (Sonnet, the first on the recipe) brought 4 jobs against a target of 10: it never searched the web,
connectors or a browser and said nothing about them; of 370 new kit ads it read 52 in full; and 2 of its 4 jobs
kept a Shine / Adzuna board link although bring_job warned `board_link`. Recipe text alone did not hold, so:

* mark_done(duty="hunt") from an assistant carries a `report` (every source, and how many kit ads it read in full);
  a short one is a 422 that says exactly what is missing (log `hunt_report_refused`, codes and counts only).
* bring_job from an assistant with a board apply link and origin ai_hunt / your_mail must say it searched the
  employer's page (`employer_search: "not_found"`), else a 422 says what to do (log `job_bring_board_refused`).
Real routes and the MCP tools on the real Postgres; the website's own calls are unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes.recipes import load_recipe


def _ok_source(calls: int = 3, ads: int = 40, brought: int = 1) -> dict[str, int]:
    return {"calls": calls, "ads": ads, "brought": brought}


GOOD_REPORT: dict[str, Any] = {
    "sources": {"mail": _ok_source(), "kit": _ok_source(1, 496, 3), "connectors": _ok_source(),
                "web": _ok_source(), "browser": {"skipped": {"code": "tool_missing",
                                                         "detail": "no browser tool in this scheduled cloud run"}}},
    "kit": {"results": 496, "new": 370, "prefilter_passed": 370, "read_in_full": 370, "unreadable": 0,
            "snippet_only": 0, "done": {"results": 496, "full": 400, "snippet": 90, "empty": 6}},
}
# The 2026-11-17 run as it reported itself: mail and kit only, 52 of 370 new kit ads read in full.
REPLAY_1117: dict[str, Any] = {
    "sources": {"mail": _ok_source(1, 12, 1), "kit": _ok_source(1, 496, 3)},
    "kit": {"results": 496, "new": 370, "prefilter_passed": 370, "read_in_full": 52, "unreadable": 0, "snippet_only": 0},
}


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "scheduler"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _done(agent: AsyncClient, **body: Any):
    return await agent.post("/api/schedule/done", json={"duty": "hunt", **body})


# ── 2. mark_done hunt report ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_1117_hunt_report_is_refused_with_what_is_missing(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, outcome="partial", report=REPLAY_1117)
        due = (await client.get("/api/schedule/due")).json()
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "connectors, web, browser" in detail and "{skipped:" in detail
    assert "52 of 370" in detail and "unread_reason" in detail
    assert due["last_done_at"]["hunt"] is None, "nothing was stamped"
    refused = [r for r in caplog.records if r.getMessage() == "hunt_report_refused"]
    assert len(refused) == 1 and refused[0].codes == "source_missing,kit_unread"
    assert refused[0].missing == "connectors,web,browser"


@pytest.mark.asyncio
async def test_an_agent_hunt_without_a_report_is_refused_and_the_website_is_not(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            bare = await _done(agent)
        assert bare.status_code == 422 and "report" in bare.json()["detail"] and "sources" in bare.json()["detail"]
        assert (await _done(client)).status_code == 200, "the website's own mark needs no report"


@pytest.mark.parametrize(("change", "words"), [
    ({"sources": {**GOOD_REPORT["sources"], "web": {"skipped": " "}}}, "web"),
    ({"sources": {**GOOD_REPORT["sources"], "web": {"calls": 2}}}, "web"),
    ({"kit": {**GOOD_REPORT["kit"], "read_in_full": 400}}, "read_in_full"),
    ({"kit": None}, "kit"),
])
@pytest.mark.asyncio
async def test_a_hunt_report_with_a_bad_source_or_kit_count_is_refused(authenticated_async_context, change, words):
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, report={**GOOD_REPORT, **change})
    assert resp.status_code == 422, resp.text
    assert words in str(resp.json()["detail"])


@pytest.mark.asyncio
async def test_a_full_hunt_report_is_accepted_and_logged_as_counts(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    short_with_reason = {**GOOD_REPORT, "kit": {**GOOD_REPORT["kit"], "read_in_full": 300, "unreadable": 70,
                                                "unread_reason": "70 ads had no text and no page would open"}}
    async with authenticated_async_context() as client, await _agent(client) as agent:
        full = await _done(agent, report=GOOD_REPORT)
        reasoned = await _done(agent, outcome="partial", report=short_with_reason)
        other = await agent.post("/api/schedule/done", json={"duty": "inbox", "report": GOOD_REPORT})
    assert full.status_code == 200 and reasoned.status_code == 200, (full.text, reasoned.text)
    assert other.status_code == 422 and "only for duty hunt" in other.json()["detail"]
    ok = [r for r in caplog.records if r.getMessage() == "hunt_report_ok"]
    assert len(ok) == 2
    assert ok[0].sources == "mail:ran,kit:ran,connectors:ran,web:ran,browser:skipped"
    assert (ok[0].kit_prefilter_passed, ok[0].kit_read_in_full) == (370, 370)
    assert "no browser tool" not in str(ok[0].__dict__), "free text is never logged"


@pytest.mark.asyncio
async def test_the_mcp_mark_done_takes_the_report(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context():
            assert (await tools.get_tool("mark_done").fn(duty="hunt", report=GOOD_REPORT))["duty"] == "hunt"
            with pytest.raises(Exception, match="connectors, web, browser"):
                await tools.get_tool("mark_done").fn(duty="hunt", report=REPLAY_1117)
    finally:
        m._current_user.reset(token)


# ── 4. employer page first ───────────────────────────────────────────────────

_SHINE = {"title": "AI Engineer", "company": "Acme Analytics", "location": "Bengaluru",
          "apply_url": "https://www.shine.com/jobs/ai-engineer/acme/123", "description": "Build agents. " * 8,
          "origin": "ai_hunt"}
# 2026-11-18 16:13: "not_found" names what was searched, at least one careers page or ATS.
_SEARCHED = {"employer_search": "not_found", "employer_searched": ["https://careers.acme.example/jobs"]}
_ADZUNA = {**_SHINE, "title": "ML Engineer", "apply_url": "https://www.adzuna.co.uk/jobs/details/456",
           "origin": "your_mail"}


async def _app_count(client: AsyncClient) -> int:
    return int((await client.get("/api/applications", params={"limit": 1})).json()["total"])


@pytest.mark.asyncio
async def test_an_agent_board_link_without_employer_search_is_refused(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            shine = await agent.post("/api/jobs/bring", json=_SHINE)
            adzuna = await agent.post("/api/jobs/bring", json=_ADZUNA)
        assert await _app_count(client) == 0, "nothing was written"
    for resp in (shine, adzuna):
        assert resp.status_code == 422, resp.text
        assert "employer's own careers page" in resp.json()["detail"]
        assert 'employer_search "not_found"' in resp.json()["detail"]
    refused = [r for r in caplog.records if r.getMessage() == "job_bring_board_refused"]
    assert [r.board for r in refused] == ["shine.com", "adzuna"], "a closed board name, never the caller's host"
    assert not any(hasattr(r, "host") for r in refused)
    assert {r.reason for r in refused} == {"employer_search_missing"}


@pytest.mark.asyncio
async def test_employer_search_not_found_brings_with_the_warning_and_other_links_are_unaffected(
    authenticated_async_context,
):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            searched = await agent.post("/api/jobs/bring", json={**_SHINE, **_SEARCHED})
            own = await agent.post("/api/jobs/bring", json={**_SHINE, "title": "Agent Engineer",
                                                            "apply_url": "https://careers.acme.example/jobs/9"})
            no_origin = await agent.post("/api/jobs/bring", json={**_SHINE, "title": "Data Engineer", "origin": None})
            bad = await agent.post("/api/jobs/bring", json={**_SHINE, "employer_search": "maybe"})
        web = await client.post("/api/jobs/bring", json={**_SHINE, "title": "Web Engineer"})
    assert searched.status_code == 200, searched.text
    assert [w["code"] for w in searched.json()["warnings"]] == ["board_link"]
    assert own.status_code == 200 and own.json()["warnings"] == []
    assert no_origin.status_code == 200 and web.status_code == 200
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_the_mcp_bring_job_takes_employer_search(authenticated_async_context):
    import json

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                              headers={"Authorization": f"Bearer {token}"})
    async with mcp_runtime(), Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
        refused = await mcp.call_tool("bring_job", dict(_SHINE))
        brought = await mcp.call_tool("bring_job", {**_SHINE, **_SEARCHED})
    assert refused.is_error and "employer_search" in refused.content[0].text
    assert not brought.is_error, brought.content[0].text
    assert [w["code"] for w in json.loads(brought.content[0].text)["warnings"]] == ["board_link"]


# ── 1, 3. the recipe says it ─────────────────────────────────────────────────


def test_the_hunt_recipe_fetches_the_known_list_reads_every_ad_and_reports():
    text = load_recipe("hunt").text
    flat = " ".join(text.split())
    assert len(text.splitlines()) <= 70
    assert "known_list" in flat and "curl" in flat and "hand" in flat
    assert "read_in_full" in flat and "prefilter_passed" in flat and "EVERY" in flat
    assert "40" in flat and "8 helpers" in flat
    for key in ("mail", "kit", "connectors", "web", "browser", "skipped"):
        assert key in flat
    assert 'employer_search "not_found"' in flat
