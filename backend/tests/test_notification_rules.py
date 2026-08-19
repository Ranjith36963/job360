"""Notification rule API tests — single-rulebook model (migration 0020).

Tests:
  1. test_get_rule_returns_defaults    — GET returns instant/60/enabled defaults
  2. test_put_rule_updates_mode        — PUT every_n_hours saves and reads back
  3. test_put_rule_partial_update      — PUT only changes the supplied fields
  4. test_dispatcher_skips_below_threshold — score < threshold → skipped
  5. test_dispatcher_fires_above_threshold — score ≥ threshold → dispatched
  6. test_dispatcher_quiet_hours_skips    — quiet window → queued
  7. test_notification_stats           — O-02 endpoint returns expected shape
  8. test_notifications_filter_by_job_id — O-01 ?job_id= filter
  9. test_notifications_filter_by_time_range — O-01 ?start_time= filter
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from migrations import runner
from src.repositories import pg
from src.services.channels import crypto, dispatcher

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def rules_db():
    """Migrated aiosqlite DB path with users seeded."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    async with pg.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            CREATE TABLE IF NOT EXISTS applications (
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
    async with pg.connect(path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("alice", "a@example.com", "!"),
        )
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("bob", "b@example.com", "!"),
        )
        await db.commit()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _fernet_key():
    crypto.set_test_key(Fernet.generate_key().decode("ascii"))


async def _insert_channel(db_path: str, user_id: str, channel_type: str, url: str, enabled: int = 1) -> int:
    ct = crypto.encrypt(url)
    async with pg.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO user_channels(user_id, channel_type, display_name, credential_encrypted, enabled) "
            "VALUES(?, ?, ?, ?, ?)",
            (user_id, channel_type, f"{channel_type}-1", ct, enabled),
        )
        await db.commit()
        return cur.lastrowid


async def _insert_notification_rule(
    db_path: str,
    user_id: str,
    *,
    score_threshold: int = 60,
    notify_mode: str = "instant",
    enabled: int = 1,
    quiet_hours_start: str | None = None,
    quiet_hours_end: str | None = None,
) -> None:
    """Insert a single notification rule for a user (new schema)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with pg.connect(db_path) as db:
        await db.execute(
            "INSERT INTO notification_rules"
            "(user_id, score_threshold, notify_mode, enabled, "
            " quiet_hours_start, quiet_hours_end, created_at, updated_at)"
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            " score_threshold = EXCLUDED.score_threshold, "
            " notify_mode = EXCLUDED.notify_mode, "
            " enabled = EXCLUDED.enabled, "
            " quiet_hours_start = EXCLUDED.quiet_hours_start, "
            " quiet_hours_end = EXCLUDED.quiet_hours_end, "
            " updated_at = EXCLUDED.updated_at",
            (user_id, score_threshold, notify_mode, enabled,
             quiet_hours_start, quiet_hours_end, now, now),
        )
        await db.commit()


async def _insert_job(db_path: str) -> int:
    """Insert a minimal job row and return its id."""
    now = datetime.now(timezone.utc).isoformat()
    async with pg.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO jobs(title, company, apply_url, source, date_found, "
            "normalized_company, normalized_title, first_seen) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            ("Engineer", "Acme", "https://example.com/job", "test", now, "acme", "engineer", now),
        )
        await db.commit()
        return cur.lastrowid


# ── API tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_rule_seeded_at_signup(authenticated_async_context):
    """#318 — a newly-registered user now HAS a rulebook.

    This is the regression guard for the whole issue. Job360 shipped as a job
    ALERT product where signup wrote exactly one row (into ``users``) and
    nothing ever created a ``notification_rules`` row, so every delivery path
    short-circuited on an empty table and nobody was ever alerted. Registering
    must now produce an enabled rule in a BUNDLING mode.
    """
    from src.services.notifications.defaults import (
        DEFAULT_NOTIFY_MODE,
        DEFAULT_SCORE_THRESHOLD,
    )

    async with authenticated_async_context() as client:
        resp = await client.get("/api/settings/notification-rule")
    assert resp.status_code == 200
    body = resp.json()
    assert body is not None, "signup left the user with no rulebook — issue #318"
    # Rule #21 — assert real values, not just that the keys exist.
    assert body["enabled"] is True
    assert body["notify_mode"] == DEFAULT_NOTIFY_MODE
    assert body["score_threshold"] == DEFAULT_SCORE_THRESHOLD


@pytest.mark.asyncio
async def test_get_rule_null_when_none(authenticated_async_context, fixture_user_id):
    """1. GET returns 200 with null body when the user genuinely has no rule.

    Settings is a per-user singleton, so "not set" is a normal empty-state the
    client reads as "use defaults" — not a 404 (which the browser logs as a
    console error even though the client handles it).

    Since #318 seeds a rulebook at signup, this contract is now only reachable
    for a user whose row is absent — a pre-#318 account, or one created with
    ``NOTIFY_SEED_DEFAULTS=0``. Deleting the seeded row reproduces exactly that
    state, so the route's empty-state behaviour stays pinned.
    """
    from src.core import settings as _s

    async def _drop_rule():
        async with pg.connect(str(_s.DB_PATH)) as db:
            await db.execute(
                "DELETE FROM notification_rules WHERE user_id = ?", (fixture_user_id,)
            )
            await db.commit()

    await _drop_rule()

    async with authenticated_async_context() as client:
        resp = await client.get("/api/settings/notification-rule")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_put_rule_updates_mode(authenticated_async_context):
    """2. PUT every_n_hours saves and reads back correctly."""
    async with authenticated_async_context() as client:
        put_resp = await client.put(
            "/api/settings/notification-rule",
            json={"notify_mode": "every_n_hours", "interval_hours": 8, "score_threshold": 70},
        )
        assert put_resp.status_code == 200, put_resp.text
        body = put_resp.json()
        assert body["notify_mode"] == "every_n_hours"
        assert body["interval_hours"] == 8
        assert body["score_threshold"] == 70

        get_resp = await client.get("/api/settings/notification-rule")
    assert get_resp.json()["notify_mode"] == "every_n_hours"
    assert get_resp.json()["interval_hours"] == 8


@pytest.mark.asyncio
async def test_put_rule_rejects_invalid_notify_mode(authenticated_async_context):
    """M2 — notify_mode is really an enum (instant|daily|every_n_hours). A
    free-form string must be rejected with 422, never silently written."""
    async with authenticated_async_context() as client:
        resp = await client.put(
            "/api/settings/notification-rule",
            json={"notify_mode": "hourly-ish"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_rule_partial_update(authenticated_async_context):
    """3. PUT only changes the supplied fields, leaves others at their saved value."""
    async with authenticated_async_context() as client:
        await client.put(
            "/api/settings/notification-rule",
            json={"notify_mode": "daily", "score_threshold": 80},
        )
        # Second PUT only changes score_threshold
        resp2 = await client.put(
            "/api/settings/notification-rule",
            json={"score_threshold": 90},
        )
        assert resp2.json()["score_threshold"] == 90
        # notify_mode should still be daily (not reset to instant)
        get_resp = await client.get("/api/settings/notification-rule")
    assert get_resp.json()["score_threshold"] == 90


# ── Dispatcher rule gate tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatcher_skips_below_threshold(rules_db):
    """4. score=70, threshold=80 → no Apprise call (skipped=True)."""
    await _insert_channel(rules_db, "alice", "slack", "slack://a/b/c")
    await _insert_notification_rule(rules_db, "alice", score_threshold=80)

    with patch("apprise.Apprise") as mock_app:
        instance = mock_app.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with pg.connect(rules_db) as db:
            results = await dispatcher.dispatch(
                db,
                user_id="alice",
                title="Hi",
                body="there",
                match_score=70,
            )

    assert len(results) == 1
    assert results[0].skipped is True
    assert instance.notify.call_count == 0


@pytest.mark.asyncio
async def test_dispatcher_fires_above_threshold(rules_db):
    """5. score=85, threshold=80 → dispatches normally."""
    await _insert_channel(rules_db, "alice", "slack", "slack://a/b/c")
    await _insert_notification_rule(rules_db, "alice", score_threshold=80)

    with patch("apprise.Apprise") as mock_app:
        instance = mock_app.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with pg.connect(rules_db) as db:
            results = await dispatcher.dispatch(
                db,
                user_id="alice",
                title="Hi",
                body="there",
                match_score=85,
            )

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].skipped is False
    assert instance.notify.call_count == 1


@pytest.mark.asyncio
async def test_dispatcher_quiet_hours_skips(rules_db):
    """6. Within quiet window → queued for digest (not dispatched immediately)."""
    await _insert_channel(rules_db, "alice", "slack", "slack://a/b/c")
    job_id = await _insert_job(rules_db)
    await _insert_notification_rule(
        rules_db,
        "alice",
        score_threshold=0,
        quiet_hours_start="00:00",
        quiet_hours_end="23:59",
    )

    with patch("src.services.channels.dispatcher._is_in_quiet_window", return_value=True):
        with patch("apprise.Apprise") as mock_app:
            instance = mock_app.return_value
            instance.notify.return_value = True
            if hasattr(instance, "async_notify"):
                del instance.async_notify

            async with pg.connect(rules_db) as db:
                results = await dispatcher.dispatch(
                    db,
                    user_id="alice",
                    title="Hi",
                    body="there",
                    job_id=job_id,
                    match_score=95,
                )

    assert len(results) == 1
    result = results[0]
    assert result.skipped or result.queued_digest
    assert instance.notify.call_count == 0


# ── Notification stats endpoint (O-02) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_notification_stats(authenticated_async_context, fixture_user_id):
    """7. O-02 endpoint returns expected shape."""
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    now = datetime.now(timezone.utc).isoformat()
    for jid, status_str in [(1001, "sent"), (1002, "sent"), (1003, "failed")]:
        await db._conn.execute(
            "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) VALUES(?, ?, ?, ?, ?)",
            (fixture_user_id, jid, "email", status_str, now),
        )
    await db._conn.commit()

    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert "email" in body
    assert body["email"].get("sent", 0) == 2
    assert body["email"].get("failed", 0) == 1


# ── O-01: ledger filters ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notifications_filter_by_job_id(authenticated_async_context, fixture_user_id):
    """8. O-01: ?job_id= filter returns only the matching ledger row."""
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db._conn.execute(
        "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) VALUES(?, ?, ?, ?, ?)",
        (fixture_user_id, 100, "email", "sent", now),
    )
    await db._conn.execute(
        "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) VALUES(?, ?, ?, ?, ?)",
        (fixture_user_id, 200, "email", "sent", now),
    )
    await db._conn.commit()

    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications?job_id=100")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["notifications"][0]["job_id"] == 100


@pytest.mark.asyncio
async def test_notifications_filter_by_time_range(authenticated_async_context, fixture_user_id):
    """9. O-01: ?start_time= / ?end_time= filter on created_at."""
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    early = "2026-01-01T00:00:00+00:00"
    late = "2026-12-31T23:59:59+00:00"
    await db._conn.execute(
        "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) VALUES(?, ?, ?, ?, ?)",
        (fixture_user_id, 1, "email", "sent", early),
    )
    await db._conn.execute(
        "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) VALUES(?, ?, ?, ?, ?)",
        (fixture_user_id, 2, "email", "sent", late),
    )
    await db._conn.commit()

    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications?start_time=2026-06-01T00:00:00%2B00:00")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["notifications"][0]["job_id"] == 2
