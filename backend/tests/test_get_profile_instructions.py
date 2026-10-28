"""Slice B3: get_profile puts `instructions`, `memory_notes`, `memory_check` first; `assistant_notes` stays.

Helpers are copied from test_instructions_cap.py, never imported (per-test schema isolation).
"""
from __future__ import annotations

import json

import pytest

NOTES = ["Never apply to agencies", "About you: Prefers to be called Ada", "Keep letters short"]


def _seed_profile(user_id: str, notes: list[str]) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    profile = UserProfile(
        cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
        preferences=UserPreferences(target_job_titles=["Data Engineer"], assistant_notes=list(notes)),
    )
    save_profile(profile, user_id, source_action="cv_upload")


@pytest.mark.asyncio
async def test_get_profile_leads_with_instructions(authenticated_async_context, fixture_user_id):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, NOTES)
        token = str((await client.post("/api/tokens", json={"name": "agent"})).json()["token"])

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            got = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)

    assert next(iter(got)) == "instructions"
    assert got["instructions"] == ["Never apply to agencies", "Keep letters short"]
    assert got["memory_notes"] == [{"group": "About you", "text": "Prefers to be called Ada"}]
    check = got["memory_check"]
    assert set(check) == {"verified", "to_verify", "missing"}
    assert all(isinstance(v, int) for v in check.values())
    assert got["assistant_notes"] == NOTES
