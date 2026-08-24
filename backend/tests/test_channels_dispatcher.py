"""Dispatcher tests — Apprise.notify is monkey-patched in every test."""
import os
import tempfile
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from migrations import runner
from src.repositories import pg
from src.services.channels import crypto, dispatcher


@pytest.fixture(autouse=True)
def _fernet_key():
    crypto.set_test_key(Fernet.generate_key().decode("ascii"))


@pytest.fixture
async def channel_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    async with pg.connect(path) as db:
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
    async with pg.connect(path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("alice", "a@x", "!"),
        )
        await db.commit()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_channel(db_path, user_id, channel_type, url, enabled=1):
    ct = crypto.encrypt(url)
    async with pg.connect(db_path) as db:
        cur = await db.execute(
            """
            INSERT INTO user_channels(user_id, channel_type, display_name,
                                      credential_encrypted, enabled)
            VALUES(?, ?, ?, ?, ?)
            """,
            (user_id, channel_type, f"{channel_type}-1", ct, enabled),
        )
        await db.commit()
        return cur.lastrowid


@pytest.mark.asyncio
async def test_dispatch_sends_to_each_enabled_channel(channel_db):
    await _insert_channel(channel_db, "alice", "slack", "slack://a/b/c")
    await _insert_channel(channel_db, "alice", "discord", "discord://w/t")

    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.add.return_value = None
        instance.notify.return_value = True
        # Remove async_notify so dispatcher falls back to sync notify.
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with pg.connect(channel_db) as db:
            results = await dispatcher.dispatch(
                db, user_id="alice", title="Hi", body="there"
            )

    assert {r.channel_type for r in results} == {"slack", "discord"}
    assert all(r.ok for r in results)
    assert instance.notify.call_count == 2


@pytest.mark.asyncio
async def test_dispatch_returns_error_on_apprise_false(channel_db):
    await _insert_channel(channel_db, "alice", "slack", "slack://a/b/c")

    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.notify.return_value = False
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with pg.connect(channel_db) as db:
            results = await dispatcher.dispatch(
                db, user_id="alice", title="Hi", body="there"
            )

    assert len(results) == 1
    assert results[0].ok is False
    assert "delivery failed" in results[0].error


@pytest.mark.asyncio
async def test_dispatch_skips_disabled(channel_db):
    await _insert_channel(channel_db, "alice", "slack", "slack://a/b/c", enabled=0)
    await _insert_channel(channel_db, "alice", "discord", "discord://w/t", enabled=1)

    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with pg.connect(channel_db) as db:
            results = await dispatcher.dispatch(
                db, user_id="alice", title="T", body="B"
            )

    assert {r.channel_type for r in results} == {"discord"}


@pytest.mark.asyncio
async def test_test_send_returns_ok_true_on_success(channel_db):
    cid = await _insert_channel(channel_db, "alice", "slack", "slack://a/b/c")

    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with pg.connect(channel_db) as db:
            result = await dispatcher.test_send(db, cid)

    assert result.ok is True
    assert result.channel_type == "slack"


@pytest.mark.asyncio
async def test_test_send_returns_error_on_exception(channel_db):
    cid = await _insert_channel(channel_db, "alice", "slack", "slack://a/b/c")

    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.notify.side_effect = RuntimeError("boom")
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with pg.connect(channel_db) as db:
            result = await dispatcher.test_send(db, cid)

    assert result.ok is False
    assert "boom" in result.error


def test_format_payload_is_pass_through_for_both_live_channels():
    """Delivery is email + webhook only, and neither wants markup.

    The chat branches (Slack ``*bold*``, Discord ``**bold**``, Telegram
    MarkdownV2) died with the channels themselves on 2026-08-24. The function
    stays as the ONE chokepoint where per-channel templating would live, so the
    HTML email upgrade is a local change — but today both channels get the
    payload untouched.
    """
    assert dispatcher.format_payload("email", "T", "B") == ("T", "B")
    assert dispatcher.format_payload("webhook", "T", "B") == ("T", "B")


def test_format_payload_does_not_resurrect_chat_markup():
    """A deleted channel type must not silently keep its old formatting.

    Guards the removal: if someone re-adds a ``slack``/``discord``/``telegram``
    branch, this fails. An unknown type falls through to plain pass-through
    rather than getting markup nobody asked for.
    """
    for dead in ("slack", "discord", "telegram"):
        assert dispatcher.format_payload(dead, "T", "B") == ("T", "B")


@pytest.mark.asyncio
async def test_test_send_rejects_cross_user_channel_id(channel_db):
    """Defense-in-depth: dispatcher returns 'not found' when caller's
    user_id does not own the channel, even if they pass a real channel_id."""
    async with pg.connect(channel_db) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("mallory", "m@x", "!"),
        )
        await db.commit()
    alice_cid = await _insert_channel(channel_db, "alice", "slack", "slack://a/b/c")

    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with pg.connect(channel_db) as db:
            # Mallory supplies alice's real channel_id but their own user_id.
            result = await dispatcher.test_send(db, alice_cid, user_id="mallory")

    assert result.ok is False
    assert "not found" in result.error
    # Apprise was never called — ownership check rejected before dispatch.
    assert MockApp.return_value.notify.call_count == 0


# ── Rule #24 matrix: all three notify_modes × both quiet-hours states ────────
#
# #318 made these paths reachable for the first time. Until signup seeded a
# rulebook, `_load_notification_rule` returned None for every user in
# production, so dispatch always took the rule-is-None branch and defaulted to
# 'instant'. Seeded users default to 'daily', which routes through gate 3 into
# `user_notification_digests` — a branch that had never executed against a real
# user. Rule #24 requires the full grid on any newly-live dispatch path, so
# here it is: 3 modes × {inside, outside} quiet hours = 6 cases.


async def _insert_rule(
    db_path,
    user_id,
    *,
    notify_mode,
    quiet_start=None,
    quiet_end=None,
    score_threshold=0,
    enabled=1,
):
    async with pg.connect(db_path) as db:
        await db.execute(
            "INSERT INTO notification_rules(user_id, notify_mode, score_threshold, "
            "quiet_hours_start, quiet_hours_end, enabled, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (user_id, notify_mode, score_threshold, quiet_start, quiet_end, enabled),
        )
        await db.commit()


async def _pending_digest_count(db_path, user_id):
    async with pg.connect(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM user_notification_digests "
            "WHERE user_id = ? AND sent = 0",
            (user_id,),
        )
        return (await cur.fetchone())[0]


# A quiet window that is active whatever time the suite runs, so the grid is
# not flaky by clock. 00:00–23:59 covers all but one minute of the day.
_QUIET_NOW = ("00:00", "23:59")


async def _run_dispatch(db_path, *, job_id=7, force=False):
    """Dispatch one job to alice's single channel; return (results, apprise_mock)."""
    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.add.return_value = None
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify
        async with pg.connect(db_path) as db:
            results = await dispatcher.dispatch(
                db,
                user_id="alice",
                title="Hi",
                body="there",
                job_id=job_id,
                match_score=90,
                force=force,
            )
    return results, instance


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["instant", "daily", "every_n_hours"])
async def test_rule24_outside_quiet_hours(channel_db, mode):
    """Outside quiet hours: only 'instant' sends now; both bundling modes queue."""
    await _insert_channel(channel_db, "alice", "email", "resend://k:f@x.io/a@b.co/")
    await _insert_rule(channel_db, "alice", notify_mode=mode)

    results, notify = await _run_dispatch(channel_db)

    assert len(results) == 1
    if mode == "instant":
        assert results[0].ok and not results[0].queued_digest
        assert notify.notify.call_count == 1
        assert await _pending_digest_count(channel_db, "alice") == 0
    else:
        assert results[0].queued_digest, f"{mode} must bundle, not send inline"
        assert notify.notify.call_count == 0, (
            f"{mode} sent inline — that is the ~280-emails-a-night shape"
        )
        assert await _pending_digest_count(channel_db, "alice") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["instant", "daily", "every_n_hours"])
async def test_rule24_inside_quiet_hours(channel_db, mode):
    """Inside quiet hours: EVERY mode queues, 'instant' included."""
    await _insert_channel(channel_db, "alice", "email", "resend://k:f@x.io/a@b.co/")
    await _insert_rule(
        channel_db,
        "alice",
        notify_mode=mode,
        quiet_start=_QUIET_NOW[0],
        quiet_end=_QUIET_NOW[1],
    )

    results, notify = await _run_dispatch(channel_db)

    assert results[0].queued_digest, f"{mode} sent during quiet hours"
    assert notify.notify.call_count == 0
    assert await _pending_digest_count(channel_db, "alice") == 1


@pytest.mark.asyncio
async def test_rule24_force_overrides_quiet_hours_and_mode(channel_db):
    """`force=True` is how send_bundle drains a digest — it bypasses both gates."""
    await _insert_channel(channel_db, "alice", "email", "resend://k:f@x.io/a@b.co/")
    await _insert_rule(
        channel_db,
        "alice",
        notify_mode="daily",
        quiet_start=_QUIET_NOW[0],
        quiet_end=_QUIET_NOW[1],
    )

    results, notify = await _run_dispatch(channel_db, force=True)

    assert results[0].ok and not results[0].queued_digest
    assert notify.notify.call_count == 1
    assert await _pending_digest_count(channel_db, "alice") == 0


@pytest.mark.asyncio
async def test_seeded_default_mode_bundles(channel_db):
    """The seeded default must be a BUNDLING mode.

    This is the interlock that makes flipping REFRESH_CATALOG_NOTIFY safe: the
    nightly refill fans out ~280 jobs per tick, so a seeded 'instant' default
    would mean up to 280 separate emails per user per night.
    """
    from src.services.notifications.defaults import DEFAULT_NOTIFY_MODE

    assert DEFAULT_NOTIFY_MODE in ("daily", "every_n_hours")

    await _insert_channel(channel_db, "alice", "email", "resend://k:f@x.io/a@b.co/")
    await _insert_rule(channel_db, "alice", notify_mode=DEFAULT_NOTIFY_MODE)

    results, notify = await _run_dispatch(channel_db)

    assert results[0].queued_digest
    assert notify.notify.call_count == 0
