"""Tests for the SQLite write-retry helper (Finding #11 — 'database is locked').

Under concurrent writes SQLite raises OperationalError('database is locked').
Before, rescore + the judge caught this and DROPPED the row. `with_write_retry`
retries the write a few times so a transient lock no longer loses data.
"""
import pytest

from src.repositories import pgsync
from src.repositories.db_retry import with_write_retry


@pytest.mark.asyncio
async def test_retries_then_succeeds_on_locked():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise pgsync.OperationalError("database is locked")
        return "ok"

    result = await with_write_retry(flaky, attempts=5)
    assert result == "ok"
    assert calls["n"] == 3  # failed twice, succeeded on the 3rd


@pytest.mark.asyncio
async def test_gives_up_after_attempts_and_reraises():
    async def always_locked():
        raise pgsync.OperationalError("database is locked")

    with pytest.raises(pgsync.OperationalError):
        await with_write_retry(always_locked, attempts=3)


@pytest.mark.asyncio
async def test_non_lock_error_not_retried():
    calls = {"n": 0}

    async def bad_sql():
        calls["n"] += 1
        raise pgsync.OperationalError("no such table: foo")

    with pytest.raises(pgsync.OperationalError):
        await with_write_retry(bad_sql, attempts=5)
    assert calls["n"] == 1  # not a lock → no retry
