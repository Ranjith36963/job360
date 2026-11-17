"""Two reply warnings on an AGENT bring (hunt recipe v24, owner 2026-11-17).

1. ``possible_duplicate``. Prod 2026-11-17: the hunt brought the same Robert Walters role a 4th time.
   The open-duplicate guard (reposts.open_duplicate_of) compares the whole company label, and each run
   wrote the agency's client into it ("Robert Walters (agency; client is a PE firm)" vs
   "Robert Walters (PE firm client)"), so it never matched. The new job is still created (a renamed
   label cannot prove it is the same job), but the reply names the open applications with the same
   role words and says what to do, and a log line records the ids.
2. ``board_link``. The apply link is a job board's page (exact hostname, never a substring): the reply
   tells the agent to find the employer's own page and set it with update_job(apply_url).

Real doors (HTTP + MCP) on the real Postgres; web-session brings get no warnings.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications.job_checks import apply_host, classify_host
from src.services.applications.reposts import role_words

_DESC = "Join the CTO office of a private equity firm. Python, LLM agents, retrieval. Graduate role."
# The real application rows (prod, 2026-11-05..17), company labels exactly as the hunt wrote them.
_RW_453: dict[str, Any] = {
    "title": "Graduate AI Engineer - PE Firm - CTO Office",
    "company": "Robert Walters (agency; client PE firm unnamed)", "location": "London",
    "apply_url": "https://www.robertwalters.co.uk/technologydigital/jobs/1917601-graduate-ai-engineer.html",
    "description": _DESC,
}
_RW_605: dict[str, Any] = {
    "title": "Graduate AI Engineer- PE Firm-CTO Office",
    "company": "Robert Walters (PE firm client)", "location": "London",
    "apply_url": "https://careers.pe-client.example/jobs/605",
    "description": _DESC,
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


async def _token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _codes(reply: dict[str, Any]) -> list[str]:
    return [w["code"] for w in reply.get("warnings") or []]


def test_role_words_ignore_punctuation_and_location() -> None:
    assert role_words(_RW_453["title"]) == role_words(_RW_605["title"]) == "graduate ai engineer pe firm cto office"
    assert role_words("Data Engineer - Glasgow, Scotland, UK") == "data engineer"


@pytest.mark.parametrize("url,host", [
    ("https://www.adzuna.co.uk/jobs/details/1", "www.adzuna.co.uk"),
    ("https://www.reed.co.uk/jobs/ai-engineer/2", "www.reed.co.uk"),
    ("https://www.shine.com/jobs/3", "www.shine.com"),
    ("https://uk.indeed.com/viewjob?jk=4", "uk.indeed.com"),
    ("https://www.linkedin.com/jobs/view/5", "www.linkedin.com"),
    ("https://www.naukri.com/job-listings-6", "www.naukri.com"),
    ("https://www.glassdoor.co.uk/job-listing/7", "www.glassdoor.co.uk"),
    ("https://www.totaljobs.com/job/8", "www.totaljobs.com"),
    ("https://uk.whatjobs.com/jobs/9", "uk.whatjobs.com"),
])
def test_known_boards_are_job_boards_by_exact_host(url: str, host: str) -> None:
    assert apply_host(url) == host
    assert classify_host(host) == "job_board"


@pytest.mark.parametrize("host", ["reed.co.uk.evil.example", "notreed.co.uk", "evilshine.com", "adzuna-jobs.example"])
def test_look_alike_hosts_are_not_boards(host: str) -> None:
    assert classify_host(host) != "job_board"


@pytest.mark.asyncio
async def test_renamed_agency_label_brings_with_a_possible_duplicate_warning(
    authenticated_async_context, fixture_user_id, caplog,
):
    """Replay of 2026-11-17: application 453 open; the same role arrives as 605 under another label."""
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = await _token(client)
    caplog.set_level(logging.INFO)
    async with mcp_runtime(), _mcp_client(token) as mcp:
        first = _payload(await mcp.call_tool("bring_job", _RW_453))
        again = _payload(await mcp.call_tool("bring_job", _RW_605))
    assert first.get("warnings") == []
    assert again["application_id"] != first["application_id"], "still created: a label alone proves nothing"
    assert again["duplicate_of"] is None
    dup = [w for w in again["warnings"] if w["code"] == "possible_duplicate"]
    assert len(dup) == 1, again["warnings"]
    assert dup[0]["application_ids"] == [first["application_id"]]
    assert "get_application" in dup[0]["next"] and "withdrawn" in dup[0]["next"]
    logged = [r for r in caplog.records if r.getMessage() == "job_bring_possible_duplicate"]
    assert len(logged) == 1
    assert logged[0].application_id == again["application_id"]
    assert logged[0].matched_ids == [first["application_id"]]
    assert logged[0].user_id == fixture_user_id
    assert not hasattr(logged[0], "title") and not hasattr(logged[0], "company"), "ids and codes only"


@pytest.mark.asyncio
async def test_no_duplicate_warning_for_short_titles_closed_or_other_roles(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = await _token(client)
        closed = (await client.post("/api/jobs/bring", json={**_RW_453, "title": "ML Platform Engineer Graduate"})).json()
        rej = await client.post(f"/api/applications/{closed['application_id']}/events", json={"event_type": "rejected"})
        assert rej.json()["status"] == "rejected", rej.text
        await client.post("/api/jobs/bring", json={**_RW_453, "title": "AI Engineer", "company": "Acme"})
    async with mcp_runtime(), _mcp_client(token) as mcp:
        short = _payload(await mcp.call_tool("bring_job", {**_RW_605, "title": "AI Engineer"}))
        after_closed = _payload(await mcp.call_tool("bring_job", {**_RW_605, "title": "ML Platform Engineer Graduate"}))
        other = _payload(await mcp.call_tool("bring_job", {**_RW_605, "title": "Graduate Data Scientist - PE Firm"}))
    for reply in (short, after_closed, other):
        assert "possible_duplicate" not in _codes(reply), reply["warnings"]


@pytest.mark.asyncio
async def test_board_link_warns_and_logs_and_employer_link_does_not(authenticated_async_context, caplog):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = await _token(client)
    caplog.set_level(logging.INFO)
    board = {**_RW_605, "title": "Agent Engineer", "company": "Acme",
             "apply_url": "https://www.reed.co.uk/jobs/agent-engineer/123"}
    own = {**board, "title": "Agent Engineer Two", "apply_url": "https://careers.acme.example/jobs/123"}
    async with mcp_runtime(), _mcp_client(token) as mcp:
        warned = _payload(await mcp.call_tool("bring_job", board))
        clean = _payload(await mcp.call_tool("bring_job", own))
    hits = [w for w in warned["warnings"] if w["code"] == "board_link"]
    assert len(hits) == 1 and hits[0]["host"] == "www.reed.co.uk"
    assert "update_job" in hits[0]["next"] and "apply_url" in hits[0]["next"]
    assert clean["warnings"] == []
    logged = [r for r in caplog.records if r.getMessage() == "job_bring_board_link"]
    assert [r.application_id for r in logged] == [warned["application_id"]]
    assert logged[0].host == "www.reed.co.uk"


@pytest.mark.asyncio
async def test_web_session_brings_carry_no_warnings(authenticated_async_context):
    async with authenticated_async_context() as client:
        await client.post("/api/jobs/bring", json=_RW_453)
        resp = await client.post("/api/jobs/bring", json={**_RW_605, "apply_url": "https://www.linkedin.com/jobs/view/1"})
        assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == []
