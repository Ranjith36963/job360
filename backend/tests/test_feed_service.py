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
    # FIX 6 — score is FROZEN when profile_version is the SAME (both None
    # here — SQLite IS treats NULL IS NULL as equal, so the score is frozen).
    # Previously this test asserted the score was OVERWRITTEN to 85; that was
    # the OLD contract.  Under the new contract, same version -> freeze the
    # first score.  bucket IS always updated regardless.
    async with aiosqlite.connect(feed_db) as db:
        svc = FeedService(db)
        first = await svc.upsert_feed_row(
            user_id="alice", job_id=1, score=60, bucket="24h"
        )
        second = await svc.upsert_feed_row(
            user_id="alice", job_id=1, score=85, bucket="24_48h"
        )
        rows = await svc.list_for_user("alice")
    assert first == second  # same row id
    assert len(rows) == 1
    # score is FROZEN at 60 — same profile_version (None IS None) -> no overwrite
    assert rows[0].score == 60
    assert rows[0].bucket == "24_48h"  # bucket is always updated


# ---------------------------------------------------------------------------
# Task 3 — profile_version stamping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_with_profile_version_stamps_column(feed_db):
    """profile_version=7 is persisted in the user_feed row."""
    async with aiosqlite.connect(feed_db) as db:
        db.row_factory = aiosqlite.Row
        svc = FeedService(db)
        await svc.upsert_feed_row(
            user_id="alice", job_id=10, score=70, bucket="24h", profile_version=7
        )
        cur = await db.execute(
            "SELECT profile_version FROM user_feed WHERE user_id = ? AND job_id = ?",
            ("alice", 10),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["profile_version"] == 7


@pytest.mark.asyncio
async def test_upsert_without_profile_version_leaves_null(feed_db):
    """Calling without profile_version kwarg must leave the column NULL."""
    async with aiosqlite.connect(feed_db) as db:
        db.row_factory = aiosqlite.Row
        svc = FeedService(db)
        await svc.upsert_feed_row(
            user_id="alice", job_id=11, score=55, bucket="24h"
        )
        cur = await db.execute(
            "SELECT profile_version FROM user_feed WHERE user_id = ? AND job_id = ?",
            ("alice", 11),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["profile_version"] is None


@pytest.mark.asyncio
async def test_upsert_updates_profile_version_on_conflict(feed_db):
    """A second upsert with a DIFFERENT profile_version updates both column and score.

    FIX 6 — version differs (3 vs 5) -> score IS overwritten (new contract:
    overwrite only when version changes).  Previously only profile_version was
    asserted; score=75 is now also checked to confirm overwrite behaviour.
    """
    async with aiosqlite.connect(feed_db) as db:
        db.row_factory = aiosqlite.Row
        svc = FeedService(db)
        await svc.upsert_feed_row(
            user_id="alice", job_id=12, score=60, bucket="24h", profile_version=3
        )
        await svc.upsert_feed_row(
            user_id="alice", job_id=12, score=75, bucket="24h", profile_version=5
        )
        cur = await db.execute(
            "SELECT profile_version, score FROM user_feed WHERE user_id = ? AND job_id = ?",
            ("alice", 12),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["profile_version"] == 5
    # score is OVERWRITTEN because version changed (3 -> 5)
    assert row["score"] == 75


# ---------------------------------------------------------------------------
# FIX 6 — version-conditional score freeze (new focused test pair)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_score_frozen_when_same_version(feed_db):
    """FIX 6 (a): same profile_version on both calls -> score is FROZEN at first value.

    Contract: when the incoming profile_version IS equal to the stored one
    (including both NULL), the score must not change.  This prevents time-based
    score drift when the same profile runs another search cycle.
    """
    async with aiosqlite.connect(feed_db) as db:
        db.row_factory = aiosqlite.Row
        svc = FeedService(db)
        # First write — score 70, version 5
        await svc.upsert_feed_row(
            user_id="alice", job_id=20, score=70, bucket="24h", profile_version=5
        )
        # Second write — score drops to 40, but SAME version -> score must stay at 70
        await svc.upsert_feed_row(
            user_id="alice", job_id=20, score=40, bucket="48h", profile_version=5
        )
        cur = await db.execute(
            "SELECT score, bucket FROM user_feed WHERE user_id = ? AND job_id = ?",
            ("alice", 20),
        )
        row = await cur.fetchone()
    assert row is not None
    # score frozen at the FIRST value — same version means no re-score happened
    assert row["score"] == 70
    # bucket is always updated regardless
    assert row["bucket"] == "48h"


@pytest.mark.asyncio
async def test_upsert_score_overwritten_when_version_changes(feed_db):
    """FIX 6 (b): different profile_version -> score IS overwritten.

    Contract: when the incoming profile_version differs from the stored one,
    the score must be replaced with the new value (a new profile version
    produced a better/worse score and we want to surface it).
    """
    async with aiosqlite.connect(feed_db) as db:
        db.row_factory = aiosqlite.Row
        svc = FeedService(db)
        # First write — score 70, version 5
        await svc.upsert_feed_row(
            user_id="alice", job_id=21, score=70, bucket="24h", profile_version=5
        )
        # Second write — different version (6) -> score MUST be overwritten to 40
        await svc.upsert_feed_row(
            user_id="alice", job_id=21, score=40, bucket="48h", profile_version=6
        )
        cur = await db.execute(
            "SELECT score, profile_version FROM user_feed WHERE user_id = ? AND job_id = ?",
            ("alice", 21),
        )
        row = await cur.fetchone()
    assert row is not None
    # score overwritten because version changed (5 -> 6)
    assert row["score"] == 40
    assert row["profile_version"] == 6
