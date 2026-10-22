"""Private, legal and risky form questions on ANY job (owner, 2026-10-22): flagged, held, raised on Needs you."""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications.kit import _question_is_sensitive, form_check
from src.services.profile import assistant_settings as rules

FORM = "https://careers.example/apply/9"
_AD = {
    "title": "Data Engineer", "company": "Certa", "location": "Remote", "country": "CA", "remote": True,
    "apply_url": "https://certa.example/careers/9", "description": "Build the pipelines.",
}
NATIONALITY = {"question": "What is your nationality?", "answer": "Canadian", "source": "profile"}


@pytest.mark.parametrize("question", [
    "What is your nationality?", "Are you a US citizen?", "Please give your passport number", "Date of birth",
    "Do you have a criminal record?", "Do you have any medical conditions we should know about?",
    "Are you pregnant?", "Marital status", "Have you ever filed for bankruptcy?", "What is your current salary?",
    "Are you a member of a trade union?", "Do you consent to a credit check?", "Will you take a drug test?",
    "Social security number", "Place of birth", "Have you served in the military?",
    "Are you legally authorised to work in Canada?", "What is your SIN or tax number?",
])
def test_private_questions_are_sensitive_in_any_country(question):
    assert _question_is_sensitive(question, None), question


@pytest.mark.parametrize("question", [
    "Why do you want this job?", "Describe your healthcare experience", "Link to your portfolio",
    "How did you hear about us?", "Years of Python experience", "Your LinkedIn URL",
    "Tell us about a time you led a team", "Page 2 of the form", "Is this role union-free?",
])
def test_ordinary_questions_are_not_sensitive(question):
    assert not _question_is_sensitive(question, None), question


def test_check_form_flags_a_private_question_and_tells_the_assistant_to_raise_it():
    out = form_check({"profile": []}, [{"question": "Date of birth", "field_type": "text"}])
    assert out["needs_confirmation"] == ["Date of birth"]
    assert out["missing"][0]["needs_confirmation"] is True
    assert "ask_user" in out["next"] and "Needs you" in out["next"]


def _facts(**kw: Any) -> rules.ApplicationFacts:
    from tests.test_assistant_settings import _facts as base  # noqa: PLC0415

    return base(**kw)


def test_an_unconfirmed_sensitive_answer_asks_in_every_mode_and_no_yes_clears_it():
    from tests.test_assistant_settings import AUTO_CFG, NOW, _counts, _eff  # noqa: PLC0415
    from tests.test_assistant_settings import FORM as GATE_FORM

    assert "sensitive_unconfirmed" in rules.REASONS
    for cfg in (AUTO_CFG, _eff()):
        d = rules.may_submit(cfg, _facts(sensitive_unconfirmed=True), GATE_FORM, _counts(since=3), now=NOW)
        assert (d.decision, d.reason) == ("ask", "sensitive_unconfirmed")
        assert "ask_user" in d.detail
    approved = rules.may_submit(AUTO_CFG, _facts(sensitive_unconfirmed=True, approved=True), GATE_FORM, _counts(), now=NOW)
    assert approved.reason == "sensitive_unconfirmed", "a yes to the CV is not a yes to a private answer"
    stopped = rules.may_submit(AUTO_CFG, _facts(sensitive_unconfirmed=True, declined=True), GATE_FORM, _counts(), now=NOW)
    assert stopped.reason == "user_declined", "a stop still wins"


@pytest.mark.asyncio
async def test_the_card_and_the_gate_read_a_private_answer_the_same_way(authenticated_async_context, fixture_user_id):
    """A typed answer to a private question is flagged ``sensitive`` on Needs you and holds check_submit until the
    user confirms it there; their Verify clears both together."""
    from src.api import dependencies as api_deps
    from src.services.applications import kit, spine

    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json=_AD)
        assert resp.status_code == 200, resp.text
        app_id = int(resp.json()["application_id"])
        assert (await client.post(f"/api/applications/{app_id}/artifacts",
                                  json={"kind": "cv", "text": "Ada Lovelace\nData engineer."})).status_code == 201
        db = await api_deps.get_db()

        async def gate_says_sensitive() -> bool:
            row = await spine.get_owned_application(db, fixture_user_id, app_id)
            return (await kit.gate_facts(db, fixture_user_id, row, email_verified=True)).sensitive_unconfirmed

        async def fill(answers: list[dict[str, Any]]) -> None:
            payload = {"form_url": FORM, "fields_count": len(answers), "answers": answers}
            r = await client.post(f"/api/applications/{app_id}/events",
                                  json={"event_type": "form_filled", "payload": payload})
            assert r.status_code == 201, r.text

        await fill([{"question": "Name", "answer": "Ada Lovelace", "source": "memory", "key": "contact.legal_last_name"}])
        assert await gate_says_sensitive() is False, "no private question, nothing to ask"
        await fill([NATIONALITY])
        card = (await client.get("/api/ready-to-send", params={"application_id": app_id})).json()["items"][0]
        assert "sensitive" in [f["code"] for f in card["flags"]]
        assert await gate_says_sensitive() is True
        confirm = await client.post(f"/api/applications/{app_id}/answers/0/confirm",
                                    json={"form_filled_event_id": card["form_filled_event_id"]})
        assert confirm.status_code == 200, confirm.text
        await fill([NATIONALITY])  # the next fill types the now-confirmed answer
        assert await gate_says_sensitive() is False, "the user's Verify clears the gate too"
