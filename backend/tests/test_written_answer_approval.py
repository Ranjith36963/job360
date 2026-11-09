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
async def test_verify_approves_the_essay_for_this_job_only_and_the_gate_clears(
    authenticated_async_context, fixture_user_id
):
    # Owner decision 2026-11-02 "A": Verify approves for THIS job only; nothing is saved for reuse
    # (reuse is the "remember" press). Before, Verify saved the essay to the profile for the company.
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
        stored = load_profile(fixture_user_id)
        assert stored is None or not [r for r in stored.user_info.answers if r.get("approved") is True]
        assert await _gate_says_unapproved(fixture_user_id, app_id) is False
        # a fill typing the same essay as an approved answer stays clear
        await _fill(client, app_id, [{**WRITTEN, "source": "approved"}])
        assert await _gate_says_unapproved(fixture_user_id, app_id) is False


# -- a CV / letter FILE field the browser uploaded is never a written essay (production 2026-11-09, app 1004) --

CV_NAME = "Ranjith_Maliga-Guruprakash_CV.pdf"
CV_FILE = {"question": "CV or resume", "answer": CV_NAME, "source": "written"}
LETTER_ESSAY = "I admire Lenskart and would bring five years of data work."


async def _uploaded(user_id: str, app_id: int, kind: str = "cv") -> int:
    """What the browser `upload` action records: a ``document_uploaded`` event of this kind."""
    from src.api import dependencies as api_deps
    from src.services.applications import spine, uploads

    db = await api_deps.get_db()
    row = await spine.get_owned_application(db, user_id, app_id)
    art = {"id": 7, "version_no": 1}
    event_id = await uploads.record(
        db, user_id=user_id, application_id=int(row["id"]), kind=kind, artifact=art, data=b"%PDF",
        fmt="pdf", renderer_version="t", host="jobs.example", recorded_by="test",
    )
    assert event_id is not None
    return 7


async def _unapproved_texts(client: AsyncClient, user_id: str, app_id: int, answers: list[dict[str, Any]]) -> list[str]:
    await _fill(client, app_id, answers)
    return [f["text"] for f in (await _card(client, app_id))["flags"] if f["code"] == "unapproved"]


@pytest.mark.asyncio
async def test_the_lenskart_cv_upload_recorded_as_written_is_not_unapproved(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _uploaded(fixture_user_id, app_id)
        assert await _unapproved_texts(client, fixture_user_id, app_id, [CV_FILE]) == []
        assert await _gate_says_unapproved(fixture_user_id, app_id) is False
        for name in ("C:\\fakepath\\Ranjith CV.pdf", "Ranjith Maliga CV.pdf"):
            assert await _unapproved_texts(client, fixture_user_id, app_id, [{**CV_FILE, "answer": name}]) == []
        label = {**CV_FILE, "answer": "CV v1 (artifact 7)", "key": "artifact.cv"}
        assert await _unapproved_texts(client, fixture_user_id, app_id, [label]) == []


@pytest.mark.asyncio
async def test_a_cv_file_name_with_no_upload_event_is_still_unapproved(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert await _unapproved_texts(client, fixture_user_id, app_id, [CV_FILE]) == [
            "A written answer needs your OK: CV or resume"
        ]
        assert await _gate_says_unapproved(fixture_user_id, app_id) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    # P0-a: an artifact.* key on an essay
    {"question": "Why do you want to work at Lenskart?", "answer": LETTER_ESSAY, "source": "written",
     "key": "artifact.letter"},
    # P0-b: an essay with a file name or an artifact label tacked on
    {"question": "Cover letter", "answer": LETTER_ESSAY + " Portfolio: my_work.pdf", "source": "written"},
    {"question": "Cover letter (paste text)", "answer": LETTER_ESSAY + " (artifact 12)", "source": "written"},
    # P1-a: a file name of the CV kind where only a CV was uploaded (cover letter has no upload)
    {"question": "Cover letter", "answer": "Someone_Else_CV.pdf", "source": "written"},
    # P1-b: a typed answer under an artifact key
    {"question": "Expected salary", "answer": "50000", "source": "written", "key": "artifact.cv"},
    {"question": "Resume", "answer": "My cover letter. I love your company.pdf", "source": "written"},
    # a typed text that only asks about the CV
    {"question": "Tell us about your CV", "answer": "I led a data team of five.", "source": "written"},
])
async def test_typed_answers_dressed_as_files_stay_unapproved(authenticated_async_context, fixture_user_id, answer):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _uploaded(fixture_user_id, app_id)  # a CV was uploaded: it must not excuse these
        assert await _unapproved_texts(client, fixture_user_id, app_id, [answer]) != []
        assert await _gate_says_unapproved(fixture_user_id, app_id) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"question": "Cover letter (paste text)", "answer": LETTER_ESSAY + "\nPortfolio: drive/letter.pdf", "source": "written"},
    {"question": "Cover letter", "answer": LETTER_ESSAY + "\\Ranjith letter.pdf", "source": "written"},
])
async def test_an_essay_before_a_path_separator_is_not_a_file_even_with_a_letter_uploaded(
    authenticated_async_context, fixture_user_id, answer,
):
    """Third security review (2026-11-09): only the browser's exact C:\\fakepath\\ prefix is stripped; text before any
    other path separator made the essay look like the file's last segment."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _uploaded(fixture_user_id, app_id, kind="cover_letter")
        assert await _unapproved_texts(client, fixture_user_id, app_id, [answer]) != []
        assert await _gate_says_unapproved(fixture_user_id, app_id) is True


@pytest.mark.asyncio
async def test_a_guessed_file_row_keeps_its_guessed_flag(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _uploaded(fixture_user_id, app_id)
        await _fill(client, app_id, [{"question": "Expected salary", "answer": "50000", "source": "guessed",
                                      "key": "artifact.cv"}])
        assert "guessed" in _codes(await _card(client, app_id))
