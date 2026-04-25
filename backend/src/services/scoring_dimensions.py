"""Pillar 2 Batch 2.9 — dimension scorers that consume JobEnrichment rows.

Four new scorers used by `JobScorer.score()` to expand the old 4-component
(title/skill/location/recency) formula into the 7-component formula from
plan §4 Batch 2.9 (weights configurable via `core/settings.py`):

  seniority_score   (0-8)  — enriched seniority vs user's target experience
  salary_score      (0-10) — band overlap with UserPreferences.salary_min/max
  visa_score        (0-6)  — only awards when user needs sponsorship AND job offers
  workplace_score   (0-6)  — Remote/Onsite/Hybrid match with UserPreferences.preferred_workplace

Each scorer gracefully returns a neutral midpoint when its signal is missing
(enrichment row absent, enum is "unknown", profile preference None).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.core.settings import (
    SALARY_WEIGHT,
    SENIORITY_WEIGHT,
    VISA_WEIGHT,
    WORKPLACE_WEIGHT,
)
from src.services.job_enrichment_schema import (
    JobEnrichment,
    SeniorityLevel,
    VisaSponsorship,
    WorkplaceType,
)
from src.services.salary import normalize_salary

# ---------------------------------------------------------------------------
# ScoreBreakdown — Step-1 B3 per-dimension score container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreBreakdown:
    """Per-dimension scoring breakdown returned by `JobScorer.score()`.

    Step-1 B3 — downstream consumers (API serialisation, frontend radar chart)
    need each dimension separately instead of the legacy single int.

    Legacy callers passing only ``config`` see the four legacy components
    populated (title/skill/location/recency) with the four Pillar 2 Batch 2.9
    dimension slots (seniority/salary/visa/workplace) defaulted to 0 — which
    makes ``match_score`` byte-identical to the pre-Step-1 int return.

    Activation of the multi-dim path (seniority/salary/visa/workplace
    populated to non-zero) happens only when BOTH ``user_preferences`` and
    ``enrichment_lookup`` are passed to ``JobScorer(...)``.
    """

    title_score: int = 0
    skill_score: int = 0
    location_score: int = 0
    recency_score: int = 0
    seniority_score: int = 0
    salary_score: int = 0
    visa_score: int = 0
    workplace_score: int = 0
    match_score: int = 0


# ---------------------------------------------------------------------------
# Seniority
# ---------------------------------------------------------------------------


_SENIORITY_RANK = {
    SeniorityLevel.INTERN: 0,
    SeniorityLevel.JUNIOR: 1,
    SeniorityLevel.MID: 2,
    SeniorityLevel.SENIOR: 3,
    SeniorityLevel.STAFF: 4,
    SeniorityLevel.PRINCIPAL: 5,
    SeniorityLevel.DIRECTOR: 6,
}


# Map UserPreferences.experience_level strings to the same rank scale.
_USER_EXPERIENCE_RANK = {
    "intern": 0,
    "internship": 0,
    "junior": 1,
    "entry": 1,
    "graduate": 1,
    "mid": 2,
    "intermediate": 2,
    "senior": 3,
    "sr": 3,
    "staff": 4,
    "lead": 4,
    "principal": 5,
    "director": 6,
    "head": 6,
    "vp": 6,
}


def seniority_score(enrichment: Optional[JobEnrichment], user_experience: str) -> int:
    """Compare enriched seniority to the user's target experience level.

    Scoring curve (absolute rank difference):
      0 ranks apart → full weight
      1 rank apart  → 62 %
      2 ranks apart → 25 %
      3+ ranks apart → 0
    Missing signal on either side → neutral half weight.
    """
    max_pts = SENIORITY_WEIGHT
    if enrichment is None or enrichment.seniority == SeniorityLevel.UNKNOWN:
        return max_pts // 2
    user_rank = _USER_EXPERIENCE_RANK.get((user_experience or "").strip().lower())
    if user_rank is None:
        return max_pts // 2

    job_rank = _SENIORITY_RANK.get(enrichment.seniority, 2)
    diff = abs(job_rank - user_rank)
    if diff == 0:
        return max_pts
    if diff == 1:
        return int(round(max_pts * 0.625))
    if diff == 2:
        return int(round(max_pts * 0.25))
    return 0


# ---------------------------------------------------------------------------
# Salary
# ---------------------------------------------------------------------------


def salary_score(
    enrichment: Optional[JobEnrichment],
    target_min: Optional[float],
    target_max: Optional[float],
) -> int:
    """Band-overlap score between the job's enriched salary and the user's
    target range.

    Contract:
      - No enrichment or no salary band → neutral `SALARY_WEIGHT // 2`.
      - User has no target range → neutral `SALARY_WEIGHT // 2`.
      - Full overlap → full weight.
      - Partial overlap → proportional to the share of the user's range the
        job covers.
      - No overlap at all → 0.
    """
    max_pts = SALARY_WEIGHT
    neutral = max_pts // 2
    if enrichment is None:
        return neutral
    normalised = normalize_salary(enrichment.salary)
    if normalised is None:
        return neutral
    if target_min is None and target_max is None:
        return neutral

    job_min, job_max = normalised
    user_min = int(target_min) if target_min is not None else job_min
    user_max = int(target_max) if target_max is not None else job_max
    if user_max < user_min:
        user_min, user_max = user_max, user_min

    overlap_min = max(job_min, user_min)
    overlap_max = min(job_max, user_max)
    if overlap_max < overlap_min:
        return 0

    job_span = max(job_max - job_min, 1)
    user_span = max(user_max - user_min, 1)
    # Denominator is the *smaller* of the two spans so that a tight job band
    # fully inside a wider user range scores full weight (rather than being
    # punished for being narrower than the user's appetite).
    smaller_span = min(job_span, user_span)
    overlap_span = overlap_max - overlap_min
    ratio = min(overlap_span / smaller_span, 1.0)
    return int(round(max_pts * ratio))


# ---------------------------------------------------------------------------
# Visa
# ---------------------------------------------------------------------------


def visa_score(enrichment: Optional[JobEnrichment], needs_visa: bool) -> int:
    """Full weight when user needs sponsorship AND the job offers it.

    - User doesn't need sponsorship → 0 (no reward for something irrelevant).
    - User needs sponsorship + job offers → `VISA_WEIGHT`.
    - User needs sponsorship + job doesn't offer → 0.
    - User needs sponsorship + unknown → half weight (can't confirm, can't deny).
    """
    if not needs_visa:
        return 0
    if enrichment is None:
        return VISA_WEIGHT // 2
    if enrichment.visa_sponsorship == VisaSponsorship.YES:
        return VISA_WEIGHT
    if enrichment.visa_sponsorship == VisaSponsorship.NO:
        return 0
    return VISA_WEIGHT // 2  # UNKNOWN


# ---------------------------------------------------------------------------
# Workplace
# ---------------------------------------------------------------------------


_WORKPLACE_MATCH = {
    ("remote", WorkplaceType.REMOTE),
    ("onsite", WorkplaceType.ONSITE),
    ("hybrid", WorkplaceType.HYBRID),
}


def workplace_score(
    enrichment: Optional[JobEnrichment],
    preferred_workplace: Optional[str],
) -> int:
    """Match the enriched workplace type against the user's preference.

    - No preference or no enrichment → neutral half weight.
    - Exact match (remote/onsite/hybrid) → full weight.
    - Preference=remote but job is hybrid, or vice versa → half weight
      (hybrid is the compromise position between remote and onsite).
    - Remote-preferred but onsite-only (or vice versa) → 0.
    - Unknown workplace type → half weight.
    """
    max_pts = WORKPLACE_WEIGHT
    if enrichment is None or not preferred_workplace:
        return max_pts // 2
    if enrichment.workplace_type == WorkplaceType.UNKNOWN:
        return max_pts // 2

    pref = preferred_workplace.strip().lower()
    wt = enrichment.workplace_type

    if (pref, wt) in _WORKPLACE_MATCH:
        return max_pts
    # Hybrid is a compromise: half-points when preference is remote/onsite
    # but job is hybrid (or the reverse).
    if wt == WorkplaceType.HYBRID or pref == "hybrid":
        return max_pts // 2
    return 0
