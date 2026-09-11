"""Slice 9 (#516) — "flag for next time": lessons written through
``record_event`` come back from three reads (docs/plans/2026-09-11-lessons/
spec.md R1): ``GET /applications/lessons`` (the web list), ``GET /profile``
``lessons`` and MCP ``get_profile`` ``lessons`` (what the agent sees before
tailoring the next CV). Value-presence (rule 21) throughout.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings

_AD_A = {
    "title": "Platform Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/9",
    "description": "Kubernetes, Go, Postgres.",
}
_AD_B = {
    "title": "Data Engineer",
    "company": "Contoso",
    "location": "Manchester",
    "apply_url": "https://contoso.example/jobs/12",
    "description": "Airflow, dbt, Postgres.",
}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Jane Doe\nPython")), user_id, source_action="cv_upload")


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _lesson(client: AsyncClient, app_id: int, detail: str, **extra: Any) -> int:
    resp = await client.post(
        f"/api/applications/{app_id}/events", json={"event_type": "lesson", "detail": detail, **extra}
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["event_id"])


@pytest.mark.asyncio
async def test_a_lesson_comes_back_from_all_three_reads(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        app_id = await _bring(client, _AD_A)
        event_id = await _lesson(client, app_id, "Always mention the Kubernetes cert.")
        listed = await client.get("/api/applications/lessons")
        profile = await client.get("/api/profile")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    assert body["lessons"] == [
        {
            "event_id": event_id,
            "application_id": app_id,
            "job_title": "Platform Engineer",
            "job_company": "Northwind",
            "detail": "Always mention the Kubernetes cert.",
            "occurred_at": body["lessons"][0]["occurred_at"],
            "recorded_by": body["lessons"][0]["recorded_by"],
        }
    ]
    assert profile.status_code == 200, profile.text
    assert profile.json()["lessons"][0]["detail"] == "Always mention the Kubernetes cert."
    assert profile.json()["lessons"][0]["application_id"] == app_id


@pytest.mark.asyncio
async def test_mcp_get_profile_hands_the_agent_the_lessons(authenticated_async_context, fixture_user_id):
    """The agent reads lessons BEFORE it writes the next CV — that is the
    whole loop. Calls the tool function the way the parity test does."""
    pytest.importorskip("mcp")
    from src.api import mcp_server

    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        app_id = await _bring(client, _AD_A)
        await _lesson(client, app_id, "Ask about sponsorship before the first call.")
        tool = mcp_server.build_server()._tool_manager.get_tool("get_profile")
        assert tool is not None
        mcp_server._current_user.set(mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com"))
        try:
            result = await tool.fn()
        finally:
            mcp_server._current_user.set(None)
    assert [row["detail"] for row in result["lessons"]] == ["Ask about sponsorship before the first call."]
    assert result["lessons"][0]["job_company"] == "Northwind"


@pytest.mark.asyncio
async def test_newest_first_across_applications_and_a_superseded_lesson_is_gone(
    authenticated_async_context,
):
    async with authenticated_async_context() as client:
        a = await _bring(client, _AD_A)
        b = await _bring(client, _AD_B)
        first = await _lesson(client, a, "old wording", occurred_at="2026-09-01T10:00:00+00:00")
        await _lesson(client, b, "second", occurred_at="2026-09-05T10:00:00+00:00")
        await _lesson(client, a, "corrected wording", occurred_at="2026-09-01T10:00:00+00:00", corrects_event_id=first)
        listed = await client.get("/api/applications/lessons")
    body = listed.json()
    assert [row["detail"] for row in body["lessons"]] == ["second", "corrected wording"]
    assert body["total"] == 2
    assert [row["job_company"] for row in body["lessons"]] == ["Contoso", "Northwind"]


@pytest.mark.asyncio
async def test_profile_carries_only_the_last_n_but_the_list_counts_all(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    monkeypatch.setattr(settings, "PROFILE_LESSONS_MAX", 2)
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        app_id = await _bring(client, _AD_A)
        for i in range(4):
            await _lesson(client, app_id, f"lesson {i}", occurred_at=f"2026-09-0{i + 1}T10:00:00+00:00")
        profile = await client.get("/api/profile")
        listed = await client.get("/api/applications/lessons", params={"limit": 3})
    assert [row["detail"] for row in profile.json()["lessons"]] == ["lesson 3", "lesson 2"]
    assert listed.json()["total"] == 4
    assert len(listed.json()["lessons"]) == 3


@pytest.mark.asyncio
async def test_limit_over_the_cap_is_422_and_offset_pages(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, _AD_A)
        await _lesson(client, app_id, "one", occurred_at="2026-09-01T10:00:00+00:00")
        await _lesson(client, app_id, "two", occurred_at="2026-09-02T10:00:00+00:00")
        too_big = await client.get("/api/applications/lessons", params={"limit": settings.LESSONS_PAGE_MAX + 1})
        page_two = await client.get("/api/applications/lessons", params={"limit": 1, "offset": 1})
        junk = await client.get("/api/applications/lessons", params={"limit": "many"})
    assert too_big.status_code == 422
    assert junk.status_code == 422
    assert [row["detail"] for row in page_two.json()["lessons"]] == ["one"]


@pytest.mark.asyncio
async def test_another_user_sees_no_lessons(authenticated_async_context):
    from tests.test_application_spine import _second_user_session_cookie, _session_client

    async with authenticated_async_context() as client:
        app_id = await _bring(client, _AD_A)
        await _lesson(client, app_id, "private lesson")
        cookie = await _second_user_session_cookie("lessons-second@example.com")
        async with _session_client(cookie) as other:
            listed = await other.get("/api/applications/lessons")
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"lessons": [], "total": 0}


@pytest.mark.asyncio
async def test_no_lessons_reads_as_empty_on_the_profile(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        profile = await client.get("/api/profile")
    assert profile.status_code == 200, profile.text
    assert profile.json()["lessons"] == []


def test_no_new_mcp_tool_for_lessons():
    """Rule M2/M5 — lessons ride `get_profile`; there is no separate tool and
    no gate to re-apply."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "api" / "mcp_server.py").read_text(encoding="utf-8")
    assert "def list_lessons" not in src
    assert "def get_lessons" not in src
