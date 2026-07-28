"""Tests for signed-cookie session management."""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from migrations import runner
from src.repositories import pg
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
    async with pg.connect(path) as db:
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
    async with pg.connect(session_db) as db:
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
        session_db, cookie, secret="a-different-secret-that-is-long-enough"
    ) is None


# ── Audit attribution: a revoked session must name WHOSE it was ───────────────


@pytest.mark.asyncio
async def test_revoke_session_returns_the_owner(session_db):
    """`revoke_session` must report which user it just signed out.

    Measured in production 2026-07-28: audit_log held 59 rows, 19 with no
    user_id. Ten were `magic_link_request`, legitimately unattributed (no
    account exists yet at request time) — but 4 `logout` and 4
    `session_revoked` had lost a user that WAS knowable, because this function
    only ever ran `DELETE FROM sessions WHERE id = ?` and never read the owner.

    So "who logged out?" could not be answered from the audit trail — in the one
    table you would reach for during an incident.
    """
    cookie = await auth_sessions.create_session(
        session_db, user_id="user-42", secret=SESSION_SECRET
    )
    owner = await auth_sessions.revoke_session(
        session_db, cookie, secret=SESSION_SECRET
    )
    assert owner == "user-42"


@pytest.mark.asyncio
async def test_revoke_session_returns_none_for_an_unknown_cookie(session_db):
    """A forged or already-revoked cookie has no owner — and must not raise.

    Logout is called with whatever cookie the browser presents, so this path is
    reachable by anyone; it has to degrade quietly rather than 500.
    """
    assert await auth_sessions.revoke_session(
        session_db, "not-a-real-cookie", secret=SESSION_SECRET
    ) is None


@pytest.mark.asyncio
async def test_session_revoked_audit_event_carries_the_user(session_db, caplog):
    """The audit line itself must be attributable, not just the return value."""
    import logging

    cookie = await auth_sessions.create_session(
        session_db, user_id="user-99", secret=SESSION_SECRET
    )
    with caplog.at_level(logging.INFO):
        await auth_sessions.revoke_session(session_db, cookie, secret=SESSION_SECRET)

    revoked = [r for r in caplog.records if getattr(r, "event", None) == "session_revoked"]
    assert revoked, "revoking a session must emit a session_revoked audit event"
    assert getattr(revoked[0], "user_id", None) == "user-99", (
        "the audit event must name the user whose session ended"
    )
