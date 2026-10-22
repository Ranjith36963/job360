"""Owner rule "I draft, you approve": a required essay (``written``) is typed only after the user approved it.

The gate (``ready.has_unapproved_written`` -> ``kit.gate_facts`` -> ``may_submit``) and the card's ``unapproved``
flag read the same rows. Real HTTP doors and real rows for the integration cases (rule #21).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings

FORM = "https://careers.example/apply/9"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
_AD = {
    "title": "Data Engineer", "company": "Certa", "location": "Remote", "country": "IN", "remote": True,
    "apply_url": "https://certa.example/careers/9", "description": "Build the pipelines.",
}
Q = "Why do you want to work at Certa?"
ESSAY = "I like pipelines, and Certa builds them for compliance teams."
WRITTEN = {"question": Q, "answer": ESSAY, "source": "written", "key": "why_company"}


def _eff(**kw: Any) -> rules.EffectiveSettings:
    kw.setdefault("apply_min_score", 50)
    kw.setdefault("above_line", "auto")
    kw.setdefault("below_line", "auto")
    return rules.effective(AssistantSettings(**kw))


def _facts(**kw: Any) -> rules.SubmitFacts:
    base: dict[str, Any] = {
        "status": "considering", "has_receipt": False, "submit_override": None, "cv_seen": True,
        "form_filled": True, "form_swept": True, "fit_score": 80, "form_host": "careers.northwind.example",
    }
    base.update(kw)
    return rules.SubmitFacts(**base)


def _gate(cfg: rules.EffectiveSettings, **kw: Any) -> rules.SubmitDecision:
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=1, applied_since_grant=0)
    return rules.may_submit(cfg, _facts(**kw), "https://careers.northwind.example/apply/7", counts, now=NOW)


def test_answer_unapproved_is_a_known_reason():
    assert "answer_unapproved" in rules.REASONS
    assert rules.SubmitFacts(
        status="considering", has_receipt=False, submit_override=None, cv_seen=True
    ).written_unapproved is False


def test_an_unapproved_written_answer_asks_in_auto_grant_and_ask_mode_and_after_submit_approved():
    until = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc).isoformat()
    cfgs = (
        _eff(submit_mode="auto_when_sure"),
        _eff(submit_mode="confirm", auto_grant_count=3, auto_grant_until=until),
        _eff(submit_mode="confirm"),
    )
    for cfg in cfgs:
        d = _gate(cfg, written_unapproved=True)
        assert (d.decision, d.reason) == ("ask", "answer_unapproved")
        assert "ask_user" in d.detail
        # the user's yes to the CV is not their OK of the essay
        approved = _gate(cfg, written_unapproved=True, approved=True)
        assert (approved.decision, approved.reason) == ("ask", "answer_unapproved")
    assert _gate(cfgs[0], written_unapproved=False).decision == "submit"


# -- integration --------------------------------------------------------------


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    cv = {"kind": "cv", "text": "Ada Lovelace\nData engineer."}
    assert (await client.post(f"/api/applications/{app_id}/artifacts", json=cv)).status_code == 201
    return app_id


async def _fill(client: AsyncClient, app_id: int, answers: list[dict[str, Any]]) -> None:
    payload = {"form_url": FORM, "fields_count": len(answers), "answers": answers}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
    assert resp.status_code == 201, resp.text


async def _card(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get("/api/ready-to-send", params={"application_id": app_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["items"][0]


def _codes(card: dict[str, Any]) -> list[str]:
    return [f["code"] for f in card["flags"] if f["code"] != "missing"]


async def _save_answers(client: AsyncClient, records: list[dict[str, Any]]) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": "user_info.answers", "value": records}]})
    assert resp.status_code == 200, resp.text


def _record(company: str | None = "Certa", topic: str = "why_company", answer: str = ESSAY) -> dict[str, Any]:
    rec: dict[str, Any] = {"question": Q, "answer": answer, "approved": True, "topic": topic}
    if company:
        rec["company"] = company
    return rec


async def _gate_says_unapproved(user_id: str, app_id: int) -> bool:
    from src.api import dependencies as api_deps
    from src.services.applications import kit, spine

    db = await api_deps.get_db()
    row = await spine.get_owned_application(db, user_id, app_id)
    facts = await kit.gate_facts(db, user_id, row, email_verified=True)
    return facts.written_unapproved


@pytest.mark.asyncio
async def test_a_written_answer_with_no_record_is_flagged_and_the_gate_sees_it(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [WRITTEN])
        card = await _card(client, app_id)
        assert "unapproved" in _codes(card)
        flag = next(f for f in card["flags"] if f["code"] == "unapproved")
        assert flag["text"] == f"A written answer needs your OK: {Q}"
        assert await _gate_says_unapproved(fixture_user_id, app_id) is True


@pytest.mark.asyncio
async def test_an_approved_record_for_this_company_and_topic_clears_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _save_answers(client, [_record()])
        await _fill(client, app_id, [{**WRITTEN, "source": "approved"}])
        card = await _card(client, app_id)
        assert "unapproved" not in _codes(card) and card["answers"][0]["verified"] is True
        assert await _gate_says_unapproved(fixture_user_id, app_id) is False


@pytest.mark.asyncio
async def test_a_record_scoped_to_another_company_is_still_flagged(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _save_answers(client, [_record(company="Northwind")])
        await _fill(client, app_id, [{**WRITTEN, "source": "approved"}])
        assert "unapproved" in _codes(await _card(client, app_id))
        assert await _gate_says_unapproved(fixture_user_id, app_id) is True


@pytest.mark.asyncio
async def test_approved_with_no_key_and_no_record_is_flagged(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [{"question": Q, "answer": ESSAY, "source": "approved"}])
        assert "unapproved" in _codes(await _card(client, app_id))
        assert await _gate_says_unapproved(fixture_user_id, app_id) is True


@pytest.mark.asyncio
async def test_verify_saves_the_essay_for_this_company_only_and_the_gate_clears(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [WRITTEN])
        card = await _card(client, app_id)
        assert "unapproved" in _codes(card)
        resp = await client.post(
            f"/api/applications/{app_id}/answers/0/confirm", json={"form_filled_event_id": card["form_filled_event_id"]}
        )
        assert resp.status_code == 200, resp.text
        assert "unapproved" not in _codes(resp.json())
        saved = [r for r in load_profile(fixture_user_id).user_info.answers if r.get("approved") is True]
        assert [(r["question"], r["answer"], r["company"], r["topic"]) for r in saved] == [
            (Q, ESSAY, "Certa", "why_company")
        ]
        assert await _gate_says_unapproved(fixture_user_id, app_id) is False
        # a fill typing the same essay as an approved answer stays clear
        await _fill(client, app_id, [{**WRITTEN, "source": "approved"}])
        assert await _gate_says_unapproved(fixture_user_id, app_id) is False
