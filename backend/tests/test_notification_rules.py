"""Step-3 B-01..05, O-01, O-02 — notification rules + dispatcher rule gate + digest.

Tests:
  1. test_list_rules_empty            — GET /settings/notification-rules → empty for new user
  2. test_create_rule                 — POST creates, GET returns it
  3. test_update_rule                 — PATCH updates score_threshold
  4. test_delete_rule_idor            — DELETE with wrong user_id → 404
  5. test_dispatcher_skips_below_threshold — score=70, threshold=80 → no dispatch
  6. test_dispatcher_fires_above_threshold — score=85, threshold=80 → dispatches
  7. test_dispatcher_quiet_hours_skips    — within quiet window → skips/queues
  8. test_notification_stats          — O-02 endpoint returns expected shape
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import aiosqlite
import pytest
from cryptography.fernet import Fernet

from migrations import runner
from src.services.channels import crypto, dispatcher

# ── Shared DB fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def rules_db():
    """Provide a migrated aiosqlite connection with notification_rules tables.

    Sets up minimal schema (users, user_channels) then runs all migrations
    so notification_rules + user_notification_digests exist.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Initialise base schema that migration 0000 expects
    async with aiosqlite.connect(path) as db:
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
    async with aiosqlite.connect(path) as db:
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


# ── Helper ────────────────────────────────────────────────────────────────────


async def _insert_channel(db_path: str, user_id: str, channel_type: str, url: str, enabled: int = 1) -> int:
    ct = crypto.encrypt(url)
    async with aiosqlite.connect(db_path) as db:
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
    channel: str,
    score_threshold: int = 60,
    notify_mode: str = "instant",
    enabled: int = 1,
    quiet_hours_start: str | None = None,
    quiet_hours_end: str | None = None,
) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO notification_rules"
            "(user_id, channel, score_threshold, notify_mode, enabled, "
            " quiet_hours_start, quiet_hours_end, created_at, updated_at)"
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, channel, score_threshold, notify_mode, enabled, quiet_hours_start, quiet_hours_end, now, now),
        )
        await db.commit()
        return cur.lastrowid


async def _insert_job(db_path: str) -> int:
    """Insert a minimal job row and return its id."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO jobs(title, company, apply_url, source, date_found, "
            "normalized_company, normalized_title, first_seen) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            ("Engineer", "Acme", "https://example.com/job", "test", now, "acme", "engineer", now),
        )
        await db.commit()
        return cur.lastrowid


# ── HTTP-layer tests (B-02) via authenticated_async_context ──────────────────


@pytest.mark.asyncio
async def test_list_rules_empty(authenticated_async_context):
    """1. GET /settings/notification-rules returns empty list for new user."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/settings/notification-rules")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rules"] == []


@pytest.mark.asyncio
async def test_create_rule(authenticated_async_context):
    """2. POST creates a rule, GET returns it with the correct fields."""
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/settings/notification-rules",
            json={
                "channel": "email",
                "score_threshold": 75,
                "notify_mode": "instant",
                "enabled": True,
            },
        )
        assert resp.status_code == 201, resp.text
        rule = resp.json()
        assert rule["channel"] == "email"
        assert rule["score_threshold"] == 75
        assert rule["enabled"] is True

        list_resp = await client.get("/api/settings/notification-rules")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["rules"]) == 1
    assert list_resp.json()["rules"][0]["channel"] == "email"


@pytest.mark.asyncio
async def test_update_rule(authenticated_async_context):
    """3. PATCH updates score_threshold."""
    async with authenticated_async_context() as client:
        create = await client.post(
            "/api/settings/notification-rules",
            json={"channel": "slack", "score_threshold": 50},
        )
        rule_id = create.json()["id"]

        patch_resp = await client.patch(
            f"/api/settings/notification-rules/{rule_id}",
            json={"score_threshold": 90},
        )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["score_threshold"] == 90


@pytest.mark.asyncio
async def test_delete_rule_idor(authenticated_async_context, rules_db):
    """4. DELETE with wrong user_id → 404 (IDOR guard).

    We insert a rule under 'alice' via direct SQL, then try to delete it
    via the authenticated test user (who is a different user).
    """
    rule_id = await _insert_notification_rule(rules_db, "alice", "email")

    async with authenticated_async_context() as client:
        resp = await client.delete(f"/api/settings/notification-rules/{rule_id}")
    # The fixture user is NOT alice, so the WHERE id=? AND user_id=? should miss.
    assert resp.status_code == 404


# ── Dispatcher rule gate tests (B-03) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatcher_skips_below_threshold(rules_db):
    """5. score=70, threshold=80 → no Apprise call (skipped=True)."""
    await _insert_channel(rules_db, "alice", "slack", "slack://a/b/c")
    await _insert_notification_rule(rules_db, "alice", "slack", score_threshold=80)

    with patch("apprise.Apprise") as mock_app:  # noqa: N806
        instance = mock_app.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with aiosqlite.connect(rules_db) as db:
            results = await dispatcher.dispatch(
                db,
                user_id="alice",
                title="Hi",
                body="there",
                match_score=70,
            )

    # Should be skipped — score < threshold
    assert len(results) == 1
    assert results[0].skipped is True
    assert instance.notify.call_count == 0


@pytest.mark.asyncio
async def test_dispatcher_fires_above_threshold(rules_db):
    """6. score=85, threshold=80 → dispatches normally."""
    await _insert_channel(rules_db, "alice", "slack", "slack://a/b/c")
    await _insert_notification_rule(rules_db, "alice", "slack", score_threshold=80)

    with patch("apprise.Apprise") as mock_app:  # noqa: N806
        instance = mock_app.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with aiosqlite.connect(rules_db) as db:
            results = await dispatcher.dispatch(
                db,
                user_id="alice",
                title="Hi",
                body="there",
                match_score=85,
            )

    # Should fire
    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].skipped is False
    assert instance.notify.call_count == 1


@pytest.mark.asyncio
async def test_dispatcher_quiet_hours_skips(rules_db):
    """7. Within quiet window → skips / queues digest.

    We patch ``_is_in_quiet_window`` to always return True so the test
    doesn't depend on the real clock — it only needs to verify the
    dispatcher respects the quiet-hours gate.
    """
    await _insert_channel(rules_db, "alice", "slack", "slack://a/b/c")
    job_id = await _insert_job(rules_db)
    await _insert_notification_rule(
        rules_db,
        "alice",
        "slack",
        score_threshold=0,
        quiet_hours_start="00:00",
        quiet_hours_end="23:59",
    )

    with patch("src.services.channels.dispatcher._is_in_quiet_window", return_value=True):
        with patch("apprise.Apprise") as mock_app:  # noqa: N806
            instance = mock_app.return_value
            instance.notify.return_value = True
            if hasattr(instance, "async_notify"):
                del instance.async_notify

            async with aiosqlite.connect(rules_db) as db:
                results = await dispatcher.dispatch(
                    db,
                    user_id="alice",
                    title="Hi",
                    body="there",
                    job_id=job_id,
                    match_score=95,
                )

    # Should be queued for digest, not dispatched
    assert len(results) == 1
    result = results[0]
    # Either skipped=True or queued_digest=True — quiet hours gate fires
    assert result.skipped or result.queued_digest
    assert instance.notify.call_count == 0


# ── Notification stats endpoint (O-02) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_notification_stats(authenticated_async_context, fixture_user_id):
    """8. O-02 endpoint returns expected shape."""
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    now = datetime.now(timezone.utc).isoformat()
    # Insert ledger rows for the fixture user — use distinct job_ids to satisfy
    # UNIQUE(user_id, job_id, channel) on notification_ledger (migration 0004).
    for jid, status_str in [(1001, "sent"), (1002, "sent"), (1003, "failed")]:
        await db._conn.execute(
            "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) " "VALUES(?, ?, ?, ?, ?)",
            (fixture_user_id, jid, "email", status_str, now),
        )
    await db._conn.commit()

    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    body = resp.json()
    # Should have email channel with sent/failed counts
    assert "email" in body
    assert body["email"].get("sent", 0) == 2
    assert body["email"].get("failed", 0) == 1


# ── O-01: ledger filters ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notifications_filter_by_job_id(authenticated_async_context, fixture_user_id):
    """O-01: ?job_id= filter returns only the matching ledger row."""
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db._conn.execute(
        "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) " "VALUES(?, ?, ?, ?, ?)",
        (fixture_user_id, 100, "email", "sent", now),
    )
    await db._conn.execute(
        "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) " "VALUES(?, ?, ?, ?, ?)",
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
    """O-01: ?start_time= / ?end_time= filter on created_at."""
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    early = "2026-01-01T00:00:00+00:00"
    late = "2026-12-31T23:59:59+00:00"
    await db._conn.execute(
        "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) " "VALUES(?, ?, ?, ?, ?)",
        (fixture_user_id, 1, "email", "sent", early),
    )
    await db._conn.execute(
        "INSERT INTO notification_ledger(user_id, job_id, channel, status, created_at) " "VALUES(?, ?, ?, ?, ?)",
        (fixture_user_id, 2, "email", "sent", late),
    )
    await db._conn.commit()

    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications?start_time=2026-06-01T00:00:00%2B00:00")
    assert resp.status_code == 200
    body = resp.json()
    # Only the late row should appear
    assert body["total"] == 1
    assert body["notifications"][0]["job_id"] == 2
