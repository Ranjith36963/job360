"""Sanity checks for the ATS slug catalog (Batch 3 expansion 104 -> 268).

Guards against accidental regressions in `core/companies.py`. Does not
network-validate each slug — that would require live HTTP and is out of
scope for the pytest-offline contract. Validation per-slug is a follow-up.
"""
from src.core.companies import (
    GREENHOUSE_COMPANIES,
    LEVER_COMPANIES,
    WORKABLE_COMPANIES,
    ASHBY_COMPANIES,
    SMARTRECRUITERS_COMPANIES,
    PINPOINT_COMPANIES,
    RECRUITEE_COMPANIES,
    PERSONIO_COMPANIES,
    WORKDAY_COMPANIES,
    SUCCESSFACTORS_COMPANIES,
    RIPPLING_COMPANIES,
)


def test_total_slug_count_exceeds_batch3_target():
    """Batch 3 target: at least 250 ATS slugs across 10+ platforms."""
    total = (
        len(GREENHOUSE_COMPANIES)
        + len(LEVER_COMPANIES)
        + len(WORKABLE_COMPANIES)
        + len(ASHBY_COMPANIES)
        + len(SMARTRECRUITERS_COMPANIES)
        + len(PINPOINT_COMPANIES)
        + len(RECRUITEE_COMPANIES)
        + len(PERSONIO_COMPANIES)
        + len(WORKDAY_COMPANIES)
        + len(SUCCESSFACTORS_COMPANIES)
        + len(RIPPLING_COMPANIES)
    )
    assert total >= 250


def test_no_duplicate_slugs_within_platform():
    """A slug must appear at most once per ATS platform list."""
    for name, lst in [
        ("Greenhouse", GREENHOUSE_COMPANIES),
        ("Lever", LEVER_COMPANIES),
        ("Workable", WORKABLE_COMPANIES),
        ("Ashby", ASHBY_COMPANIES),
        ("SmartRecruiters", SMARTRECRUITERS_COMPANIES),
        ("Pinpoint", PINPOINT_COMPANIES),
        ("Recruitee", RECRUITEE_COMPANIES),
        ("Personio", PERSONIO_COMPANIES),
        ("Rippling", RIPPLING_COMPANIES),
    ]:
        assert len(lst) == len(set(lst)), f"{name} has duplicate slugs"


def test_workday_entries_have_required_fields():
    for entry in WORKDAY_COMPANIES:
        for field in ("tenant", "wd", "site", "name"):
            assert field in entry, f"Workday entry missing {field!r}: {entry}"


def test_successfactors_entries_have_required_fields():
    for entry in SUCCESSFACTORS_COMPANIES:
        for field in ("name", "sitemap_url"):
            assert field in entry, f"SuccessFactors entry missing {field!r}: {entry}"
