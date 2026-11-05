"""Memory first when a form is filled (owner 2026-11-05, plan S1).

A question in Memory's families (right to work, sponsorship, on-site, salary, notice, relocation, location,
equality, languages, contact) is answered from Memory: the job country's page, else its region's, else ANY's.
A saved answer in those families is never used, and saved answers that disagree no longer block it. Nothing
in Memory = a gap naming the page to fill. The user's Instructions come first in check_form's result.
Helpers are defined here, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import kit

SPONSOR_Q = "Will you now or in the future require visa sponsorship?"
US_AUTH_Q = "Are you legally authorized to work in the United States?"


def _profile(countries: list[dict[str, Any]], answers: list[dict[str, Any]], notes: Any = None) -> Any:
    from src.services.profile.models import UserInfo, UserPreferences, UserProfile

    return UserProfile(user_info=UserInfo(answers=answers, right_to_work={"countries": countries}),
                       preferences=UserPreferences(assistant_notes=notes or []))


def _saved(question: str, answer: str, topic: str, **extra: Any) -> dict[str, Any]:
    return {"question": question, "answer": answer, "approved": True, "topic": topic, **extra}


def _form(profile: Any, country: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
    """check_form's pure core over a REAL kit, exactly as check_form builds it."""
    answers, _missing = kit.compute_answers(profile, [], country, None)
    return kit.form_check(answers, questions, records=profile.user_info.answers, country=country,
                          instructions=kit.user_instructions(profile, country))


def test_gb_sponsorship_comes_from_memory_and_the_disagreeing_saved_twins_never_block_it():
    twins = [_saved("Visa sponsorship (UK)", "Yes", "sponsorship.GB.yes_no"),
             _saved(SPONSOR_Q, "No", "sponsorship.gb.yes_no")]
    profile = _profile([{"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}], twins)
    out = _form(profile, "GB", [{"question": SPONSOR_Q}, {"question": "Visa sponsorship (UK)",
                                                          "key": "sponsorship.GB.yes_no"}])
    assert out["missing"] == []
    assert [(a["value"], a["source"], a["key"], a["matched_by"]) for a in out["answered"]] == [
        ("No", "memory", "right_to_work.GB.needs_sponsorship", "wording"),
        ("No", "memory", "right_to_work.GB.needs_sponsorship", "key")]
    answers, missing = kit.compute_answers(profile, [], "GB", None)
    assert answers["approved_text"] == []
    assert [m for m in missing if m["why"] == kit.AMBIGUOUS] == []


def test_us_work_authorisation_comes_from_north_america_and_the_saved_us_answer_is_ignored():
    saved = [_saved(US_AUTH_Q, "I have US work authorization", "right_to_work.US.authorized", country="US")]
    profile = _profile([{"country": "NORTH_AMERICA", "work_authorization": "needs_sponsorship",
                         "needs_sponsorship": True}], saved)
    out = _form(profile, "US", [{"question": US_AUTH_Q}, {"question": SPONSOR_Q}])
    assert out["missing"] == []
    assert [(a["key"], a["value"], a["source"]) for a in out["answered"]] == [
        ("right_to_work.US.work_authorization", "No", "memory"),
        ("right_to_work.US.needs_sponsorship", "Yes", "memory"),
    ]


def test_poland_is_answered_from_the_europe_page_field_by_field():
    europe = {"country": "EUROPE", "needs_sponsorship": True}
    for countries in ([europe], [europe, {"country": "PL", "onsite": "yes"}]):
        profile = _profile(countries, [_saved(SPONSOR_Q, "No", "sponsorship.PL.yes_no", country="PL")])
        out = _form(profile, "PL", [{"question": SPONSOR_Q}])
        assert [(a["key"], a["value"], a["source"]) for a in out["answered"]] == [
            ("right_to_work.PL.needs_sponsorship", "Yes", "memory")], countries
        items = kit.compute_answers(profile, [], "PL", None)[0]["right_to_work"]
        assert [i["applies_via"] for i in items if i["key"] == "right_to_work.PL.needs_sponsorship"] == ["EUROPE"]


def test_a_question_outside_memory_still_uses_the_saved_answer():
    essay = "Tell us about a project you are proud of."
    profile = _profile([{"country": "GB", "needs_sponsorship": False}], [_saved(essay, "The pipeline rebuild.",
                                                                                 "essay.proud_project")])
    out = _form(profile, "GB", [{"question": essay}])
    assert [(a["value"], a["source"], a["matched_by"]) for a in out["answered"]] == [
        ("The pipeline rebuild.", "approved_text", "wording")]


def test_nothing_in_memory_is_a_gap_naming_the_country_page_never_the_saved_answer():
    profile = _profile([{"country": "IN", "needs_sponsorship": False}],
                       [_saved(SPONSOR_Q, "No", "sponsorship.GB.yes_no", country="GB")])
    out = _form(profile, "GB", [{"question": SPONSOR_Q}])
    assert out["answered"] == []
    assert [m["why"] for m in out["missing"]] == [
        "right_to_work.GB.needs_sponsorship is not in Memory: ask the user once, then add it on your GB page "
        "(or its region's page); a saved form answer is not used"]


def test_the_users_instructions_come_first_and_win_over_memory():
    remote = "Remote anywhere: automatic yes, no visa needed."
    profile = _profile([{"country": "EUROPE", "needs_sponsorship": True, "instructions": [remote]}], [],
                       notes=["On-site: UK and India always yes."])
    out = _form(profile, "DE", [{"question": "Do you require visa sponsorship for this remote role?"}])
    assert list(out)[0] == "user_instructions"
    assert out["user_instructions"] == {
        "lines": ["On-site: UK and India always yes."],
        "country": {"for": "DE", "from": "EUROPE", "lines": [remote]},
    }
    assert out["next"].startswith(kit.FORM_NEXT_INSTRUCTIONS)
    assert [a["value"] for a in out["answered"]] == ["Yes"]  # Memory's value; the Instructions line overrides it


@pytest.mark.parametrize("question,expected", [
    ("Do you have the right to work in the UK?", "right_to_work.{cc}.work_authorization"),
    ("Visa sponsorship within 18 months (UK)", "right_to_work.{cc}.needs_sponsorship"),
    ("What is your notice period?", "logistics.notice_period"),
    ("Do you need a visa?", None),  # no single Memory fact: a gap, never a guess
    ("Are you authorized to work in the US and will you require sponsorship?", None),
])
def test_memory_question_maps_wording_to_one_memory_fact_or_none(question, expected):
    from src.services.profile.ask_once import memory_question

    assert memory_question(None, question) == (True, expected)


def test_the_ready_card_never_turns_a_memory_answer_into_a_saved_one():
    from src.services.applications import ready
    from src.services.profile.edits import WEB_ACTOR

    key = "right_to_work.GB.needs_sponsorship"
    profile = _profile([{"country": "GB", "needs_sponsorship": False}],
                       [_saved(SPONSOR_Q, "Yes", "sponsorship.GB.yes_no", country="GB")])
    have = {i["key"]: i for i in kit.compute_answers(profile, [], "GB", None)[0]["right_to_work"]}
    typed = {"question": SPONSOR_Q, "answer": "No", "key": key, "source": "memory"}
    saved = {"user_info.right_to_work": (WEB_ACTOR, "2026-11-01T09:00:00+00:00")}
    assert ready._user_check(typed, profile, saved, None, have, None, "GB")[:2] == (True, None)


# ── Plan S2 (owner 2026-11-05): no NEW saved answer for a Memory fact; Memory is set by the user in Job360 ──


@pytest.mark.parametrize("op", ["append", "upsert"])
def test_a_new_saved_answer_in_memorys_families_is_refused_naming_the_country_page(op):
    from src.services.profile import edits
    from src.services.profile.edits import ProfileEditError

    new = _saved("Do you need visa sponsorship?", "No", "sponsorship.GB.yes_no")
    with pytest.raises(ProfileEditError) as info:
        edits.resolve_ops([("user_info.answers", op, new, None)], {"user_info.answers": []})
    assert info.value.status_code == 422
    assert "user_info.right_to_work" in info.value.detail and "your GB page" in info.value.detail
    essay = _saved("Why do you want to join us?", "The mission.", "why_us")
    [(_, value)] = edits.resolve_ops([("user_info.answers", op, essay, None)], {"user_info.answers": []})
    assert value == [essay], "outside Memory: saved as before"


@pytest.mark.asyncio
async def test_update_profile_refuses_a_sponsorship_upsert_and_saves_nothing(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.edits import current_edit_id

    async with authenticated_async_context() as client:
        before = current_edit_id(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": "user_info.answers", "op": "upsert",
            "value": _saved("Do you need visa sponsorship?", "No", "sponsorship.GB.yes_no")}]})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "Nothing was saved." in detail and "user_info.right_to_work" in detail and "your GB page" in detail
        assert current_edit_id(fixture_user_id) == before, "nothing written"
