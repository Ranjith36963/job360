"""Tests for signed-cookie session management."""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from migrations import runner
from src.services.auth import sessions as auth_sessions

SESSION_SECRET = "test-secret-" + "x" * 32


@pytest.fixture
async def session_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Apply migrations up through 0001_auth only — later migrations
    # (0002_multi_tenant) rebuild user_actions / applications which this
    # test has no reason to create.
    await runner.up(path, target="0001_auth")
    async with aiosqlite.connect(path) as db:
        # Insert a placeholder user matching the sessions FK.
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("user-1", "u@example.test", "!"),
        )
        await db.commit()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.mark.asyncio
async def test_session_create_and_resolve(session_db):
    cookie = await auth_sessions.create_session(
        session_db, user_id="user-1", secret=SESSION_SECRET
    )
    assert isinstance(cookie, str) and "." in cookie
    resolved = await auth_sessions.resolve_session(
        session_db, cookie, secret=SESSION_SECRET
    )
    assert resolved == "user-1"


@pytest.mark.asyncio
async def test_session_revoke(session_db):
    cookie = await auth_sessions.create_session(
        session_db, user_id="user-1", secret=SESSION_SECRET
    )
    await auth_sessions.revoke_session(session_db, cookie, secret=SESSION_SECRET)
    assert await auth_sessions.resolve_session(
        session_db, cookie, secret=SESSION_SECRET
    ) is None


@pytest.mark.asyncio
async def test_cookie_tampering_rejected(session_db):
    cookie = await auth_sessions.create_session(
        session_db, user_id="user-1", secret=SESSION_SECRET
    )
    # Tamper the signature deterministically by flipping its FIRST base64 char.
    # Flipping the LAST char is flaky: unpadded base64 discards the final char's
    # low bits, so a flip can decode to the same HMAC (~5% of runs) and the
    # tampered cookie still verifies. The first signature char's 6 bits are all
    # meaningful, so flipping it always changes the decoded HMAC → guaranteed reject.
    last_dot = cookie.rfind(".")
    sig = cookie[last_dot + 1 :]
    flipped_sig = ("a" if sig[0] != "a" else "b") + sig[1:]
    tampered = cookie[: last_dot + 1] + flipped_sig
    assert await auth_sessions.resolve_session(
        session_db, tampered, secret=SESSION_SECRET
    ) is None


@pytest.mark.asyncio
async def test_expired_session_returns_none(session_db):
    # Create a session that is already past expiry by manipulating expires_at.
    cookie = await auth_sessions.create_session(
        session_db, user_id="user-1", secret=SESSION_SECRET
    )
    async with aiosqlite.connect(session_db) as db:
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        await db.execute("UPDATE sessions SET expires_at = ?", (past,))
        await db.commit()
    assert await auth_sessions.resolve_session(
        session_db, cookie, secret=SESSION_SECRET
    ) is None


@pytest.mark.asyncio
async def test_wrong_secret_rejected(session_db):
    cookie = await auth_sessions.create_session(
        session_db, user_id="user-1", secret=SESSION_SECRET
    )
    assert await auth_sessions.resolve_session(
        session_db, cookie, secret="a-different-secret-that-is-long-enough"  # noqa: S106  # test-only dummy secret, not a real credential
    ) is None
