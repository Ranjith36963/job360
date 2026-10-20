"""Gap audit #14: the form-question regexes know UK wording and legal terms.

`_question_is_sensitive` flags a question for user confirmation; `_is_knockout` makes
form_check list it in `knockouts` so the assistant stops for the user first.
"""
import pytest

from src.services.applications.kit import _is_knockout, _question_is_sensitive, form_check

SENSITIVE = [
    "Are you authorised to work in the UK?",
    "Are you authorized to work in the United States?",
    "Are you legally entitled to work in the UK?",
    "Are you entitled to work in the UK?",
    "Do you have permission to work in the UK?",
    "Do you hold active SC or DV security clearance?",
    "Do you hold a security clearance?",
    "Do you hold SC clearance?",
    "Do you hold DV clearance?",
    "Are you willing to undergo BPSS checks?",
    "Are you willing to undergo vetting?",
    "What is your expected pay / day rate?",
    "What is your day-rate?",
    "What is your expected salary?",
    "What is your notice period?",
    "Are you a US person for ITAR purposes?",
    "Are you subject to export controls?",
    "Are you subject to any export control restrictions?",
    "Are you bound by a non-compete agreement?",
    "Are you bound by a noncompete?",
    "Do you consent to interview recording?",
    "Do you consent to the recording of this interview?",
    "Do you consent to recording the interview?",
    "Have you previously been employed by Acme?",
    "Were you previously employed at Acme Ltd?",
    "Have you previously been employed with us?",
]

KNOCKOUT = [
    "Are you over 18?",
    "Are you at least 18 years old?",
    "Are you over the age of 18?",
    "Are you at least the age of 18?",
    "Do you hold a full UK driving licence?",
    "Do you hold a valid driving license?",
    # Same eligibility fact as the existing "right to work" knockout, in UK wording.
    "Are you authorised to work in the UK?",
    "Are you legally entitled to work in the UK?",
    "Are you aged 18 or over?",
    "Are you 18 years of age or older?",
]

NEITHER = [
    "Describe a challenge you solved",
    "Please describe your experience with Python",
    "What is your name?",
    "Which school did you attend?",
    "Upload your CV",
    "How did you hear about us?",
    "Why do you want to work here?",
    "Please discuss your DVD of work samples",
    "Add a link to your portfolio (e.g. DVD, disc or web)",
    "Do you have an SCSS or Scrum certificate?",
    "Tell us about your record of shipping features",
    "Link to your LinkedIn profile",
    "Do you hold a B. Sc. or an M. Sc?",
    "Are you based in Charleston, SC?",
    "Describe your experience vetting suppliers",
    "We operate in over 180 countries. Why do you want to join us?",
    "Have you led a team of over 18 engineers?",
]


@pytest.mark.parametrize("text", SENSITIVE)
def test_sensitive_uk_wording(text):
    assert _question_is_sensitive(text, None)


@pytest.mark.parametrize("text", KNOCKOUT)
def test_knockout_uk_wording(text):
    assert _is_knockout(text, None)


@pytest.mark.parametrize("text", NEITHER)
def test_ordinary_questions_are_neither(text):
    assert not _question_is_sensitive(text, None)
    assert not _is_knockout(text, None)


def test_clearance_and_notice_period_need_confirmation_but_do_not_stop_the_run():
    for text in ("Do you hold active SC or DV security clearance?", "What is your notice period?"):
        assert _question_is_sensitive(text, None)
        assert not _is_knockout(text, None)


def test_form_check_flags_the_uk_questions():
    result = form_check(
        {},
        [
            {"question": "Are you authorised to work in the UK?"},
            {"question": "Do you hold a full UK driving licence?"},
            {"question": "What is your name?"},
        ],
    )
    by_q = {m["question"]: m for m in result["missing"]}
    assert by_q["Are you authorised to work in the UK?"]["needs_confirmation"] is True
    assert "needs_confirmation" not in by_q["What is your name?"]
    assert [k["question"] for k in result["knockouts"]] == [
        "Are you authorised to work in the UK?",
        "Do you hold a full UK driving licence?",
    ]


@pytest.mark.parametrize("text", ["Do you have a B.Sc in Computer Science?", "Do you hold an M.Sc?"])
def test_a_degree_abbreviation_is_not_sc_clearance(text):
    assert not _question_is_sensitive(text, None)
