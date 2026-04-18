"""Tests for the 5-column date model + ghost detection schema migration.

Part of Pillar 3 Batch 1. Adds:
  posted_at, first_seen_at, last_seen_at, last_updated_at,
  date_confidence, date_posted_raw (date model)
  consecutive_misses, staleness_state (ghost detection hooks)
"""
import asyncio

import pytest

from src.repositories.database import JobDatabase


@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def _cols(db):
    cursor = asyncio.run(db._conn.execute("PRAGMA table_info(jobs)"))
    rows = asyncio.run(cursor.fetchall())
    return {row[1]: row for row in rows}


def test_jobs_has_posted_at(db):
    assert "posted_at" in _cols(db)


def test_jobs_has_first_seen_at(db):
    assert "first_seen_at" in _cols(db)


def test_jobs_has_last_seen_at(db):
    assert "last_seen_at" in _cols(db)


def test_jobs_has_last_updated_at(db):
    assert "last_updated_at" in _cols(db)


def test_jobs_has_date_confidence(db):
    assert "date_confidence" in _cols(db)


def test_jobs_has_date_posted_raw(db):
    assert "date_posted_raw" in _cols(db)


def test_jobs_has_consecutive_misses(db):
    assert "consecutive_misses" in _cols(db)


def test_jobs_has_staleness_state(db):
    assert "staleness_state" in _cols(db)


def test_date_confidence_default_is_low(db):
    row = _cols(db)["date_confidence"]
    # row = (cid, name, type, notnull, dflt_value, pk)
    dflt = row[4]
    assert dflt is not None and "low" in str(dflt)


def test_staleness_state_default_is_active(db):
    row = _cols(db)["staleness_state"]
    dflt = row[4]
    assert dflt is not None and "active" in str(dflt)


def test_consecutive_misses_default_is_zero(db):
    row = _cols(db)["consecutive_misses"]
    dflt = row[4]
    assert dflt is not None and str(dflt).strip() == "0"


def test_migrate_is_idempotent(db):
    """Running _migrate() twice should be a no-op (not raise, not duplicate columns)."""
    asyncio.run(db._migrate())
    asyncio.run(db._migrate())
    cols = _cols(db)
    # Any of the new columns listed once
    assert "posted_at" in cols
    assert "date_confidence" in cols


def test_existing_first_seen_column_preserved(db):
    """Legacy first_seen and date_found columns MUST remain for back-compat."""
    cols = _cols(db)
    assert "first_seen" in cols
    assert "date_found" in cols
