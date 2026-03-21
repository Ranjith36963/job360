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


def test_dedup_preserves_seniority_prefix():
    """'Senior ML Engineer' and 'ML Engineer' are distinct roles — must NOT dedup."""
    jobs = [
        _make_job(title="Senior ML Engineer", company="DeepMind", source="reed"),
        _make_job(title="ML Engineer", company="DeepMind", source="adzuna"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 2


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


def test_dedup_company_region_suffix():
    """'Barclays UK' and 'Barclays' should dedup to 1 job."""
    jobs = [
        _make_job(title="AI Engineer", company="Barclays UK", source="ashby"),
        _make_job(title="AI Engineer", company="Barclays", source="greenhouse"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1


# ---- Pass 2: Description similarity dedup ----


_LONG_DESC = (
    "We are looking for an experienced AI Engineer to join our team. "
    "You will work on cutting-edge machine learning models using Python, "
    "PyTorch, and TensorFlow. Experience with LLMs and RAG pipelines "
    "is highly desirable. Strong communication skills required."
)


def test_dedup_similar_description_different_titles():
    """Same company + nearly identical descriptions = duplicate even with different titles."""
    jobs = [
        _make_job(title="AI Engineer", company="DeepMind", description=_LONG_DESC,
                  match_score=70, source="reed"),
        _make_job(title="Machine Learning Engineer", company="DeepMind",
                  description=_LONG_DESC, match_score=65, source="adzuna"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1
    assert result[0].match_score == 70  # kept the higher score


def test_dedup_similar_description_keeps_better():
    """When descriptions match, keep the one with more data."""
    jobs = [
        _make_job(title="AI Engineer", company="DeepMind", description=_LONG_DESC,
                  match_score=60, source="reed"),
        _make_job(title="ML Engineer", company="DeepMind", description=_LONG_DESC,
                  match_score=80, salary_min=70000, salary_max=90000, source="adzuna"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 1
    assert result[0].match_score == 80


def test_dedup_different_descriptions_kept():
    """Same company but genuinely different descriptions = NOT deduplicated."""
    jobs = [
        _make_job(title="AI Engineer", company="DeepMind",
                  description="Build computer vision models for autonomous vehicles. "
                              "Requires expertise in OpenCV, YOLO, and real-time inference."),
        _make_job(title="Data Scientist", company="DeepMind",
                  description="Analyze business metrics and create dashboards. "
                              "Requires SQL, Tableau, and statistical modelling."),
    ]
    result = deduplicate(jobs)
    assert len(result) == 2


def test_dedup_short_descriptions_not_compared():
    """Short descriptions should not trigger similarity dedup (too unreliable)."""
    jobs = [
        _make_job(title="AI Engineer", company="DeepMind", description="AI role"),
        _make_job(title="ML Engineer", company="DeepMind", description="ML role"),
    ]
    result = deduplicate(jobs)
    assert len(result) == 2


def test_dedup_different_companies_not_compared():
    """Similar descriptions at DIFFERENT companies are NOT duplicates."""
    jobs = [
        _make_job(title="AI Engineer", company="DeepMind", description=_LONG_DESC),
        _make_job(title="AI Engineer", company="Revolut", description=_LONG_DESC),
    ]
    result = deduplicate(jobs)
    assert len(result) == 2
