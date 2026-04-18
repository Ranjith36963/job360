"""FeedService tests — Phase 3 (reads) + upsert used by Phase 4."""
import os
import tempfile

import aiosqlite
import pytest

from migrations import runner
from src.services.feed import FeedService


@pytest.fixture
async def feed_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Legacy schema must exist before 0002 rebuild clauses fire.
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            """
        )
        await db.commit()
    await runner.up(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("alice", "a@x", "!"),
        )
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("bob", "b@x", "!"),
        )
        await db.commit()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


async def _seed(db_path, rows):
    async with aiosqlite.connect(db_path) as db:
        svc = FeedService(db)
        for user_id, job_id, score, bucket, status in rows:
            await svc.upsert_feed_row(
                user_id=user_id, job_id=job_id, score=score, bucket=bucket
            )
            if status != "active":
                await svc.update_status(user_id, job_id, status)


@pytest.mark.asyncio
async def test_list_for_user_returns_active_only(feed_db):
    await _seed(feed_db, [
        ("alice", 1, 85, "24h", "active"),
        ("alice", 2, 70, "24h", "skipped"),
    ])
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        rows = await svc.list_for_user("alice")
    assert len(rows) == 1
    assert rows[0].job_id == 1


@pytest.mark.asyncio
async def test_list_for_user_filters_by_bucket(feed_db):
    await _seed(feed_db, [
        ("alice", 1, 85, "24h", "active"),
        ("alice", 2, 70, "3_7d", "active"),
    ])
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        rows = await svc.list_for_user("alice", bucket="24h")
    assert [r.job_id for r in rows] == [1]


@pytest.mark.asyncio
async def test_list_for_user_scoped_per_user(feed_db):
    await _seed(feed_db, [
        ("alice", 1, 85, "24h", "active"),
        ("bob", 2, 90, "24h", "active"),
    ])
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        alice_rows = await svc.list_for_user("alice")
        bob_rows = await svc.list_for_user("bob")
    assert [r.job_id for r in alice_rows] == [1]
    assert [r.job_id for r in bob_rows] == [2]


@pytest.mark.asyncio
async def test_list_pending_notifications_filters_by_threshold(feed_db):
    await _seed(feed_db, [
        ("alice", 1, 60, "24h", "active"),
        ("alice", 2, 85, "24h", "active"),
        ("alice", 3, 95, "24h", "active"),
    ])
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        rows = await svc.list_pending_notifications("alice", min_score=80)
    assert sorted(r.job_id for r in rows) == [2, 3]


@pytest.mark.asyncio
async def test_mark_notified_excludes_from_subsequent_pending(feed_db):
    await _seed(feed_db, [
        ("alice", 1, 85, "24h", "active"),
        ("alice", 2, 90, "24h", "active"),
    ])
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        pending = await svc.list_pending_notifications("alice", min_score=80)
        await svc.mark_notified([r.id for r in pending])
        pending_after = await svc.list_pending_notifications("alice", min_score=80)
    assert len(pending) == 2
    assert pending_after == []


@pytest.mark.asyncio
async def test_update_status_skipped_hides_from_dashboard(feed_db):
    await _seed(feed_db, [
        ("alice", 1, 85, "24h", "active"),
    ])
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        await svc.update_status("alice", 1, "skipped")
        rows = await svc.list_for_user("alice")
    assert rows == []


@pytest.mark.asyncio
async def test_cascade_stale_marks_job_across_users(feed_db):
    await _seed(feed_db, [
        ("alice", 42, 85, "24h", "active"),
        ("bob", 42, 70, "24h", "active"),
    ])
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        updated = await svc.cascade_stale(42)
        alice_rows = await svc.list_for_user("alice")
        bob_rows = await svc.list_for_user("bob")
    assert updated == 2
    assert alice_rows == [] and bob_rows == []


@pytest.mark.asyncio
async def test_upsert_is_idempotent_on_conflict(feed_db):
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        first = await svc.upsert_feed_row(
            user_id="alice", job_id=1, score=60, bucket="24h"
        )
        second = await svc.upsert_feed_row(
            user_id="alice", job_id=1, score=85, bucket="24_48h"
        )
        rows = await svc.list_for_user("alice")
    assert first == second
    assert len(rows) == 1
    assert rows[0].score == 85  # upsert updated the score
    assert rows[0].bucket == "24_48h"
