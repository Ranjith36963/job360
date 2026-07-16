"""Tests for notification_tick and _bundle_due."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from migrations import runner
from src.repositories import pg
from src.workers.tasks import _bundle_due, notification_tick, send_bundle

# ── DB fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
async def tick_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    async with pg.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL, action TEXT NOT NULL,
                notes TEXT DEFAULT '', created_at TEXT NOT NULL, UNIQUE(job_id)
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL, stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, UNIQUE(job_id)
            );
            """
        )
        await db.commit()
    await runner.up(path)
    async with pg.connect(path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash, timezone) VALUES(?, ?, ?, ?)",
            ("alice", "a@x", "!", "UTC"),
        )
        await db.commit()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ── _bundle_due unit tests ───────────────────────────────────────────────────


def test_bundle_due_instant_never():
    rule = {"notify_mode": "instant", "daily_send_time": "08:00"}
    now = datetime.now(timezone.utc)
    assert _bundle_due(rule, now_utc=now) is False


def test_bundle_due_every_n_hours_no_last_sent():
    rule = {"notify_mode": "every_n_hours", "interval_hours": 6, "last_sent_at": None}
    now = datetime.now(timezone.utc)
    assert _bundle_due(rule, now_utc=now) is True


def test_bundle_due_every_n_hours_not_yet():
    now = datetime.now(timezone.utc)
    last = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rule = {"notify_mode": "every_n_hours", "interval_hours": 6, "last_sent_at": last}
    assert _bundle_due(rule, now_utc=now) is False


def test_bundle_due_every_n_hours_elapsed():
    now = datetime.now(timezone.utc)
    last = (now - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rule = {"notify_mode": "every_n_hours", "interval_hours": 6, "last_sent_at": last}
    assert _bundle_due(rule, now_utc=now) is True


def test_bundle_due_daily_wrong_hour():
    now = datetime(2026, 6, 18, 14, 30, tzinfo=timezone.utc)
    rule = {"notify_mode": "daily", "daily_send_time": "08:00"}
    assert _bundle_due(rule, now_utc=now, user_tz="UTC") is False


def test_bundle_due_daily_right_hour():
    now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
    rule = {"notify_mode": "daily", "daily_send_time": "08:00"}
    assert _bundle_due(rule, now_utc=now, user_tz="UTC") is True


# ── notification_tick integration tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_notification_tick_enqueues_when_due(tick_db):
    """notification_tick enqueues send_bundle for users whose rule is due."""
    now = datetime.now(timezone.utc)
    last = (now - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with pg.connect(tick_db) as db:
        now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        await db.execute(
            "INSERT INTO notification_rules(user_id, notify_mode, interval_hours, "
            "last_sent_at, enabled, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            ("alice", "every_n_hours", 6, last, 1, now_str, now_str),
        )
        await db.commit()

    enqueued = []
    async with pg.connect(tick_db) as db:
        ctx = {"db": db, "enqueue": lambda fn, *a: enqueued.append((fn, *a))}
        result = await notification_tick(ctx)

    assert result["enqueued"] == 1
    assert enqueued[0][0] == "send_bundle"
    assert enqueued[0][1] == "alice"


@pytest.mark.asyncio
async def test_notification_tick_skips_instant(tick_db):
    """notification_tick does not enqueue for instant-mode users."""
    async with pg.connect(tick_db) as db:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await db.execute(
            "INSERT INTO notification_rules(user_id, notify_mode, enabled, created_at, updated_at) "
            "VALUES(?,?,?,?,?)",
            ("alice", "instant", 1, now_str, now_str),
        )
        await db.commit()

    enqueued = []
    async with pg.connect(tick_db) as db:
        ctx = {"db": db, "enqueue": lambda fn, *a: enqueued.append((fn, *a))}
        result = await notification_tick(ctx)

    assert result["enqueued"] == 0
    assert enqueued == []


@pytest.mark.asyncio
async def test_send_bundle_no_pending(tick_db):
    """send_bundle returns zeros when no pending digest rows."""
    async with pg.connect(tick_db) as db:
        ctx = {"db": db}
        result = await send_bundle(ctx, "alice")
    assert result == {"sent": 0, "failed": 0, "jobs_count": 0}


@pytest.mark.asyncio
async def test_send_bundle_dispatches(tick_db):
    """send_bundle calls dispatcher and marks rows sent."""
    # Insert a job
    async with pg.connect(tick_db) as db:
        now_str = datetime.now(timezone.utc).isoformat()
        cur = await db.execute(
            "INSERT INTO jobs(title, company, apply_url, source, date_found, "
            "normalized_company, normalized_title, first_seen) VALUES(?,?,?,?,?,?,?,?)",
            ("Dev", "Corp", "https://x.com", "test", now_str, "corp", "dev", now_str),
        )
        job_id = cur.lastrowid
        await db.execute(
            "INSERT INTO user_notification_digests(user_id, channel, job_id) VALUES(?,?,?)",
            ("alice", "slack", job_id),
        )
        await db.commit()

    from src.services.channels.dispatcher import ChannelSendResult

    dispatched = []

    async def fake_dispatch(db, *, user_id, title, body, force=False, **kw):
        dispatched.append({"user_id": user_id, "force": force})
        return [ChannelSendResult(channel_id=1, channel_type="slack", ok=True)]

    async with pg.connect(tick_db) as db:
        ctx = {"db": db, "dispatcher": fake_dispatch}
        result = await send_bundle(ctx, "alice")

    assert result["jobs_count"] == 1
    assert result["sent"] == 1
    assert dispatched[0]["force"] is True


# ── send_bundle ledger + failure handling (caught by live verification) ──────


async def _seed_job_and_digest(path, channel="slack"):
    async with pg.connect(path) as db:
        now_str = datetime.now(timezone.utc).isoformat()
        cur = await db.execute(
            "INSERT INTO jobs(title, company, apply_url, source, date_found, "
            "normalized_company, normalized_title, first_seen) VALUES(?,?,?,?,?,?,?,?)",
            ("Dev", "Corp", "https://x.com", "test", now_str, "corp", "dev", now_str),
        )
        job_id = cur.lastrowid
        await db.execute(
            "INSERT INTO user_notification_digests(user_id, channel, job_id) VALUES(?,?,?)",
            ("alice", channel, job_id),
        )
        await db.commit()
    return job_id


@pytest.mark.asyncio
async def test_send_bundle_success_writes_ledger_and_drains(tick_db):
    job_id = await _seed_job_and_digest(tick_db)
    from src.services.channels.dispatcher import ChannelSendResult

    async def ok_dispatch(db, *, user_id, title, body, force=False, **kw):
        return [ChannelSendResult(channel_id=1, channel_type="slack", ok=True)]

    async with pg.connect(tick_db) as db:
        res = await send_bundle({"db": db, "dispatcher": ok_dispatch}, "alice")
        assert res["sent"] == 1
        # ledger row written as 'sent'
        led = await (await db.execute(
            "SELECT status FROM notification_ledger WHERE user_id='alice' AND channel='slack' AND job_id=?",
            (job_id,))).fetchone()
        assert led is not None and led[0] == "sent"
        # queue drained
        pend = await (await db.execute(
            "SELECT COUNT(*) FROM user_notification_digests WHERE user_id='alice' AND sent=0")).fetchone()
        assert pend[0] == 0
        # last_sent_at stamped
        async with pg.connect(tick_db) as _:
            pass


@pytest.mark.asyncio
async def test_send_bundle_failure_keeps_rows_and_marks_failed(tick_db):
    job_id = await _seed_job_and_digest(tick_db)
    from src.services.channels.dispatcher import ChannelSendResult

    async def bad_dispatch(db, *, user_id, title, body, force=False, **kw):
        return [ChannelSendResult(channel_id=1, channel_type="slack", ok=False, error="boom")]

    async with pg.connect(tick_db) as db:
        res = await send_bundle({"db": db, "dispatcher": bad_dispatch}, "alice")
        assert res["failed"] == 1 and res["sent"] == 0
        # ledger row 'failed', not 'sent'
        led = await (await db.execute(
            "SELECT status FROM notification_ledger WHERE user_id='alice' AND channel='slack' AND job_id=?",
            (job_id,))).fetchone()
        assert led is not None and led[0] == "failed"
        # queue rows KEPT for retry (not drained, not lost)
        pend = await (await db.execute(
            "SELECT COUNT(*) FROM user_notification_digests WHERE user_id='alice' AND sent=0")).fetchone()
        assert pend[0] == 1


@pytest.mark.asyncio
async def test_send_bundle_dlq_after_max_retries(tick_db):
    job_id = await _seed_job_and_digest(tick_db)
    from src.services.channels.dispatcher import ChannelSendResult
    from src.workers.tasks import MAX_BUNDLE_RETRIES

    # Pre-seed a ledger row already at the retry cap.
    async with pg.connect(tick_db) as db:
        await db.execute(
            "INSERT INTO notification_ledger(user_id, job_id, channel, status, retry_count) "
            "VALUES('alice', ?, 'slack', 'failed', ?)",
            (job_id, MAX_BUNDLE_RETRIES),
        )
        await db.commit()

    async def bad_dispatch(db, *, user_id, title, body, force=False, **kw):
        return [ChannelSendResult(channel_id=1, channel_type="slack", ok=False, error="boom")]

    async with pg.connect(tick_db) as db:
        await send_bundle({"db": db, "dispatcher": bad_dispatch}, "alice")
        led = await (await db.execute(
            "SELECT status FROM notification_ledger WHERE user_id='alice' AND channel='slack' AND job_id=?",
            (job_id,))).fetchone()
        assert led[0] == "dlq"
        # queue rows dropped (DLQ)
        pend = await (await db.execute(
            "SELECT COUNT(*) FROM user_notification_digests WHERE user_id='alice' AND sent=0")).fetchone()
        assert pend[0] == 0
