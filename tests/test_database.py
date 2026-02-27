import pytest
import asyncio
from datetime import datetime, timezone

from src.models import Job
from src.storage.database import JobDatabase


@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(database.init_db())
    yield database
    loop.run_until_complete(database.close())


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer",
        company="DeepMind",
        apply_url="https://example.com/job",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London",
        description="AI role",
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_init_creates_tables(db):
    loop = asyncio.get_event_loop()
    tables = loop.run_until_complete(db.get_tables())
    assert "jobs" in tables
    assert "run_log" in tables


def test_insert_and_check_seen(db):
    loop = asyncio.get_event_loop()
    job = _make_job()
    key = job.normalized_key()
    assert loop.run_until_complete(db.is_job_seen(key)) is False
    loop.run_until_complete(db.insert_job(job))
    assert loop.run_until_complete(db.is_job_seen(key)) is True


def test_duplicate_insert_ignored(db):
    loop = asyncio.get_event_loop()
    job = _make_job()
    loop.run_until_complete(db.insert_job(job))
    loop.run_until_complete(db.insert_job(job))  # should not raise
    count = loop.run_until_complete(db.count_jobs())
    assert count == 1


def test_insert_different_jobs(db):
    loop = asyncio.get_event_loop()
    j1 = _make_job(title="AI Engineer", company="DeepMind")
    j2 = _make_job(title="ML Engineer", company="Revolut")
    loop.run_until_complete(db.insert_job(j1))
    loop.run_until_complete(db.insert_job(j2))
    count = loop.run_until_complete(db.count_jobs())
    assert count == 2


def test_log_run(db):
    loop = asyncio.get_event_loop()
    stats = {
        "total_found": 50,
        "new_jobs": 10,
        "per_source": {"reed": 20, "adzuna": 30},
    }
    loop.run_until_complete(db.log_run(stats))
    runs = loop.run_until_complete(db.get_run_logs())
    assert len(runs) == 1
    assert runs[0]["total_found"] == 50


def test_get_new_jobs_since(db):
    loop = asyncio.get_event_loop()
    j1 = _make_job(title="AI Engineer", company="DeepMind")
    loop.run_until_complete(db.insert_job(j1))
    jobs = loop.run_until_complete(db.get_new_jobs_since(hours=1))
    assert len(jobs) == 1
