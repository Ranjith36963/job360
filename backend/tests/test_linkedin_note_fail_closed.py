"""A LinkedIn draft that is too long for a connection note is refused at save, unless the person is connected.

2026-11-02 (owner's screenshot): a 264-character note reached Needs you in red because the 200 limit was checked
only when the caller said ``draft_kind="connection_note"``, and the send gate skipped it for an unsaved LinkedIn
state. Now: no draft_kind = a note unless ``linkedin_state`` is connected; the gate judges an unknown state by the
note limit too; and a draft already stored over the limit is the ASSISTANT's job (blocker actor ``assistant``,
``ref.reason`` note_too_long; contact ``note_rewrite``) and never in the own-browser send queue.
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.api import dependencies as api_deps
from src.core import settings
from src.repositories import pgsync
from src.services.applications import blockers, spine
from src.services.profile.models import CVData, UserPreferences, UserProfile

_AD = {
    "title": "Platform Engineer", "company": "Razorpay", "location": "Bengaluru", "country": "IN", "remote": False,
    "apply_url": "https://razorpay.example/careers/1", "description": "Build payment rails.",
}
LONG = "x" * 264


@pytest.fixture(autouse=True)
def _line(monkeypatch):
    async def fifty(_user_id: str) -> int:
        return 50

    monkeypatch.setattr(spine, "user_score_line", fifty)


async def _contact(client: AsyncClient, state: str | None = None, n: int = 0) -> tuple[int, int]:
    ad = {**_AD, "title": f"Platform Engineer {n}", "apply_url": f"https://razorpay.example/careers/{n}"}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    made = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": f"Sudhir Reddy {n}", "linkedin_url": f"https://www.linkedin.com/in/sudhir{n}"},
    )
    assert made.status_code == 201, made.text
    cid = int(made.json()["contact"]["id"])
    if state is not None:
        patched = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": state})
        assert patched.status_code == 200, patched.text
    return app_id, cid


async def _save(client: AsyncClient, app_id: int, cid: int, text: str, **body: Any):
    return await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "contact_id": cid, "channel": "linkedin", "text": text, **body},
    )


async def _set_state(client: AsyncClient, cid: int, state: str) -> None:
    resp = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": state})
    assert resp.status_code == 200, resp.text


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python engineer.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["Engineer"])),
        user_id, source_action="cv_upload",
    )


def _sql(statement: str, params: tuple = ()) -> None:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute(statement, params)
        conn.commit()


@pytest.mark.asyncio
async def test_a_264_char_draft_with_no_draft_kind_to_an_unknown_state_contact_is_refused(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _contact(client)  # linkedin_state never saved = unknown
        refused = await _save(client, app_id, cid, LONG)
        assert refused.status_code == 422, refused.text
        assert "at most 200 characters" in refused.text and "this one is 264" in refused.text
        # Declaring "message" to someone not known to be connected does not dodge the limit either.
        assert (await _save(client, app_id, cid, LONG, draft_kind="message")).status_code == 422
        not_connected = await _contact(client, "not_connected", 1)
        assert (await _save(client, not_connected[0], not_connected[1], LONG)).status_code == 422
        assert (await _save(client, app_id, cid, "x" * 200)).status_code == 201  # exactly the limit still saves
        # Nothing was stored cut or half-saved: only the 200-character version exists.
        person = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
        assert [len(m["text"]) for m in person["outreach"]["messages"]] == [200]


@pytest.mark.asyncio
async def test_the_same_draft_to_a_connected_contact_is_accepted_up_to_the_message_limit(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _contact(client, "connected")
        assert (await _save(client, app_id, cid, LONG)).status_code == 201
        assert (await _save(client, app_id, cid, "x" * 400)).status_code == 201
        over = await _save(client, app_id, cid, "x" * 401)
        assert over.status_code == 422 and "at most 400 characters" in over.text
        # An InMail keeps its own 1,900 limit whatever the state.
        unknown_app, unknown = await _contact(client, None, 1)
        assert (await _save(client, unknown_app, unknown, "x" * 1900, draft_kind="inmail")).status_code == 201
        assert (await _save(client, unknown_app, unknown, "x" * 1901, draft_kind="inmail")).status_code == 422


@pytest.mark.asyncio
async def test_premium_allows_300_and_refuses_301(authenticated_async_context):
    async with authenticated_async_context() as client:
        _seed_profile(authenticated_async_context.fixture_user_id)
        patched = await client.patch(
            "/api/profile", json={"edits": [{"path": "preferences.linkedin_premium", "value": True}]}
        )
        assert patched.status_code == 200, patched.text
        app_id, cid = await _contact(client)
        assert (await _save(client, app_id, cid, "x" * 300)).status_code == 201
        over = await _save(client, app_id, cid, "x" * 301)
        assert over.status_code == 422 and "at most 300 characters" in over.text and "Premium" in over.text


@pytest.mark.asyncio
async def test_check_outreach_stops_an_over_limit_stored_draft_for_an_unknown_state(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cid = await _contact(client, "connected")
        mid = (await _save(client, app_id, cid, LONG)).json()["artifact_id"]  # fine while connected
        await _set_state(client, cid, "")  # the page showed nothing we saved: unknown
        got = (await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid})).json()
        assert (got["decision"], got["reason"]) == ("stop", "note_too_long")
        await _set_state(client, cid, "not_connected")
        got = (await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid})).json()
        assert (got["decision"], got["reason"]) == ("stop", "note_too_long")


@pytest.mark.asyncio
async def test_an_over_limit_stored_draft_is_the_assistants_job_and_not_in_the_send_queue(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        app_id, cid = await _contact(client, "connected")
        mid = (await _save(client, app_id, cid, LONG)).json()["artifact_id"]
        short_app, short_cid = await _contact(client, "not_connected", 1)
        short_mid = (await _save(client, short_app, short_cid, "Hi Sudhir, a short note")).json()["artifact_id"]
        for person, message in ((cid, mid), (short_cid, short_mid)):
            clicked = await client.post(
                f"/api/contacts/{person}/own-browser-send", json={"message_id": message, "decision": "approve"}
            )
            assert clicked.status_code == 200, clicked.text
        _sql("UPDATE applications SET status = 'applied', fit_score = 90 WHERE id IN (?, ?)", (app_id, short_app))
        queue = lambda: client.get("/api/own-browser/queue")  # noqa: E731
        assert sorted(t["contact_id"] for t in (await queue()).json()["tasks"]) == sorted([cid, short_cid])

        await _set_state(client, cid, "")  # connected no longer saved: the 264 characters are a connection note
        assert [t["contact_id"] for t in (await queue()).json()["tasks"]] == [short_cid]

        person = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
        assert person["note_rewrite"] == {"limit": 200, "chars": 264}
        assert (await client.get(f"/api/people?contact_id={short_cid}")).json()["person"]["note_rewrite"] is None

        got = await blockers.blockers_for(await api_deps.get_db(), uid, [app_id, short_app])
        rewrite = [b for b in got[app_id] if b["kind"] == "outreach_send"]
        assert [(b["actor"], b["ref"]) for b in rewrite] == [
            ("assistant", {"contact_ids": [cid], "reason": "note_too_long"})
        ]
        assert rewrite[0]["label"].startswith("rewrite ")
        # The short note, approved by the user, is still on the user's PC as before.
        assert [(b["actor"], b["label"]) for b in got[short_app] if b["kind"] == "outreach_send"] == [
            ("your_pc", "1 LinkedIn note")
        ]
