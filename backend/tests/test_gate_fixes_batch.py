"""Six submit-gate and kit fixes from a real application run (ElevenLabs, Ashby, 2026-10-22/23).

The owner decided each one: the newest form check wins, Job360 counts the blank optional
questions itself, an old ``upload_failed`` block clears once a later upload worked, clearing a
block can say why, an assistant can close its OWN out-of-date asks (with a reason), and the kit's
``missing`` leaves salary to check_form. Helpers are defined here, never imported (per-test
schema isolation).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

FORM = "https://jobs.ashby.example/elevenlabs/apply"
_AD = {
    "title": "Forward Deployed Engineer", "company": "ElevenLabs", "location": "London", "country": "GB",
    "remote": False, "apply_url": FORM, "description": "Ship voice AI to customers. Python, TypeScript.",
}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python engineer.", name="Ada Lovelace", location="London, UK"),
            preferences=UserPreferences(target_job_titles=["Forward Deployed Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _post(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _ok(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await _post(client, app_id, event_type, payload)
    assert resp.status_code == 201, resp.text


async def _verdict(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. The newest form check wins ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_old_unpaged_check_does_not_outvote_a_newer_paged_one(
    authenticated_async_context, fixture_user_id, form_check_gate
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        # 20 Oct: a whole-form check (no page) with 5 required questions unanswered
        await _ok(client, app_id, "form_checked", {"questions_count": 12, "answered": 7, "missing": 5,
                                                    "missing_required": 5})
        # later: the one-page form checked again, with page 1, nothing required missing
        await _ok(client, app_id, "form_checked", {"questions_count": 12, "answered": 12, "missing": 0,
                                                    "missing_required": 0, "page": 1, "pages_total": 1})
        await _ok(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 12})
        await _ok(client, app_id, "form_swept", {"fields_checked": 12, "fixed": 0, "page": 1, "pages_total": 1})
        assert (await _verdict(client, app_id))["reason"] != "required_unanswered"


@pytest.mark.asyncio
async def test_a_newer_unpaged_check_is_the_only_one_that_counts(
    authenticated_async_context, fixture_user_id, form_check_gate
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _ok(client, app_id, "form_checked", {"questions_count": 4, "answered": 3, "missing": 1,
                                                    "missing_required": 1, "page": 1, "pages_total": 1})
        await _ok(client, app_id, "form_checked", {"questions_count": 4, "answered": 4, "missing": 0,
                                                    "missing_required": 0})
        await _ok(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 4})
        await _ok(client, app_id, "form_swept", {"fields_checked": 4, "fixed": 0, "page": 1, "pages_total": 1})
        assert (await _verdict(client, app_id))["reason"] != "required_unanswered"


def test_current_checks_keeps_only_what_the_newest_check_allows():
    from src.services.applications import kit

    def ev(i: int, **p: Any) -> dict[str, Any]:
        return {"id": i, "event_type": "form_checked", "payload": p}

    paged_newest = [ev(3, page=2), ev(2), ev(1, page=1)]
    assert [e["id"] for e in kit._current_checks(paged_newest)] == [3, 1]
    flat_newest = [ev(3), ev(2, page=1), ev(1)]
    assert [e["id"] for e in kit._current_checks(flat_newest)] == [3]


@pytest.mark.asyncio
async def test_an_old_unpaged_short_sweep_does_not_outvote_newer_paged_sweeps(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _ok(client, app_id, "form_checked", {"questions_count": 12, "answered": 12, "missing": 0})
        await _ok(client, app_id, "form_swept", {"fields_checked": 6, "fixed": 0})  # old and short
        await _ok(client, app_id, "form_checked", {"questions_count": 12, "answered": 12, "missing": 0,
                                                    "page": 1, "pages_total": 1})
        await _ok(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 12})
        await _ok(client, app_id, "form_swept", {"fields_checked": 12, "fixed": 0, "page": 1, "pages_total": 1})
        db = await api_deps.get_db()
        assert await kit._sweep_shortfalls(db, fixture_user_id, app_id) == (False, False, False)


# ── 2. Job360 counts the blank optional questions itself ────────────────────


@pytest.mark.asyncio
async def test_fields_skipped_count_toward_the_sweep_and_the_stop_says_the_numbers(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _ok(client, app_id, "form_checked", {"questions_count": 12, "answered": 12, "missing": 0})
        await _ok(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 6,
                                                   "fields_skipped": ["Pronouns", "Portfolio", "Twitter"]})
        await _ok(client, app_id, "form_swept", {"fields_checked": 6, "fixed": 0})
        short = await _verdict(client, app_id)
        assert (short["decision"], short["reason"]) == ("stop", "form_count_mismatch")
        assert "You checked 9 of 12 questions; count blank optional ones too" in short["detail"]
        skipped = ["Pronouns", "Portfolio", "Twitter", "GitHub", "Website", "Cover note"]
        await _ok(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 6, "fields_skipped": skipped})
        await _ok(client, app_id, "form_swept", {"fields_checked": 6, "fixed": 0})
        assert (await _verdict(client, app_id))["reason"] != "form_count_mismatch"


# ── 3. An old upload_failed block clears itself after a later upload ────────


async def _fill_and_sweep(client: AsyncClient, app_id: int) -> None:
    """The form rules come before the blocked rule: fill and sweep, so the verdict reaches it."""
    await _ok(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 4})
    await _ok(client, app_id, "form_swept", {"fields_checked": 4, "fixed": 0})


async def _record_upload(user_id: str, app_id: int) -> None:
    from src.api import dependencies as api_deps
    from src.services.applications import uploads

    db = await api_deps.get_db()
    event_id = await uploads.record(
        db, user_id=user_id, application_id=app_id, kind="cv", artifact={"id": 1, "version_no": 1},
        data=b"%PDF-1.4 cv", fmt="pdf", renderer_version="test", host="jobs.ashby.example", recorded_by="web",
    )
    assert event_id is not None


@pytest.mark.asyncio
async def test_an_upload_failed_block_clears_once_a_later_upload_worked(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill_and_sweep(client, app_id)
        await _ok(client, app_id, "blocked", {"reason": "upload_failed", "step": "CV upload"})
        stuck = await _verdict(client, app_id)
        assert (stuck["decision"], stuck["reason"]) == ("ask", "blocked")
        assert (await client.get(f"/api/applications/{app_id}/controls")).json()["blocked"]["reason"] == "upload_failed"
        await _record_upload(fixture_user_id, app_id)
        assert (await _verdict(client, app_id))["reason"] != "blocked"
        assert (await client.get(f"/api/applications/{app_id}/controls")).json()["blocked"] is None


@pytest.mark.asyncio
async def test_a_later_upload_does_not_clear_any_other_block(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill_and_sweep(client, app_id)
        await _ok(client, app_id, "blocked", {"reason": "captcha", "step": "submit"})
        await _record_upload(fixture_user_id, app_id)
        assert (await _verdict(client, app_id))["reason"] == "blocked"
        # an upload BEFORE an upload_failed block does not clear it either
        await _ok(client, app_id, "unblocked", {"resolution": "user_did_it"})
        await _ok(client, app_id, "blocked", {"reason": "upload_failed"})
        assert (await _verdict(client, app_id))["reason"] == "blocked"


# ── 4. Clearing a block can say why ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_unblocked_takes_an_optional_note_shown_on_the_timeline(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _ok(client, app_id, "blocked", {"reason": "upload_failed"})
        too_long = await _post(client, app_id, "unblocked", {"resolution": "retried", "note": "x" * 201})
        assert too_long.status_code == 422 and "200 characters" in too_long.text
        unknown = await _post(client, app_id, "unblocked", {"resolution": "retried", "why": "x"})
        assert unknown.status_code == 422 and 'optional "note"' in unknown.json()["detail"]
        await _ok(client, app_id, "unblocked", {"resolution": "retried", "note": "The CV upload worked on 22 Oct."})
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        cleared = next(e for e in events if e["event_type"] == "unblocked")
        assert cleared["payload"]["note"] == "The CV upload worked on 22 Oct."
        assert cleared["detail"] == "The CV upload worked on 22 Oct."


# ── 5. An assistant closes its OWN out-of-date asks, with a reason ──────────


async def _mint_token(authenticated_async_context, name: str) -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": name})
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]


def _bearer(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


@pytest.mark.asyncio
async def test_an_assistant_withdraws_only_its_own_ask_and_must_say_why(authenticated_async_context, fixture_user_id):
    mine, other = await _mint_token(authenticated_async_context, "agent"), await _mint_token(
        authenticated_async_context, "other agent"
    )
    async with authenticated_async_context() as web:
        _seed_profile(fixture_user_id)
        app_id = await _bring(web)
        users_ask = (await web.post("/api/asks", json={"question": "Mine?", "application_id": app_id})).json()
    async with _bearer(mine) as agent, _bearer(other) as stranger:
        own = (await agent.post("/api/asks", json={"question": "Notice period?", "application_id": app_id})).json()
        no_reason = await agent.post(f"/api/asks/{own['id']}/withdraw")
        assert no_reason.status_code == 422 and "why" in no_reason.text
        assert (await stranger.post(f"/api/asks/{own['id']}/withdraw", json={"reason": "old"})).status_code == 403
        assert (await agent.post(f"/api/asks/{users_ask['id']}/withdraw", json={"reason": "old"})).status_code == 403
        long = await agent.post(f"/api/asks/{own['id']}/withdraw", json={"reason": "x" * 201})
        assert long.status_code == 422
        gone = await agent.post(f"/api/asks/{own['id']}/withdraw", json={"reason": "The form no longer asks it."})
        assert gone.status_code == 200, gone.text
        body = gone.json()
        assert body["status"] == "withdrawn" and body["withdrawn_reason"] == "The form no longer asks it."
        assert body["withdrawn_by"] == own["asked_by"]
    async with authenticated_async_context() as web:
        events = (await web.get(f"/api/applications/{app_id}")).json()["events"]
        taken = next(e for e in events if e["event_type"] == "ask_withdrawn")
        assert taken["payload"]["reason"] == "The form no longer asks it."
        assert "The form no longer asks it." in taken["detail"]
        # the user still closes any ask, with no reason needed
        assert (await web.post(f"/api/asks/{users_ask['id']}/withdraw")).status_code == 200


@pytest.mark.asyncio
async def test_mcp_answer_ask_withdraw_closes_the_assistants_own_ask(authenticated_async_context, fixture_user_id):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context, "agent")
    async with authenticated_async_context() as web:
        users_ask = (await web.post("/api/asks", json={"question": "Yours?"})).json()
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime(), Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
        made = json.loads((await mcp.call_tool("ask_user", {"question": "Stale?"})).content[0].text)
        both = await mcp.call_tool("answer_ask", {"ask_id": made["id"], "answer": "x", "withdraw": "old"})
        assert both.is_error and "not both" in both.content[0].text
        refused = await mcp.call_tool("answer_ask", {"ask_id": users_ask["id"], "withdraw": "old"})
        assert refused.is_error and "403" in refused.content[0].text
        done = await mcp.call_tool("answer_ask", {"ask_id": made["id"], "withdraw": "Answered in the form already."})
        assert not done.is_error, done
        out = json.loads(done.content[0].text)
        assert out["status"] == "withdrawn" and out["withdrawn_reason"] == "Answered in the form already."


# ── 6. The kit's missing leaves salary to check_form ────────────────────────


@pytest.mark.asyncio
async def test_kit_missing_has_no_salary_entries_but_keeps_the_rest(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        resp = await client.get(f"/api/applications/{app_id}/kit")
        assert resp.status_code == 200, resp.text
        keys = {m["key"] for m in resp.json()["missing"]}
        assert not any(k == "salary" or k.startswith("salary.") or "salary_by_country" in k for k in keys), keys
        assert {"right_to_work.GB.work_authorization", "contact.email"} <= keys


def test_kit_missing_drops_only_salary():
    from src.services.applications import kit

    missing = [
        {"key": "salary.GB", "why": "x"}, {"key": "preferences.salary_by_country", "why": "x"},
        {"key": "salary", "why": "x"}, {"key": "user_info.equality", "why": "x"}, {"key": "contact.phone", "why": "x"},
    ]
    assert [m["key"] for m in kit.kit_missing(missing)] == ["user_info.equality", "contact.phone"]
