"""Slice C (2026-10-13) - weak spots from a live run: tool descriptions match the validator,
a small update_profile reply, "waiting for your yes" next steps, a plain proof label, the daily-check
hint, current_salary, and the apply recipe lines. Real doors, VALUES asserted (rule #21)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import kit as kit_service
from src.services.applications.next_step import kit_flags, next_step
from src.services.applications.proof import proof_label

_AD = {
    "title": "AI Engineer", "company": "QuantCo", "location": "London",
    "apply_url": "https://jobs.lever.co/quantco-/1", "description": "RAG, Python, LLM APIs.",
}
CV = "Ada Lovelace\nPython."


def _flat(text: str) -> str:
    return " ".join(text.split())


# -- #16 the tool description cannot drift from the validator --------------------------------------


def test_record_event_description_names_every_closed_payload_key():
    from src.api.mcp_server import build_server

    tools = {t.name: t for t in build_server()._tool_manager.list_tools()}
    text = _flat(tools["record_event"].description or "")
    for event, keys in kit_service.EVENT_PAYLOAD_KEYS.items():
        match = re.search(rf"\b{event}\b[^;]*?\{{([^}}]*)\}}", text) or re.search(
            rf"\b{event}\b[^{{]*\{{([^}}]*)\}}", text
        )
        assert match, f"{event} has no {{payload}} in the record_event description"
        for key in keys:
            assert key in match.group(1) or key in text, f"{event}: key {key!r} missing from the description"
    for key in kit_service.FORM_FILLED_REQUIRED | kit_service.FORM_FILLED_OPTIONAL:
        assert key in text, key
    for source in kit_service.ANSWER_SOURCES:
        assert source in text, f"answer source {source!r} missing from the description"


def test_description_payload_per_event_matches_exactly():
    """Stronger than 'the word appears somewhere': the braces after each event hold EXACTLY the validator's keys."""
    from src.api.mcp_server import build_server

    tools = {t.name: t for t in build_server()._tool_manager.list_tools()}
    text = _flat(tools["record_event"].description or "")
    for event, keys in kit_service.EVENT_PAYLOAD_KEYS.items():
        found = [
            {k.strip().split(":")[0] for k in m.split(",") if k.strip()}
            for m in re.findall(rf"\b{event}(?: and \w+)? \{{([^}}]*)\}}", text)
        ]
        assert any(f == set(keys) or f == {f"{k}" for k in keys} for f in found), (event, found)


def test_description_lists_every_closed_value_set():
    """Live 2026-10-13: 'browser_frozen' was refused and the description never listed the allowed blocked reasons.
    Every value the validators accept must appear in the record_event description."""
    from src.api.mcp_server import build_server
    from src.services.applications import blocked as blocked_service

    tools = {t.name: t for t in build_server()._tool_manager.list_tools()}
    text = _flat(tools["record_event"].description or "")
    assert len(text) <= 2000
    for reason in blocked_service.BLOCKED_REASONS:
        assert re.search(rf"(?<![\w]){reason}(?![\w])", text), f"blocked reason {reason!r} missing from the description"
    for resolution in blocked_service.UNBLOCK_RESOLUTIONS:
        assert resolution in text, f"unblocked resolution {resolution!r} missing from the description"
    for reason in kit_service.HOLD_RELEASE_REASONS:
        assert re.search(rf"(?<![\w]){reason}(?![\w])", text), f"hold_released reason {reason!r} missing"


# -- #19 waiting for your yes ----------------------------------------------------------------------


def test_next_step_waits_for_the_yes_then_says_submit():
    base: dict[str, Any] = dict(
        status="considering", has_fit=True, cv_versions=1, receipts=0, interview_at=None, has_lesson=False
    )
    assert next_step(**base)["code"] == "apply"
    filled = next_step(**base, form_filled=True)
    assert filled == {"code": "confirm_submit", "label": "Form filled — waiting for your yes to submit"}
    approved = next_step(**base, form_filled=True, submit_approved=True)
    assert approved == {"code": "submit_now", "label": "You said yes — submit, then it is recorded"}
    # once applied, none of it shows
    assert next_step(**{**base, "status": "applied", "receipts": 1}, form_filled=True)["code"] == "wait"


def test_kit_flags_newest_wins_and_decline_clears():
    assert kit_flags([]) == (False, False)
    assert kit_flags([(1, "form_filled")]) == (True, False)
    assert kit_flags([(1, "form_filled"), (2, "submit_approved")]) == (True, True)
    assert kit_flags([(1, "form_filled"), (2, "submit_declined")]) == (False, False)
    # a new fill after the yes needs a new yes
    assert kit_flags([(1, "form_filled"), (2, "submit_approved"), (3, "form_filled")]) == (True, False)
    assert kit_flags([(1, "form_filled"), (2, "submit_approved"), (3, "submit_declined")]) == (False, False)
    assert kit_flags([(1, "form_filled"), (2, "submit_declined"), (3, "submit_approved")]) == (True, True)


async def _ready(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    app_id = int(resp.json()["application_id"])
    await client.put(f"/api/applications/{app_id}/fit", json={"score": 68, "verdict": "good", "score_source": "full_ad"})
    await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV})
    return app_id


@pytest.mark.asyncio
async def test_route_and_list_show_the_waiting_states(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _ready(client)

        async def both() -> tuple[dict, dict]:
            detail = (await client.get(f"/api/applications/{app_id}")).json()["next_step"]
            rows = (await client.get("/api/applications")).json()["applications"]
            return detail, next(a for a in rows if a["id"] == app_id)["next_step"]

        assert (await both())[0]["code"] == "apply"
        r = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "form_filled", "payload": {"form_url": "https://jobs.lever.co/x", "fields_count": 2}},
        )
        assert r.status_code == 201, r.text
        detail, listed = await both()
        assert detail == listed and detail["code"] == "confirm_submit"
        art = (await client.get(f"/api/applications/{app_id}")).json()["artifacts"]
        cv = next(a for a in art if a["kind"] == "cv")
        r = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "submit_approved", "payload": {
                "artifact_id": cv["id"], "sha256": hashlib.sha256(CV.encode()).hexdigest(), "where": "web"}},
        )
        assert r.status_code == 201, r.text
        detail, listed = await both()
        assert detail == listed and detail["code"] == "submit_now"
        await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})
        assert (await both())[0]["code"] == "wait"


# -- #20 proof label -------------------------------------------------------------------------------


def test_proof_label_words():
    none = {"has_confirmation": False, "has_email": False, "has_page_text": False, "screenshots": 0}
    assert proof_label(none) == "claimed, unverified"
    assert proof_label({**none, "has_confirmation": True, "has_email": True}) == "confirmation id + email"
    assert proof_label({**none, "has_page_text": True, "screenshots": 1}) == "page text + screenshot, no application id"
    assert proof_label({**none, "screenshots": 2}) == "screenshot, no application id"


@pytest.mark.asyncio
async def test_receipt_and_list_carry_the_proof_label(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _ready(client)
        await client.post(
            f"/api/applications/{app_id}/receipt", json={"channel": "company_site", "confirmation": "REF-42"}
        )
        rows = (await client.get("/api/applications")).json()["applications"]
        assert next(a for a in rows if a["id"] == app_id)["proof"]["proof_label"] == "confirmation id"
        r = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "proof_text", "payload": {"text": "Thanks for applying", "page_host": "lever.co"}},
        )
        assert r.status_code == 201, r.text
        rows = (await client.get("/api/applications")).json()["applications"]
        assert next(a for a in rows if a["id"] == app_id)["proof"]["proof_label"] == "confirmation id + page text"


# -- #9 small update_profile reply, #21 hint --------------------------------------------------------


async def _mcp(authenticated_async_context, fixture_user_id):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    async with authenticated_async_context() as client:
        save_profile(
            UserProfile(cv_data=CVData(raw_text="Python.", name="Ada", location="London")),
            fixture_user_id, source_action="cv_upload",
        )
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


@pytest.mark.asyncio
async def test_mcp_update_profile_reply_is_small_and_record_application_hints(
    authenticated_async_context, fixture_user_id
):
    pytest.importorskip("mcp")
    from src.api.mcp_server import DAILY_CHECK_HINT, mcp_runtime

    mcp = await _mcp(authenticated_async_context, fixture_user_id)
    async with mcp_runtime():
        async with mcp as session:
            res = await session.call_tool(
                "update_profile",
                {"edits": [{"path": "user_info.logistics", "value": {
                    "current_salary": {"amount": 45000, "currency": "gbp", "period": "year"}}, "remember": True}]},
            )
            assert not res.is_error, res.content[0].text
            body = json.loads(res.content[0].text)
            assert set(body) == {"applied", "waiting", "not_saved", "normalized", "profile_version", "profile_edit_id"}
            assert body["not_saved"] == []
            assert "profile" not in body and "raw_text" not in res.content[0].text
            assert body["applied"][0]["path"] == "user_info.logistics"
            got = json.loads((await session.call_tool("get_profile", {})).content[0].text)
            assert got["fields"]["user_info.logistics"]["current_salary"] == {
                "amount": 45000, "currency": "GBP", "period": "year"}
            bad = await session.call_tool(
                "update_profile",
                {"edits": [{"path": "user_info.logistics", "value": {"current_salary": {"amount": 1, "foo": 2}}}]},
            )
            assert bad.is_error

            job = json.loads((await session.call_tool("bring_job", {
                "title": "AI Engineer", "company": "QuantCo", "location": "London",
                "apply_url": "https://jobs.lever.co/quantco-/9", "description": "RAG", "country": "GB",
                "remote": False})).content[0].text)
            job_id = job.get("job_id")
            rec = json.loads((await session.call_tool(
                "record_application", {"job_id": job_id, "channel": "company_site"})).content[0].text)
            assert rec["hint"] == DAILY_CHECK_HINT
            await session.call_tool("update_profile", {"edits": [{"path": "preferences.daily_check", "value": "ask"}]})
            job2 = json.loads((await session.call_tool("bring_job", {
                "title": "ML Eng", "company": "Other", "location": "London",
                "apply_url": "https://jobs.lever.co/other/2", "description": "RAG", "country": "GB",
                "remote": False})).content[0].text)
            rec2 = json.loads((await session.call_tool(
                "record_application", {"job_id": job2["job_id"], "channel": "company_site"})).content[0].text)
            assert "hint" not in rec2


# -- #7 current_salary validator --------------------------------------------------------------------


def test_current_salary_closed_keys():
    from src.services.profile.user_info import ProfileEditError, validate_user_info

    ok = validate_user_info(
        "user_info.logistics", {"current_salary": {"amount": 5000, "currency": "eur", "period": "Month"}}
    )
    assert ok["current_salary"] == {"amount": 5000, "currency": "EUR", "period": "month"}
    for bad in ({"amount": 5000}, {"amount": 5000, "currency": "EUR", "period": "week"},
                {"amount": -1, "currency": "EUR", "period": "year"},
                {"amount": 1, "currency": "EUR", "period": "year", "floor": 2}):
        with pytest.raises(ProfileEditError):
            validate_user_info("user_info.logistics", {"current_salary": bad})


# -- recipes ---------------------------------------------------------------------------------------


def test_apply_recipe_carries_the_live_run_lessons():
    from src.api.routes.recipes import load_recipe

    text = load_recipe("apply").text
    apply = _flat(text.lower())
    assert len(text.splitlines()) <= 70
    for words in (
        "cover letter only if the form has a field for it or the ad asks",
        "jobs.ashbyhq.com/api/non-user-graphql", "greenhouse boards-api", "lever", "never guess fields",
        "another autofill tool", "simplify", "re-read each value right before submit",
        "`found_on` stays where it was found", "`channel` is where it was sent",
        "kind `answers` labelled `video`", "never tick an attestation", "get_recipe('daily')",
    ):
        assert words in apply, words
    assert "current_salary" in load_recipe("rules").text
