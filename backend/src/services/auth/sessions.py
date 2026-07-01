"""Signed-cookie session management.

Cookie layout: ``<session_id>.<hmac>`` — the signature is verified FIRST,
before any DB lookup, so tampered cookies never hit SQLite. On verified
cookies we then fetch the row, check ``expires_at``, and return the user id.

Security properties:
- ``itsdangerous`` signing (HMAC-SHA256) — constant-time compare.
- Session id is a 128-bit uuid4 hex (collision-resistant).
- Revocation is durable — logout deletes the row, subsequent resolves fail.
- Absolute expiry of 30 days (config via ``SESSION_MAX_AGE_DAYS``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from itsdangerous import BadSignature, TimestampSigner

from src.repositories import pg as aiosqlite
from src.repositories.db_retry import open_db
from src.utils.logger import get_audit_logger

SESSION_MAX_AGE_DAYS = 30


def _signer(secret: str) -> TimestampSigner:
    return TimestampSigner(secret, salt="job360.session")


async def create_session(
    db_path: str,
    *,
    user_id: str,
    secret: str,
    user_agent: Optional[str] = None,
    ip_hash: Optional[str] = None,
) -> str:
    """Create a session row and return the signed cookie value."""
    sid = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_MAX_AGE_DAYS)
    async with open_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO sessions(id, user_id, expires_at, user_agent, ip_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sid, user_id, expires.isoformat(), user_agent, ip_hash),
        )
        await db.commit()
    signed = _signer(secret).sign(sid.encode("ascii")).decode("ascii")
    get_audit_logger().info("session_created", extra={"event": "session_created", "user_id": user_id})
    return signed


def _unsign(cookie: str, secret: str) -> Optional[str]:
    """Return the raw session id if the cookie signature is valid, else None."""
    try:
        raw = _signer(secret).unsign(cookie.encode("ascii"), max_age=None)
    except BadSignature:
        return None
    return raw.decode("ascii")


async def resolve_session(
    db_path: str, cookie: str, *, secret: str
) -> Optional[str]:
    """Return the ``user_id`` for a valid, unexpired session cookie, else None.

    Signature is verified before any DB lookup.
    """
    sid = _unsign(cookie, secret)
    if sid is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    async with open_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT user_id, expires_at FROM sessions WHERE id = ?", (sid,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now:
            return None
        # Slide last_seen; best-effort — ignore commit contention.
        await db.execute(
            "UPDATE sessions SET last_seen = ? WHERE id = ?", (now, sid)
        )
        await db.commit()
    return row["user_id"]


async def revoke_session(db_path: str, cookie: str, *, secret: str) -> None:
    sid = _unsign(cookie, secret)
    if sid is None:
        return
    async with open_db(db_path) as db:
        await db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        await db.commit()
    get_audit_logger().info("session_revoked", extra={"event": "session_revoked", "session_id": sid[:8]})


async def revoke_all_for_user(db_path: str, user_id: str) -> int:
    """Delete every session row for a user — terminating all their sessions
    across devices. Used on password/email change (rule #26) so a session held
    elsewhere (other device, stolen cookie) cannot survive a credential change.
    Returns the number of sessions removed.
    """
    async with open_db(db_path) as db:
        cur = await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.commit()
        get_audit_logger().info(
            "sessions_revoked_all",
            extra={"event": "sessions_revoked_all", "user_id": user_id, "count": cur.rowcount},
        )
        return cur.rowcount
