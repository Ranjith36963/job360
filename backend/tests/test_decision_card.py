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

import json

import pytest

from src.services.delivery.decision_card import (
    DecisionCard,
    build_decision_card,
    format_salary_range,
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


def _enr(min_v=None, max_v=None, currency="GBP", frequency="annual"):
    """A ``job_enrichment.salary`` blob, the shape the dashboard reads."""
    return json.dumps(
        {"min": min_v, "max": max_v, "currency": currency, "frequency": frequency}
    )


def test_salary_is_formatted_exactly_as_the_dashboard_formats_it():
    """Parity is about the WORDS, not just the numbers.

    ``formatSalaryRange`` in JobCard.tsx renders ``£70k–£85k``. An email saying
    "£70,000 - £85,000" for the same job is a visible discrepancy to the only
    person whose opinion counts, even though both are "correct".
    """
    card = build_decision_card(
        _row(salary=None, enr_salary=_enr(70000, 85000)),
        site_base_url="https://job360.uk",
    )
    assert card.salary == "£70k–£85k"


def test_salary_open_ended_and_sub_thousand_forms_match_the_dashboard():
    def salary_for(lo, hi):
        return build_decision_card(
            _row(salary=None, enr_salary=_enr(lo, hi)),
            site_base_url="https://job360.uk",
        ).salary

    # normalize_salary mirrors a single-sided band, so one bound produces an
    # equal min/max — which the dashboard renders as one number, not "£70k+".
    assert salary_for(70000, 70000) == "£70k", "an equal range is one number, not a range"
    assert salary_for(950, 950) == "£950", "below £1k the dashboard does not use 'k'"
    assert salary_for(None, None) is None


def test_open_ended_salary_branches_match_the_dashboard():
    """The ``£70k+`` and ``up to £85k`` branches, tested where they are reachable.

    ``build_decision_card`` cannot produce a one-sided band: ``normalize_salary``
    mirrors a single bound into an equal min/max. So the two open-ended branches
    in ``format_salary_range`` were never exercised by the end-to-end test above
    — its name promised coverage the assertions could not deliver. Testing the
    formatter directly is the only way to reach them, and they still have to
    match ``formatSalaryRange`` in JobCard.tsx exactly.
    """
    assert format_salary_range(70000, None) == "£70k+"
    assert format_salary_range(None, 85000) == "up to £85k"
    assert format_salary_range(None, None) is None


def test_thousands_rounding_matches_javascript_not_python():
    """Half-up, like ``Math.round`` — not Python's round-half-to-even.

    ``round(70.5)`` is 70 in Python and 71 in JavaScript. The dashboard uses
    ``Math.round``, so a £70,500 salary would read £70k in the email and £71k on
    the screen. This is the single assertion that pins the two together; it fails
    if anyone "simplifies" the formatter back to ``round()``.
    """
    assert format_salary_range(70500, 70500) == "£71k"
    assert format_salary_range(71500, 71500) == "£72k"
    # And a value that is NOT a .5 boundary must be unaffected by the change.
    assert format_salary_range(70400, 70400) == "£70k"


def test_unknown_currency_yields_no_salary_rather_than_a_wrong_one():
    """An unconvertible currency must produce silence, not a guess.

    The dashboard drops the salary entirely when ``is_known_currency`` fails
    (``api/routes/jobs.py:73-75``). Printing an unconverted foreign number next
    to a UK-only catalog would be worse than printing nothing.
    """
    card = build_decision_card(
        _row(salary=None, enr_salary=_enr(70000, 85000, currency="ZZZ")),
        site_base_url="https://job360.uk",
    )
    assert card.salary is None


def test_malformed_enrichment_blob_does_not_raise():
    """Enrichment JSON is LLM-produced. It must never 500 a send."""
    for junk in ("not json", "[]", "", None, "{"):
        card = build_decision_card(
            _row(salary=None, enr_salary=junk), site_base_url="https://job360.uk"
        )
        assert card.salary is None, f"junk blob {junk!r} should yield no salary"


def test_structured_salary_wins_over_a_legacy_string():
    """When both exist the normalised blob is the truth.

    The legacy free-text ``salary`` column is whatever a source happened to
    scrape; the enrichment blob is normalised to annual GBP by the same parser
    the dashboard uses. Preferring the string would reintroduce the per-source
    formatting chaos the normalisation removed.
    """
    card = build_decision_card(
        _row(salary="70k-85k per annum", enr_salary=_enr(70000, 85000)),
        site_base_url="https://job360.uk",
    )
    assert card.salary == "£70k–£85k"


def test_absent_salary_and_location_are_none_not_a_string():
    """Absent optional fields must be ``None`` so the renderer omits the line.

    The two follow-up assertions this test used to carry —
    ``"None" not in (card.salary or "")`` — were tautologies: the lines above
    already proved the value was ``None``, so the expression reduced to
    ``"None" not in ""`` and no implementation change could ever turn it red.
    Removed rather than reworded.
    """
    card = build_decision_card(
        _row(salary=None, enr_salary=None, location=None),
        site_base_url="https://job360.uk",
    )
    assert card.salary is None
    assert card.location is None


def test_missing_required_text_falls_back_instead_of_printing_none():
    """A NULL title/company must become readable words, not the string "None".

    This is what the previous test's docstring *claimed* to cover and did not:
    it never set ``company=None``, so the ``"Unknown company"`` fallback had no
    coverage at all. The old payload builders f-string'd row values straight
    into the body, so a NULL company really would have put the four characters
    ``None`` in front of a user.
    """
    card = build_decision_card(
        _row(title=None, company=None), site_base_url="https://job360.uk"
    )
    assert card.title == "Untitled role"
    assert card.company == "Unknown company"
    assert "None" not in card.title
    assert "None" not in card.company


def test_blank_and_whitespace_text_is_treated_as_absent():
    """``""`` and ``"   "`` are absence, not content.

    A whitespace-only company would otherwise pass a bare ``is not None`` check
    and render as an empty gap in the email where a name should be.
    """
    card = build_decision_card(
        _row(title="   ", company="", location="  ", salary=None, enr_salary=None),
        site_base_url="https://job360.uk",
    )
    assert card.title == "Untitled role"
    assert card.company == "Unknown company"
    assert card.location is None
