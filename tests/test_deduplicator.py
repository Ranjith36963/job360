from datetime import datetime, timezone
from src.models import Job
from src.filters.deduplicator import deduplicate


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer",
        company="DeepMind",
        apply_url="https://example.com",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London",
        description="AI role",
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_dedup_identical_jobs():
    jobs = [
        _make_job(source="reed", apply_url="https://reed.co.uk/1"),
        _make_job(source="adzuna", apply_url="https://adzuna.co.uk/2"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1


def test_dedup_keeps_different_jobs():
    jobs = [
        _make_job(title="AI Engineer", company="DeepMind"),
        _make_job(title="ML Engineer", company="Revolut"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 2


def test_dedup_normalizes_company():
    jobs = [
        _make_job(company="DeepMind Ltd", source="reed"),
        _make_job(company="deepmind", source="adzuna"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1


def test_dedup_keeps_highest_score():
    jobs = [
        _make_job(source="reed", match_score=60),
        _make_job(source="adzuna", match_score=80),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1
    assert result[0].match_score == 80


def test_dedup_keeps_most_complete_on_tie():
    jobs = [
        _make_job(source="reed", match_score=70, salary_min=60000, salary_max=80000),
        _make_job(source="adzuna", match_score=70),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1
    assert result[0].salary_min == 60000


def test_dedup_empty_list():
    assert deduplicate([]) == []


def test_dedup_single_job():
    jobs = [_make_job()]
    result = deduplicate(jobs)
    assert len(result) == 1


# ---- Smarter deduplication tests ----


def test_dedup_strips_seniority_prefix():
    """'Senior ML Engineer' and 'ML Engineer' at same company should dedup."""
    jobs = [
        _make_job(title="Senior ML Engineer", company="DeepMind", source="reed"),
        _make_job(title="ML Engineer", company="DeepMind", source="adzuna"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1


def test_dedup_strips_trailing_job_code():
    """'AI Engineer - 12345' and 'AI Engineer' at same company should dedup."""
    jobs = [
        _make_job(title="AI Engineer - REQ-12345", company="DeepMind", source="reed"),
        _make_job(title="AI Engineer", company="DeepMind", source="adzuna"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1


def test_dedup_strips_parenthetical():
    """'AI Engineer (London)' and 'AI Engineer' at same company should dedup."""
    jobs = [
        _make_job(title="AI Engineer (London)", company="DeepMind", source="reed"),
        _make_job(title="AI Engineer", company="DeepMind", source="adzuna"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1


def test_dedup_different_roles_same_company():
    """Different actual roles at same company should NOT dedup."""
    jobs = [
        _make_job(title="AI Engineer", company="DeepMind"),
        _make_job(title="Data Scientist", company="DeepMind"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 2


def test_dedup_company_suffix_normalization():
    """Companies with suffix variants should dedup."""
    jobs = [
        _make_job(title="AI Engineer", company="Acme Solutions", source="reed"),
        _make_job(title="AI Engineer", company="acme", source="adzuna"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1
