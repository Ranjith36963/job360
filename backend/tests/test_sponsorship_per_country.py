"""Owner 2026-10-26 - a sponsorship / right-to-work answer belongs to ONE country.

On production the Palantir (London, UK) form was filled "require sponsorship: Yes" from an approved
answer saved for Cadence's US H1B question (topic ``sponsorship.cadence``, no country), while the
user's UK record says no sponsorship is needed. A saved answer whose topic starts ``sponsorship.`` or
``right_to_work.`` now matches only a job in the country its topic names; ``remote`` / ``any`` mean
every country; naming no country, it matches only its own (company-scoped) employer.
Helpers are defined here, never imported.
"""
from __future__ import annotations

from src.services.applications import kit
from src.services.profile.answer_match import find_answer, fits_country

H1B = "Will you now, or in the future, require sponsorship for employment visa status (e.g. H1B visa status)?"
SAVED = [
    {"question": H1B, "answer": "Yes", "approved": True, "topic": "sponsorship.cadence"},
    {"question": "Visa sponsorship (UK)", "answer": "No", "approved": True, "topic": "sponsorship.GB.yes_no"},
    {"question": "Sponsorship for a remote job?", "answer": "No", "approved": True, "topic": "sponsorship.remote.yes_no"},
    {"question": "Notice period", "answer": "Immediately", "approved": True, "topic": "notice_period.text"},
]


def test_a_us_yes_never_answers_a_uk_form():
    assert find_answer(SAVED, question=H1B, country="GB") is None
    assert find_answer(SAVED, topic="sponsorship.GB.yes_no", country="GB")["answer"] == "No"
    assert find_answer(SAVED, topic="sponsorship.GB.yes_no", country="US") is None
    assert find_answer(SAVED, topic="sponsorship.remote.yes_no", country="DE")["answer"] == "No"
    assert find_answer(SAVED, topic="notice_period.text")["answer"] == "Immediately"  # other topics unchanged


def test_one_employers_own_wording_still_answers_that_employer():
    scoped = {**SAVED[0], "company": "Cadence"}
    assert fits_country(scoped, "US") and not fits_country(SAVED[0], "US")


def test_check_form_takes_the_uk_answer_for_a_uk_job():
    out = kit.form_check({}, [{"question": H1B}, {"question": "x", "key": "sponsorship.GB.yes_no"}], SAVED, None,
                         None, country="GB")
    assert [m["question"] for m in out["missing"]] == [H1B]
    assert [a["value"] for a in out["answered"]] == ["No"]
