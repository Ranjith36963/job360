"""`next_step` (2026-09-20): the one line at the top of an application, read
off the stored record — never a judgement of the job. Pinned as a table over
the pure function, plus one round-trip through the route and the MCP tool.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.services.applications.next_step import next_step

_AD = {
    "title": "AI Engineer",
    "company": "QuantCo",
    "location": "London",
    "apply_url": "https://jobs.lever.co/quantco-/1",
    "description": "RAG, Python, LLM APIs.",
}


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        (dict(status="considering", has_fit=False, cv_versions=0, receipts=0, interview_at=None, has_lesson=False), "judge_fit"),
        (dict(status="considering", has_fit=True, cv_versions=0, receipts=0, interview_at=None, has_lesson=False), "write_cv"),
        (dict(status="considering", has_fit=True, cv_versions=2, receipts=0, interview_at=None, has_lesson=False), "apply"),
        (dict(status="applied", has_fit=True, cv_versions=2, receipts=0, interview_at=None, has_lesson=False), "record_receipt"),
        (dict(status="applied", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "wait"),
        (dict(status="replied", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "respond"),
        (dict(status="interview_requested", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "schedule"),
        (dict(status="interview_requested", has_fit=True, cv_versions=2, receipts=1, interview_at="2026-09-22T09:00:00+00:00", has_lesson=False), "interview"),
        (dict(status="interview_done", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "await_outcome"),
        (dict(status="offer", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "decide"),
        (dict(status="rejected", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "lesson"),
        (dict(status="rejected", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=True), "closed"),
        (dict(status="ghosted", has_fit=False, cv_versions=0, receipts=0, interview_at=None, has_lesson=False), "lesson"),
    ],
)
def test_every_state_maps_to_one_next_step(kwargs, code):
    out = next_step(**kwargs)
    assert out["code"] == code
    assert out["label"]


def test_the_interview_label_carries_the_date():
    out = next_step(status="interview_scheduled", has_fit=True, cv_versions=1, receipts=1,
                    interview_at="2026-09-22T09:00:00+00:00", has_lesson=False)
    assert "2026-09-22T09:00:00+00:00" in out["label"]


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


@pytest.mark.asyncio
async def test_the_route_moves_the_next_step_as_the_record_grows(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        r0 = await client.get(f"/api/applications/{app_id}")
        assert r0.json()["next_step"]["code"] == "judge_fit"
        await client.put(f"/api/applications/{app_id}/fit", json={"score": 68, "verdict": "good"})
        r1 = await client.get(f"/api/applications/{app_id}")
        assert r1.json()["next_step"]["code"] == "write_cv"
        await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "cv v1"})
        r2 = await client.get(f"/api/applications/{app_id}")
        assert r2.json()["next_step"]["code"] == "apply"
        await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company site"})
        r3 = await client.get(f"/api/applications/{app_id}")
        assert r3.json()["next_step"]["code"] == "wait"
        await client.post(f"/api/applications/{app_id}/events", json={"event_type": "rejected"})
        r4 = await client.get(f"/api/applications/{app_id}")
        assert r4.json()["next_step"]["code"] == "lesson"
        await client.post(f"/api/applications/{app_id}/events", json={"event_type": "lesson", "detail": "ask about base first"})
        r5 = await client.get(f"/api/applications/{app_id}")
    assert r5.json()["next_step"] == {"code": "closed", "label": "Closed"}


@pytest.mark.asyncio
async def test_the_agent_sees_the_same_next_step(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        tool = mcp_server.build_server()._tool_manager.get_tool("get_application")
        assert tool is not None
        mcp_server._current_user.set(mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com"))
        try:
            result = await tool.fn(app_id)
        finally:
            mcp_server._current_user.set(None)
    assert result["next_step"]["code"] == "judge_fit"
