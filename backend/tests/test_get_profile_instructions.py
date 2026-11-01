"""Slice B3: get_profile puts `instructions`, `memory_notes`, `memory_check` first; S4 dropped the `assistant_notes` copy.

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
            # An assistant's own guess counts as "to check", never as verified (the live overlay is read).
            saved = await mcp.call_tool("update_profile", {"edits": [
                {"path": "user_info.contact", "value": {"email": "ada@example.com"}, "source": "agent_inferred"}]})
            assert not saved.is_error, saved.content
            after = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)["memory_check"]

    assert next(iter(got)) == "instructions"
    assert got["instructions"] == ["Never apply to agencies", "Keep letters short"]
    assert got["memory_notes"] == [{"group": "About you", "text": "Prefers to be called Ada"}]
    check = got["memory_check"]
    assert set(check) == {"verified", "to_verify", "missing"}
    assert all(isinstance(v, int) for v in check.values())
    assert "assistant_notes" not in got  # S4: the deprecated copy is gone; `instructions` + `memory_notes` hold it
    assert after["verified"] == check["verified"] and after["to_verify"] == check["to_verify"] + 1
