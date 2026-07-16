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
async def test_purge_deletes_derived_children_but_keeps_user_records():
    """docs/fable/02 D4 — purging a job must not orphan its catalog-derived rows.

    The pg shim strips every FK clause (incl. ON DELETE CASCADE), so nothing
    cascades at the DB level: before this fix, job_enrichment / job_embeddings /
    user_feed / user_notification_digests rows survived forever pointing at a
    deleted job. `user_feed` is the worst — one row per user per job.

    The flip side (rule #3): the USER's own records must SURVIVE the catalog
    purge. An application (Kanban entry) must not vanish because the shared job
    listing aged out.
    """
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        cur = await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, date_found, "
            "normalized_company, normalized_title, first_seen, last_seen_at) "
            "VALUES ('Stale', 'Co', '', ?, 'reed', ?, ?, ?, ?, ?)",
            ("https://e/stale", old, "co", "stale", old, old),
        )
        cur2 = await db._conn.execute(
            "SELECT id FROM jobs WHERE normalized_title = 'stale'"
        )
        job_id = (await cur2.fetchone())[0]
        uid = "00000000-0000-0000-0000-000000000001"

        # Catalog-derived children → must be purged with the job.
        await db._conn.execute(
            "INSERT INTO job_enrichment (job_id, title_canonical, category) VALUES (?, 'x', 'eng')",
            (job_id,),
        )
        await db._conn.execute(
            "INSERT INTO job_embeddings (job_id, model_version) VALUES (?, 'v1')", (job_id,)
        )
        await db._conn.execute(
            "INSERT INTO user_feed (user_id, job_id, score, bucket) VALUES (?, ?, 50, 'good')",
            (uid, job_id),
        )
        await db._conn.execute(
            "INSERT INTO user_notification_digests (user_id, channel, job_id) VALUES (?, 'email', ?)",
            (uid, job_id),
        )
        # User-authored record → must SURVIVE. Insert without user_id so this works
        # against both the init_db schema and the post-0002 one (which defaults it).
        await db._conn.execute(
            "INSERT INTO applications (job_id, stage, created_at, updated_at) "
            "VALUES (?, 'applied', ?, ?)",
            (job_id, old, old),
        )
        await db._conn.commit()

        deleted = await db.purge_old_jobs(days=30)
        assert deleted == 1

        for table in ("job_enrichment", "job_embeddings", "user_feed", "user_notification_digests"):
            c = await db._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE job_id = ?", (job_id,))
            assert (await c.fetchone())[0] == 0, f"{table} orphaned a row for the purged job"

        c = await db._conn.execute(
            "SELECT COUNT(*) FROM applications WHERE job_id = ?", (job_id,)
        )
        assert (await c.fetchone())[0] == 1, "user's application must survive the catalog purge"
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
