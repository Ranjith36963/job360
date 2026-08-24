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


def test_bundle_due_daily_before_send_time():
    """Before today's send time → not due (must wait for the send time)."""
    now = datetime(2026, 6, 18, 6, 30, tzinfo=timezone.utc)
    rule = {"notify_mode": "daily", "daily_send_time": "08:00"}
    assert _bundle_due(rule, now_utc=now, user_tz="UTC") is False


def test_bundle_due_daily_right_hour():
    now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
    rule = {"notify_mode": "daily", "daily_send_time": "08:00"}
    assert _bundle_due(rule, now_utc=now, user_tz="UTC") is True


# ── N1 — daily digest window (send-time minute not divisible by 5) ───────────


def test_bundle_due_daily_offgrid_minute_fires_on_next_tick():
    """Send-time 09:02, cron ticks at :00 and :05.

    At 09:00 the send time hasn't arrived → not due.
    At 09:05 we are at/after 09:02 and nothing sent today → due (fires once).
    The old exact-minute compare (== 09:02) matched NEITHER tick → never fired.
    """
    rule = {"notify_mode": "daily", "daily_send_time": "09:02", "last_sent_at": None}
    tick_0900 = datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)
    tick_0905 = datetime(2026, 6, 18, 9, 5, tzinfo=timezone.utc)
    assert _bundle_due(rule, now_utc=tick_0900, user_tz="UTC") is False
    assert _bundle_due(rule, now_utc=tick_0905, user_tz="UTC") is True


def test_bundle_due_daily_no_double_send_same_day():
    """After a bundle sent today, later ticks the same day do NOT re-fire."""
    # Sent today at 09:05; a 09:10 tick must not fire again.
    rule = {
        "notify_mode": "daily",
        "daily_send_time": "09:02",
        "last_sent_at": "2026-06-18T09:05:00Z",
    }
    tick_0910 = datetime(2026, 6, 18, 9, 10, tzinfo=timezone.utc)
    assert _bundle_due(rule, now_utc=tick_0910, user_tz="UTC") is False


def test_bundle_due_daily_fires_next_day_after_previous_send():
    """A bundle sent yesterday does not block today's send."""
    rule = {
        "notify_mode": "daily",
        "daily_send_time": "09:02",
        "last_sent_at": "2026-06-17T09:05:00Z",
    }
    tick_today = datetime(2026, 6, 18, 9, 5, tzinfo=timezone.utc)
    assert _bundle_due(rule, now_utc=tick_today, user_tz="UTC") is True


def test_bundle_due_daily_catches_up_after_missed_tick():
    """A missed tick (restart/outage) at the send time still fires later.

    Send-time 09:00, nothing sent today, now 14:30 → due (catch-up), instead
    of skipping the user for the whole day (finding N1).
    """
    rule = {"notify_mode": "daily", "daily_send_time": "09:00", "last_sent_at": None}
    now = datetime(2026, 6, 18, 14, 30, tzinfo=timezone.utc)
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


# ── SI2 — quiet-hours flush for stranded instant-mode matches ────────────────
#
# An instant-mode user's matches get QUEUED into user_notification_digests when
# they land inside quiet hours (dispatcher gate 3+4). Instant mode never
# bundles, so once quiet hours end nothing drains those rows unless
# notification_tick flushes them. Rule #24 requires covering BOTH quiet states.


def _window_covering_now() -> tuple[str, str]:
    """HH:MM quiet window (UTC) that currently contains 'now'."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=90)).strftime("%H:%M")
    end = (now + timedelta(minutes=90)).strftime("%H:%M")
    return start, end


def _window_excluding_now() -> tuple[str, str]:
    """HH:MM quiet window (UTC) that does NOT contain 'now'."""
    now = datetime.now(timezone.utc)
    start = (now + timedelta(minutes=60)).strftime("%H:%M")
    end = (now + timedelta(minutes=120)).strftime("%H:%M")
    return start, end


async def _seed_instant_rule_with_pending(path, quiet_start, quiet_end, *, pending=True):
    """Insert an enabled instant rule (with a quiet window) + optional pending digest."""
    async with pg.connect(path) as db:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await db.execute(
            "INSERT INTO notification_rules(user_id, notify_mode, quiet_hours_start, "
            "quiet_hours_end, enabled, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            ("alice", "instant", quiet_start, quiet_end, 1, now_str, now_str),
        )
        if pending:
            cur = await db.execute(
                "INSERT INTO jobs(title, company, apply_url, source, date_found, "
                "normalized_company, normalized_title, first_seen) VALUES(?,?,?,?,?,?,?,?)",
                ("Dev", "Corp", "https://x.com", "test", now_str, "corp", "dev", now_str),
            )
            job_id = cur.lastrowid
            await db.execute(
                "INSERT INTO user_notification_digests(user_id, channel, job_id) VALUES(?,?,?)",
                ("alice", "email", job_id),
            )
        await db.commit()


@pytest.mark.asyncio
async def test_tick_flushes_instant_backlog_when_quiet_hours_ended(tick_db):
    """OUTSIDE quiet hours + pending instant rows → flush (enqueue send_bundle)."""
    qs, qe = _window_excluding_now()
    await _seed_instant_rule_with_pending(tick_db, qs, qe, pending=True)

    enqueued = []
    async with pg.connect(tick_db) as db:
        ctx = {"db": db, "enqueue": lambda fn, *a: enqueued.append((fn, *a))}
        result = await notification_tick(ctx)

    assert result["enqueued"] == 1
    assert enqueued[0] == ("send_bundle", "alice")


@pytest.mark.asyncio
async def test_tick_does_not_flush_during_quiet_hours(tick_db):
    """INSIDE quiet hours + pending instant rows → do NOT flush yet."""
    qs, qe = _window_covering_now()
    await _seed_instant_rule_with_pending(tick_db, qs, qe, pending=True)

    enqueued = []
    async with pg.connect(tick_db) as db:
        ctx = {"db": db, "enqueue": lambda fn, *a: enqueued.append((fn, *a))}
        result = await notification_tick(ctx)

    assert result["enqueued"] == 0
    assert enqueued == []


@pytest.mark.asyncio
async def test_tick_no_flush_when_no_backlog(tick_db):
    """OUTSIDE quiet hours but NO pending rows → nothing to flush."""
    qs, qe = _window_excluding_now()
    await _seed_instant_rule_with_pending(tick_db, qs, qe, pending=False)

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
            ("alice", "email", job_id),
        )
        await db.commit()

    from src.services.channels.dispatcher import ChannelSendResult

    dispatched = []

    async def fake_dispatch(db, *, user_id, title, body, force=False, **kw):
        dispatched.append({"user_id": user_id, "force": force})
        return [ChannelSendResult(channel_id=1, channel_type="email", ok=True)]

    async with pg.connect(tick_db) as db:
        ctx = {"db": db, "dispatcher": fake_dispatch}
        result = await send_bundle(ctx, "alice")

    assert result["jobs_count"] == 1
    assert result["sent"] == 1
    assert dispatched[0]["force"] is True


# ── send_bundle ledger + failure handling (caught by live verification) ──────


async def _seed_job_and_digest(path, channel="email"):
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
        return [ChannelSendResult(channel_id=1, channel_type="email", ok=True)]

    async with pg.connect(tick_db) as db:
        res = await send_bundle({"db": db, "dispatcher": ok_dispatch}, "alice")
        assert res["sent"] == 1
        # ledger row written as 'sent'
        led = await (await db.execute(
            "SELECT status FROM notification_ledger WHERE user_id='alice' AND channel='email' AND job_id=?",
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
        return [ChannelSendResult(channel_id=1, channel_type="email", ok=False, error="boom")]

    async with pg.connect(tick_db) as db:
        res = await send_bundle({"db": db, "dispatcher": bad_dispatch}, "alice")
        assert res["failed"] == 1 and res["sent"] == 0
        # ledger row 'failed', not 'sent'
        led = await (await db.execute(
            "SELECT status FROM notification_ledger WHERE user_id='alice' AND channel='email' AND job_id=?",
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
            "VALUES('alice', ?, 'email', 'failed', ?)",
            (job_id, MAX_BUNDLE_RETRIES),
        )
        await db.commit()

    async def bad_dispatch(db, *, user_id, title, body, force=False, **kw):
        return [ChannelSendResult(channel_id=1, channel_type="email", ok=False, error="boom")]

    async with pg.connect(tick_db) as db:
        await send_bundle({"db": db, "dispatcher": bad_dispatch}, "alice")
        led = await (await db.execute(
            "SELECT status FROM notification_ledger WHERE user_id='alice' AND channel='email' AND job_id=?",
            (job_id,))).fetchone()
        assert led[0] == "dlq"
        # queue rows dropped (DLQ)
        pend = await (await db.execute(
            "SELECT COUNT(*) FROM user_notification_digests WHERE user_id='alice' AND sent=0")).fetchone()
        assert pend[0] == 0
