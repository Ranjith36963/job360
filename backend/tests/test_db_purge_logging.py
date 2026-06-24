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
