"""A knock-out question answered by the user's OWN Memory row for the job's country no longer stops the run.

Live run 2026-11-19 09:08 (applications 1133 Navan, 1134 Abound, 1135 EDITED; asks 716-718): every UK form had a
sponsorship / visa / right-to-work question. check_form listed each one in ``knockouts`` from its wording alone
(kit.form_check), even with the user's own saved GB answer filled, and ``next`` said "tell the user BEFORE drafting
or typing anything (stop_for_user)". The run typed nothing (form_filled fields_count 0) and asked the owner to
confirm answers already in Memory. Owner, 2026-11-19: such a question is filled from the saved answer and no
longer stops the run; a question with no saved answer, an answer not the user's own, and one pay figure out of a
saved range still come to the user. Helpers are defined here, never imported.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from src.services.applications import kit

SPONSOR_Q = "Will you now or in the future require employment visa sponsorship?"
RTW_Q = "Do you have the right to work in the UK?"
PAY_Q = "Expected salary"


def _profile(needs_sponsorship: Optional[bool] = False) -> Any:
    from src.services.profile.models import UserInfo, UserPreferences, UserProfile

    gb: dict[str, Any] = {"country": "GB", "work_authorization": "citizen"}
    if needs_sponsorship is not None:
        gb["needs_sponsorship"] = needs_sponsorship
    return UserProfile(
        user_info=UserInfo(right_to_work={"countries": [gb]}),
        preferences=UserPreferences(salary_by_country=[
            {"country": "GB", "min": 40000, "max": 45000, "currency": "GBP", "period": "year"}]),
    )


def _form(questions: list[str], users: Optional[frozenset[str]], profile: Any = None) -> dict[str, Any]:
    """check_form's pure core over a REAL kit; ``users`` = the Memory keys that are the user's own word."""
    profile = profile or _profile()
    answers, _missing = kit.compute_answers(profile, [], "GB", None)
    return kit.form_check(answers, [{"question": q} for q in questions], records=profile.user_info.answers,
                          country="GB", users=users)


def _all_memory_keys(profile: Any = None) -> frozenset[str]:
    answers, _missing = kit.compute_answers(profile or _profile(), [], "GB", None)
    return frozenset(str(i["key"]) for block in answers.values() for i in block if i.get("source") == "memory")


def test_the_users_own_sponsorship_and_rtw_answers_fill_and_do_not_stop_the_run():
    out = _form([SPONSOR_Q, RTW_Q], _all_memory_keys())
    assert [(a["key"], a["value"]) for a in out["answered"]] == [
        ("right_to_work.GB.needs_sponsorship", "No"), ("right_to_work.GB.work_authorization", "Yes")]
    assert out["knockouts"] == []
    assert "stop_for_user" not in out
    assert "stop_for_user" not in out["next"]


def test_an_answer_that_is_not_the_users_own_word_still_stops():
    out = _form([SPONSOR_Q], frozenset())
    assert [k["question"] for k in out["knockouts"]] == [SPONSOR_Q]
    assert out["stop_for_user"] is True


def test_a_knockout_with_no_saved_answer_still_stops():
    out = _form([SPONSOR_Q], _all_memory_keys(), profile=_profile(needs_sponsorship=None))
    assert [k["question"] for k in out["knockouts"]] == [SPONSOR_Q]
    assert out["stop_for_user"] is True


def test_pay_from_a_saved_range_still_stops_even_when_it_is_the_users_own():
    out = _form([PAY_Q, SPONSOR_Q], _all_memory_keys())
    assert [k["question"] for k in out["knockouts"]] == [PAY_Q]
    assert out["stop_for_user"] is True


def test_without_the_users_reading_nothing_changes():
    out = _form([SPONSOR_Q], None)
    assert [k["question"] for k in out["knockouts"]] == [SPONSOR_Q]


# ── Through the real HTTP door: the user's own GB row (saved on the web) vs an assistant's write ──

FORM = "https://careers.example/apply/1135"
GB_ROW = {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}


async def _http_form(client: Any, app_id: int, *questions: str) -> dict[str, Any]:
    body = {"form_url": FORM, "questions": [{"question": q} for q in questions]}
    resp = await client.post(f"/api/applications/{app_id}/kit/form-check", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _bring_gb(client: Any) -> int:
    ad = {"title": "Junior Data Scientist", "company": "Acme GB", "location": "London", "country": "GB",
          "apply_url": "https://acme-gb.example/careers/1", "description": "Build models."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


def _grab_audit() -> tuple[list[dict[str, Any]], Any]:
    import logging

    records: list[dict[str, Any]] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(dict(record.__dict__))

    logger = logging.getLogger("job360.audit")
    handler, old = _Grab(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def _stop() -> None:
        logger.removeHandler(handler)
        logger.setLevel(old)
    return records, _stop


@pytest.mark.asyncio
async def test_the_users_own_web_saved_row_fills_the_knockout_and_logs_it(authenticated_async_context):
    records, stop = _grab_audit()
    try:
        async with authenticated_async_context() as client:
            resp = await client.patch("/api/profile", json={"edits": [
                {"path": "user_info.right_to_work", "value": {"countries": [GB_ROW]}}]})
            assert resp.status_code == 200, resp.text
            app_id = await _bring_gb(client)
            out = await _http_form(client, app_id, SPONSOR_Q, RTW_Q)
    finally:
        stop()
    assert [(a["key"], a["value"]) for a in out["answered"]] == [
        ("right_to_work.GB.needs_sponsorship", "No"), ("right_to_work.GB.work_authorization", "Yes")]
    assert out["knockouts"] == [] and out.get("stop_for_user") is None
    filled = [r for r in records if r.get("event") == "form_check_knockout_filled"]
    assert len(filled) == 1 and str(filled[0]["application_id"]) == str(app_id)
    assert filled[0]["keys"] == "right_to_work.GB.needs_sponsorship,right_to_work.GB.work_authorization"
    assert filled[0]["reason"] == "users_own_memory"


@pytest.mark.asyncio
async def test_a_row_an_assistant_saved_still_stops_the_run(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring_gb(client)
        token = str((await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"])
        from httpx import ASGITransport, AsyncClient

        from src.api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            resp = await agent.patch("/api/profile", json={"edits": [
                {"path": "user_info.right_to_work", "value": {"countries": [GB_ROW]}, "remember": True,
                 "source": "user_said"}]})
            assert resp.status_code == 200, resp.text
        out = await _http_form(client, app_id, SPONSOR_Q)
    assert [k["question"] for k in out["knockouts"]] == [SPONSOR_Q]
    assert out["stop_for_user"] is True


@pytest.mark.parametrize("question", [
    "Will you now or in the future require employment visa sponsorship?",  # EDITED, ask 718
    "Do you require employment sponsorship to work in the UK?",
])
def test_employment_visa_sponsorship_is_the_direct_wording(question):
    out = _form([question], _all_memory_keys())
    assert [(a["key"], a["value"]) for a in out["answered"]] == [("right_to_work.GB.needs_sponsorship", "No")]


@pytest.mark.parametrize("question", [
    # A visa is not sponsorship (a Graduate visa needs no sponsor): still a gap, never read as needs_sponsorship.
    "Would you now or in the future require a visa for employment for where this job is based?",  # Abound, ask 717
    "Are you eligible to work in the UK without employment visa sponsorship or not?",
])
def test_a_visa_or_an_unclear_wording_stays_a_gap(question):
    out = _form([question], _all_memory_keys())
    assert out["answered"] == [] and [k["question"] for k in out["knockouts"]] == [question]
