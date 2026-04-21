"""Pillar 2 Batch 2.9 — tests for the four new dimension scorers."""
from __future__ import annotations

import pytest

from src.core.settings import (
    SALARY_WEIGHT,
    SENIORITY_WEIGHT,
    VISA_WEIGHT,
    WORKPLACE_WEIGHT,
)
from src.services.job_enrichment_schema import (
    EmploymentType,
    EmployerType,
    ExperienceLevel,
    JobCategory,
    JobEnrichment,
    SalaryBand,
    SalaryFrequency,
    SeniorityLevel,
    VisaSponsorship,
    WorkplaceType,
)
from src.services.scoring_dimensions import (
    salary_score,
    seniority_score,
    visa_score,
    workplace_score,
)


def _enrichment(**overrides) -> JobEnrichment:
    base = dict(
        title_canonical="X",
        category=JobCategory.SOFTWARE_ENGINEERING,
    )
    base.update(overrides)
    return JobEnrichment(**base)


# ---------------------------------------------------------------------------
# Seniority
# ---------------------------------------------------------------------------


def test_seniority_perfect_match_full_weight():
    e = _enrichment(seniority=SeniorityLevel.SENIOR)
    assert seniority_score(e, "senior") == SENIORITY_WEIGHT


def test_seniority_one_rank_off_gets_62_percent():
    e = _enrichment(seniority=SeniorityLevel.SENIOR)
    expected = int(round(SENIORITY_WEIGHT * 0.625))
    assert seniority_score(e, "mid") == expected


def test_seniority_two_ranks_off_gets_quarter_weight():
    e = _enrichment(seniority=SeniorityLevel.STAFF)
    expected = int(round(SENIORITY_WEIGHT * 0.25))
    assert seniority_score(e, "mid") == expected


def test_seniority_three_ranks_off_gets_zero():
    e = _enrichment(seniority=SeniorityLevel.DIRECTOR)
    assert seniority_score(e, "junior") == 0


def test_seniority_unknown_enrichment_neutral():
    e = _enrichment(seniority=SeniorityLevel.UNKNOWN)
    assert seniority_score(e, "senior") == SENIORITY_WEIGHT // 2


def test_seniority_no_enrichment_neutral():
    assert seniority_score(None, "senior") == SENIORITY_WEIGHT // 2


def test_seniority_empty_user_experience_neutral():
    e = _enrichment(seniority=SeniorityLevel.SENIOR)
    assert seniority_score(e, "") == SENIORITY_WEIGHT // 2


# ---------------------------------------------------------------------------
# Salary
# ---------------------------------------------------------------------------


def test_salary_full_overlap_full_weight():
    """Job band fully contained in user band → max score."""
    e = _enrichment(salary=SalaryBand(min=70_000, max=80_000, currency="GBP",
                                       frequency=SalaryFrequency.ANNUAL))
    assert salary_score(e, 60_000, 100_000) == SALARY_WEIGHT


def test_salary_partial_overlap_proportional():
    """Job band overlaps a portion of user range, scored against smaller span."""
    e = _enrichment(salary=SalaryBand(min=50_000, max=75_000, currency="GBP",
                                       frequency=SalaryFrequency.ANNUAL))
    # user 60-100k (40k), job 50-75k (25k) → overlap 60-75k (15k).
    # Denominator is the smaller span (25k) so ratio = 15/25 = 0.6.
    expected = int(round(SALARY_WEIGHT * 0.6))
    assert salary_score(e, 60_000, 100_000) == expected


def test_salary_no_overlap_returns_zero():
    e = _enrichment(salary=SalaryBand(min=30_000, max=40_000, currency="GBP",
                                       frequency=SalaryFrequency.ANNUAL))
    assert salary_score(e, 80_000, 120_000) == 0


def test_salary_missing_enrichment_neutral():
    assert salary_score(None, 60_000, 100_000) == SALARY_WEIGHT // 2


def test_salary_missing_user_range_neutral():
    e = _enrichment(salary=SalaryBand(min=60_000, max=80_000, currency="GBP",
                                       frequency=SalaryFrequency.ANNUAL))
    assert salary_score(e, None, None) == SALARY_WEIGHT // 2


def test_salary_missing_band_in_enrichment_neutral():
    """Research-report recommendation — don't punish jobs for missing pay info."""
    e = _enrichment()  # salary defaults to all-None
    assert salary_score(e, 60_000, 100_000) == SALARY_WEIGHT // 2


# ---------------------------------------------------------------------------
# Visa
# ---------------------------------------------------------------------------


def test_visa_user_doesnt_need_returns_zero():
    """No reward for something the user doesn't need."""
    e = _enrichment(visa_sponsorship=VisaSponsorship.YES)
    assert visa_score(e, needs_visa=False) == 0


def test_visa_needed_and_offered_full_weight():
    e = _enrichment(visa_sponsorship=VisaSponsorship.YES)
    assert visa_score(e, needs_visa=True) == VISA_WEIGHT


def test_visa_needed_and_declined_zero():
    e = _enrichment(visa_sponsorship=VisaSponsorship.NO)
    assert visa_score(e, needs_visa=True) == 0


def test_visa_needed_and_unknown_half_weight():
    """Can't confirm, can't deny — partial credit."""
    e = _enrichment(visa_sponsorship=VisaSponsorship.UNKNOWN)
    assert visa_score(e, needs_visa=True) == VISA_WEIGHT // 2


def test_visa_no_enrichment_half_weight_when_needed():
    assert visa_score(None, needs_visa=True) == VISA_WEIGHT // 2


# ---------------------------------------------------------------------------
# Workplace
# ---------------------------------------------------------------------------


def test_workplace_exact_remote_match():
    e = _enrichment(workplace_type=WorkplaceType.REMOTE)
    assert workplace_score(e, "remote") == WORKPLACE_WEIGHT


def test_workplace_exact_onsite_match():
    e = _enrichment(workplace_type=WorkplaceType.ONSITE)
    assert workplace_score(e, "onsite") == WORKPLACE_WEIGHT


def test_workplace_exact_hybrid_match():
    e = _enrichment(workplace_type=WorkplaceType.HYBRID)
    assert workplace_score(e, "hybrid") == WORKPLACE_WEIGHT


def test_workplace_hybrid_is_compromise_for_remote_user():
    e = _enrichment(workplace_type=WorkplaceType.HYBRID)
    assert workplace_score(e, "remote") == WORKPLACE_WEIGHT // 2


def test_workplace_remote_user_hates_onsite():
    e = _enrichment(workplace_type=WorkplaceType.ONSITE)
    assert workplace_score(e, "remote") == 0


def test_workplace_no_preference_neutral():
    e = _enrichment(workplace_type=WorkplaceType.REMOTE)
    assert workplace_score(e, None) == WORKPLACE_WEIGHT // 2


def test_workplace_no_enrichment_neutral():
    assert workplace_score(None, "remote") == WORKPLACE_WEIGHT // 2


def test_workplace_unknown_type_neutral():
    e = _enrichment(workplace_type=WorkplaceType.UNKNOWN)
    assert workplace_score(e, "remote") == WORKPLACE_WEIGHT // 2


# ---------------------------------------------------------------------------
# JobScorer integration — the 7-component formula
# ---------------------------------------------------------------------------


def test_jobscorer_with_enrichment_scores_higher_than_without():
    """End-to-end: a job with a great enrichment should outscore one without,
    given the same title/skill/location/recency base."""
    from datetime import datetime, timezone
    from src.models import Job
    from src.services.profile.models import SearchConfig, UserPreferences
    from src.services.skill_matcher import JobScorer

    today = datetime.now(timezone.utc).isoformat()

    def _job(title, description):
        return Job(
            title=title,
            company="Acme",
            apply_url="https://example.com",
            source="greenhouse",
            location="London, UK",
            description=description,
            date_found=today,
        )

    config = SearchConfig(
        job_titles=["ML Engineer"],
        primary_skills=["python", "pytorch"],
    )
    prefs = UserPreferences(
        salary_min=70000, salary_max=90000,
        experience_level="senior",
        preferred_workplace="remote",
        needs_visa=True,
    )
    great_enrichment = _enrichment(
        title_canonical="Senior ML Engineer",
        category=JobCategory.MACHINE_LEARNING,
        seniority=SeniorityLevel.SENIOR,
        workplace_type=WorkplaceType.REMOTE,
        visa_sponsorship=VisaSponsorship.YES,
        salary=SalaryBand(min=75000, max=85000, currency="GBP",
                          frequency=SalaryFrequency.ANNUAL),
    )
    enrichments = {"great": great_enrichment}
    lookup = lambda job: enrichments.get(job.company.lower())  # noqa: E731

    # Build two scorers with the same base config but different enrichment data.
    scorer_enriched = JobScorer(
        config,
        user_preferences=prefs,
        enrichment_lookup=lambda job: great_enrichment,
    )
    scorer_base = JobScorer(config)   # no enrichment path

    job = _job("ML Engineer", "Python PyTorch role.")
    enriched_score = scorer_enriched.score(job)
    base_score = scorer_base.score(job)
    assert enriched_score > base_score
    # 100 cap still holds.
    assert enriched_score <= 100


def test_jobscorer_enrichment_lookup_returning_none_falls_back_to_base():
    """A scorer with user_preferences but an empty enrichment_lookup scores
    the same as a plain-config scorer (no double counting of dimensions)."""
    from datetime import datetime, timezone
    from src.models import Job
    from src.services.profile.models import SearchConfig, UserPreferences
    from src.services.skill_matcher import JobScorer

    today = datetime.now(timezone.utc).isoformat()
    job = Job(
        title="ML Engineer", company="Acme", apply_url="https://example.com",
        source="reed", location="London, UK",
        description="Python PyTorch role.", date_found=today,
    )
    config = SearchConfig(job_titles=["ML Engineer"], primary_skills=["python", "pytorch"])
    prefs = UserPreferences(preferred_workplace="remote", needs_visa=False)

    with_prefs = JobScorer(
        config,
        user_preferences=prefs,
        enrichment_lookup=lambda j: None,
    )
    without = JobScorer(config)
    assert with_prefs.score(job) == without.score(job)


def test_jobscorer_dim_bonus_caps_at_100():
    """Even a 'perfect' job can't exceed the 100-point ceiling."""
    from datetime import datetime, timezone
    from src.models import Job
    from src.services.profile.models import SearchConfig, UserPreferences
    from src.services.skill_matcher import JobScorer

    today = datetime.now(timezone.utc).isoformat()
    # Give every dimension maximum signal.
    job = Job(
        title="ML Engineer", company="Acme", apply_url="https://example.com",
        source="reed", location="London, UK",
        description=" ".join(["python", "pytorch", "tensorflow", "langchain",
                              "rag", "llm", "nlp", "deep learning", "aws",
                              "docker", "kubernetes"] * 3),
        date_found=today,
    )
    config = SearchConfig(
        job_titles=["ML Engineer"],
        primary_skills=["python", "pytorch", "tensorflow", "langchain"],
        secondary_skills=["rag", "llm", "nlp", "deep learning"],
        tertiary_skills=["aws", "docker", "kubernetes"],
    )
    prefs = UserPreferences(
        salary_min=70000, salary_max=90000,
        experience_level="senior", preferred_workplace="remote",
        needs_visa=True,
    )
    perfect = _enrichment(
        seniority=SeniorityLevel.SENIOR,
        workplace_type=WorkplaceType.REMOTE,
        visa_sponsorship=VisaSponsorship.YES,
        salary=SalaryBand(min=75000, max=85000, currency="GBP",
                          frequency=SalaryFrequency.ANNUAL),
    )
    scorer = JobScorer(config, user_preferences=prefs,
                       enrichment_lookup=lambda job: perfect)
    assert scorer.score(job) == 100
