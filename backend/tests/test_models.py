import pytest

from src.models import Job


def test_job_creation_with_required_fields():
    job = Job(
        title="AI Engineer",
        company="DeepMind",
        apply_url="https://example.com/job",
        source="reed",
        date_found="2024-01-01T00:00:00Z",
    )
    assert job.title == "AI Engineer"
    assert job.company == "DeepMind"
    assert job.source == "reed"


def test_job_defaults():
    job = Job(
        title="ML Engineer",
        company="Test Co",
        apply_url="https://example.com",
        source="adzuna",
        date_found="2024-01-01T00:00:00Z",
    )
    assert job.location == ""
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.description == ""
    assert job.visa_flag is False
    # Pillar 3 Batch 1 — 5-column date model default fields
    assert job.posted_at is None
    assert job.date_confidence == "low"
    assert job.date_posted_raw is None


def test_job_accepts_lifecycle_timestamp_fields():
    """Step-1 B1: Job must accept first_seen_at / last_seen_at.

    `staleness_state` left the dataclass with the ghost detector that wrote it
    (slice 5, #483). The `jobs` COLUMN stays — `POST /pipeline/{job_id}` still
    refuses a `confirmed_expired` row — but nothing constructs a Job carrying
    one any more.
    """
    job = Job(
        title="ML Engineer",
        company="Test Co",
        apply_url="https://example.com",
        source="reed",
        date_found="2024-01-01T00:00:00Z",
        first_seen_at="2020-01-01T00:00:00Z",
        last_seen_at="2020-06-01T00:00:00Z",
    )
    assert job.first_seen_at == "2020-01-01T00:00:00Z"
    assert job.last_seen_at == "2020-06-01T00:00:00Z"


def test_job_lifecycle_fields_default_to_none():
    """Step-1 B1: lifecycle fields default to None so insert_job can apply fallback."""
    job = Job(
        title="ML Engineer",
        company="Test Co",
        apply_url="https://example.com",
        source="reed",
        date_found="2024-01-01T00:00:00Z",
    )
    assert job.first_seen_at is None
    assert job.last_seen_at is None


def test_job_accepts_five_column_date_fields():
    job = Job(
        title="ML Engineer",
        company="Test Co",
        apply_url="https://example.com",
        source="reed",
        date_found="2024-01-01T00:00:00Z",
        posted_at="2026-04-15T10:00:00+00:00",
        date_confidence="high",
        date_posted_raw="2026-04-15T10:00:00Z",
    )
    assert job.posted_at == "2026-04-15T10:00:00+00:00"
    assert job.date_confidence == "high"
    assert job.date_posted_raw == "2026-04-15T10:00:00Z"


def test_normalized_key():
    job = Job(
        title="AI Engineer",
        company="DeepMind Ltd",
        location="London",
        apply_url="https://example.com",
        source="reed",
        date_found="2024-01-01T00:00:00Z",
    )
    key = job.normalized_key()
    assert key == ("deepmind", "ai engineer")


@pytest.mark.fast
def test_normalized_key_strips_suffixes():
    for suffix in ["Ltd", "Limited", "Inc", "PLC", "Corporation", "Corp", "Group"]:
        job = Job(
            title="Data Scientist",
            company=f"Acme {suffix}",
            apply_url="https://example.com",
            source="reed",
            date_found="2024-01-01T00:00:00Z",
        )
        company, title = job.normalized_key()
        assert company == "acme", f"Failed to strip '{suffix}'"


def test_normalized_key_case_insensitive():
    j1 = Job(title="AI ENGINEER", company="DEEPMIND", apply_url="x", source="a", date_found="x")
    j2 = Job(title="ai engineer", company="deepmind", apply_url="y", source="b", date_found="y")
    assert j1.normalized_key() == j2.normalized_key()


def test_job_with_salary():
    job = Job(
        title="ML Engineer",
        company="Revolut",
        salary_min=60000,
        salary_max=80000,
        apply_url="https://example.com",
        source="adzuna",
        date_found="2024-01-01T00:00:00Z",
    )
    assert job.salary_min == 60000
    assert job.salary_max == 80000


# ---- Company cleaning tests ----


def test_company_empty_becomes_unknown():
    job = Job(title="AI Engineer", company="", apply_url="x", source="a", date_found="x")
    assert job.company == "Unknown"


def test_company_nan_becomes_unknown():
    job = Job(title="AI Engineer", company="nan", apply_url="x", source="a", date_found="x")
    assert job.company == "Unknown"


def test_company_none_str_becomes_unknown():
    job = Job(title="AI Engineer", company="None", apply_url="x", source="a", date_found="x")
    assert job.company == "Unknown"


def test_company_whitespace_becomes_unknown():
    job = Job(title="AI Engineer", company="   ", apply_url="x", source="a", date_found="x")
    assert job.company == "Unknown"


def test_company_valid_unchanged():
    job = Job(title="AI Engineer", company="DeepMind", apply_url="x", source="a", date_found="x")
    assert job.company == "DeepMind"


# ---- HTML decoding tests ----


def test_html_decode_title():
    job = Job(title="AI &amp; ML Engineer", company="Test", apply_url="x", source="a", date_found="x")
    assert job.title == "AI & ML Engineer"


def test_html_decode_company():
    job = Job(title="Engineer", company="Smith &amp; Sons", apply_url="x", source="a", date_found="x")
    assert job.company == "Smith & Sons"


# ---- Salary is stored as stated, never clamped ----
#
# A clamp used to live in `Job.__post_init__` and judged every number as if it
# were GBP-annual: it destroyed an NHS hourly rate (30.27 < 10,000) while
# waving that job's maximum through untouched (45 is not > 500,000), so the row
# advertised "45" as a salary. Universal Shelf step 2 moved the policy into the
# gate, which annualised and converted first; slice 5 (#483) deleted the gate
# with the rest of the sourcing era.
#
# What remains is the invariant that survived all three states and is the one
# the product still needs: a brought ad's salary is stored EXACTLY as the ad
# stated it. Job360 does not reinterpret the number.


def test_job_stores_the_stated_salary_untouched():
    job = Job(
        title="Pharmacist", company="NHS", apply_url="x", source="user_brought",
        date_found="x", salary_min=30.27, salary_max=45.0,
    )
    assert job.salary_min == 30.27
    assert job.salary_max == 45.0


def test_job_stores_a_normal_annual_band_untouched():
    job = Job(
        title="AI Engineer", company="Test", apply_url="x", source="user_brought",
        date_found="x", salary_min=60000, salary_max=90000,
    )
    assert job.salary_min == 60000
    assert job.salary_max == 90000


# ---- Region suffix stripping tests ----


def test_normalized_key_strips_region_suffix():
    """'Barclays UK' should normalize to 'barclays'."""
    job = Job(title="AI Engineer", company="Barclays UK", apply_url="x", source="a", date_found="x")
    company, _ = job.normalized_key()
    assert company == "barclays"


def test_normalized_key_strips_region_suffix_variants():
    for suffix in ["UK", "US", "USA", "DE", "SG", "EU", "EMEA", "APAC", "Global", "International"]:
        job = Job(title="Data Scientist", company=f"Acme {suffix}", apply_url="x", source="a", date_found="x")
        company, _ = job.normalized_key()
        assert company == "acme", f"Failed to strip region suffix '{suffix}'"


# ---- Normalization divergence documentation tests (F-015, F-030) ----


def test_normalized_key_consistency():
    """normalized_key() produces consistent results for equivalent companies."""
    j1 = Job(title="AI Engineer", company="DeepMind Ltd", apply_url="x", source="a", date_found="x")
    j2 = Job(title="AI Engineer", company="DeepMind", apply_url="x", source="a", date_found="x")
    assert j1.normalized_key() == j2.normalized_key()


def test_normalized_key_does_not_strip_seniority():
    """normalized_key() intentionally does NOT strip seniority prefixes.

    Hard rule #1: this function IS the DB UNIQUE constraint. "Senior AI
    Engineer" and "AI Engineer" at the same company are two different ads and
    must stay two rows — collapsing them would make one user's paste land on
    another's job.
    """
    j1 = Job(title="Senior AI Engineer", company="DeepMind", apply_url="x", source="a", date_found="x")
    j2 = Job(title="AI Engineer", company="DeepMind", apply_url="x", source="a", date_found="x")
    assert j1.normalized_key() != j2.normalized_key(), "Seniority should NOT be stripped by normalized_key"


def test_normalized_key_caps_component_length():
    """Live failure 2026-07-30: one scraped job's title blew Postgres's btree
    index-row limit ("index row size 3128 exceeds maximum 2704" on the jobs
    UNIQUE(normalized_company, normalized_title)) and that single poison row
    aborted the ENTIRE catalog insert — the whole 257s fetch thrown away.

    Rule #1 safety: the DB UNIQUE constraint is the one consumer of this
    function, so capping here caps exactly what the index sees.
    """
    monster = Job(
        title="Senior Engineer " * 300,  # ~4,800 chars of scraped garbage
        company="Acme " * 300,
        apply_url="https://x.test/1",
        source="test",
        date_found="2026-07-30",
    )
    company, title = monster.normalized_key()
    assert len(company) <= 300
    assert len(title) <= 300
    # Two monsters that share their first 300 chars must still collide — that
    # is the dedup working, not a loss: page-of-text titles differing only
    # after char 300 are the same scraped listing.
    monster2 = Job(
        title="Senior Engineer " * 300 + "different tail",
        company="Acme " * 300,
        apply_url="https://x.test/2",
        source="test",
        date_found="2026-07-30",
    )
    assert monster.normalized_key() == monster2.normalized_key()
    # And sane keys are untouched.
    normal = Job(
        title="Software Engineer",
        company="Acme Ltd",
        apply_url="https://x.test/3",
        source="test",
        date_found="2026-07-30",
    )
    assert normal.normalized_key() == ("acme", "software engineer")
