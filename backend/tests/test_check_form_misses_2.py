"""check_form misses, round 2 (PC browser on real forms after #1527, 2026-11-06).

1. Dial code from a number with no separator ("+447442564327"): the saved residence country settles the split.
2. The employer sponsorship wording may shorten the job company to a leading prefix ("Zema Global" for
   "Zema Global Data Corporation") when every dropped word is corporate filler.
3. Plain email labels ("Email address", "E-mail", "Your email") take contact.email; someone else's is a gap.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from src.services.applications import kit


def _profile(needs_sponsorship: bool = False, **contact: Any) -> Any:
    from src.services.profile.models import CVData, UserInfo, UserPreferences, UserProfile

    saved = {"legal_first_name": "Ada", "legal_last_name": "Lovelace", "phone": "+447442564327",
             "email": "ada@example.com", **contact}
    return UserProfile(
        user_info=UserInfo(contact={k: v for k, v in saved.items() if v is not None}, right_to_work={"countries": [
            {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": needs_sponsorship}]}),
        preferences=UserPreferences(salary_by_country=[
            {"country": "GB", "min": 40000, "max": 45000, "currency": "GBP", "period": "year"}]),
        cv_data=CVData(),
    )


def _one(profile: Any, question: str, company: str = "Zema Global Data Corporation", **extra: Any
         ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    answers, _missing = kit.compute_answers(profile, [], "GB", None)
    out = kit.form_check(answers, [{"question": question, **extra}], records=profile.user_info.answers,
                         company_key=kit.normalized_company_key(company), country="GB")
    return (out["answered"][0] if out["answered"] else None), (out["missing"][0] if out["missing"] else None)


# ── 1. Dial code from the residence country ──

@pytest.mark.parametrize("phone,residence,expected", [
    ("+447442564327", "GB", "+44"), ("+447442564327", "United Kingdom", "+44"), ("+14155550100", "US", "+1"),
    ("+44 7700 900123", "GB", "+44"),
])
def test_the_residence_country_settles_the_dial_code(phone, residence, expected):
    answered, missing = _one(_profile(phone=phone, residence_country=residence), "Dial code")
    assert missing is None, missing
    assert answered["value"] == expected


@pytest.mark.parametrize("phone,residence", [
    ("+447442564327", "IN"), ("+4917612345678", "GB"), ("+44", "GB"), ("447442564327", "GB"),
    ("+447442564327", None),
])
def test_a_number_not_starting_with_the_residence_code_is_a_gap(phone, residence):
    answered, missing = _one(_profile(phone=phone, residence_country=residence), "Dial code")
    assert answered is None and missing is not None


# ── 2. The job company shortened to a leading prefix of filler words ──

def _employer(company: str, example: str = "") -> str:
    return (f"Will you now or in the future require sponsorship from {company} to obtain, maintain or extend "
            f"employment based work authorization{' (' + example + ')' if example else ''}?")


ZEMA_LIVE = ("Will you now or in the future require sponsorship from Zema Global to obtain, maintain or extend "
             "employment based work authorization (for example, H-1B or another employment visa status)?")


def test_the_live_zema_wording_is_answered():
    for needs, expected in ((False, "No"), (True, "Yes")):
        answered, missing = _one(_profile(needs), ZEMA_LIVE)
        assert missing is None, missing
        assert answered["value"] == expected


@pytest.mark.parametrize("said,job", [
    ("Zema Global", "Zema Global Data Corporation"), ("Zema", "Zema Global Data Corporation"),
    ("Zema Global Data Corporation", "Zema Global Data Corporation"), ("Acme Robotics", "Acme Robotics Solutions"),
])
def test_a_leading_prefix_dropping_only_filler_is_the_jobs_company(said, job):
    answered, missing = _one(_profile(True), _employer(said, "for example, H-1B"), company=job)
    assert missing is None, (said, job, missing)
    assert answered["value"] == "Yes"


@pytest.mark.parametrize("said,job", [
    ("Global", "Zema Global Data Corporation"), ("Acme", "Acme Robotics Solutions"),
    ("Zema Global Data", "Zema Global"), ("Zem", "Zem Data Labs"), ("Zema Data", "Zema Global Data"),
    ("Initech", "Zema Global Data Corporation"),
])
def test_any_other_shortening_is_a_gap(said, job):
    for needs in (False, True):
        answered, missing = _one(_profile(needs), _employer(said), company=job)
        assert answered is None, (said, job, needs, answered)
        assert missing is not None


@pytest.mark.parametrize("example", [
    "for example, another employment visa status", "for example, H-1B or another visa status",
    "for example, H-1B or another employment visa status or none", "for example, H-1B or any employment visa status",
])
def test_only_the_exact_another_visa_phrase_is_allowed(example):
    answered, missing = _one(_profile(True), _employer("Zema Global", example))
    assert answered is None, (example, answered)
    assert missing is not None


# ── 3. Email ──

@pytest.mark.parametrize("question", ["Email address", "Email", "E-mail", "Your email", "Email Address*",
                                      "E-mail address"])
def test_plain_email_labels_take_the_saved_email(question):
    answered, missing = _one(_profile(), question)
    assert missing is None, missing
    assert (answered["key"], answered["value"]) == ("contact.email", "ada@example.com")


@pytest.mark.parametrize("question", ["Referee email", "Manager email", "Company email"])
def test_someone_elses_email_is_a_gap(question):
    for extra in ({}, {"key": "contact.email"}):
        answered, missing = _one(_profile(), question, **extra)
        assert answered is None, (question, extra, answered)
        assert missing is not None
