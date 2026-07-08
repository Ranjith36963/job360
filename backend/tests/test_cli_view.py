"""Tests for src/cli_view.py — Rich terminal view."""

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.cli_view import _load_jobs_sync, display_jobs


def _create_test_db(db_path: str, jobs: list[dict]):
    """Create a test SQLite DB with jobs."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            salary_min REAL,
            salary_max REAL,
            description TEXT DEFAULT '',
            apply_url TEXT NOT NULL,
            source TEXT NOT NULL,
            date_found TEXT NOT NULL,
            match_score INTEGER DEFAULT 0,
            visa_flag INTEGER DEFAULT 0,
            normalized_company TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            UNIQUE(normalized_company, normalized_title)
        )
    """)
    for job in jobs:
        conn.execute(
            """INSERT OR IGNORE INTO jobs
            (title, company, location, salary_min, salary_max, description,
             apply_url, source, date_found, match_score, visa_flag,
             normalized_company, normalized_title, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job["title"], job["company"], job.get("location", ""),
                job.get("salary_min"), job.get("salary_max"),
                job.get("description", ""),
                job["apply_url"], job["source"], job["date_found"],
                job.get("match_score", 50), int(job.get("visa_flag", False)),
                job["company"].lower(), job["title"].lower(),
                job.get("first_seen", datetime.now(timezone.utc).isoformat()),
            ),
        )
    conn.commit()
    conn.close()


def _sample_jobs():
    """Create sample job data."""
    now = datetime.now(timezone.utc)
    return [
        {
            "title": "AI Engineer",
            "company": "TestCo",
            "location": "London",
            "salary_min": 60000,
            "salary_max": 90000,
            "description": "Python PyTorch role",
            "apply_url": "https://example.com/1",
            "source": "greenhouse",
            "date_found": (now - timedelta(hours=5)).isoformat(),
            "match_score": 80,
            "visa_flag": True,
            "first_seen": (now - timedelta(hours=5)).isoformat(),
        },
        {
            "title": "ML Engineer",
            "company": "OldCo",
            "location": "Manchester",
            "apply_url": "https://example.com/2",
            "source": "reed",
            "date_found": (now - timedelta(days=10)).isoformat(),
            "match_score": 60,
            "visa_flag": False,
            "first_seen": (now - timedelta(days=10)).isoformat(),
        },
        {
            "title": "Data Scientist",
            "company": "NewCo",
            "location": "Remote",
            "apply_url": "https://example.com/3",
            "source": "adzuna",
            "date_found": (now - timedelta(hours=30)).isoformat(),
            "match_score": 45,
            "visa_flag": True,
            "first_seen": (now - timedelta(hours=30)).isoformat(),
        },
    ]


def test_load_jobs_sync_returns_recent():
    """_load_jobs_sync should return recent jobs and filter old ones."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _create_test_db(db_path, _sample_jobs())
        jobs = _load_jobs_sync(db_path=db_path, days=7, min_score=30)
        # Should include the recent job but not the 10-day old one
        titles = [j["title"] for j in jobs]
        assert "AI Engineer" in titles
        assert "ML Engineer" not in titles  # older than 7 days
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_load_jobs_sync_min_score_filter():
    """_load_jobs_sync should respect min_score."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _create_test_db(db_path, _sample_jobs())
        jobs = _load_jobs_sync(db_path=db_path, days=7, min_score=50)
        titles = [j["title"] for j in jobs]
        assert "AI Engineer" in titles
        assert "Data Scientist" not in titles  # score 45 < 50
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_load_jobs_sync_returns_empty_on_db_error(monkeypatch):
    """_load_jobs_sync degrades to an empty list when the query raises.

    Under SQLite a missing file made ``SELECT * FROM jobs`` raise "no such
    table", so the old test pointed at a nonexistent path and expected ``[]``.
    Under Postgres the path is advisory and the shared catalog is always
    present, so we instead force the query to fail — exercising the same
    graceful-degradation branch (``except Exception: return []``).
    """
    import src.cli_view as cli_view

    class _BadConn:
        row_factory = None

        def execute(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

        def close(self):
            pass

    monkeypatch.setattr(cli_view.sqlite3, "connect", lambda *a, **k: _BadConn())
    assert _load_jobs_sync(db_path="unused-path.db") == []


def test_display_jobs_runs_without_error(capsys, tmp_path):
    """display_jobs should run without error even with no DB."""
    display_jobs(db_path=str(tmp_path / "nonexistent_job360_test.db"))
    captured = capsys.readouterr()
    assert "No jobs found" in captured.out or "Job360" in captured.out


def test_display_jobs_with_data(capsys):
    """display_jobs should output bucketed results with test data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _create_test_db(db_path, _sample_jobs())
        display_jobs(db_path=db_path, hours=168, min_score=30)
        captured = capsys.readouterr()
        assert "Job360" in captured.out
    finally:
        Path(db_path).unlink(missing_ok=True)
