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
    answered, missing = _kit_missing(records, "GB")
    assert [a["value"] for a in answered] == ["Yes"] and missing == []


def test_opposite_answers_in_two_cases_give_no_match_and_the_kit_shows_it_missing():
    records = [_rec("sponsorship.GB.yes_no", "Yes"), _rec("sponsorship.gb.yes_no", "No")]
    assert find_answer(records, topic="sponsorship.GB.yes_no", country="GB") is None
    assert find_answer(records, topic="sponsorship.gb.yes_no", country="GB") is None
    answered, missing = _kit_missing(records, "GB")
    assert answered == [] and len(missing) == 1 and "ambiguous" in missing[0]["why"]
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
async def test_answer_once_shows_a_conflict_row_and_remembering_one_value_removes_it(
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
        resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
            {"topic": row["topic"], "question": row["question"], "answer": "No"}]})
        assert resp.status_code == 200, resp.text
        gb = (await client.get("/api/profile/questionnaire")).json()["countries"][0]["questions"]
        assert not any(q["conflict"] for q in gb)
    mine = [r for r in load_profile(fixture_user_id).user_info.answers if "sponsorship" in str(r.get("topic"))]
    assert [(r["topic"], r["answer"]) for r in mine] == [("sponsorship.GB.yes_no", "No")]
