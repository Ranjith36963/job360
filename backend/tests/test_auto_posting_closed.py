"""The AI closes an application whose posting is gone (owner 2026-11-02; services/applications/auto_close.py).

Real Postgres through the real doors: an assistant (bearer token) records ``blocked`` over HTTP or MCP, the
user reads ``/api/needs-you-count`` and undoes on the web. Pinned, one test per guardrail:
* proof read off the ad's own page closes it (``withdrawn``, typed + auditable) and it leaves Needs you;
* no ``evidence`` = no close: the user still sees "Posting closed";
* evidence from another site (a mail host) = no close; an email alone never closes anything;
* a receipt = no close; a started submit with no outcome = no close;
* ``evidence`` goes only with ``posting_closed`` (422);
* the user's one web Undo reopens it; an assistant cannot; nothing to undo is 409.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

HOST = "careers.northwind.example"


async def _agent(web: AsyncClient) -> AsyncClient:
    from src.api.main import app

    resp = await web.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {resp.json()['token']}"})


async def _bring(web: AsyncClient, n: int) -> int:
    ad = {"title": f"Data Engineer {n}", "company": f"Northwind{n}", "location": "London", "country": "GB",
          "remote": False, "apply_url": f"https://{HOST}/jobs/{n}", "description": "Build the pipelines."}
    resp = await web.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _closed(agent: AsyncClient, app_id: int, **extra: Any) -> Any:
    payload = {"reason": "posting_closed", "step": "open the ad", "page_host": HOST, **extra}
    return await agent.post(f"/api/applications/{app_id}/events", json={"event_type": "blocked", "payload": payload})


async def _detail(web: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await web.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(detail: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    return [e for e in detail["events"] if e["event_type"] == event_type]


async def _count(web: AsyncClient) -> dict[str, Any]:
    resp = await web.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _open_asks(web: AsyncClient, app_id: int) -> list[dict[str, Any]]:
    asks = (await web.get("/api/asks")).json()["asks"]
    return [a for a in asks if a.get("application_id") == app_id and a["status"] == "open"]


@pytest.mark.asyncio
async def test_a_closed_page_with_proof_closes_and_leaves_needs_you(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 1)
        async with await _agent(web) as agent:
            resp = await _closed(agent, app_id, evidence="http_404")
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "withdrawn"
        assert any(w.startswith("Job360 closed this application") for w in resp.json()["warnings"])
        detail = await _detail(web, app_id)
        assert detail["status"] == "withdrawn"
        [closed] = _events(detail, "withdrawn")
        assert closed["detail"] == "posting closed" and closed["recorded_by"] == "token:claude-code"
        assert closed["payload"]["closed_by"] == "posting_closed" and closed["payload"]["evidence"] == "http_404"
        assert closed["payload"]["page_host"] == HOST
        assert closed["payload"]["blocked_event_id"] == _events(detail, "blocked")[0]["id"]
        assert [e["payload"]["resolution"] for e in _events(detail, "unblocked")] == ["skipped"]
        count = await _count(web)
        assert app_id not in count["job_ids"] and "posting_closed" not in count["job_ids_by_kind"]
        assert app_id not in count["other_ask_ids"] and await _open_asks(web, app_id) == []


@pytest.mark.asyncio
async def test_no_proof_means_no_close_and_the_user_still_decides(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 2)
        async with await _agent(web) as agent:
            resp = await _closed(agent, app_id)
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        detail = await _detail(web, app_id)
        assert detail["status"] == "considering" and _events(detail, "withdrawn") == []
        assert (await _count(web))["job_ids_by_kind"]["posting_closed"] == [app_id]


@pytest.mark.asyncio
async def test_proof_from_a_mail_host_is_not_the_ad_page_and_never_closes(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 3)
        async with await _agent(web) as agent:
            resp = await _closed(agent, app_id, evidence="page_says_closed", page_host="mail.google.com")
            assert resp.status_code == 201, resp.text
            # A blocked record can carry no email source at all.
            sourced = await agent.post(f"/api/applications/{app_id}/events", json={
                "event_type": "blocked", "payload": {"reason": "posting_closed", "page_host": HOST,
                                                     "evidence": "page_says_closed"},
                "source": {"kind": "email", "message_id": "<closed@northwind.example>", "sender": "hr@northwind.example",
                           "subject": "This role is filled", "received_at": "2026-11-01T09:00:00+00:00"}})
            assert sourced.status_code == 422, sourced.text
        detail = await _detail(web, app_id)
        assert detail["status"] == "considering" and _events(detail, "withdrawn") == []
        assert (await _count(web))["job_ids_by_kind"]["posting_closed"] == [app_id]


@pytest.mark.asyncio
async def test_a_receipt_means_no_close(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 4)
        receipt = await web.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})
        assert receipt.status_code in (200, 201), receipt.text
        async with await _agent(web) as agent:
            resp = await _closed(agent, app_id, evidence="http_410")
        assert resp.status_code == 201, resp.text
        detail = await _detail(web, app_id)
        assert detail["status"] == "applied" and _events(detail, "withdrawn") == []


@pytest.mark.asyncio
async def test_a_started_submit_with_no_outcome_means_no_close(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 5)
        async with await _agent(web) as agent:
            started = await agent.post(f"/api/applications/{app_id}/events",
                                       json={"event_type": "submit_attempted", "payload": {}})
            assert started.status_code == 201, started.text
            resp = await _closed(agent, app_id, evidence="page_says_closed")
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        assert _events(await _detail(web, app_id), "withdrawn") == []


@pytest.mark.asyncio
async def test_evidence_goes_only_with_posting_closed(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 6)
        async with await _agent(web) as agent:
            wrong = await agent.post(f"/api/applications/{app_id}/events", json={
                "event_type": "blocked", "payload": {"reason": "captcha", "page_host": HOST, "evidence": "http_404"}})
            bad = await _closed(agent, app_id, evidence="recruiter_said_so")
        assert wrong.status_code == 422 and "evidence goes only with posting_closed" in wrong.text
        assert bad.status_code == 422 and "evidence goes only with posting_closed" in bad.text
        assert (await _detail(web, app_id))["events"][-1]["event_type"] == "brought"


@pytest.mark.asyncio
async def test_the_users_one_web_undo_reopens_it_and_an_assistant_cannot(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 7)
        assert (await web.post(f"/api/applications/{app_id}/auto/undo")).status_code == 409  # nothing to undo
        async with await _agent(web) as agent:
            assert (await _closed(agent, app_id, evidence="page_says_closed")).json()["status"] == "withdrawn"
            assert (await agent.post(f"/api/applications/{app_id}/auto/undo")).status_code == 403
        undo = await web.post(f"/api/applications/{app_id}/auto/undo")
        assert undo.status_code == 201, undo.text
        detail = await _detail(web, app_id)
        assert detail["status"] == "considering"
        [note] = [e for e in _events(detail, "note") if e["corrects_event_id"] is not None]
        assert note["corrects_event_id"] == _events(detail, "withdrawn")[0]["id"] and note["recorded_by"] == "web"
        assert note["detail"] == "Reopened: you said the posting is still open"
        assert "posting_closed" not in (await _count(web))["job_ids_by_kind"]  # the block was cleared with it
        assert (await web.post(f"/api/applications/{app_id}/auto/undo")).status_code == 409


@pytest.mark.asyncio
async def test_the_mcp_record_event_door_closes_it_too(authenticated_async_context):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as web:
        app_id = await _bring(web, 8)
        token = (await web.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                                  headers={"Authorization": f"Bearer {token}"})
        async with mcp_runtime():
            async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
                result = await mcp.call_tool("record_event", {
                    "application_id": app_id, "event_type": "blocked",
                    "payload": {"reason": "posting_closed", "page_host": HOST, "evidence": "page_says_closed"}})
                assert not result.is_error, result.content[0].text
                assert json.loads(result.content[0].text)["status"] == "withdrawn"
        assert (await _detail(web, app_id))["status"] == "withdrawn"
        assert app_id not in (await _count(web))["job_ids"]


@pytest.mark.asyncio
async def test_a_job_the_owner_said_send_on_is_never_auto_closed(authenticated_async_context):
    import hashlib

    async with authenticated_async_context() as web:
        app_id = await _bring(web, 9)
        cv = await web.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, Python."})
        assert cv.status_code == 201, cv.text
        yes = await web.post(f"/api/applications/{app_id}/events", json={"event_type": "submit_approved", "payload": {
            "artifact_id": int(cv.json()["artifact_id"]), "sha256": hashlib.sha256(b"Ada, Python.").hexdigest(),
            "where": "web"}})
        assert yes.status_code == 201, yes.text
        async with await _agent(web) as agent:
            resp = await _closed(agent, app_id, evidence="http_404")
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        detail = await _detail(web, app_id)
        assert detail["status"] == "considering" and _events(detail, "withdrawn") == []
        assert (await _count(web))["job_ids_by_kind"]["posting_closed"] == [app_id]


@pytest.mark.asyncio
async def test_a_job_with_an_open_ask_to_the_owner_is_never_auto_closed(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 10)
        async with await _agent(web) as agent:
            ask = await agent.post("/api/asks", json={"application_id": app_id, "question": "Notice period?"})
            assert ask.status_code == 201, ask.text
            resp = await _closed(agent, app_id, evidence="page_says_closed")
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        assert _events(await _detail(web, app_id), "withdrawn") == []
        assert len(await _open_asks(web, app_id)) == 1  # the owner's question (merged with the block's) stays open


@pytest.mark.asyncio
async def test_an_assistants_look_alike_withdrawn_is_not_undoable(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, 11)
        async with await _agent(web) as agent:
            block = await _closed(agent, app_id)  # no evidence: a real block, never a proven close
            fake = await agent.post(f"/api/applications/{app_id}/events", json={
                "event_type": "withdrawn", "detail": "posting closed",
                "payload": {"closed_by": "posting_closed", "blocked_event_id": block.json()["event_id"]}})
            assert fake.status_code == 201 and fake.json()["status"] == "withdrawn", fake.text
        assert (await web.post(f"/api/applications/{app_id}/auto/undo")).status_code == 409
        assert (await _detail(web, app_id))["status"] == "withdrawn"
