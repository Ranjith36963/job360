"""Property tests: find scoring bugs NOBODY thought to write a test for.

WHY THIS FILE EXISTS. Every product-quality bug we shipped was found by a
human noticing, never by a test:

    #156  a job paying EXACTLY the user's target scored 0 - five points WORSE
          than a job that published no salary at all
    #160  choosing "Executive" in the profile form scored every job neutrally,
          because the scorer had never heard of that value

An example-based test only checks the cases someone imagined. Nobody imagined
"what if the salary is a single point" or "what if the UI offers a level the
scorer doesn't know" - so nothing caught them.

A PROPERTY test states a rule that must hold for EVERY input, then sweeps the
whole input space looking for a counter-example. It finds the case you did not
think of, which is exactly the class of bug that keeps reaching production.

Deterministic grid sweeps, not random sampling: a failure here must be
reproducible on the first re-run, and CI must never flake on a seed.
"""

from __future__ import annotations

import pytest

from src.core.settings import SALARY_WEIGHT, SENIORITY_WEIGHT
from src.services.job_enrichment_schema import (
    JobCategory,
    JobEnrichment,
    SalaryBand,
    SalaryFrequency,
    SeniorityLevel,
)
from src.services.scoring_dimensions import salary_score, seniority_score

TARGET_MIN, TARGET_MAX = 50_000, 70_000


def _enr(**ov) -> JobEnrichment:
    return JobEnrichment(
        title_canonical="X", category=JobCategory.SOFTWARE_ENGINEERING, **ov
    )


def _band(mn=None, mx=None) -> SalaryBand:
    return SalaryBand(min=mn, max=mx, currency="GBP", frequency=SalaryFrequency.ANNUAL)


# ---------------------------------------------------------------------------
# PROPERTY 1 — KNOWN must never lose to UNKNOWN.
#
# This is bug #156, stated as a rule instead of an example. A job that PUBLISHES
# a salary inside the user's target must never score below a job that published
# nothing at all. Publishing good information cannot be a penalty.
#
# The original bug: an unfloored span made a single-point salary score 0, while
# "no salary" scored the neutral half-weight. Publishing £60k when the user
# wanted £50-70k was worse than saying nothing.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("amount", range(TARGET_MIN, TARGET_MAX + 1, 2_000))
def test_publishing_an_in_range_salary_never_scores_worse_than_publishing_none(amount):
    unknown = salary_score(_enr(), TARGET_MIN, TARGET_MAX)
    known = salary_score(_enr(salary=_band(mn=amount)), TARGET_MIN, TARGET_MAX)
    assert known >= unknown, (
        f"a job paying exactly {amount:,} (inside the {TARGET_MIN:,}-{TARGET_MAX:,} "
        f"target) scored {known}, but publishing NOTHING scores {unknown}. "
        "Publishing a good salary must never be a penalty."
    )


# ---------------------------------------------------------------------------
# PROPERTY 2 — MONOTONICITY. Moving a salary closer to the target must never
# lower its score. Any dip is a hole in the curve, which is what a zero-width
# band was: a single point of the domain scoring lower than its neighbours.
# ---------------------------------------------------------------------------
def test_salary_score_has_no_holes_across_the_range():
    """Sweep the whole plausible salary range; no point may score below BOTH
    of its neighbours. A dip like that is a discontinuity, and every scoring
    discontinuity we have shipped was a bug."""
    step = 1_000
    amounts = list(range(20_000, 120_001, step))
    scores = [salary_score(_enr(salary=_band(mn=a)), TARGET_MIN, TARGET_MAX) for a in amounts]
    for i in range(1, len(scores) - 1):
        lo, mid, hi = scores[i - 1], scores[i], scores[i + 1]
        assert not (mid < lo and mid < hi), (
            f"hole in the salary curve at {amounts[i]:,}: scores {lo} -> {mid} -> {hi}. "
            "A single salary scoring worse than both its neighbours is a bug, "
            "not a preference."
        )


# ---------------------------------------------------------------------------
# PROPERTY 3 — NO DEAD INPUT. Every experience level the UI offers must
# actually change the score.
#
# This is bug #160 as a rule. The profile form offered "executive"; the scorer
# had never heard of it, so it fell through to a permanent neutral and an
# INTERNSHIP scored the same as a DIRECTOR role. The UI and the scorer had
# drifted apart and nothing noticed.
#
# The list below is the contract with the UI (frontend PreferencesForm.tsx).
# If someone adds an option there, this test fails until the scorer learns it -
# which is the point.
# ---------------------------------------------------------------------------
UI_EXPERIENCE_LEVELS = ["entry", "mid", "senior", "lead", "executive"]


@pytest.mark.parametrize("level", UI_EXPERIENCE_LEVELS)
def test_every_experience_level_the_ui_offers_actually_discriminates(level):
    """A level the scorer does not know produces a FLAT curve — the same score
    for every job, from intern to director. That is what "dead dimension"
    looks like, and it is exactly what "executive" did before #160.

    Note the invariant is FLATNESS, not "director != intern". The curve is
    deliberately symmetric on abs(rank difference) (see seniority_score's
    docstring): for a *senior* user, a director role and an internship are both
    three ranks away and SHOULD score the same. An earlier version of this test
    asserted director != intern and failed on that legitimate design — the code
    was right and the test was wrong.
    """
    curve = {seniority_score(_enr(seniority=j), level) for j in SeniorityLevel}
    assert len(curve) > 1, (
        f"experience level '{level}' gives every job the identical score "
        f"({curve.pop()}) — intern and director alike. The scorer does not know "
        "this value, so the seniority dimension is dead for anyone who picks it."
    )


# ---------------------------------------------------------------------------
# PROPERTY 4 — BOUNDS. No dimension may exceed its own declared weight, in
# either direction. A dimension quietly worth double its weight silently
# re-balances every other dimension.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("amount", range(0, 200_001, 10_000))
def test_salary_score_stays_within_its_weight(amount):
    s = salary_score(_enr(salary=_band(mn=amount)), TARGET_MIN, TARGET_MAX)
    assert -SALARY_WEIGHT <= s <= SALARY_WEIGHT, (
        f"salary {amount:,} scored {s}, outside +/-{SALARY_WEIGHT}"
    )


@pytest.mark.parametrize("level", UI_EXPERIENCE_LEVELS)
@pytest.mark.parametrize("job_level", list(SeniorityLevel))
def test_seniority_score_stays_within_its_weight(level, job_level):
    s = seniority_score(_enr(seniority=job_level), level)
    assert -SENIORITY_WEIGHT <= s <= SENIORITY_WEIGHT, (
        f"user '{level}' vs job '{job_level}' scored {s}, outside +/-{SENIORITY_WEIGHT}"
    )


# ---------------------------------------------------------------------------
# PROPERTY 5 — MISSING DATA IS NEUTRAL, NEVER PUNISHING. A job that simply did
# not state something must not be pushed below a job that stated something bad.
# Otherwise the feed quietly prefers vague listings over honest ones.
# ---------------------------------------------------------------------------
def test_missing_data_is_never_worse_than_clearly_wrong_data():
    unknown = salary_score(_enr(), TARGET_MIN, TARGET_MAX)
    far_off = salary_score(_enr(salary=_band(mn=8_000, mx=11_000)), TARGET_MIN, TARGET_MAX)
    assert unknown >= far_off, (
        f"a job with NO salary scored {unknown}, but one paying 8-11k (far below "
        f"the {TARGET_MIN:,} target) scored {far_off}. Saying nothing should not "
        "beat saying something clearly unsuitable."
    )
