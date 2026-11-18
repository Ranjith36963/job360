"""Right to work comes from Memory (owner decision 2026-11-17: "use memory for right to work").

A form question on right to work or sponsorship is answered from the user's own Memory row for THAT job's
country (its region's, else ANY's) and filled with no per-form confirmation. check_form and check_submit's
gate read "the user's own Memory" through ONE helper (``kit.memory_key_is_users``), so they can never disagree
again. Still flagged ``needs_confirmation``: an assistant-drafted row, a block an assistant saved last, a job
country with no row, and any sensitive question with no answer (a criminal record).

Production 2026-11-17, application 1031 (GB): the autonomy shadow said right to work was fine, yet the run asked
"please confirm UK work authorisation and sponsorship" before filling. Real HTTP doors, VALUE asserts (rule #21).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

AUTH_Q = "Are you legally authorized to work in the United Kingdom?"
SPONSOR_Q = "Will you require sponsorship to work in the UK?"
CRIMINAL_Q = "Do you have any unspent criminal convictions?"
FORM = "https://careers.example/apply/1031"
GB = {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}
EUROPE = {"country": "EUROPE", "work_authorization": "citizen", "needs_sponsorship": False}
RTW_KEYS = {AUTH_Q: "right_to_work.GB.work_authorization", SPONSOR_Q: "right_to_work.GB.needs_sponsorship"}


def _rtw(*rows: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"path": "user_info.right_to_work", "value": {"countries": list(rows)}, **extra}


async def _patch(client: AsyncClient, *edits: dict[str, Any]) -> dict[str, Any]:
    resp = await client.patch("/api/profile", json={"edits": list(edits)})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _bring(client: AsyncClient, country: str) -> int:
    ad = {"title": "Data Engineer", "company": f"Acme {country}", "location": "", "country": country,
          "apply_url": f"https://acme-{country.lower()}.example/careers/1", "description": "Build the pipelines."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _check_form(client: AsyncClient, app_id: int, *questions: str) -> dict[str, Any]:
    body = {"form_url": FORM, "questions": [{"question": q} for q in questions]}
    resp = await client.post(f"/api/applications/{app_id}/kit/form-check", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _gate_says_sensitive(client: AsyncClient, user_id: str, app_id: int, answered: list[dict[str, Any]]) -> bool:
    """Record the fill exactly as check_form answered it, then read check_submit's ``sensitive_unconfirmed``."""
    typed = [{"question": a["question"], "answer": a["value"], "source": "memory", "key": a["key"]} for a in answered]
    payload = {"form_url": FORM, "fields_count": len(typed), "answers": typed}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
    assert resp.status_code == 201, resp.text
    from src.api import dependencies as api_deps
    from src.services.applications import kit, spine

    db = await api_deps.get_db()
    row = await spine.get_owned_application(db, user_id, app_id)
    return (await kit.gate_facts(db, user_id, row, email_verified=True)).sensitive_unconfirmed


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _token(client: AsyncClient) -> str:
    return str((await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"])


def _flagged(out: dict[str, Any]) -> dict[str, bool]:
    return {r["question"]: bool(r.get("needs_confirmation")) for r in out["answered"] + out["missing"]}


@pytest.mark.asyncio
async def test_the_users_own_gb_row_answers_right_to_work_with_no_confirmation(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as client:
        await _patch(client, _rtw(GB))
        app_id = await _bring(client, "GB")
        out = await _check_form(client, app_id, AUTH_Q, SPONSOR_Q)
        assert [(a["key"], a["value"], a["source"]) for a in out["answered"]] == [
            (RTW_KEYS[AUTH_Q], "Yes", "memory"), (RTW_KEYS[SPONSOR_Q], "No", "memory")]
        assert out["needs_confirmation"] == [] and _flagged(out) == {AUTH_Q: False, SPONSOR_Q: False}
        assert "ask_user" not in out["next"]
        assert await _gate_says_sensitive(client, fixture_user_id, app_id, out["answered"]) is False


@pytest.mark.asyncio
async def test_the_users_own_europe_row_answers_a_gb_job_whoever_saved_another_block_last(
    authenticated_async_context, fixture_user_id,
):
    """Application 1031's shape: the gate read the user's own region row as theirs, check_form flagged it, because
    the kit took the confirmation of the LAST block it read (equality, saved by the assistant) for the region
    fallback. Both now read the same helper."""
    async with authenticated_async_context() as client:
        await _patch(client, _rtw(EUROPE))
        app_id = await _bring(client, "GB")
        async with _agent(await _token(client)) as agent:
            await _patch(agent, {"path": "user_info.equality", "value": {"gender": "Man"}, "remember": True})
        out = await _check_form(client, app_id, AUTH_Q, SPONSOR_Q)
        assert [(a["key"], a["value"]) for a in out["answered"]] == [(RTW_KEYS[AUTH_Q], "Yes"),
                                                                     (RTW_KEYS[SPONSOR_Q], "No")]
        assert out["needs_confirmation"] == [], out["answered"]
        assert all("confirmed" not in a for a in out["answered"]), "the user's own region row is no inference"
        kit_rtw = (await client.get(f"/api/applications/{app_id}/kit")).json()["answers"]["right_to_work"]
        assert {i["key"]: i["state"] for i in kit_rtw if i["key"].startswith("right_to_work.GB.")} == {
            RTW_KEYS[AUTH_Q]: "confirmed", RTW_KEYS[SPONSOR_Q]: "confirmed"}
        assert await _gate_says_sensitive(client, fixture_user_id, app_id, out["answered"]) is False


@pytest.mark.asyncio
async def test_an_assistant_drafted_row_still_needs_confirmation(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        anywhere = {**GB, "country": "ANY"}
        await _patch(client, _rtw(anywhere))
        app_id = await _bring(client, "GB")
        async with _agent(await _token(client)) as agent:
            out = await _patch(agent, _rtw(anywhere, GB))  # a GB row copied from ANY: an assistant's draft
            assert out["not_saved"] == [], "a drafts-only write is saved as a draft"
        from src.services.profile.storage import load_profile

        rows = {r["country"]: r for r in load_profile(fixture_user_id).user_info.right_to_work["countries"]}
        assert rows["GB"]["set_by"] == "assistant"
        out = await _check_form(client, app_id, AUTH_Q, SPONSOR_Q)
        assert _flagged(out) == {AUTH_Q: True, SPONSOR_Q: True}
        assert await _gate_says_sensitive(client, fixture_user_id, app_id, out["answered"]) is True


@pytest.mark.asyncio
async def test_a_block_an_agent_saved_last_still_needs_confirmation(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, "GB")
        async with _agent(await _token(client)) as agent:
            await _patch(agent, _rtw(GB, remember=True, source="user_said"))
        out = await _check_form(client, app_id, AUTH_Q, SPONSOR_Q)
        assert [a["value"] for a in out["answered"]] == ["Yes", "No"]
        assert _flagged(out) == {AUTH_Q: True, SPONSOR_Q: True}
        assert await _gate_says_sensitive(client, fixture_user_id, app_id, out["answered"]) is True


@pytest.mark.asyncio
async def test_a_job_country_with_no_row_is_still_flagged(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _patch(client, _rtw({"country": "US", "work_authorization": "citizen", "needs_sponsorship": False}))
        app_id = await _bring(client, "GB")
        out = await _check_form(client, app_id, AUTH_Q, SPONSOR_Q)
        assert out["answered"] == []
        assert _flagged(out) == {AUTH_Q: True, SPONSOR_Q: True}
        assert "ask_user" in out["next"]


@pytest.mark.asyncio
async def test_a_criminal_record_question_with_no_answer_is_still_flagged(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _patch(client, _rtw(GB))
        app_id = await _bring(client, "GB")
        out = await _check_form(client, app_id, AUTH_Q, CRIMINAL_Q)
        assert _flagged(out) == {AUTH_Q: False, CRIMINAL_Q: True}
        assert out["needs_confirmation"] == [CRIMINAL_Q]


# ── The two checks share one reading: check_form's flag == the gate's ``sensitive`` flag, case by case ──

T = "2026-11-01T00:00:00+00:00"
_WEB = {"path": "user_info.right_to_work", "set_by": "web", "set_at": T, "source": ""}
_AGENT = {"path": "user_info.right_to_work", "set_by": "agent:claude", "set_at": T, "source": "user_said",
          "remember_consent_at": T}
_AGENT_EQUALITY = {"path": "user_info.equality", "set_by": "agent:claude", "set_at": T, "source": "",
                   "remember_consent_at": T}
_DRAFT = {"country": "AE", "needs_sponsorship": True, "set_by": "assistant"}


@pytest.mark.parametrize(("rows", "overlay", "flagged"), [
    ([GB], [_WEB], False),
    ([EUROPE], [_WEB, _AGENT_EQUALITY], False),
    ([{"country": "ANY", **{k: v for k, v in GB.items() if k != "country"}}], [_WEB, _AGENT_EQUALITY], False),
    ([GB, _DRAFT], [_AGENT], False),  # marks in the block: the unmarked GB row is the user's own
    ([GB], [_AGENT], True),  # no marks: the assistant saved the block last
    ([{**GB, "set_by": "assistant"}], [_WEB], True),  # drafted
    ([GB], [], True),  # nothing saved in Memory's history: not the user's word
], ids=["web-gb", "web-europe", "web-any", "rows-marked", "agent-block", "drafted", "no-history"])
def test_check_form_and_the_gate_agree_on_every_memory_row(rows, overlay, flagged):
    from src.services.applications import kit, ready
    from src.services.profile.models import UserInfo, UserProfile

    profile = UserProfile(user_info=UserInfo(right_to_work={"countries": rows}, equality={"gender": "Man"}))
    answers, _missing = kit.compute_answers(profile, overlay, "GB", None)
    users = kit.users_memory_keys(profile, overlay, answers)
    out = kit.form_check(answers, [{"question": AUTH_Q}, {"question": SPONSOR_Q}], records=[], country="GB",
                         users=users)
    assert [a["key"] for a in out["answered"]] == [RTW_KEYS[AUTH_Q], RTW_KEYS[SPONSOR_Q]]
    assert _flagged(out) == {AUTH_Q: flagged, SPONSOR_Q: flagged}
    have = {i["key"]: i for block in answers.values() for i in block}
    saved = {str(r["path"]): (r.get("set_by"), r.get("set_at")) for r in overlay}
    typed = [{"question": a["question"], "answer": a["value"], "source": "memory", "key": a["key"]}
             for a in out["answered"]]
    _rows, flags = ready._answer_rows(typed, have, saved, profile, None, country="GB")
    assert {f["key"] for f in flags if f["code"] == "sensitive"} == (set(RTW_KEYS.values()) if flagged else set())
