"""Product design rule #29: filled shelves work harder; empty shelves stay SILENT.

Owner rule (2026-08-07, docs/product/product_design_rules.md). Like Indeed/LinkedIn:
match on what the user filled; an empty preference means "don't care" — never
a penalty, never a per-job zero, never a guess.

The enforceable form: with an EMPTY user side, every dimension scorer must
return a CONSTANT no matter what the job looks like. A constant cannot change
ranking order — that is what "silent" means. These tests feed each scorer
wildly different jobs and assert the empty-side result never varies.
"""
from __future__ import annotations

from src.services.job_enrichment import JobEnrichment
from src.services.prefilter import FilterProfile, location_ok
from src.services.scoring_dimensions import (
    salary_score,
    seniority_score,
    visa_score,
    workplace_score,
)

# A spread of very different jobs: none, senior/remote/high-pay, junior/onsite.
_ENRICHMENTS = [
    None,
    JobEnrichment(
        title_canonical="Staff Engineer", category="software_engineering",
        seniority="senior", workplace_type="remote",
        salary={"min": 120000, "max": 150000, "currency": "GBP", "period": "year"},
        visa_sponsorship="yes",
    ),
    JobEnrichment(
        title_canonical="Junior Analyst", category="data_science",
        seniority="junior", workplace_type="onsite",
        salary={"min": 22000, "max": 26000, "currency": "GBP", "period": "year"},
        visa_sponsorship="no",
    ),
]


def _constant(values: list) -> bool:
    return len(set(values)) == 1


def test_empty_salary_pref_is_silent() -> None:
    assert _constant([salary_score(e, None, None) for e in _ENRICHMENTS])


def test_empty_experience_level_is_silent() -> None:
    assert _constant([seniority_score(e, "") for e in _ENRICHMENTS])


def test_empty_workplace_pref_is_silent() -> None:
    assert _constant([workplace_score(e, None) for e in _ENRICHMENTS])
    assert _constant([workplace_score(e, "") for e in _ENRICHMENTS])


def test_no_visa_need_is_silent() -> None:
    assert _constant([visa_score(e, False) for e in _ENRICHMENTS])


def test_empty_location_prefs_prefilter_passes_everything() -> None:
    from src.models import Job

    profile = FilterProfile()  # nothing filled
    jobs = [
        Job(title="A", company="X", apply_url="", source="", date_found="",
            location="London"),
        Job(title="B", company="Y", apply_url="", source="", date_found="",
            location="Tokyo"),
        Job(title="C", company="Z", apply_url="", source="", date_found="",
            location=""),
    ]
    assert all(location_ok(profile, j) for j in jobs)


def test_filled_side_still_discriminates() -> None:
    """The rule has two halves — silence when empty, WORK when filled. Guard
    against a lazy fix that makes scorers constant always."""
    filled = [seniority_score(e, "senior") for e in _ENRICHMENTS]
    assert not _constant(filled)
