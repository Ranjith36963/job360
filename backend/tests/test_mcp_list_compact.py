"""2026-11-01: list_applications compact=true - short rows for walking the queue.

A full queued list was 215,129 characters for 91 rows and the apply runs named its size as a reason to
stop. compact=true keeps what a run branches on and drops the rest. Helpers are local to this module.
"""
from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from src.services.profile.models import CVData, UserPreferences, UserProfile

COMPACT_ROW_KEYS = {
    "id", "job_title", "job_company", "job_url", "country", "status", "fit_score", "line_side",
    "queue_position", "next_step", "next", "waiting_on", "browser", "preflight",
}


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")


async def _queued_job(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote", "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200 and resp.json()["queued"] is True, resp.text
    return int(resp.json()["application_id"])


@pytest.mark.asyncio
async def test_compact_rows_keep_what_a_run_branches_on(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tool = m.build_server()._tool_manager.get_tool("list_applications")
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context() as client:
            _seed(fixture_user_id)
            first, second = await _queued_job(client, 1), await _queued_job(client, 2)
            full = await tool.fn(queued=True)
            short = await tool.fn(queued=True, compact=True)
    finally:
        m._current_user.reset(token)

    assert [r["id"] for r in short["applications"]] == [r["id"] for r in full["applications"]]
    assert {first, second} <= {r["id"] for r in short["applications"]}
    row = next(r for r in short["applications"] if r["id"] == first)
    assert set(row) <= COMPACT_ROW_KEYS | {"hold"}
    assert {"id", "job_url", "next_step", "waiting_on", "browser", "preflight"} <= set(row)
    assert "blockers" not in row and "proof" not in row and "fit_verdict" not in row
    full_row = next(r for r in full["applications"] if r["id"] == first)
    assert row["next_step"] == full_row["next_step"]["code"]
    assert row["browser"] == full_row["browser"]["kind"]
    assert row["waiting_on"] == [f"{b['kind']}:{b['actor']}" for b in full_row["blockers"]]
    assert row["preflight"]["has_cv"] == full_row["preflight"]["has_cv"]
    assert short["queue_summary"] == full["queue_summary"]
    assert len(json.dumps(short)) < len(json.dumps(full))


@pytest.mark.asyncio
async def test_without_compact_the_rows_are_unchanged(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tool = m.build_server()._tool_manager.get_tool("list_applications")
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context() as client:
            _seed(fixture_user_id)
            await _queued_job(client, 3)
            out = await tool.fn(queued=True)
    finally:
        m._current_user.reset(token)
    row = out["applications"][0]
    assert "blockers" in row and "proof" in row and isinstance(row["next_step"], dict)
