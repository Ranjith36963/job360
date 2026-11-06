"""A saved answer's topic compares case-insensitively; two saved answers to one topic that disagree are a
contradiction: no answer is picked, the kit shows it missing, and Answer once asks which is right.
Production 2026-11-03: ``sponsorship.GB.yes_no`` = Yes and ``sponsorship.gb.yes_no`` = No were both saved."""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import kit
from src.services.profile.answer_match import conflicts, find_answer, topic_key

Q = "Will you now or in the future require visa sponsorship?"


def _rec(topic: str, answer: str, **extra: Any) -> dict[str, Any]:
    return {"question": Q, "answer": answer, "approved": True, "topic": topic, **extra}


def _kit_missing(records: list[dict[str, Any]], country: str, key: str = "sponsorship.GB.yes_no") -> tuple[list, list]:
    form = kit.form_check({}, [{"question": Q, "key": key}], records=records, country=country)
    return form["answered"], form["missing"]


def test_topic_key_is_casefold_and_a_topic_like_artifact_cv_is_never_rewritten():
    from src.services.profile.user_info import upsert_answer

    assert topic_key(" Sponsorship.GB.yes_no ") == topic_key("sponsorship.gb.yes_no")
    assert upsert_answer([], _rec("artifact.cv", "x"))[0]["topic"] == "artifact.cv"


def test_two_topics_differing_only_in_case_with_the_same_answer_are_one_match():
    records = [_rec("sponsorship.GB.yes_no", "Yes"), _rec("sponsorship.gb.yes_no", "Yes")]
    got = find_answer(records, topic="sponsorship.Gb.yes_no", country="GB")
    assert got is not None and got["answer"] == "Yes"
    assert conflicts(records, country="GB") == []
    # Memory first (owner 2026-11-05, S1): a sponsorship question takes Memory's GB page, never a saved answer.
    answered, missing = _kit_missing(records, "GB")
    assert answered == [] and len(missing) == 1 and "your GB page" in missing[0]["why"]


def test_opposite_answers_in_two_cases_give_no_match_and_the_kit_shows_it_missing():
    records = [_rec("sponsorship.GB.yes_no", "Yes"), _rec("sponsorship.gb.yes_no", "No")]
    assert find_answer(records, topic="sponsorship.GB.yes_no", country="GB") is None
    assert find_answer(records, topic="sponsorship.gb.yes_no", country="GB") is None
    answered, missing = _kit_missing(records, "GB")
    assert answered == [] and len(missing) == 1 and "your GB page" in missing[0]["why"]  # Memory first (S1)
    assert conflicts(records, country="GB") == [
        {"topic": "sponsorship.GB.yes_no", "question": Q, "values": ["Yes", "No"]}]


def test_other_countries_are_unaffected_by_a_gb_contradiction():
    records = [_rec("sponsorship.GB.yes_no", "Yes"), _rec("sponsorship.gb.yes_no", "No"),
               _rec("sponsorship.US.yes_no", "Yes")]
    got = find_answer(records, topic="sponsorship.us.yes_no", country="US")
    assert got is not None and got["answer"] == "Yes"
    assert conflicts(records, country="US") == []


def test_a_new_save_with_different_case_replaces_its_twin():
    from src.services.profile.user_info import upsert_answer

    saved = upsert_answer([_rec("sponsorship.GB.yes_no", "Yes")], _rec("sponsorship.gb.yes_no", "No"))
    assert [(r["topic"], r["answer"]) for r in saved] == [("sponsorship.gb.yes_no", "No")]
    both = upsert_answer([_rec("sponsorship.GB.yes_no", "Yes"), _rec("sponsorship.gb.yes_no", "No"),
                          _rec("contact.phone", "1")], _rec("sponsorship.GB.yes_no", "No", country="GB"))
    assert [(r["topic"], r["answer"]) for r in both] == [("sponsorship.GB.yes_no", "No"), ("contact.phone", "1")]


def test_the_validator_keeps_the_topic_as_sent():
    from src.services.profile import user_info

    out = user_info.validate_user_info(user_info.USER_INFO_ANSWERS_PATH, [
        {"question": Q, "answer": "No", "approved": True, "topic": "location.current.in"}])
    assert out[0]["topic"] == "location.current.in"


def test_a_write_never_rewrites_or_merges_saved_twins():
    from src.services.profile import user_info

    path = user_info.USER_INFO_ANSWERS_PATH
    other = "Do you need a visa?"
    for second in ("No", "Yes"):
        kept = user_info.validate_user_info(path, [
            _rec("sponsorship.GB.yes_no", "Yes"), _rec("sponsorship.gb.yes_no", second, question=other)])
        assert [r["topic"] for r in kept] == ["sponsorship.GB.yes_no", "sponsorship.gb.yes_no"]


@pytest.mark.asyncio
async def test_answer_once_shows_a_conflict_row_and_a_press_on_it_saves_nothing(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile import edits as profile_edits
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        saved = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": [
                {"country": "GB", "work_authorization": "citizen"}]}}]})
        assert saved.status_code == 200, saved.text
        # Recorded without the validator (it would collapse the twins); production holds both.
        profile_edits.record_edits(
            fixture_user_id, profile_edits.WEB_ACTOR,
            [("user_info.answers", [_rec("sponsorship.GB.yes_no", "Yes"),
                                    _rec("sponsorship.gb.yes_no", "No", question="Do you need a visa?")])],
            enforce_rate_limit=False, source=profile_edits.SOURCE_USER_SAID,
        )
        gb = (await client.get("/api/profile/questionnaire")).json()["countries"][0]["questions"]
        row = next(q for q in gb if q["conflict"])
        assert (row["topic"], row["conflict"], row["answer"]) == ("sponsorship.GB.yes_no", ["Yes", "No"], None)
        # Memory first (owner 2026-11-05, plan S2): sponsorship is Memory's, so a press saves no answer; the
        # stored twins are left for the owner's clean-up (S3), never deleted here.
        # owner 2026-11-06: answer once writes Memory (refines S2)
        # GB's Memory holds no sponsorship fact yet, so the press fills it.
        resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
            {"topic": row["topic"], "question": row["question"], "answer": "No"}]})
        assert resp.status_code == 200 and resp.json()["saved"] == [], resp.text
        assert resp.json()["memory"]["sponsorship.GB.yes_no"]["key"] == "right_to_work.GB.needs_sponsorship"
        gb_page = [{"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}]
        assert load_profile(fixture_user_id).user_info.right_to_work["countries"] == gb_page
        # Now Memory is set: a press with another value is a conflict and writes nothing.
        resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
            {"topic": row["topic"], "question": row["question"], "answer": "Yes"}]})
        assert resp.status_code == 200 and resp.json()["saved"] == [] and "memory" not in resp.json(), resp.text
        assert resp.json()["conflicts"]["sponsorship.GB.yes_no"].startswith("your GB page says No; this answer says Yes")
        assert load_profile(fixture_user_id).user_info.right_to_work["countries"] == gb_page
    mine = [r for r in load_profile(fixture_user_id).user_info.answers if "sponsorship" in str(r.get("topic"))]
    assert [(r["topic"], r["answer"]) for r in mine] == [("sponsorship.GB.yes_no", "Yes"), ("sponsorship.gb.yes_no", "No")]


# ── Verifier seams (2026-11-03): twins worded differently, and a narrower answer over a broader one ──

VISA_Q = "Do you need a visa?"
PAY_Q = "Which interview slot suits you?"  # outside Memory: saved answers still resolve it


def _profile(records: list[dict[str, Any]]) -> Any:
    from src.services.profile.models import UserInfo, UserProfile

    return UserProfile(user_info=UserInfo(answers=records))


def _real_form(records: list[dict[str, Any]], country: str, questions: list[dict[str, Any]],
               company_key: Any = None) -> dict[str, Any]:
    """check_form's pure core over a REAL kit (compute_answers), as check_form builds it."""
    answers, _missing = kit.compute_answers(_profile(records), [], country, company_key)
    return kit.form_check(answers, questions, records=records, company_key=company_key, country=country)


def _ready_verifies(records: list[dict[str, Any]], question: str, typed: str, key: Any, country: str,
                    company_key: Any = None) -> bool:
    from src.services.applications import ready

    a = {"question": question, "answer": typed, "key": key, "source": "approved_text"}
    return ready._user_check(a, _profile(records), {}, company_key, {}, None, country)[0]


def _twins_worded_differently() -> list[dict[str, Any]]:
    return [_rec("sponsorship.GB.yes_no", "Yes"), _rec("sponsorship.gb.yes_no", "No", question=VISA_Q)]


def test_twins_worded_differently_are_missing_from_the_kit_as_ambiguous():
    records = _twins_worded_differently()
    answers, missing = kit.compute_answers(_profile(records), [], "GB", None)
    assert [i["value"] for i in answers["approved_text"]] == []
    # Memory first (S1): saved sponsorship answers are not the kit's, so they no longer block it as ambiguous;
    # the kit asks for the GB page's fact instead.
    assert [m for m in missing if "ambiguous" in m["why"]] == []
    assert "right_to_work.GB.needs_sponsorship" in [m["key"] for m in missing]


@pytest.mark.parametrize("key", [Q, VISA_Q, "sponsorship.GB.yes_no", "sponsorship.gb.yes_no", None])
@pytest.mark.parametrize("question", [Q, VISA_Q])
def test_twins_worded_differently_give_no_answer_on_the_form_by_any_key(key, question):
    form = _real_form(_twins_worded_differently(), "GB", [{"question": question, "key": key}])
    assert form["answered"] == []
    assert len(form["missing"]) == 1 and "ask the user" in form["missing"][0]["why"]  # Memory first (S1)


@pytest.mark.parametrize("question,typed", [(Q, "Yes"), (VISA_Q, "No"), (Q, "No"), (VISA_Q, "Yes")])
@pytest.mark.parametrize("key", [None, "sponsorship.GB.yes_no"])
def test_twins_worded_differently_are_never_verified_on_the_ready_card(question, typed, key):
    from src.services.applications import ready

    records = _twins_worded_differently()
    assert ready._approved_record(_profile(records), question, None, None, "GB") is None
    assert not _ready_verifies(records, question, typed, key, "GB")


def _pay(answer: str, **extra: Any) -> dict[str, Any]:
    return {"question": PAY_Q, "answer": answer, "approved": True, "topic": "form.interview_slot", **extra}


_NARROWER = {
    "country-bound": ([_pay("50000"), _pay("60000", country="GB")], None),
    "country-bound saved first": ([_pay("60000", country="GB"), _pay("50000")], None),
    "company-scoped": ([_pay("50000"), _pay("60000", company="Northwind")], "Northwind"),
    "company-scoped saved first": ([_pay("60000", company="Northwind"), _pay("50000")], "Northwind"),
}


@pytest.mark.parametrize("case", list(_NARROWER))
def test_a_narrower_answer_wins_over_a_broader_one_everywhere(case):
    from src.services.applications import ready
    from src.services.profile.answer_match import resolve_answer

    records, company = _NARROWER[case]
    ck = kit.normalized_company_key(company) if company else None
    assert conflicts(records, country="GB") == [] and conflicts(records, country="GB", company_key=ck) == []
    rec, note = resolve_answer(records, topic="form.interview_slot", company_key=ck, country="GB")
    assert note is None and rec is not None and rec["answer"] == "60000"
    answers, missing = kit.compute_answers(_profile(records), [], "GB", ck)
    assert [i["value"] for i in answers["approved_text"]] == ["60000"]
    assert not [m for m in missing if "ambiguous" in m["why"]]
    for key in (PAY_Q, "form.interview_slot", None):
        form = _real_form(records, "GB", [{"question": PAY_Q, "key": key}], ck)
        assert [a["value"] for a in form["answered"]] == ["60000"], (key, form["missing"])
    for key in (None, "form.interview_slot"):
        got = ready._approved_record(_profile(records), PAY_Q, ck, key, "GB")
        assert got is not None and got["answer"] == "60000"
        assert _ready_verifies(records, PAY_Q, "60000", key, "GB", ck)
        assert not _ready_verifies(records, PAY_Q, "50000", key, "GB", ck)
