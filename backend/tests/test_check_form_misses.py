"""check_form misses Memory already held (PC browser, live forms 2026-11-06, after #1525).

1. Plain name labels ("First name", "Surname") take the saved legal names; someone else's name is a gap.
2. Plain phone labels take contact.phone; "Country code" takes only the saved number's own "+44".
3. Greenhouse's standard sponsorship wordings are read as-is (Yes = needs sponsorship).
4. "Desired Base Salary (... Excluding Bonus, Commission, and Benefits)" is the base range; "including" is a gap.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from src.services.applications import kit

PHONE = "+44 7700 900123"


def _profile(needs_sponsorship: bool = False, **contact: Any) -> Any:
    from src.services.profile.models import CVData, UserInfo, UserPreferences, UserProfile

    saved = {"legal_first_name": "Ada", "legal_last_name": "Lovelace", "phone": PHONE, **contact}
    return UserProfile(
        user_info=UserInfo(contact={k: v for k, v in saved.items() if v is not None}, right_to_work={"countries": [
            {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": needs_sponsorship}]}),
        preferences=UserPreferences(salary_by_country=[
            {"country": "GB", "min": 40000, "max": 45000, "currency": "GBP", "period": "year"}]),
        cv_data=CVData(),
    )


JOB_COMPANY = "Acme Robotics"  # THIS application's job company (check_form passes its rule-1 key)


def _one(profile: Any, question: str, **extra: Any) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    answers, _missing = kit.compute_answers(profile, [], "GB", None)
    out = kit.form_check(answers, [{"question": question, **extra}], records=profile.user_info.answers,
                         company_key=kit.normalized_company_key(JOB_COMPANY), country="GB")
    return (out["answered"][0] if out["answered"] else None), (out["missing"][0] if out["missing"] else None)


# ── 1. Names ──

@pytest.mark.parametrize("question,key,value", [
    ("First name", "contact.legal_first_name", "Ada"), ("Last name", "contact.legal_last_name", "Lovelace"),
    ("Given name", "contact.legal_first_name", "Ada"), ("Family name", "contact.legal_last_name", "Lovelace"),
    ("Surname", "contact.legal_last_name", "Lovelace"), ("Legal first name", "contact.legal_first_name", "Ada"),
    ("Legal last name", "contact.legal_last_name", "Lovelace"), ("First Name*", "contact.legal_first_name", "Ada"),
])
def test_plain_name_labels_take_the_saved_legal_names(question, key, value):
    answered, missing = _one(_profile(), question)
    assert missing is None, missing
    assert (answered["key"], answered["value"]) == (key, value)


def test_preferred_first_name_only_from_a_saved_preferred_name():
    answered, missing = _one(_profile(preferred_name="Addie"), "Preferred first name")
    assert missing is None and (answered["key"], answered["value"]) == ("contact.preferred_name", "Addie")
    answered, missing = _one(_profile(), "Preferred first name")
    assert answered is None and missing is not None  # never the legal name in its place


@pytest.mark.parametrize("question", ["Referrer first name", "Emergency contact first name", "First and last name",
                                      "Manager's surname", "Middle name"])
def test_someone_elses_name_is_a_gap(question):
    for extra in ({}, {"key": "contact.legal_first_name"}, {"key": "contact.legal_last_name"}):
        answered, missing = _one(_profile(), question, **extra)
        assert answered is None, (question, extra, answered)
        assert missing is not None


# ── 2. Phone and dial code ──

@pytest.mark.parametrize("question", ["Phone", "Phone number", "Mobile", "Mobile number", "Telephone"])
def test_plain_phone_labels_take_the_saved_number(question):
    answered, missing = _one(_profile(), question)
    assert missing is None, missing
    assert (answered["key"], answered["value"]) == ("contact.phone", PHONE)


@pytest.mark.parametrize("question", ["Dial code", "Phone country code", "Country calling code", "Calling code",
                                      "Dialing code", "Country dial code"])
def test_dial_code_is_the_saved_numbers_own_prefix(question):
    answered, missing = _one(_profile(), question)
    assert missing is None, missing
    assert answered["value"] == "+44"


def test_a_bare_country_code_is_a_gap_it_can_be_an_address_field():
    answered, missing = _one(_profile(), "Country code")
    assert answered is None and missing is not None


@pytest.mark.parametrize("phone", ["07700 900123", "+447700900123", "+4 4 7700 900123", "+0 123 456"])
def test_a_dial_code_the_saved_number_does_not_show_is_a_gap(phone):
    answered, missing = _one(_profile(phone=phone), "Dial code")
    assert answered is None and missing is not None


def test_the_dial_code_must_be_the_residence_countrys_own():
    answered, missing = _one(_profile(residence_country="GB"), "Dial code")
    assert missing is None and answered["value"] == "+44"
    answered, missing = _one(_profile(residence_country="IN"), "Dial code")
    assert answered is None and missing is not None


@pytest.mark.parametrize("question", ["Emergency contact phone", "Referee phone number", "Manager's mobile"])
def test_someone_elses_phone_is_a_gap(question):
    for extra in ({}, {"key": "contact.phone"}):
        answered, missing = _one(_profile(), question, **extra)
        assert answered is None, (question, extra, answered)
        assert missing is not None


# ── 3. Standard sponsorship wordings (Yes = needs sponsorship) ──

GREENHOUSE = "Will you now or in the future require visa sponsorship for employment?"
EMPLOYER = ("Will you now or in the future require sponsorship from Acme Robotics Inc. to obtain, maintain or "
            "extend employment based work authorization (for example, H-1B, TN or E-3 visa status)?")


@pytest.mark.parametrize("question", [
    GREENHOUSE, EMPLOYER,
    "Will you now or in the future require sponsorship from Acme Robotics to obtain, maintain or extend "
    "employment-based work authorization?",
])
def test_standard_sponsorship_wordings_are_read_as_is(question):
    for needs, expected in ((False, "No"), (True, "Yes")):
        answered, missing = _one(_profile(needs), question)
        assert missing is None, (question, missing)
        assert answered["value"] == expected and not answered.get("polarity")


@pytest.mark.parametrize("question", [
    "Will you now or in the future require sponsorship from Acme Robotics to obtain, maintain or extend employment "
    "based work authorization (for example, H-1B) or not?",
    "Will you now or in the future require sponsorship from Acme Robotics to obtain, maintain or extend employment "
    "based work authorization (if not, say no)?",
    "Will you not require sponsorship from Acme Robotics to obtain, maintain or extend employment based work "
    "authorization?",
])
def test_the_employer_wording_off_its_exact_shape_is_a_gap(question):
    for needs in (False, True):
        answered, missing = _one(_profile(needs), question)
        assert answered is None, (question, needs, answered)
        assert missing is not None


def _employer(company: str = JOB_COMPANY, example: str = "") -> str:
    return (f"Will you now or in the future require sponsorship from {company} to obtain, maintain or extend "
            f"employment based work authorization{' (' + example + ')' if example else ''}?")


@pytest.mark.parametrize("example", [
    "for example, H-1B", "for example, H1B, O-1 or TN status", "e.g. H-1B, OPT, CPT, STEM OPT, EAD, L1 etc.",
    "such as H-1B / TN visa", "for example, H-1B …", "for example, H-1B1, H-2B, L-1A, L-1B, E-2 or F-1",
    "for example, J-1, TPS, DACA, GC or green card status", "for example, H-1B, TN or E-3 visa status",
])
def test_visa_examples_are_noise(example):
    answered, missing = _one(_profile(True), _employer(example=example))
    assert missing is None, (example, missing)
    assert answered["value"] == "Yes"


@pytest.mark.parametrize("example", [
    "for example, none", "for example, n/a", "for example, citizens are exempt", "for example, you don't",
    "for example, answer N", "including those who are exempt", "such as citizens who already hold the right to work",
    "for example, NO", "for example, NA", "for example, NOT", "for example, NONE", "for example, NIL",
    "for example, YES", "for example, NOPE", "for example, USA", "for example, NO H-1B", "for example, H-1B, USA",
    "for example, Q-1", "for example, green", "for example, or",
])
def test_an_example_that_is_no_visa_name_is_a_gap(example):
    for needs in (False, True):
        answered, missing = _one(_profile(needs), _employer(example=example))
        assert answered is None, (example, needs, answered)
        assert missing is not None


@pytest.mark.parametrize("company", ["Acme Robotics", "Acme Robotics Inc.", "acme robotics", "ACME ROBOTICS LIMITED",
                                     "Acme Robotics GmbH", "Acme Robotics Corp", "Acme Robotics plc",
                                     "Acme Robotics, LLC"])
def test_the_jobs_own_company_is_noise(company):
    answered, missing = _one(_profile(False), _employer(company))
    assert missing is None, (company, missing)
    assert answered["value"] == "No"


@pytest.mark.parametrize("company", [
    "anyone except Acme", "nobody", "none of Acme", "Acme except for citizens", "Nobody", "Anyone Except Acme",
    "Neither Corp", "Nothing Ltd", "Cannot Ltd", "Ineligible Ltd", "Waived Ltd", "Acme Citizens Only", "Acme Doesnt",
    "Acme", "Globex Corporation", "Initech Ltd", "Acme Robotics except for citizens", "anyone except Acme Robotics",
])
def test_any_company_but_the_jobs_own_is_a_gap(company):
    for needs in (False, True):
        answered, missing = _one(_profile(needs), _employer(company))
        assert answered is None, (company, needs, answered)
        assert missing is not None


def test_with_no_job_company_the_employer_wording_is_a_gap():
    profile = _profile(True)
    answers, _missing = kit.compute_answers(profile, [], "GB", None)
    out = kit.form_check(answers, [{"question": _employer()}], records=profile.user_info.answers, country="GB")
    assert out["answered"] == [] and len(out["missing"]) == 1


# ── 4. Base salary excluding bonus ──

@pytest.mark.parametrize("question", [
    "Desired Base Salary (Please Include the Amount and Currency. Excluding Bonus, Commission, and Benefits)",
    "Expected salary excluding bonus", "Expected salary (excl. bonus and equity)",
    "Desired salary not including commission",
])
def test_base_salary_excluding_bonus_is_answered(question):
    answered, missing = _one(_profile(), question)
    assert missing is None, missing
    assert (answered["key"], answered["value"]) == ("salary.GB", "40000-45000 GBP year")


@pytest.mark.parametrize("question", [
    "Salary including bonus", "Expected salary incl. bonus", "Desired salary plus commission",
    "Expected salary with equity", "Expected salary excluding pension",
    "Desired salary excluding bonus, including commission",
])
def test_salary_with_bonus_or_another_exclusion_is_a_gap(question):
    answered, missing = _one(_profile(), question)
    assert answered is None, answered
    assert "ask the user" in missing["why"]
