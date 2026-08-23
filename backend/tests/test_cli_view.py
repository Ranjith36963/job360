"""Tests for src/cli_view.py — Rich terminal view."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.cli_view import _load_jobs_sync, display_jobs
from src.repositories import pgsync


def _create_test_db(db_path: str, jobs: list[dict]):
    """Create a jobs table + rows for this db_path.

    Not a file: under ``pg.TEST_MODE`` the db_path string is hashed to its own
    Postgres schema, so each distinct path behaves like a private database.
    """
    conn = pgsync.connect(db_path)
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


UNKNOWN_DB_PATH = "/tmp/nonexistent_job360_test.db"


def _shared_catalog_job_count() -> int:
    """READ-ONLY: how many rows the shared ``public`` catalog holds right now.

    Never writes, never deletes. ``db_path=None`` maps to the ``public`` schema
    (``pg.schema_for_path``), which on a developer machine is the real dev
    catalog (thousands of rows) and on fresh CI is empty.
    """
    conn = pgsync.connect(None)
    try:
        has_table = conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'jobs'"
        ).fetchone()[0]
        if not has_table:
            return 0
        return int(conn.execute("SELECT count(*) FROM public.jobs").fetchone()[0])
    finally:
        conn.close()


def test_load_jobs_sync_unknown_db_path_never_leaks_another_schema():
    """An unknown db_path yields NO jobs — never another schema's rows.

    This is NOT a "missing file" test. There is no SQLite here. ``conftest.py``
    sets ``pg.TEST_MODE``, so a db_path *string* is hashed to a POSTGRES SCHEMA
    (``pg.schema_for_path``). A path that was never used is therefore not an
    error at all: it simply names a schema, which ``connect()`` creates empty.

    The guarantee that actually matters is isolation. Postgres resolves an
    unqualified ``FROM jobs`` by WALKING the search_path, so if the path ever
    regains a ``, public`` fallback, it walks past the empty test schema and
    binds to the SHARED catalog — and this call returns live rows while looking
    perfectly healthy. Measured before that fallback was removed: this exact
    function returned 6047 shared rows.
    """
    # 1. Structural proof — independent of what any database happens to contain,
    #    so it fails identically on a loaded dev box and on empty CI.
    conn = pgsync.connect(UNKNOWN_DB_PATH)
    try:
        own_tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'jobs'"
        ).fetchone()[0]
        assert own_tables == 0, "precondition: this path must map to a schema with no jobs table"

        resolved = conn.execute("SELECT to_regclass('jobs')::text").fetchone()[0]
        assert resolved is None, (
            f"search_path leaks outside the test schema: unqualified 'jobs' "
            f"resolved to {resolved!r}. Someone re-added a fallback to "
            f"SET search_path in pg.py/pgsync.py."
        )
    finally:
        conn.close()

    # 2. Behavioural proof, with the filters widened so ONLY isolation can decide
    #    the answer. The original narrow filters were what hid the bug: they
    #    returned [] by luck, because `first_seen >= now-7d` and `match_score >= 30`
    #    happened to intersect to zero rows in the shared catalog on that day.
    assert _load_jobs_sync(db_path=UNKNOWN_DB_PATH, days=99999, min_score=0) == []

    # 3. And the narrow call the CLI actually makes.
    assert _load_jobs_sync(db_path=UNKNOWN_DB_PATH) == []


def test_load_jobs_sync_unknown_db_path_ignores_populated_shared_catalog():
    """Isolation must hold when the shared catalog demonstrably HAS rows.

    Read-only against ``public``. Skips where there is nothing to leak (fresh
    CI); the structural assertion in the test above is the machine-independent
    guard that always runs.
    """
    shared_rows = _shared_catalog_job_count()
    if not shared_rows:
        pytest.skip("shared public catalog is empty here — nothing could leak")

    jobs = _load_jobs_sync(db_path=UNKNOWN_DB_PATH, days=99999, min_score=0)
    assert jobs == [], (
        f"leaked {len(jobs)} rows out of the shared public catalog "
        f"({shared_rows} rows) into a test pointed at an unused db_path"
    )


def test_display_jobs_runs_without_error(capsys):
    """display_jobs should run without error against an unused db_path."""
    display_jobs(db_path=UNKNOWN_DB_PATH)
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
