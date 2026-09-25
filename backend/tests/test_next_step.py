"""`next_step` (2026-09-20): the one line at the top of an application, read
off the stored record — never a judgement of the job. Pinned as a table over
the pure function, plus one round-trip through the route and the MCP tool.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from src.services.applications.next_step import next_step

_BEFORE_INTERVIEW = datetime(2026, 9, 1, tzinfo=timezone.utc)
_AFTER_INTERVIEW = datetime(2026, 9, 23, tzinfo=timezone.utc)

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
        (dict(status="interview_requested", has_fit=True, cv_versions=2, receipts=1, interview_at="2026-09-22T09:00:00+00:00", has_lesson=False, now=_BEFORE_INTERVIEW), "interview"),
        (dict(status="interview_requested", has_fit=True, cv_versions=2, receipts=1, interview_at="2026-09-22T09:00:00+00:00", has_lesson=False, now=_AFTER_INTERVIEW), "record_outcome"),
        (dict(status="interview_done", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "await_outcome"),
        (dict(status="offer", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "decide"),
        (dict(status="rejected", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False), "lesson"),
        (dict(status="rejected", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=True), "closed"),
        (dict(status="ghosted", has_fit=False, cv_versions=0, receipts=0, interview_at=None, has_lesson=False), "lesson"),
        # Owner decision, 2026-09-25 — a due follow-up wins over every other
        # code on an OPEN application, whatever stage it's otherwise at.
        (dict(status="applied", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False, follow_up_due=True), "follow_up"),
        (dict(status="considering", has_fit=False, cv_versions=0, receipts=0, interview_at=None, has_lesson=False, follow_up_due=True), "follow_up"),
        # …but never overrides a CLOSED status's own code — "closed" stays closed.
        (dict(status="rejected", has_fit=True, cv_versions=2, receipts=1, interview_at=None, has_lesson=False, follow_up_due=True), "lesson"),
    ],
)
def test_every_state_maps_to_one_next_step(kwargs, code):
    out = next_step(**kwargs)
    assert out["code"] == code
    assert out["label"]


def test_the_future_interview_label_has_no_raw_timestamp():
    out = next_step(status="interview_scheduled", has_fit=True, cv_versions=1, receipts=1,
                    interview_at="2026-09-22T09:00:00+00:00", has_lesson=False, now=_BEFORE_INTERVIEW)
    assert out["code"] == "interview"
    assert "2026-09-22T09:00:00+00:00" not in out["label"]


def test_a_z_suffixed_interview_at_parses():
    out = next_step(status="interview_scheduled", has_fit=True, cv_versions=1, receipts=1,
                    interview_at="2026-09-22T09:00:00Z", has_lesson=False, now=_BEFORE_INTERVIEW)
    assert out["code"] == "interview"


def test_an_unparseable_interview_at_is_treated_as_upcoming():
    out = next_step(status="interview_scheduled", has_fit=True, cv_versions=1, receipts=1,
                    interview_at="not a date", has_lesson=False, now=_AFTER_INTERVIEW)
    assert out["code"] == "interview"


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
async def test_list_next_step_matches_detail_next_step(authenticated_async_context):
    """2026-09-24 — the applications list card's next_step must be the exact
    same value `get_application`'s next_step reads, at every stage the
    record moves through (fit, CV, receipt, a scheduled interview, a
    rejection, a lesson). The list computes it from one batched events
    query (spine.list_applications), never per-row — this proves the batch
    agrees with the per-application read, not just that it runs."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)

        async def _list_next_step() -> dict:
            resp = await client.get("/api/applications")
            assert resp.status_code == 200, resp.text
            row = next(a for a in resp.json()["applications"] if a["id"] == app_id)
            return row["next_step"]

        async def _detail_next_step() -> dict:
            resp = await client.get(f"/api/applications/{app_id}")
            assert resp.status_code == 200, resp.text
            return resp.json()["next_step"]

        async def _assert_matches() -> None:
            list_step = await _list_next_step()
            detail_step = await _detail_next_step()
            assert list_step == detail_step

        await _assert_matches()  # judge_fit
        await client.put(f"/api/applications/{app_id}/fit", json={"score": 68, "verdict": "good"})
        await _assert_matches()  # write_cv
        await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "cv v1"})
        await _assert_matches()  # apply
        await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company site"})
        await _assert_matches()  # wait
        await client.post(f"/api/applications/{app_id}/events", json={"event_type": "interview_requested"})
        await _assert_matches()  # schedule (no date yet)
        await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "interview_scheduled", "scheduled_at": "2027-06-01T09:00:00+00:00"},
        )
        await _assert_matches()  # interview (date in the future)
        await client.post(f"/api/applications/{app_id}/events", json={"event_type": "rejected"})
        await _assert_matches()  # lesson
        await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "lesson", "detail": "ask about base first"},
        )
        await _assert_matches()  # closed


@pytest.mark.asyncio
async def test_list_summary_matches_detail_for_location_fit_receipt_interview(authenticated_async_context):
    """Owner decision, 2026-09-25 — the wide applications-list row reads
    job_location, fit_score/fit_verdict, last_receipt_at and interview_at
    straight off the list summary (never a per-row detail fetch). Each of
    these is now filled by a batched query in spine.list_applications
    (job_location/fit_* on the main SELECT, last_receipt_at off one grouped
    query alongside the existing interview_at batch) — this proves the list
    values agree with `get_application`'s own read of the same application,
    not just that the fields exist (rule #21 — real values, not schema
    presence)."""
    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json=_AD)
        assert resp.status_code == 200, resp.text
        app_id = int(resp.json()["application_id"])

        async def _list_row() -> dict:
            r = await client.get("/api/applications")
            assert r.status_code == 200, r.text
            return next(a for a in r.json()["applications"] if a["id"] == app_id)

        async def _detail() -> dict:
            r = await client.get(f"/api/applications/{app_id}")
            assert r.status_code == 200, r.text
            return r.json()

        # Before any fit/receipt/interview: location is the bring-time
        # snapshot, everything else stays silent (None/"" — rule #29).
        row = await _list_row()
        detail = await _detail()
        assert row["job_location"] == detail["job"]["job_location"] == _AD["location"]
        assert row["fit_score"] is None
        assert row["fit_verdict"] == ""
        assert detail["fit"] is None
        assert row["last_receipt_at"] is None
        assert row["interview_at"] is None
        assert detail["interview_at"] is None

        # Fit judged — the list score/verdict must match the detail's fit
        # object exactly.
        await client.put(f"/api/applications/{app_id}/fit", json={"score": 68, "verdict": "Strong match"})
        row = await _list_row()
        detail = await _detail()
        assert row["fit_score"] == detail["fit"]["score"] == 68
        assert row["fit_verdict"] == detail["fit"]["verdict"] == "Strong match"

        # A receipt — the list's last_receipt_at must match the detail's
        # (only) receipt's sent_at.
        await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company site"})
        row = await _list_row()
        detail = await _detail()
        assert row["last_receipt_at"] == detail["receipts"][0]["sent_at"]
        assert row["last_receipt_at"] is not None

        # A scheduled interview — the list's interview_at must match the
        # detail's top-level interview_at.
        await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "interview_scheduled", "scheduled_at": "2027-06-01T09:00:00+00:00"},
        )
        row = await _list_row()
        detail = await _detail()
        assert row["interview_at"] == detail["interview_at"] == "2027-06-01T09:00:00+00:00"


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
