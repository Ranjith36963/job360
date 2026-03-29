import csv
import tempfile
from datetime import datetime, timezone

from src.models import Job
from src.storage.csv_export import export_to_csv


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer",
        company="DeepMind",
        location="London",
        salary_min=70000,
        salary_max=100000,
        apply_url="https://example.com/job",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        description="AI role requiring Python",
        match_score=85,
        visa_flag=True,
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_csv_export_creates_file():
    jobs = [_make_job(), _make_job(title="ML Engineer", company="Revolut")]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    export_to_csv(jobs, path)
    with open(path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2


def test_csv_export_correct_headers():
    jobs = [_make_job()]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    export_to_csv(jobs, path)
    with open(path) as f:
        reader = csv.reader(f)
        headers = next(reader)
    expected = [
        "job_title", "company", "location", "salary",
        "match_score", "role", "skill", "seniority", "experience",
        "credentials", "location_score", "recency", "semantic", "penalty",
        "apply_url", "source", "date_found", "visa_flag",
        "matched_skills", "missing_required", "missing_preferred",
        "transferable_skills", "job_type", "experience_level",
    ]
    assert headers == expected


def test_csv_export_salary_format():
    jobs = [_make_job(salary_min=60000, salary_max=80000)]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    export_to_csv(jobs, path)
    with open(path) as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["salary"] == "60000-80000"


def test_csv_export_empty_salary():
    jobs = [_make_job(salary_min=None, salary_max=None)]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    export_to_csv(jobs, path)
    with open(path) as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["salary"] == ""
