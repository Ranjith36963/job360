"""Owner rule 2026-10-26: "You will not ask me a few blank questions. I want you to ask ALL the blank questions."

Application 138 (Alaan, Rippling) reached submit with an optional B2B SaaS radio, Pronouns, Cover letter and
the SMS consent left blank, none of them in the ask. check_submit now answers ``ask`` / ``blank_unasked`` for
every ``fields_skipped`` field that no fill answer, no ask on the job and no saved answer of the user covers;
"Leave blank" is a valid answer and is saved as the user's word for that employer. Helpers are copied, never
imported from another test module.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

FORM = "https://ats.rippling.example/alaan/jobs/1/apply"
FORM2 = "https://ats.rippling.example/alaan/jobs/2/apply"
CONSENT = "Tick Yes or No to receive text messages from AlaanPay"
B2B = "Any prior experience in B2B SaaS companies?"


def _ad(title: str, url: str) -> dict[str, Any]:
    return {"title": title, "company": "Alaan", "location": "Dubai", "country": "AE", "remote": False,
            "apply_url": url, "description": "Build finance automation. Python."}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python engineer.", name="Ada Lovelace", location="Dubai, AE"),
            preferences=UserPreferences(target_job_titles=["Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


async def _bring(client: AsyncClient, title: str = "Software Engineer", url: str = FORM) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(title, url))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ok(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text


async def _fill(client: AsyncClient, app_id: int, skipped: list[str], url: str = FORM) -> None:
    """A complete, swept form: 4 typed fields plus the named blanks (the counts match check_form)."""
    total = 4 + len(skipped)
    await _ok(client, app_id, "form_checked", {"questions_count": total, "answered": 4, "missing": len(skipped),
                                                "missing_required": 0})
    await _ok(client, app_id, "form_filled", {"form_url": url, "fields_count": 4, "fields_skipped": skipped})
    await _ok(client, app_id, "form_swept", {"fields_checked": 4, "fixed": 0})


async def _verdict(client: AsyncClient, app_id: int, url: str = FORM) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": url})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(key: str, question: str, answer: str = "No") -> dict[str, Any]:
    return {"key": key, "question": question, "answer": answer, "source": "guess",
            "choices": ["Yes", "No", "Leave blank"]}


async def _ask(client: AsyncClient, app_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    resp = await client.post("/api/asks", json={"question": "Form answers for Alaan", "application_id": app_id,
                                                "kind": "question", "questions": rows})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_a_skipped_optional_field_nobody_asked_about_is_blank_unasked(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, [B2B, "Pronouns"])
        v = await _verdict(client, app_id)
        assert (v["decision"], v["reason"]) == ("ask", "blank_unasked")
        assert B2B in v["detail"] and "Pronouns" in v["detail"] and "Leave blank" in v["detail"]


@pytest.mark.asyncio
async def test_a_skipped_consent_is_never_silent(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, [CONSENT])
        v = await _verdict(client, app_id)
        assert (v["decision"], v["reason"]) == ("ask", "blank_unasked") and CONSENT in v["detail"]


@pytest.mark.asyncio
async def test_a_field_held_by_an_open_or_answered_ask_passes(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, [B2B, "Pronouns", CONSENT])
        # One row by wording, one by key, the consent by its form wording: only those count.
        ask = await _ask(client, app_id, [_row("b2b_saas", B2B), _row("pronouns", "Your pronouns", "Leave blank")])
        v = await _verdict(client, app_id)
        assert v["reason"] == "blank_unasked" and CONSENT in v["detail"] and "Pronouns" not in v["detail"]
        ask = await _ask(client, app_id, [_row("consent", CONSENT)])  # merges into the open ask
        assert (await _verdict(client, app_id))["reason"] != "blank_unasked"
        answers = {q["key"]: "No" for q in ask["questions"]}
        resp = await client.post(f"/api/asks/{ask['id']}/answer", json={"answers": answers})
        assert resp.status_code == 200, resp.text
        assert (await _verdict(client, app_id))["reason"] != "blank_unasked"


@pytest.mark.asyncio
async def test_a_withdrawn_ask_does_not_cover_a_blank(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, ["Pronouns"])
        ask = await _ask(client, app_id, [_row("pronouns", "Pronouns")])
        resp = await client.post(f"/api/asks/{ask['id']}/withdraw", json={"reason": "old"})
        assert resp.status_code == 200, resp.text
        assert (await _verdict(client, app_id))["reason"] == "blank_unasked"


@pytest.mark.asyncio
async def test_leave_blank_is_saved_for_the_employer_and_reused_by_the_next_form(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, ["Cover letter", CONSENT])
        # "Leave blank" is a valid answer even where the row's choices do not list it.
        ask = await _ask(client, app_id, [
            {"key": "cover_letter", "question": "Cover letter", "answer": "Leave blank", "source": "guess"},
            {"key": "consent", "question": CONSENT, "answer": "No", "source": "guess", "choices": ["Yes", "No"]},
        ])
        resp = await client.post(f"/api/asks/{ask['id']}/answer",
                                 json={"answers": {"cover_letter": "leave  BLANK", "consent": "Leave blank"}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["answers"] == {"cover_letter": "Leave blank", "consent": "Leave blank"}
        saved = load_profile(fixture_user_id).user_info.answers
        rows = {r["question"]: r for r in saved}
        assert rows["Cover letter"]["answer"] == "Leave blank" and rows["Cover letter"]["approved"] is True
        assert rows[CONSENT]["company"] == "Alaan" and rows[CONSENT]["answer"] == "Leave blank"

        # A second Alaan form with the same blanks and NO ask: the user's saved "Leave blank" covers both.
        other = await _bring(client, "Data Engineer", FORM2)
        await _fill(client, other, ["Cover letter", CONSENT, "Pronouns"], url=FORM2)
        v = await _verdict(client, other, FORM2)
        assert v["reason"] == "blank_unasked" and "Pronouns" in v["detail"]
        assert CONSENT not in v["detail"] and "Cover letter" not in v["detail"]


@pytest.mark.asyncio
async def test_leave_blank_for_one_employer_keeps_the_answer_every_other_employer_uses(
    authenticated_async_context, fixture_user_id
):
    """reviewer-bugs P1 on #1273: user_info keeps ONE answer per wording, so a "Leave blank" for one employer
    must never replace the user's unscoped answer. The answered ask still covers this job's blank."""
    from src.services.applications import ready
    from src.services.profile import edits as profile_edits
    from src.services.profile import user_info
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        profile_edits.record_edits(
            fixture_user_id, "test", [(user_info.USER_INFO_ANSWERS_PATH,
                                       [{"question": "Pronouns", "answer": "she/her", "approved": True}])],
            enforce_rate_limit=False, source=profile_edits.SOURCE_USER_SAID,
        )
        app_id = await _bring(client)
        await _fill(client, app_id, ["Pronouns"])
        ask = await _ask(client, app_id, [
            {"key": "pronouns", "question": "Pronouns", "answer": "she/her", "source": "memory"},
        ])
        resp = await client.post(f"/api/asks/{ask['id']}/answer", json={"answers": {"pronouns": "Leave blank"}})
        assert resp.status_code == 200, resp.text
        saved = load_profile(fixture_user_id)
        acme = ready._approved_record(saved, "Pronouns", ready._company_key("Acme"))
        assert acme is not None and acme["answer"] == "she/her" and not acme.get("company")
        assert (await _verdict(client, app_id))["reason"] != "blank_unasked"


@pytest.mark.asyncio
async def test_a_fill_without_blanks_never_asks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, [])
        assert (await _verdict(client, app_id))["reason"] != "blank_unasked"


def test_the_decision_names_the_first_blanks_then_counts_the_rest():
    from src.services.profile import assistant_settings as rules

    assert "blank_unasked" in rules.REASONS
    blanks = tuple(f"Q{i}" for i in range(11))
    d = rules.blank_unasked_decision(blanks)
    assert (d.decision, d.reason) == ("ask", "blank_unasked")
    assert "11 field(s)" in d.detail and "Q7" in d.detail and "Q8" not in d.detail and "(and 3 more)" in d.detail


# ── MCP parity: ask_user / answer_ask are the same route functions ─────────────────────────


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
async def test_mcp_check_submit_ask_user_and_answer_ask_close_the_blank(
    authenticated_async_context, fixture_user_id
):
    from src.api.mcp_server import mcp_runtime
    from src.services.profile.storage import load_profile

    token = await _mint_token(authenticated_async_context)
    async with authenticated_async_context() as web:
        _seed_profile(fixture_user_id)
        app_id = await _bring(web)
        await _fill(web, app_id, ["Pronouns"])
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            first = await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": FORM, "session": "s"})
            assert not first.is_error, first
            assert json.loads(first.content[0].text)["reason"] == "blank_unasked"
            made = await mcp.call_tool("ask_user", {
                "question": "Form answers for Alaan", "application_id": app_id, "kind": "question",
                "questions": [_row("pronouns", "Pronouns", "Leave blank")],
            })
            assert not made.is_error, made
            ask = json.loads(made.content[0].text)
            done = await mcp.call_tool("answer_ask", {
                "ask_id": ask["id"], "answers": {"pronouns": "Leave blank"}, "user_words": "leave it blank",
            })
            assert not done.is_error, done
            again = await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": FORM, "session": "s"})
            assert json.loads(again.content[0].text)["reason"] != "blank_unasked"
    rows = [r for r in load_profile(fixture_user_id).user_info.answers if r["question"] == "Pronouns"]
    assert rows and rows[-1]["answer"] == "Leave blank" and rows[-1]["company"] == "Alaan"
