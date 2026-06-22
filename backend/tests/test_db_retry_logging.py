"""DB-layer logging (full-lifecycle logging piece B).

`with_write_retry` previously retried SQLite locks silently. Now each retry and
the final give-up are logged on `job360.db` so lock contention is visible in
data/logs/ instead of invisible.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import pytest

from src.repositories.db_retry import with_write_retry


def test_retries_are_logged_then_succeeds(caplog):
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    with caplog.at_level(logging.WARNING, logger="job360.db"):
        result = asyncio.run(with_write_retry(fn, attempts=5, base_delay=0))

    assert result == "ok"
    retries = [r for r in caplog.records if getattr(r, "event", "") == "db_write_retry"]
    assert len(retries) == 2  # failed twice, then the 3rd call succeeded


def test_final_lock_failure_is_logged_and_raised(caplog):
    async def fn():
        raise sqlite3.OperationalError("database is locked")

    with caplog.at_level(logging.ERROR, logger="job360.db"):
        with pytest.raises(sqlite3.OperationalError):
            asyncio.run(with_write_retry(fn, attempts=2, base_delay=0))

    fails = [r for r in caplog.records if getattr(r, "event", "") == "db_write_failed"]
    assert fails, "final lock failure was not logged"


def test_non_lock_error_not_retried_or_logged(caplog):
    async def fn():
        raise sqlite3.OperationalError("no such column: bogus")

    with caplog.at_level(logging.WARNING, logger="job360.db"):
        with pytest.raises(sqlite3.OperationalError):
            asyncio.run(with_write_retry(fn, attempts=3, base_delay=0))

    # A bad-SQL error is raised immediately — no retry/fail lock logs.
    assert not [r for r in caplog.records if getattr(r, "event", "").startswith("db_write")]
