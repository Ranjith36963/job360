import csv
import tempfile
import asyncio
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
    loop = asyncio.get_event_loop()
    jobs = [_make_job(), _make_job(title="ML Engineer", company="Revolut")]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    loop.run_until_complete(export_to_csv(jobs, path))
    with open(path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2


def test_csv_export_correct_headers():
    loop = asyncio.get_event_loop()
    jobs = [_make_job()]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    loop.run_until_complete(export_to_csv(jobs, path))
    with open(path) as f:
        reader = csv.reader(f)
        headers = next(reader)
    expected = [
        "job_title", "company", "location", "salary",
        "match_score", "apply_url", "source", "date_found", "visa_flag",
    ]
    assert headers == expected


def test_csv_export_salary_format():
    loop = asyncio.get_event_loop()
    jobs = [_make_job(salary_min=60000, salary_max=80000)]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    loop.run_until_complete(export_to_csv(jobs, path))
    with open(path) as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["salary"] == "60000-80000"


def test_csv_export_empty_salary():
    loop = asyncio.get_event_loop()
    jobs = [_make_job(salary_min=None, salary_max=None)]
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    loop.run_until_complete(export_to_csv(jobs, path))
    with open(path) as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["salary"] == ""
