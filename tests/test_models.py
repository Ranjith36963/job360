from datetime import datetime, timezone
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
    assert job.match_score == 0
    assert job.visa_flag is False
    assert job.is_new is True


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


# ---- Salary outlier tests ----


def test_salary_low_nullified():
    job = Job(title="AI Engineer", company="Test", salary_min=50, salary_max=80000,
              apply_url="x", source="a", date_found="x")
    assert job.salary_min is None
    assert job.salary_max == 80000


def test_salary_high_nullified():
    job = Job(title="AI Engineer", company="Test", salary_min=50000, salary_max=999999,
              apply_url="x", source="a", date_found="x")
    assert job.salary_min == 50000
    assert job.salary_max is None


def test_salary_normal_unchanged():
    job = Job(title="AI Engineer", company="Test", salary_min=60000, salary_max=90000,
              apply_url="x", source="a", date_found="x")
    assert job.salary_min == 60000
    assert job.salary_max == 90000


def test_salary_boundary_kept():
    """10000 and 500000 are at the boundary and should be kept."""
    job = Job(title="AI Engineer", company="Test", salary_min=10000, salary_max=500000,
              apply_url="x", source="a", date_found="x")
    assert job.salary_min == 10000
    assert job.salary_max == 500000
