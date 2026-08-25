"""What the daily email actually says.

The old digest body was one line per job: ``• Title @ Company — apply_url``
(``workers/tasks.py:1034``). It shipped a link and nothing else, while the
dashboard showed a score, the judge's verdict, its written reason and a salary.

These tests pin the replacement. They are mostly *product* assertions, because
the failure mode here is not a crash — it is an email that technically sends
and that a real person deletes, or worse, reports as spam.

The trust argument, stated once:
    95% of job seekers have met a fake job offer and email is the single most
    common channel for them. A bare list of links from an unfamiliar sender is
    shaped exactly like that fraud. The one thing a scammer structurally cannot
    write is *why this specific job matches your specific CV* — so the reason
    line is not decoration, it is the proof of legitimacy. Tests below treat it
    as load-bearing.
"""
from __future__ import annotations

from src.services.delivery.decision_card import DecisionCard
from src.services.delivery.email_body import (
    render_digest_subject,
    render_digest_text,
)


def _card(**overrides) -> DecisionCard:
    base = dict(
        job_id=147,
        title="Senior Python Engineer",
        company="Acme Ltd",
        url="https://job360.uk/jobs/147",
        primary_score=81,
        is_judged=True,
        keyword_score=62,
        verdict="strong",
        reason="Four years of Postgres matches their core requirement.",
        location="London",
        salary="£70,000 - £85,000",
    )
    base.update(overrides)
    return DecisionCard(**base)


# ---------------------------------------------------------------------------
# Subject line
# ---------------------------------------------------------------------------

def test_subject_states_the_count_plainly():
    """No hype, no urgency, no emoji, no money in the subject.

    "£85k role URGENT" is scam grammar. The subject says what arrived and
    nothing more — that is what a real service sounds like.
    """
    subject = render_digest_subject(shown=2, considered=41)
    assert "2" in subject
    for scammy in ("!", "URGENT", "urgent", "£", "$", "🔥", "ACT NOW"):
        assert scammy not in subject


def test_empty_day_subject_says_nothing_rather_than_pretending():
    """An empty day is "nothing today", never "0 matches".

    Note the assertion is on a standalone zero, not on the character: the brand
    name "Job360" contains one, and a naive ``"0" not in subject`` fails on the
    product's own name.
    """
    subject = render_digest_subject(shown=0, considered=41)
    assert " 0 " not in f" {subject} ", "an empty day must not be reported as a count of 0"
    assert "nothing" in subject.lower()


def test_singular_and_plural_are_both_grammatical():
    assert "1 job" in render_digest_subject(shown=1, considered=10)
    assert "2 jobs" in render_digest_subject(shown=2, considered=10)


# ---------------------------------------------------------------------------
# Body — the verdict comes first
# ---------------------------------------------------------------------------

def test_body_leads_with_the_verdict_not_with_the_list():
    """The first line must answer "is this worth my evening?".

    Choice-overload research is unambiguous: a list is a decision the reader
    has to make, and a long one is a decision they skip. Leading with the count
    turns the email from a task into an answer.
    """
    body = render_digest_text([_card()], considered=41, dropped_reasons=[])
    first_line = body.strip().splitlines()[0]
    assert "1 job" in first_line
    assert "https://" not in first_line, "the first line is a verdict, not a link"


def test_body_says_out_loud_what_was_thrown_away():
    """The honest "no" — the single most important line in the email.

    Unexplained silence is what psychologists call *ambiguous rejection*, and it
    is measurably more damaging to a job seeker than a clear refusal. Saying
    "we checked 41 and dropped 38, here is why" converts silence into a reason.
    It also makes our filtering legible, so a user who disagrees can go and
    change their preferences instead of quietly concluding we are useless.
    """
    body = render_digest_text(
        [_card()],
        considered=41,
        dropped_reasons=["too junior", "wrong location", "no visa sponsorship"],
    )
    assert "41" in body
    assert "40" in body, "considered minus shown must be stated, not left as maths"
    assert "too junior" in body
    assert "no visa sponsorship" in body


def test_dropped_line_is_omitted_when_nothing_was_dropped():
    """Do not print "dropped 0" — a zero stated aloud reads as a system burping."""
    body = render_digest_text([_card()], considered=1, dropped_reasons=[])
    assert "dropped 0" not in body.lower()


# ---------------------------------------------------------------------------
# Body — each card carries what the dashboard carries
# ---------------------------------------------------------------------------

def test_judged_card_shows_score_verdict_and_reason():
    body = render_digest_text([_card()], considered=1, dropped_reasons=[])
    assert "Senior Python Engineer" in body
    assert "Acme Ltd" in body
    assert "81" in body
    assert "strong" in body
    assert "Four years of Postgres" in body
    assert "£70,000 - £85,000" in body
    assert "London" in body


def test_card_links_to_our_page_only():
    """No third-party domain appears in the email at all.

    Every link is job360.uk. This is the attribution signal AND the anti-scam
    signal: an email whose links point at unfamiliar hosts is the shape of the
    fraud these readers are trained to fear.
    """
    body = render_digest_text([_card()], considered=1, dropped_reasons=[])
    assert "https://job360.uk/jobs/147" in body
    assert "boards.example.com" not in body


def test_unjudged_card_does_not_invent_a_reason():
    """When the judge has not run, we show the keyword score and say nothing else.

    Filling the gap with a generated-sounding sentence would poison the one
    thing that makes the email trustworthy.

    ``primary_score`` and ``keyword_score`` are deliberately DIFFERENT numbers
    here. The fixture used to set both to 62, which made ``assert "62" in body``
    unable to fail on the thing it claimed: a renderer that dropped
    ``primary_score`` and printed only ``keyword_score`` would still have
    passed. 41 appears nowhere in the rendered output, so the assertion below
    can only hold if the primary score is what was rendered.
    """
    card = _card(
        is_judged=False, verdict=None, reason=None, primary_score=62, keyword_score=41
    )
    body = render_digest_text([card], considered=1, dropped_reasons=[])
    assert "62" in body, "the primary score must be the number shown"
    assert "41" not in body, (
        "the keyword score must NOT be rendered for an unjudged card — it IS "
        "the primary score there, and printing both would show one job two scores"
    )
    assert "strong" not in body
    assert "None" not in body


def test_absent_salary_and_location_are_simply_missing():
    card = _card(salary=None, location=None)
    body = render_digest_text([card], considered=1, dropped_reasons=[])
    assert "None" not in body
    assert "Senior Python Engineer" in body


# ---------------------------------------------------------------------------
# Body — the empty day
# ---------------------------------------------------------------------------

def test_empty_day_still_produces_a_real_email():
    """"Nothing good today" is a feature, not a failure.

    Two reasons it ships. First, it removes the dread of an unopened inbox —
    the reader learns that silence from us means silence, not that something is
    broken. Second, it is the structural guard against the send-volume budget
    starving a user into total silence: there is always one email, so a user
    can always see the dial and turn it.
    """
    body = render_digest_text([], considered=41, dropped_reasons=["too junior"])
    assert body.strip(), "an empty day must still have a body"
    assert "nothing" in body.lower()
    assert "41" in body, "still show the work we did on their behalf"


def test_body_never_shouts():
    """No exclamation marks, no all-caps words, anywhere.

    Enthusiasm punctuation is the register of the mail this reader is
    suspicious of. Quiet is credible.
    """
    body = render_digest_text([_card()], considered=41, dropped_reasons=["too junior"])
    assert "!" not in body
    shouted = [
        w for w in body.split()
        if len(w) > 3 and w.isalpha() and w.isupper()
    ]
    assert shouted == [], f"all-caps words found: {shouted}"
