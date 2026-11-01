"""Instructions cap (slice B2, owner decision 2026-10-28): at most 20 instruction lines
(`preferences.assistant_notes` entries WITHOUT a Memory step prefix). The cap only bites when
a write makes the count grow, so a stored list already over it can still be edited down or sideways.

Helpers are copied from test_preference_memory.py, never imported (per-test schema isolation).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

NOTES_PATH = "preferences.assistant_notes"


def _lines(n: int) -> list[str]:
    return [f"Rule number {i}" for i in range(n)]


def _seed_profile(user_id: str, notes: list[str] | None = None) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    profile = UserProfile(
        cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
        preferences=UserPreferences(target_job_titles=["Data Engineer"], assistant_notes=list(notes or [])),
    )
    save_profile(profile, user_id, source_action="cv_upload")


async def _patch(client: AsyncClient, value: list[str]):
    return await client.patch("/api/profile", json={"edits": [{"path": NOTES_PATH, "value": value}]})


async def _save_prefs(client: AsyncClient, prefs: dict[str, Any]):
    return await client.post("/api/profile/preferences", data={"preferences": json.dumps(prefs)})


async def _stored(client: AsyncClient) -> list[str]:
    return list((await client.get("/api/profile")).json()["preferences"]["assistant_notes"])


@pytest.mark.asyncio
async def test_21st_instruction_is_refused_on_the_web(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, _lines(20))
        resp = await _patch(client, [*_lines(20), "One too many"])
        assert resp.status_code == 422, resp.text
        assert "at most 20 instructions" in resp.text
        resp = await _save_prefs(client, {"assistant_notes": [*_lines(20), "One too many"]})
        assert resp.status_code == 422, resp.text
        assert "at most 20 instructions" in resp.text
        assert await _stored(client) == _lines(20)
        # Exactly 20 is fine.
        assert (await _patch(client, _lines(19) + ["Swapped"])).status_code == 200


@pytest.mark.asyncio
async def test_stored_25_lines_still_edit_while_the_count_does_not_grow(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, _lines(25))
        keep = [*_lines(24), "Reworded last rule"]
        assert (await _patch(client, keep)).status_code == 200
        assert await _stored(client) == keep
        lower = keep[:22]
        assert (await _save_prefs(client, {"assistant_notes": lower})).status_code == 200
        assert await _stored(client) == lower
        # Growing it again is refused.
        assert (await _patch(client, [*lower, "More"])).status_code == 422


@pytest.mark.asyncio
async def test_step_prefixed_lines_are_not_counted(authenticated_async_context, fixture_user_id):
    facts = [f"About you: fact {i}" for i in range(10)] + ["Equality: another fact"]
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, _lines(20))
        both = [*_lines(20), *facts]
        assert (await _patch(client, both)).status_code == 200
        assert await _stored(client) == both
        assert (await _save_prefs(client, {"assistant_notes": [*both, "Logistics: yet another"]})).status_code == 200
        # A 21st line WITHOUT a prefix is still refused with the facts present.
        assert (await _patch(client, [*both, "Logistics: yet another", "Plain 21st"])).status_code == 422


@pytest.mark.asyncio
async def test_mcp_update_profile_gets_the_same_refusal(authenticated_async_context, fixture_user_id):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, _lines(20))
        token = str((await client.post("/api/tokens", json={"name": "agent"})).json()["token"])

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            for edit in (
                {"path": NOTES_PATH, "value": [*_lines(20), "One too many"]},
                {"path": NOTES_PATH, "op": "append", "value": "One too many"},
            ):
                result = await mcp.call_tool("update_profile", {"edits": [edit]})
                assert result.is_error, result.content[0].text
                assert "at most 20 instructions" in result.content[0].text
            after = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)
            assert after["settings"]["waiting"] == []
            assert after["instructions"] == _lines(20) and "assistant_notes" not in after


def test_validate_edit_counts_only_unprefixed_lines():
    from src.services.profile import edits

    over = [*_lines(20), "Plain 21st"]
    with pytest.raises(edits.ProfileEditError) as exc:
        edits.validate_edit(NOTES_PATH, over, stored_notes=_lines(20))
    assert exc.value.status_code == 422
    assert edits.validate_edit(NOTES_PATH, over, stored_notes=_lines(25)) == over
    assert edits.validate_edit(NOTES_PATH, [*_lines(20), "Job targets: x"], stored_notes=_lines(20))
