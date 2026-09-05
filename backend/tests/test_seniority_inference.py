"""Tests for CV-derived seniority inference (services/profile/seniority.py).

Covers a manager-review regression (2026-08-07): the first cut of this module
walked positions most-recent-first and returned the first title with a tier
word, which let a short recent internship outrank years of sustained senior
work. The fixtures below (anonymised from two real production profiles that
both wrongly inferred "intern") pin the fix.

Slice 5 (#483) dropped the `resolve_experience_level` / `seniority_score`
half of this file: the job scorer they belonged to is deleted. What the
module produces is now profile DATA — a fact read off the CV that the user's
own agent reads — so the tests that survive are the ones about the inference
itself.
"""
from __future__ import annotations

import datetime

from src.services.profile.seniority import infer_experience_level

# Pinned reference "today" so any fixture using an ongoing ("Present") role
# is deterministic regardless of when the suite actually runs.
_TODAY = datetime.date(2026, 1, 1)


def _position(title: str, dates: str, **overrides) -> dict:
    pos = {"company": "Acme", "title": title, "dates": dates, "location": "", "bullets": []}
    pos.update(overrides)
    return pos


# ---------------------------------------------------------------------------
# infer_experience_level — a sustained role's title/duration sets the floor
# ---------------------------------------------------------------------------


def test_title_word_on_a_sustained_role_wins():
    positions = [_position("Senior Backend Engineer", "2021 - Present")]
    assert infer_experience_level(positions, today=_TODAY) == "senior"


def test_graduate_title_on_a_sustained_role_infers_junior_tier():
    # job_signals' seniority_terms.txt maps "graduate" phrases to the JUNIOR
    # tier (there is no separate SeniorityLevel.GRADUATE member) — so the
    # legitimate output is "junior" (what the shared detector returns).
    positions = [_position("Graduate Software Engineer", "2023 - 2024")]
    assert infer_experience_level(positions, today=_TODAY) == "junior"


def test_linkedin_position_shape_is_supported():
    # LinkedIn positions use separate start/end keys instead of one "dates"
    # string — both shapes must resolve through the same parser.
    linkedin_positions = [
        {"title": "Staff Engineer", "company": "Acme", "start": "2020", "end": "Present", "description": ""}
    ]
    assert infer_experience_level(None, linkedin_positions, today=_TODAY) == "staff"


def test_sustained_floor_survives_a_later_short_role():
    """The exact mechanism the manager-review fix protects: a role that
    cleared the sustained bar (3 years here) must not be pulled back down by
    a LATER role that never clears it — regardless of the later role's title
    or how recent it is."""
    positions = [
        _position("Senior Manager", "2015 - 2018"),  # 3 years, sustained
        _position("Freelance Helper", "Mar 2024 - May 2024"),  # ~3 months, short
    ]
    assert infer_experience_level(positions, today=_TODAY) == "senior"


def test_duration_promotes_a_title_neutral_sustained_role():
    # No seniority word in the title at all — the 3-year duration alone must
    # still produce a non-empty, above-entry-level read.
    positions = [_position("Software Development Engineer", "2019 - 2022")]
    assert infer_experience_level(positions, today=_TODAY) == "mid"


# ---------------------------------------------------------------------------
# infer_experience_level — total-duration fallback (no role individually
# sustained; the true last resort, signal 3)
# ---------------------------------------------------------------------------


def test_total_duration_fallback_when_no_single_role_sustains():
    # Two short stints (4 months each), neither clears _SUSTAINED_MONTHS on
    # its own; their SUM (~8 months) is banded instead. Must not be "intern".
    positions = [
        _position("Research Assistant", "Jan 2024 - Apr 2024"),
        _position("Junior Analyst", "Jun 2024 - Sep 2024"),
    ]
    assert infer_experience_level(positions, today=_TODAY) == "junior"


# ---------------------------------------------------------------------------
# Regression fixtures — anonymised shapes from two real production profiles
# that both wrongly inferred "intern" under the pre-fix most-recent-first
# walk. Titles/dates only; no employer names or personal identifiers.
# ---------------------------------------------------------------------------


def test_real_profile_a_sustained_engineer_not_demoted_by_recent_internship():
    """A short recent internship must not outrank years of sustained senior
    work. This user held a plainly-titled engineering role for ~3 years
    (2019-2022, no seniority word in the title) and, separately, a 3-month
    internship in late 2024 and an earlier 13-month internship (2017-2018).
    The pre-fix walk-back logic returned "intern" because that was the most
    recent title with a tier word. The fix: only the 3-year role clears the
    sustained-duration bar, and its own length alone (no title word needed)
    earns "mid" — neither internship, however recent, can pull that back
    down."""
    positions = [
        _position("Blockchain Engineer Intern", "Oct 2024 - December 2024"),
        _position("Software Development Engineer", "June 2019 - August 2022"),
        _position("Intern", "May 2017 - June 2018"),
    ]
    result = infer_experience_level(positions, today=_TODAY)
    assert result != "intern"
    assert result == "mid"


def test_real_profile_b_short_stints_do_not_default_to_intern():
    """A short recent internship must not outrank years of sustained senior
    work — and, symmetrically, a lone short internship must not be the
    default answer just because it's the only role with an explicit tier
    word. This user has ~1 year of total experience across two ~4-month
    roles (an internship, then a title-neutral R&D role); neither
    individually clears the sustained bar, so the total (~8 months) is
    banded instead. The pre-fix walk-back logic returned "intern" because
    the most recent title had no tier word and the walk landed on the older
    internship."""
    positions = [
        _position("AI Solutions Engineer - R&D", "June 2025 - September 2025"),
        _position("AI/ML Engineer Intern", "October 2024 - January 2025"),
    ]
    result = infer_experience_level(positions, today=_TODAY)
    assert result != "intern"
    assert result == "junior"


# ---------------------------------------------------------------------------
# infer_experience_level — defensive behaviour
# ---------------------------------------------------------------------------


def test_empty_positions_infer_nothing():
    assert infer_experience_level([], []) == ""
    assert infer_experience_level(None, None) == ""


def test_unparseable_dates_do_not_raise():
    positions = [_position("", "???## not a date at all")]
    assert infer_experience_level(positions) == ""

    positions_with_title = [_position("Widget Wrangler", "asdf glorp nonsense")]
    # No exception, and no tier word / no parseable duration -> empty result.
    assert infer_experience_level(positions_with_title) == ""


def test_malformed_position_entries_do_not_raise():
    # Not a dict, missing keys entirely, wrong types — none of this may crash.
    garbage = ["not-a-dict", {}, {"title": None, "dates": None}, 42, None]
    assert infer_experience_level(garbage) == ""
