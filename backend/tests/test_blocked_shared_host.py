"""Two applications on ONE ATS host never block each other's events (assistant-browser run 2026-11-18 17:05).

Root cause: ``blocked`` opens Job360's own Needs-you ask for THAT application (``blocked.ask_question``: the
page host is in its words), and ``asks.create_ask`` refused any ask whose words name another open application's
company as a whole word (owner 2026-10-24, one ask per job). A tenant host carries the employer's short name
("lbg" in ``lbg.wd3.myworkdayjobs.com``), so a second open application labelled "LBG" made the block on the
"Lloyds Bank" application a 422 and the block was never recorded.

Now the check is keyed by application_id: Job360's own block ask is about the application it is recorded on,
so it never scans other jobs' names; and a company name inside a dotted host name never counts as naming that
job (an assistant's "sign in on <host>" ask). An assistant's ask that names another job outright is still
refused (the per-job rule stays).
Helpers are copied, never imported (per-test schema isolation).
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import blocked as blocked_service

HOST = "lbg.wd3.myworkdayjobs.com"


def _ad(company: str, title: str, n: int) -> dict[str, Any]:
    return {
        "title": title, "company": company, "location": "London", "country": "GB", "remote": False,
        "apply_url": f"https://{HOST}/LBG_Careers/job/London/Role_{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake.",
    }


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _bring(client: AsyncClient, company: str, title: str, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(company, title, n))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _open_asks(client: AsyncClient, app_id: int) -> list[dict[str, Any]]:
    resp = await client.get("/api/asks", params={"status": "open"})
    assert resp.status_code == 200, resp.text
    return [a for a in resp.json()["asks"] if a["application_id"] == app_id]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def log_capture():
    audit = logging.getLogger("job360.audit")
    handler = _Capture()
    audit.addHandler(handler)
    old = audit.level
    audit.setLevel(logging.INFO)
    yield handler
    audit.removeHandler(handler)
    audit.setLevel(old)


# Every reason that opens Job360's own ask (posting_closed opens none; otp_needed opens one while the user
# enters codes, the default).
_ASK_REASONS = sorted(set(blocked_service.BLOCKED_REASONS) - {"posting_closed"})


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", _ASK_REASONS)
async def test_a_block_records_while_another_open_application_shares_the_host(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        token = await _mint_token(client)
        lloyds = await _bring(client, "Lloyds Bank", "Data Engineer", 1)
        lbg = await _bring(client, "LBG", "Platform Engineer", 2)
        async with _bearer_client(token) as agent:
            # Application B (same host) already holds an open block and its ask.
            other = await _event(agent, lbg, "blocked", {"reason": "captcha", "step": "open form", "page_host": HOST,
                                                         "browser": "own_browser"})
            assert other.status_code == 201, other.text
            resp = await _event(agent, lloyds, "blocked", {"reason": reason, "step": "open form", "page_host": HOST,
                                                           "browser": "own_browser"})
            assert resp.status_code == 201, resp.text
        asks = await _open_asks(client, lloyds)
        assert [a["question"] for a in asks] == [blocked_service.ask_question({"reason": reason, "page_host": HOST})]
        assert len(await _open_asks(client, lbg)) == 1  # B's own ask is untouched


@pytest.mark.asyncio
async def test_the_web_users_block_records_too(authenticated_async_context):
    async with authenticated_async_context() as client:
        lloyds = await _bring(client, "Lloyds Bank", "Data Engineer", 1)
        await _bring(client, "LBG", "Platform Engineer", 2)
        resp = await _event(client, lloyds, "blocked", {"reason": "login_needed", "step": "sign in", "page_host": HOST})
        assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_an_assistant_ask_naming_the_shared_host_is_this_jobs_ask(authenticated_async_context, log_capture):
    """own_browser.md: after a block, ONE ask_user "sign in on <host>": the host names no other job."""
    async with authenticated_async_context() as client:
        token = await _mint_token(client)
        lloyds = await _bring(client, "Lloyds Bank", "Data Engineer", 1)
        await _bring(client, "LBG", "Platform Engineer", 2)
        async with _bearer_client(token) as agent:
            ok = await agent.post("/api/asks", json={
                "question": f"Please sign in on {HOST} on your PC, then tell me.", "kind": "login",
                "application_id": lloyds,
            })
            assert ok.status_code in (200, 201), ok.text
            # The per-job rule stays: an ask that names the other job outright is still refused, and logged.
            both = await agent.post("/api/asks", json={
                "question": "Expected salary for Lloyds Bank and LBG?", "application_id": lloyds,
            })
            assert both.status_code == 422 and "one ask per job" in both.text and "LBG" in both.text
    refused = [r for r in log_capture.records if r.get("event") == "ask_refused" and r.get("reason") == "names_other_job"]
    assert len(refused) == 1
    assert refused[0]["application_id"] == str(lloyds) and refused[0]["named"] == 1
