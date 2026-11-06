"""check_form on real forms (live run 2026-11-06): three wrong or missing answers.

1. Polarity. "Are you eligible to ... work in the UK WITHOUT requiring employer sponsorship?" maps to
   ``right_to_work.GB.needs_sponsorship``; a saved False must answer it "Yes" (the negation), never "No",
   which would knock the user out. A wording whose polarity cannot be told is a gap, never a guess.
2. "Link to your LinkedIn profile" is the saved ``contact.linkedin_url`` (as "LinkedIn URL" already was).
3. "Desired Pay" / "Desired compensation" are the job country's saved salary range, in its saved currency;
   a form asking in another currency, or a country with no salary record, is still a gap.
Helpers are defined here, never imported.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from src.services.applications import kit

LINKEDIN = "https://www.linkedin.com/in/example-person"
GITHUB = "https://github.com/example-person"


def _profile(needs_sponsorship: Optional[bool] = False, salary: Optional[list[dict[str, Any]]] = None) -> Any:
    from src.services.profile.models import CVData, UserInfo, UserPreferences, UserProfile

    gb: dict[str, Any] = {"country": "GB", "work_authorization": "citizen"}
    if needs_sponsorship is not None:
        gb["needs_sponsorship"] = needs_sponsorship
    return UserProfile(
        user_info=UserInfo(contact={"linkedin_url": LINKEDIN}, right_to_work={"countries": [gb]}),
        preferences=UserPreferences(salary_by_country=salary if salary is not None else [
            {"country": "GB", "min": 40000, "max": 45000, "currency": "GBP", "period": "annual"}]),
        cv_data=CVData(links=[GITHUB]),
    )


def _form(profile: Any, questions: list[dict[str, Any]], country: str = "GB") -> dict[str, Any]:
    """check_form's pure core over a REAL kit, exactly as check_form builds it."""
    answers, _missing = kit.compute_answers(profile, [], country, None)
    return kit.form_check(answers, questions, records=profile.user_info.answers, country=country)


def _one(profile: Any, question: str, **extra: Any) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    out = _form(profile, [{"question": question, **extra}])
    return (out["answered"][0] if out["answered"] else None), (out["missing"][0] if out["missing"] else None)


# ── 1. Sponsorship polarity ──

LIVE_Q = ("Are you eligible to live and work in the UK without requiring employer sponsorship now or "
          "in the future?")


@pytest.mark.parametrize("question,needs,expected", [
    (LIVE_Q, False, "Yes"),
    (LIVE_Q, True, "No"),
    ("Are you eligible to work in the UK without sponsorship?", False, "Yes"),
    ("Can you work in the UK without visa sponsorship?", True, "No"),
    ("Will you now or in the future require sponsorship?", False, "No"),
    ("Will you now or in the future require sponsorship?", True, "Yes"),
    ("Do you require visa sponsorship? Yes/No", False, "No"),
])
def test_sponsorship_answer_follows_the_questions_polarity(question, needs, expected):
    answered, missing = _one(_profile(needs), question)
    assert missing is None, missing
    assert (answered["key"], answered["value"]) == ("right_to_work.GB.needs_sponsorship", expected)


def test_the_live_inverted_question_is_yes_even_when_the_run_names_the_kit_key():
    answered, missing = _one(_profile(False), LIVE_Q, key="right_to_work.GB.needs_sponsorship")
    assert missing is None and answered["value"] == "Yes"
    out = _form(_profile(False), [{"question": LIVE_Q}])
    assert out["knockouts"] == [{"question": LIVE_Q, "key": None, "value": "Yes"}]


def test_right_to_work_still_maps_as_today():
    answered, missing = _one(_profile(False), "Do you have the right to work in the UK?")
    assert missing is None
    assert (answered["key"], answered["value"]) == ("right_to_work.GB.work_authorization", "Yes")


@pytest.mark.parametrize("question", [
    "Are you unable to work in the UK without sponsorship?",          # double negative
    "Will you not require sponsorship to work in the UK?",            # a "not" on the asking verb
    "Do you need sponsorship, or can you work without it?",           # both readings in one question
    "Sponsorship without restrictions?",                              # "without" with no eligibility word
    "Is it true that you don't need sponsorship?",
])
def test_an_unclear_sponsorship_wording_is_a_gap_never_a_guess(question):
    answered, missing = _one(_profile(False), question)
    assert answered is None, answered
    assert "polarity" in missing["why"] and missing["needs_confirmation"] is True


# ── 2. Contact links ──

@pytest.mark.parametrize("question", [
    "Link to your LinkedIn profile", "LinkedIn URL", "LinkedIn", "LinkedIn Profile URL",
    "Please share a link to your LinkedIn", "Your LinkedIn page",
])
def test_linkedin_link_labels_take_the_saved_contact_url(question):
    answered, missing = _one(_profile(), question)
    assert missing is None, missing
    assert (answered["key"], answered["value"]) == ("contact.linkedin_url", LINKEDIN)


@pytest.mark.parametrize("question", ["GitHub", "Link to your GitHub profile", "GitHub URL"])
def test_github_link_labels_take_the_saved_github_url(question):
    answered, missing = _one(_profile(), question)
    assert missing is None, missing
    assert (answered["key"], answered["value"]) == ("contact.github_url", GITHUB)


@pytest.mark.parametrize("question", [
    "How did you hear about us? LinkedIn",
    "LinkedIn or personal website",
    "Did you find this job on LinkedIn?",
])
def test_a_question_that_only_mentions_a_site_is_not_its_link(question):
    answered, _missing = _one(_profile(), question)
    assert answered is None or answered["key"] != "contact.linkedin_url"


# ── 3. Desired pay ──

@pytest.mark.parametrize("question", ["Desired Pay", "Expected salary", "Salary expectations",
                                      "Desired compensation", "What is your desired pay?"])
def test_pay_wordings_take_the_job_countrys_saved_range(question):
    answered, missing = _one(_profile(), question)
    assert missing is None, missing
    assert (answered["key"], answered["value"]) == ("salary.GB", "40000-45000 GBP annual")


def test_desired_pay_is_a_knockout_like_expected_salary():
    out = _form(_profile(), [{"question": "Desired Pay"}, {"question": "Expected salary"}])
    assert [(k["question"], k["value"]) for k in out["knockouts"]] == [
        ("Desired Pay", "40000-45000 GBP annual"), ("Expected salary", "40000-45000 GBP annual")]


def test_desired_pay_with_no_salary_record_for_the_jobs_country_still_stops():
    profile = _profile(salary=[{"country": "IN", "min": 2000000, "max": 2500000, "currency": "INR",
                                "period": "annual"}])
    answered, missing = _one(profile, "Desired Pay")
    assert answered is None and missing["required"] is True
    assert "salary.GB is not in Memory" in missing["why"]


@pytest.mark.parametrize("question", ["Desired pay (USD)", "Expected salary in EUR", "Desired compensation ($)"])
def test_desired_pay_asked_in_another_currency_still_stops(question):
    answered, missing = _one(_profile(), question)
    assert answered is None, answered
    assert "never converts" in missing["why"]


def test_desired_pay_in_the_saved_currency_is_answered():
    answered, missing = _one(_profile(), "Desired pay (GBP, £)")
    assert missing is None and answered["value"] == "40000-45000 GBP annual"
