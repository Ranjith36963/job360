"""The ONE definition of what a matched job says — shared by dashboard and email.

Why this module exists
----------------------
"Which number is this job's score?" was answered in two places that did not
agree by construction, only by luck:

* **SQL** (``services/feed.py:90,104``) ranks the feed by
  ``COALESCE(llm_fit_score, score)`` — the judge's score whenever it is present.
* **React** (``frontend/src/components/jobs/JobCard.tsx:98-99``) shows the judge
  score only when ``llm_fit_score != null AND llm_verdict != null``.

Those are different predicates. They agree today only because
``llm_matcher.save_verdict`` writes all three judge columns in one UPDATE and
``clear_user_verdicts`` nulls all three together — so the "score without a
verdict" state is not currently reachable *from the writer*. That is
parity-by-coincidence. Adding a third copy in a Python email builder is how
coincidence becomes a bug, so there is exactly one implementation and both
callers use it.

The rule these tests pin: **the number shown is the number ranked by.** A badge
that disagrees with the sort order is worse than no badge — it teaches the user
the ranking is arbitrary.
"""
from __future__ import annotations

import pytest

from src.services.delivery.decision_card import (
    DecisionCard,
    build_decision_card,
    resolve_primary_score,
)

# ---------------------------------------------------------------------------
# resolve_primary_score — the shared predicate
# ---------------------------------------------------------------------------

def test_unjudged_row_uses_keyword_score():
    """No judge verdict yet: the keyword score is the primary score.

    This is the common case. The judge is asynchronous, so at the moment a
    digest is built most rows have never been judged.
    """
    score, is_judged = resolve_primary_score(
        feed_score=62, llm_fit_score=None, llm_verdict=None
    )
    assert score == 62
    assert is_judged is False


def test_judged_row_uses_judge_score():
    score, is_judged = resolve_primary_score(
        feed_score=62, llm_fit_score=81, llm_verdict="strong"
    )
    assert score == 81
    assert is_judged is True


def test_fit_score_without_verdict_is_not_treated_as_judged():
    """The disagreement between SQL and React, resolved deliberately.

    SQL's COALESCE would rank this row by 81. React would badge it 62. We pick
    React's stricter rule — a score with no verdict has no *words* to show, and
    an email that prints a number it cannot explain is exactly the unexplained
    ranking this product exists to avoid. The trade is that such a row sorts
    higher than it displays; that is acceptable and, per the module docstring,
    not currently reachable from the writer.
    """
    score, is_judged = resolve_primary_score(
        feed_score=62, llm_fit_score=81, llm_verdict=None
    )
    assert score == 62
    assert is_judged is False


def test_verdict_without_fit_score_is_not_treated_as_judged():
    score, is_judged = resolve_primary_score(
        feed_score=62, llm_fit_score=None, llm_verdict="strong"
    )
    assert score == 62
    assert is_judged is False


def test_blank_verdict_string_is_not_a_verdict():
    """An empty or whitespace verdict is absence, not a value.

    A row whose verdict is ``""`` would pass a naive ``is not None`` check and
    render a badge reading "AI: ". Rule #29's habit applied to delivery: an
    empty field means "nothing to say", never a blank thing said.
    """
    for blank in ("", "   "):
        score, is_judged = resolve_primary_score(
            feed_score=62, llm_fit_score=81, llm_verdict=blank
        )
        assert score == 62, f"blank verdict {blank!r} should not promote the score"
        assert is_judged is False


def test_missing_feed_score_is_zero_not_none():
    """A NULL feed score must not propagate into arithmetic or a template.

    ``user_feed.score`` is NOT NULL in practice, but the digest query joins and
    a defensive default here is cheaper than a ``None`` reaching an f-string as
    the literal text "None".
    """
    score, is_judged = resolve_primary_score(
        feed_score=None, llm_fit_score=None, llm_verdict=None
    )
    assert score == 0
    assert is_judged is False


@pytest.mark.parametrize(
    "fit_score,expected",
    [(0, 0), (100, 100)],
)
def test_boundary_judge_scores_are_honoured(fit_score, expected):
    """A judge score of 0 is a real verdict, not a missing one.

    ``COALESCE`` and ``or`` differ exactly here: ``llm_fit_score or score``
    would silently fall back to the keyword score for a judged-as-zero job,
    hiding the judge's strongest possible "no".
    """
    score, is_judged = resolve_primary_score(
        feed_score=55, llm_fit_score=fit_score, llm_verdict="weak"
    )
    assert score == expected
    assert is_judged is True


# ---------------------------------------------------------------------------
# build_decision_card — the row -> card mapping
# ---------------------------------------------------------------------------

def _row(**overrides):
    """A feed row shaped like the digest query returns."""
    base = {
        "job_id": 147,
        "title": "Senior Python Engineer",
        "company": "Acme Ltd",
        "location": "London",
        "score": 62,
        "llm_fit_score": 81,
        "llm_verdict": "strong",
        "llm_reason": "4 years of Postgres matches their core requirement.",
        "salary": "£70,000 - £85,000",
        "apply_url": "https://boards.example.com/jobs/147",
    }
    base.update(overrides)
    return base


def test_card_carries_the_judge_words_when_judged():
    card = build_decision_card(_row(), site_base_url="https://job360.uk")
    assert isinstance(card, DecisionCard)
    assert card.primary_score == 81
    assert card.is_judged is True
    assert card.verdict == "strong"
    assert card.reason.startswith("4 years of Postgres")
    # The keyword score is kept as a secondary signal, exactly as the dashboard
    # keeps its "kw 62" pill — not dropped just because the judge spoke.
    assert card.keyword_score == 62


def test_card_url_points_at_our_page_never_the_raw_apply_url():
    """The click must land on Job360, not on the employer's board.

    Three reasons, all load-bearing:
      1. Attribution — a raw ``apply_url`` click is invisible to us, and click
         data is the only honest signal for the send-volume budget.
      2. Staleness — ``apply_url`` rots. Our page can say "this closed" instead
         of dumping the user on a 404 with our name attached to it.
      3. Trust — a job-alert email whose links point at unfamiliar third-party
         domains is indistinguishable from the scam mail these users are
         drowning in.
    """
    card = build_decision_card(_row(), site_base_url="https://job360.uk")
    assert card.url == "https://job360.uk/jobs/147"
    assert "boards.example.com" not in card.url


def test_card_url_tolerates_a_trailing_slash_on_the_base():
    card = build_decision_card(_row(), site_base_url="https://job360.uk/")
    assert card.url == "https://job360.uk/jobs/147"


def test_unjudged_card_has_no_verdict_words_and_says_so():
    """An unjudged card must not fabricate a reason.

    The email's whole trust claim is "we tell you why". When we do not know
    why, the honest output is no reason at all — never a generated-sounding
    filler line.
    """
    card = build_decision_card(
        _row(llm_fit_score=None, llm_verdict=None, llm_reason=None),
        site_base_url="https://job360.uk",
    )
    assert card.is_judged is False
    assert card.primary_score == 62
    assert card.verdict is None
    assert card.reason is None


def test_missing_optional_fields_do_not_become_the_string_none():
    """Absent salary/location must render as absence, not as "None".

    The old payload builders used f-strings straight over row values, so a NULL
    company would have printed the four characters ``None`` into a user's
    inbox.
    """
    card = build_decision_card(
        _row(salary=None, location=None), site_base_url="https://job360.uk"
    )
    assert card.salary is None
    assert card.location is None
    assert "None" not in (card.salary or "")
    assert "None" not in (card.location or "")
