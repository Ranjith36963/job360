"""Integration tests for the staleness state-machine writer (Step-1.5 S1.5-A/B/C).

These tests fail fast if the deferred Pillar-3-Batch-1 writer ever stops
running `transition()` from the absence sweep. Pure ghost_detection unit
tests live in `test_ghost_detection.py`; this file deliberately exercises
the DB write path so a regression in `mark_missed_for_source` would surface
here even when the pure transition() function still passes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.models import Job
from src.repositories.database import JobDatabase


@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def _aged_job(hours_old: float, **overrides) -> Job:
    """Build a Job whose `last_seen_at` is ``hours_old`` hours in the past."""
    last_seen = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()
    defaults = dict(
        title="ML Engineer",
        company="Acme",
        apply_url="https://acme.example/job/1",
        source="acme_ats",
        date_found=datetime.now(timezone.utc).isoformat(),
        last_seen_at=last_seen,
        first_seen_at=last_seen,
    )
    defaults.update(overrides)
    return Job(**defaults)


def _staleness_for(db: JobDatabase, key: tuple[str, str]) -> str | None:
    async def _q():
        cursor = await db._conn.execute(
            "SELECT staleness_state FROM jobs " "WHERE normalized_company = ? AND normalized_title = ?",
            key,
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    return asyncio.run(_q())


def test_single_miss_does_not_advance_state(db):
    """One missed cycle is noise — state must remain 'active'."""
    job = _aged_job(hours_old=20.0)
    asyncio.run(db.insert_job(job))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    assert _staleness_for(db, job.normalized_key()) == "active"


def test_two_misses_and_12h_age_promotes_to_possibly_stale(db):
    """misses>=2 + age>=12h must promote to 'possibly_stale'."""
    job = _aged_job(hours_old=15.0)
    asyncio.run(db.insert_job(job))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    assert _staleness_for(db, job.normalized_key()) == "possibly_stale"


def test_three_misses_and_24h_age_promotes_to_likely_stale(db):
    """misses>=3 + age>=24h must promote to 'likely_stale'."""
    job = _aged_job(hours_old=30.0)
    asyncio.run(db.insert_job(job))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    assert _staleness_for(db, job.normalized_key()) == "likely_stale"


def test_seeing_job_again_resets_to_active(db):
    """update_last_seen() resets the misses counter and state — Step-1 baseline."""
    job = _aged_job(hours_old=15.0)
    asyncio.run(db.insert_job(job))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    assert _staleness_for(db, job.normalized_key()) == "possibly_stale"
    asyncio.run(db.update_last_seen(job.normalized_key()))
    assert _staleness_for(db, job.normalized_key()) == "active"


def test_confirmed_expired_is_sticky(db):
    """A job in 'confirmed_expired' must not be demoted by an absence sweep."""
    job = _aged_job(hours_old=30.0)
    asyncio.run(db.insert_job(job))
    asyncio.run(db.update_staleness_state(_id_for(db, job.normalized_key()), "confirmed_expired"))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    assert _staleness_for(db, job.normalized_key()) == "confirmed_expired"


def _id_for(db: JobDatabase, key: tuple[str, str]) -> int:
    async def _q():
        cursor = await db._conn.execute(
            "SELECT id FROM jobs " "WHERE normalized_company = ? AND normalized_title = ?",
            key,
        )
        row = await cursor.fetchone()
        return row[0]

    return asyncio.run(_q())


def _null_timestamps(db: JobDatabase, key: tuple[str, str], *, null_first: bool) -> None:
    """Force last_seen_at (and optionally first_seen_at) to NULL for one row —
    reproduces the M6 premise: catalog rows that lack the timestamp."""
    # Two fixed literal statements (no interpolation) so this stays SQL-injection
    # free even under ruff S608 — the WHERE values are still bound params.
    async def _q():
        await db._conn.execute(
            "UPDATE jobs SET last_seen_at = NULL "
            "WHERE normalized_company = ? AND normalized_title = ?",
            key,
        )
        if null_first:
            await db._conn.execute(
                "UPDATE jobs SET first_seen_at = NULL "
                "WHERE normalized_company = ? AND normalized_title = ?",
                key,
            )
        await db._conn.commit()

    asyncio.run(_q())


def test_null_last_seen_still_advances_by_misses_alone(db):
    """M6 (second path): a job with NULL last_seen_at AND NULL first_seen_at must
    still go stale on repeated misses via the pipeline sweep. Pre-fix,
    mark_missed_for_source computed age_hours=0.0 and transition(misses, 0.0)
    could NEVER promote, so the job stayed 'active' forever — the same M6 bug the
    nightly sweep was fixed for, in the more-frequent pipeline path."""
    job = _aged_job(hours_old=30.0)
    asyncio.run(db.insert_job(job))
    _null_timestamps(db, job.normalized_key(), null_first=True)
    for _ in range(3):
        asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    assert _staleness_for(db, job.normalized_key()) == "likely_stale"


def test_null_last_seen_falls_back_to_first_seen_age(db):
    """When last_seen_at is NULL but first_seen_at is old, the sweep must use
    first_seen_at as the age proxy (not 0.0), so an old, repeatedly-missed job
    promotes. Pre-fix it stayed 'active' because age was pinned to 0.0."""
    job = _aged_job(hours_old=30.0)
    asyncio.run(db.insert_job(job))
    _null_timestamps(db, job.normalized_key(), null_first=False)  # first_seen_at kept (30h old)
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    asyncio.run(db.mark_missed_for_source("acme_ats", seen_keys=set()))
    assert _staleness_for(db, job.normalized_key()) == "likely_stale"


def test_seen_job_not_marked_missed(db):
    """A job present in seen_keys must be skipped entirely by the sweep."""
    job = _aged_job(hours_old=30.0)
    asyncio.run(db.insert_job(job))
    asyncio.run(
        db.mark_missed_for_source(
            "acme_ats",
            seen_keys={job.normalized_key()},
        )
    )
    # consecutive_misses must remain 0 — no state advancement.
    assert _staleness_for(db, job.normalized_key()) == "active"
