"""DB-layer purge logging (full-lifecycle logging gap J).

Bulk deletion (purge_old_jobs) used to run silently. It now logs the deleted
count on job360.db.repo, so data removal is never invisible.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from src.repositories.database import JobDatabase


@pytest.mark.asyncio
async def test_purge_old_jobs_logs_deletion(caplog):
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, date_found, "
            "normalized_company, normalized_title, first_seen) "
            "VALUES ('Old', 'Co', '', ?, 'reed', ?, ?, ?, ?)",
            ("https://e/old", old, "co", "old", old),
        )
        await db._conn.commit()

        with caplog.at_level(logging.INFO, logger="job360.db.repo"):
            deleted = await db.purge_old_jobs(days=30)

        assert deleted == 1
        recs = [r for r in caplog.records if getattr(r, "event", "") == "purge_old_jobs"]
        assert recs, "purge was not logged"
        assert recs[-1].deleted == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_purge_keeps_still_live_jobs():
    """docs/fable/02 — a job first seen long ago but STILL SEEN recently survives
    the purge (keyed on last_seen_at liveness, not first_seen ingestion)."""
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        # first_seen 60d ago BUT last_seen_at 2d ago → still live, must survive
        await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, date_found, "
            "normalized_company, normalized_title, first_seen, last_seen_at) "
            "VALUES ('Live', 'Co', '', ?, 'reed', ?, ?, ?, ?, ?)",
            ("https://e/live", old, "co", "live", old, recent),
        )
        # genuinely stale: last_seen_at 60d ago → must be purged
        await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, date_found, "
            "normalized_company, normalized_title, first_seen, last_seen_at) "
            "VALUES ('Stale', 'Co', '', ?, 'reed', ?, ?, ?, ?, ?)",
            ("https://e/stale", old, "co", "stale", old, old),
        )
        await db._conn.commit()

        deleted = await db.purge_old_jobs(days=30)
        assert deleted == 1  # only the stale one
        cur = await db._conn.execute("SELECT title FROM jobs")
        titles = sorted(r[0] for r in await cur.fetchall())
        assert titles == ["Live"], titles
    finally:
        await db.close()
