"""Dispatcher tests — Apprise.notify is monkey-patched in every test."""
import os
import tempfile
from unittest.mock import patch

import aiosqlite
import pytest
from cryptography.fernet import Fernet

from migrations import runner
from src.services.channels import crypto, dispatcher


@pytest.fixture(autouse=True)
def _fernet_key():
    crypto.set_test_key(Fernet.generate_key().decode("ascii"))


@pytest.fixture
async def channel_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
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
        await db.commit()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_channel(db_path, user_id, channel_type, url, enabled=1):
    ct = crypto.encrypt(url)
    async with aiosqlite.connect(db_path) as db:
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

        async with aiosqlite.connect(channel_db) as db:
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

        async with aiosqlite.connect(channel_db) as db:
            results = await dispatcher.dispatch(
                db, user_id="alice", title="Hi", body="there"
            )

    assert len(results) == 1
    assert results[0].ok is False
    assert "returned False" in results[0].error


@pytest.mark.asyncio
async def test_dispatch_skips_disabled(channel_db):
    await _insert_channel(channel_db, "alice", "slack", "slack://a/b/c", enabled=0)
    await _insert_channel(channel_db, "alice", "discord", "discord://w/t", enabled=1)

    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify

        async with aiosqlite.connect(channel_db) as db:
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

        async with aiosqlite.connect(channel_db) as db:
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

        async with aiosqlite.connect(channel_db) as db:
            result = await dispatcher.test_send(db, cid)

    assert result.ok is False
    assert "boom" in result.error


def test_format_payload_variants():
    assert dispatcher.format_payload("slack", "T", "B")[1].startswith("*T*")
    assert dispatcher.format_payload("discord", "T", "B")[1].startswith("**T**")
    assert dispatcher.format_payload("email", "T", "B") == ("T", "B")


@pytest.mark.asyncio
async def test_test_send_rejects_cross_user_channel_id(channel_db):
    """Defense-in-depth: dispatcher returns 'not found' when caller's
    user_id does not own the channel, even if they pass a real channel_id."""
    async with aiosqlite.connect(channel_db) as db:
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

        async with aiosqlite.connect(channel_db) as db:
            # Mallory supplies alice's real channel_id but their own user_id.
            result = await dispatcher.test_send(db, alice_cid, user_id="mallory")

    assert result.ok is False
    assert "not found" in result.error
    # Apprise was never called — ownership check rejected before dispatch.
    assert MockApp.return_value.notify.call_count == 0
