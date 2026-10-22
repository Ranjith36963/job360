"""Gap roadmap slice 8: the user's web Send yes is bound to what the card showed.

``POST /send/approve`` stores a server-side ``fingerprint`` (cover letter, newest fill, form page,
the assistant's newest profile edit) on ``submit_approved``; check_submit counts the yes only while
all of it still matches and it is younger than ``SUBMIT_APPROVAL_TTL_MINUTES``, else asks
``approval_stale`` naming the part. Answers the user changed (``type_first``) never cancel it
(owner decision 2026-10-19). Real HTTP + MCP doors, VALUE asserts (rule #21). Helpers are copied,
never imported from another test module.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
FORM = "https://careers.northwind.example/apply/7"
NOTICE = {"question": "Preferred start date", "answer": "1 month", "source": "profile"}
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines. Python, dbt.",
}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


def _sql(statement: str, params: tuple = ()) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> int:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["event_id"])


async def _fill(client: AsyncClient, app_id: int, answers: list[dict[str, Any]], url: str = FORM) -> int:
    """The assistant fills the form and sweeps it (the gate stops on either missing)."""
    fill = await _event(client, app_id, "form_filled", {"form_url": url, "fields_count": len(answers), "answers": answers})
    await _event(client, app_id, "form_swept", {"fields_checked": len(answers), "fixed": 0, "answers": answers})
    return fill


async def _check(client: AsyncClient, app_id: int, url: str = FORM) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": url})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _approved_app(client: AsyncClient, user_id: str) -> tuple[int, int]:
    """A brought job with a CV, a filled + swept form and the user's web Send yes: user_approved."""
    _seed_profile(user_id)
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    fit = {"score": 80, "verdict": "ok", "score_source": "full_ad"}
    assert (await client.put(f"/api/applications/{app_id}/fit", json=fit)).status_code == 200
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
    assert resp.status_code == 201, resp.text
    fill = await _fill(client, app_id, [NOTICE])
    assert (await _check(client, app_id))["reason"] == "submit_mode_confirm", "no yes yet: the normal ask"
    yes = await client.post(f"/api/applications/{app_id}/send/approve", params={"form_filled_event_id": fill})
    assert yes.status_code == 201, yes.text
    return app_id, fill


async def _token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _stale(verdict: dict[str, Any], part: str) -> None:
    assert (verdict["decision"], verdict["reason"]) == ("ask", "approval_stale"), verdict
    assert f"({part}:" in verdict["detail"] and "submit mode is confirm" in verdict["detail"].lower(), verdict


@pytest.mark.asyncio
async def test_the_yes_stores_the_fingerprint_and_holds_while_nothing_changes(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id, fill = await _approved_app(client, fixture_user_id)
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        stored = [e for e in detail["events"] if e["event_type"] == "submit_approved"][-1]["payload"]
        assert stored["where"] == "web" and stored["version"] == 1, "the old keys stay for old readers"
        assert stored["fingerprint"] == {
            "cover_letter_id": None, "cover_letter_sha256": None, "form_filled_event_id": fill,
            "form_url": FORM, "profile_edit_id": 0,
        }
        assert (await _check(client, app_id))["reason"] == "user_approved"
        assert (await _check(client, app_id))["decision"] == "submit", "reading twice changes nothing"


@pytest.mark.asyncio
async def test_a_new_cv_or_letter_voids_the_yes(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, _ = await _approved_app(client, fixture_user_id)
        letter = {"kind": "cover_letter", "text": "Dear Northwind, I would love to join."}
        assert (await client.post(f"/api/applications/{app_id}/artifacts", json=letter)).status_code == 201
        _stale(await _check(client, app_id), "cover_letter")
        controls = (await client.get(f"/api/applications/{app_id}/controls")).json()
        assert controls["cv"]["approved"] is None, "the Send button shows again"
        # A fresh yes covers the letter; then a new CV version voids it the old way (no match at all).
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        assert (await _check(client, app_id))["reason"] == "user_approved"
        resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT + "\nNew."})
        assert resp.status_code == 201, resp.text
        await _fill(client, app_id, [NOTICE])
        verdict = await _check(client, app_id)
        assert (verdict["decision"], verdict["reason"]) == ("ask", "submit_mode_confirm"), verdict


@pytest.mark.asyncio
async def test_a_refill_with_other_answers_voids_the_yes(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, _ = await _approved_app(client, fixture_user_id)
        await _fill(client, app_id, [NOTICE])  # the same answers typed again: the yes holds
        assert (await _check(client, app_id))["reason"] == "user_approved"
        await _fill(client, app_id, [{**NOTICE, "answer": "3 months"}])
        _stale(await _check(client, app_id), "form_fill")


@pytest.mark.asyncio
async def test_another_form_page_voids_the_yes(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, _ = await _approved_app(client, fixture_user_id)
        later_step = FORM + "/review"  # a later step of the same form is the same page
        await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0, "form_url": later_step})
        assert (await _check(client, app_id, later_step))["reason"] == "user_approved"
        other = "https://careers.northwind.example/apply/8"
        await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0, "form_url": other})
        _stale(await _check(client, app_id, other), "form_page")


@pytest.mark.asyncio
async def test_an_assistant_profile_edit_voids_the_yes_and_the_users_own_does_not(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id, _ = await _approved_app(client, fixture_user_id)
        mine = {"path": "preferences.target_job_titles", "value": ["Data Engineer", "Analytics Engineer"]}
        assert (await client.patch("/api/profile", json={"edits": [mine]})).status_code == 200
        assert (await _check(client, app_id))["reason"] == "user_approved", "the user's own web edit"
        token = await _token(client)
    async with _bearer_client(token) as agent:
        theirs = {"path": "preferences.target_job_titles", "value": ["Data Engineer"]}
        assert (await agent.patch("/api/profile", json={"edits": [theirs]})).status_code == 200, "an assistant edit"
        _stale(await _check(agent, app_id), "profile")


@pytest.mark.asyncio
async def test_the_yes_expires_and_so_does_a_legacy_yes_without_a_fingerprint(
    authenticated_async_context, fixture_user_id
):
    def backdate(app_id: int, minutes: int) -> None:
        old = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        _sql("UPDATE application_events SET recorded_at = ? WHERE application_id = ? AND event_type = 'submit_approved'",
             (old, app_id))

    ttl = settings.SUBMIT_APPROVAL_TTL_MINUTES
    assert ttl == 120
    async with authenticated_async_context() as client:
        app_id, _ = await _approved_app(client, fixture_user_id)
        backdate(app_id, ttl - 5)
        assert (await _check(client, app_id))["reason"] == "user_approved", "inside the window"
        backdate(app_id, ttl + 1)
        verdict = await _check(client, app_id)
        _stale(verdict, "expired")
        assert f"older than {ttl} minutes" in verdict["detail"]
        token = await _token(client)
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        cv = [e for e in detail["events"] if e["event_type"] == "submit_approved"][-1]["payload"]
    # A chat yes (and every yes stored before slice 8) carries no fingerprint: CV + time only.
    async with _bearer_client(token) as agent:
        legacy = {"artifact_id": cv["artifact_id"], "sha256": cv["sha256"], "where": "chat"}
        await _event(agent, app_id, "submit_approved", legacy)
        await _fill(agent, app_id, [{**NOTICE, "answer": "3 months"}])
        assert (await _check(agent, app_id))["reason"] == "user_approved", "not bound to the fill"
        backdate(app_id, ttl + 1)
        _stale(await _check(agent, app_id), "expired")


@pytest.mark.asyncio
async def test_answers_the_user_changed_after_the_yes_never_void_it(authenticated_async_context, fixture_user_id):
    """Owner decision 2026-10-19: the user's own edited answer does not block the yes. The type_first
    lock holds the submit until the form holds the user's word; the assistant's re-fill that types
    exactly that word keeps the yes, while any other re-fill does not."""
    async with authenticated_async_context() as client:
        app_id, _ = await _approved_app(client, fixture_user_id)
        word = {"question": "Preferred start date", "answer": "2 months", "approved": True}
        edit = {"path": "user_info.answers", "value": [word]}
        assert (await client.patch("/api/profile", json={"edits": [edit]})).status_code == 200, "a web edit"
        locked = await _check(client, app_id)
        assert (locked["decision"], locked["reason"]) == ("stop", "type_first_pending"), locked
        assert locked["type_first"] == [{"question": "Preferred start date", "answer": "2 months"}]
        await _fill(client, app_id, [{**NOTICE, "answer": "2 months", "source": "approved"}])
        verdict = await _check(client, app_id)
        assert (verdict["decision"], verdict["reason"], verdict["type_first"]) == ("submit", "user_approved", [])
        # Negative control: a re-fill with an answer the user never saw is a form they have not seen.
        salary = {"question": "Preferred team size", "answer": "5-8 people", "source": "profile"}
        await _fill(client, app_id, [{**NOTICE, "answer": "2 months", "source": "approved"}, salary])
        _stale(await _check(client, app_id), "form_fill")


@pytest.mark.asyncio
async def test_mcp_check_submit_answers_exactly_as_the_route(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id, _ = await _approved_app(client, fixture_user_id)
        letter = {"kind": "cover_letter", "text": "Dear Northwind."}
        assert (await client.post(f"/api/applications/{app_id}/artifacts", json=letter)).status_code == 201
        route = await _check(client, app_id)
        token = await _token(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": FORM})
    assert not result.is_error, result.content[0].text
    tool = json.loads(result.content[0].text)
    _stale(tool, "cover_letter")
    assert {k: tool[k] for k in ("decision", "reason", "detail")} == {k: route[k] for k in ("decision", "reason", "detail")}
