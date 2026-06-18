"""Tests for notification_tick and _bundle_due."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from migrations import runner
from src.workers.tasks import _bundle_due, notification_tick, send_bundle

# ── DB fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
async def tick_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    async with aiosqlite.connect(path) as db:
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
    async with aiosqlite.connect(path) as db:
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
    async with aiosqlite.connect(tick_db) as db:
        now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        await db.execute(
            "INSERT INTO notification_rules(user_id, notify_mode, interval_hours, "
            "last_sent_at, enabled, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            ("alice", "every_n_hours", 6, last, 1, now_str, now_str),
        )
        await db.commit()

    enqueued = []
    async with aiosqlite.connect(tick_db) as db:
        ctx = {"db": db, "enqueue": lambda fn, *a: enqueued.append((fn, *a))}
        result = await notification_tick(ctx)

    assert result["enqueued"] == 1
    assert enqueued[0][0] == "send_bundle"
    assert enqueued[0][1] == "alice"


@pytest.mark.asyncio
async def test_notification_tick_skips_instant(tick_db):
    """notification_tick does not enqueue for instant-mode users."""
    async with aiosqlite.connect(tick_db) as db:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await db.execute(
            "INSERT INTO notification_rules(user_id, notify_mode, enabled, created_at, updated_at) "
            "VALUES(?,?,?,?,?)",
            ("alice", "instant", 1, now_str, now_str),
        )
        await db.commit()

    enqueued = []
    async with aiosqlite.connect(tick_db) as db:
        ctx = {"db": db, "enqueue": lambda fn, *a: enqueued.append((fn, *a))}
        result = await notification_tick(ctx)

    assert result["enqueued"] == 0
    assert enqueued == []


@pytest.mark.asyncio
async def test_send_bundle_no_pending(tick_db):
    """send_bundle returns zeros when no pending digest rows."""
    async with aiosqlite.connect(tick_db) as db:
        ctx = {"db": db}
        result = await send_bundle(ctx, "alice")
    assert result == {"sent": 0, "failed": 0, "jobs_count": 0}


@pytest.mark.asyncio
async def test_send_bundle_dispatches(tick_db):
    """send_bundle calls dispatcher and marks rows sent."""
    # Insert a job
    async with aiosqlite.connect(tick_db) as db:
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

    async with aiosqlite.connect(tick_db) as db:
        ctx = {"db": db, "dispatcher": fake_dispatch}
        result = await send_bundle(ctx, "alice")

    assert result["jobs_count"] == 1
    assert result["sent"] == 1
    assert dispatched[0]["force"] is True
