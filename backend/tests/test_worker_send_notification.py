"""Tests for workers.tasks.send_notification — Batch 3.5 Deliverable D.

Uses ``ctx['dispatcher']`` to bypass real Apprise — no network, no
Redis. Ledger assertions prove:
  1. Each channel result produces exactly one notification_ledger row.
  2. ok=True rows get status='sent', failures get status='failed' + error_message.
  3. Idempotency via UNIQUE(user_id, job_id, channel) — retry does not dup.
  4. Return value reports {sent, failed} counts.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import aiosqlite
import pytest

from migrations import runner
from src.services.channels.dispatcher import ChannelSendResult
from src.workers.tasks import send_notification


@pytest.fixture
async def db_ctx():
    """Build a minimal multi-tenant DB + return ctx['db']."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Batch 2 test fixture pattern: pre-create user_actions + applications
    # so migration 0002 can rebuild them with user_id columns.
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT DEFAULT '',
                salary_min REAL,
                salary_max REAL,
                description TEXT DEFAULT '',
                apply_url TEXT NOT NULL,
                source TEXT NOT NULL,
                date_found TEXT NOT NULL,
                match_score INTEGER DEFAULT 0,
                visa_flag INTEGER DEFAULT 0,
                experience_level TEXT DEFAULT '',
                normalized_company TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                UNIQUE(normalized_company, normalized_title)
            );
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
    # Seed 1 job + 1 user
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("alice", "a@example.test", "!"),
        )
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO jobs (title, company, apply_url, source, date_found,
               normalized_company, normalized_title, first_seen)
               VALUES (?, ?, ?, 'test', ?, ?, ?, ?)""",
            ("AI Engineer", "Acme", "https://acme.test/a", now,
             "acme", "ai engineer", now),
        )
        await db.commit()
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    yield {"db": conn}
    await conn.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _ledger_rows(db) -> list[dict]:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT user_id, job_id, channel, status, error_message, retry_count "
        "FROM notification_ledger ORDER BY id"
    )
    return [dict(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_send_notification_dispatches_each_channel_and_marks_sent(db_ctx):
    """Two channels both succeed → two ledger rows in status='sent'."""
    async def fake_dispatcher(db, *, user_id, title, body):
        return [
            ChannelSendResult(channel_id=1, channel_type="email", ok=True),
            ChannelSendResult(channel_id=2, channel_type="slack", ok=True),
        ]

    db_ctx["dispatcher"] = fake_dispatcher
    result = await send_notification(db_ctx, "alice", 1, "instant")

    assert result == {"sent": 2, "failed": 0}
    rows = await _ledger_rows(db_ctx["db"])
    assert len(rows) == 2
    assert all(r["status"] == "sent" for r in rows)
    channels = {r["channel"] for r in rows}
    assert channels == {"email", "slack"}


@pytest.mark.asyncio
async def test_send_notification_marks_failed_with_error_message(db_ctx):
    """A failing channel produces status='failed' + error_message."""
    async def fake_dispatcher(db, *, user_id, title, body):
        return [
            ChannelSendResult(
                channel_id=3, channel_type="discord",
                ok=False, error="apprise returned False",
            ),
        ]

    db_ctx["dispatcher"] = fake_dispatcher
    result = await send_notification(db_ctx, "alice", 1, "instant")

    assert result == {"sent": 0, "failed": 1}
    rows = await _ledger_rows(db_ctx["db"])
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert "apprise returned False" in rows[0]["error_message"]


@pytest.mark.asyncio
async def test_send_notification_returns_mixed_counts(db_ctx):
    async def fake_dispatcher(db, *, user_id, title, body):
        return [
            ChannelSendResult(channel_id=1, channel_type="email", ok=True),
            ChannelSendResult(channel_id=2, channel_type="slack", ok=True),
            ChannelSendResult(
                channel_id=3, channel_type="telegram",
                ok=False, error="boom",
            ),
        ]

    db_ctx["dispatcher"] = fake_dispatcher
    result = await send_notification(db_ctx, "alice", 1, "instant")
    assert result == {"sent": 2, "failed": 1}


@pytest.mark.asyncio
async def test_send_notification_is_idempotent_per_channel(db_ctx):
    """Calling twice produces the same 2 ledger rows (UNIQUE constraint)."""
    async def fake_dispatcher(db, *, user_id, title, body):
        return [
            ChannelSendResult(channel_id=1, channel_type="email", ok=True),
            ChannelSendResult(channel_id=2, channel_type="slack", ok=True),
        ]

    db_ctx["dispatcher"] = fake_dispatcher
    await send_notification(db_ctx, "alice", 1, "instant")
    await send_notification(db_ctx, "alice", 1, "instant")  # retry

    rows = await _ledger_rows(db_ctx["db"])
    # Still exactly 2 rows — UNIQUE(user_id, job_id, channel) held
    assert len(rows) == 2
    assert {r["channel"] for r in rows} == {"email", "slack"}


@pytest.mark.asyncio
async def test_send_notification_handles_unknown_job(db_ctx):
    """job_id not in jobs table → {sent: 0, failed: 0}, no dispatcher call."""
    called = []

    async def fake_dispatcher(db, *, user_id, title, body):
        called.append((user_id, title))
        return []

    db_ctx["dispatcher"] = fake_dispatcher
    result = await send_notification(db_ctx, "alice", 9999, "instant")
    assert result == {"sent": 0, "failed": 0}
    assert called == [], "dispatcher should not be invoked for unknown job"
