"""Owner 2026-10-24 - LinkedIn state on a contact, and drafts that say what they are.

`linkedin_state` (connected | not_connected | pending | unknown) is an append-only contact edit, unset
= unknown (rule #29). An outreach draft may carry `draft_kind`; a connection note over LinkedIn's limit
(200, 300 with Premium) or an InMail body over 1,900 is refused. Job360 never sends. Helpers are copied,
never imported from another test module.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Applied AI Engineer",
    "company": "BJAK",
    "location": "Remote",
    "apply_url": "https://bjak.example/careers/1",
    "description": "Build agents for finance.",
}


async def _linked_contact(client: AsyncClient) -> tuple[int, int]:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    made = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Muzir Kassim"})
    assert made.status_code == 201, made.text
    return app_id, int(made.json()["contact"]["id"])


async def _save(client: AsyncClient, app_id: int, cid: int, **body: Any):
    return await client.post(
        f"/api/applications/{app_id}/artifacts", json={"kind": "outreach", "contact_id": cid, **body}
    )


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python engineer.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["AI Engineer"])),
        user_id, source_action="cv_upload",
    )


@pytest.mark.asyncio
async def test_linkedin_state_is_unknown_until_set_and_keeps_its_history(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _linked_contact(client)
        person = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
        assert person["linkedin_state"] is None

        resp = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "not_connected"})
        assert resp.status_code == 200, resp.text
        resp = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "connected"})
        body = resp.json()
        assert body["linkedin_state"] == "connected"
        assert [e["value"] for e in body["edit_history"]["linkedin_state"]][-2:] == ["not_connected", "connected"]

        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert detail["contacts"][0]["linkedin_state"] == "connected"

        cleared = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": ""})
        assert cleared.json()["linkedin_state"] is None
        bad = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": "friends"})
        assert bad.status_code == 422 and "linkedin_state must be one of" in bad.text


@pytest.mark.asyncio
async def test_a_connection_note_over_200_is_refused_and_300_with_premium(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _linked_contact(client)
        ok = await _save(client, app_id, cid, channel="linkedin", text="x" * 200, draft_kind="connection_note")
        assert ok.status_code == 201, ok.text
        long = await _save(client, app_id, cid, channel="linkedin", text="x" * 201, draft_kind="connection_note")
        assert long.status_code == 422 and "at most 200 characters" in long.text and "201" in long.text
        # Without a draft_kind the old behaviour stays: saved, with a warning.
        plain = await _save(client, app_id, cid, channel="linkedin", text="x" * 201)
        assert plain.status_code == 201, plain.text

        _seed_profile(fixture_user_id)
        patched = await client.patch(
            "/api/profile", json={"edits": [{"path": "preferences.linkedin_premium", "value": True}]}
        )
        assert patched.status_code == 200, patched.text
        premium = await _save(client, app_id, cid, channel="linkedin", text="x" * 300, draft_kind="connection_note")
        assert premium.status_code == 201, premium.text
        over = await _save(client, app_id, cid, channel="linkedin", text="x" * 301, draft_kind="connection_note")
        assert over.status_code == 422 and "at most 300 characters" in over.text


@pytest.mark.asyncio
async def test_inmail_cap_and_kind_channel_rules(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _linked_contact(client)
        ok = await _save(client, app_id, cid, channel="linkedin", text="x" * 1900, draft_kind="inmail")
        assert ok.status_code == 201, ok.text
        over = await _save(client, app_id, cid, channel="linkedin", text="x" * 1901, draft_kind="inmail")
        assert over.status_code == 422 and "1900" in over.text
        msg = await _save(client, app_id, cid, channel="linkedin", text="x" * 1000, draft_kind="message")
        assert msg.status_code == 201, msg.text
        wrong = await _save(client, app_id, cid, channel="linkedin", text="Hi", draft_kind="email")
        assert wrong.status_code == 422 and "channel email" in wrong.text
        bad = await _save(client, app_id, cid, channel="linkedin", text="Hi", draft_kind="tweet")
        assert bad.status_code == 422 and "draft_kind must be one of" in bad.text
        sent = await client.post(
            f"/api/contacts/{cid}/outreach", json={"entry": "sent", "channel": "linkedin", "draft_kind": "message"}
        )
        assert sent.status_code == 422 and "only for a drafted message" in sent.text


@pytest.mark.asyncio
async def test_a_client_key_stored_before_draft_kind_still_matches(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _linked_contact(client)
        first = await _save(client, app_id, cid, channel="email", text="Hello", client_key="k-1")
        again = await _save(client, app_id, cid, channel="email", text="Hello", client_key="k-1")
        assert again.status_code in (200, 201), again.text
        assert again.json()["artifact_id"] == first.json()["artifact_id"]


async def _mint_token(authenticated_async_context) -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


@pytest.mark.asyncio
async def test_mcp_update_contact_and_save_artifact_carry_the_new_fields(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with authenticated_async_context() as web:
        app_id, cid = await _linked_contact(web)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            done = await mcp.call_tool("update_contact", {"contact_id": cid, "linkedin_state": "pending"})
            assert not done.is_error, done
            assert json.loads(done.content[0].text)["linkedin_state"] == "pending"
            refused = await mcp.call_tool(
                "save_artifact",
                {"kind": "outreach", "application_id": app_id, "contact_id": cid, "channel": "linkedin",
                 "text": "x" * 250, "draft_kind": "connection_note"},
            )
            assert refused.is_error
            cold = await mcp.call_tool(
                "save_artifact",
                {"kind": "outreach", "contact_id": cid, "channel": "linkedin", "text": "x" * 250,
                 "draft_kind": "connection_note"},
            )
            assert cold.is_error
