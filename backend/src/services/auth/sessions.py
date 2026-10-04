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

import asyncio
import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional, cast

from itsdangerous import BadSignature, TimestampSigner

from src.core import settings
from src.repositories import pg
from src.repositories.db_retry import open_db
from src.utils.logger import get_audit_logger

SESSION_MAX_AGE_DAYS = 30

logger = logging.getLogger("job360.auth.sessions")

# ── Background `last_seen` touch (FC-008) ─────────────────────────────────────
# The touch runs OFF the request path: the request returns at once and the
# UPDATE runs in a task with its own connection. Prod commits can take 10 s+,
# and awaiting the write made the first request after an idle window take
# 16.7 s (measured 2026-10-04).
#
# These structures are in-process and bounded. They only ever SKIP an
# informational write; they never grant or refuse access. Losing them (restart,
# another worker process) costs at most a few extra writes.
_MAX_INFLIGHT_TOUCHES = 1000  # beyond this many running touches, skip (informational)
_MAX_RECENT_TOUCHES = 10000  # LRU cap on remembered successful touches
_touch_tasks: set[asyncio.Task[None]] = set()  # strong refs, so no GC mid-flight
_inflight_touches: dict[str, asyncio.Task[None]] = {}  # session id -> its running touch
_recent_touches: OrderedDict[str, datetime] = OrderedDict()  # session id -> last OK touch


async def _write_last_seen(db_path: str, sid: str, when_iso: str) -> None:
    """The touch itself, on its OWN short-lived connection (never the request's)."""
    async with open_db(db_path) as db:
        await db.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (when_iso, sid))
        await db.commit()


async def _run_touch(db_path: str, sid: str, when: datetime) -> None:
    """Run one touch and swallow every failure: a failed touch never affects a request."""
    try:
        await _write_last_seen(db_path, sid, when.isoformat())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — informational write, must never raise
        logger.warning(
            "session last_seen touch failed (session %s...): %s: %s", sid[:8], type(exc).__name__, exc
        )
        return
    _recent_touches[sid] = when
    _recent_touches.move_to_end(sid)
    while len(_recent_touches) > _MAX_RECENT_TOUCHES:
        _recent_touches.popitem(last=False)


def _touch_in_flight(sid: str) -> bool:
    """True while a touch for ``sid`` is running on THIS event loop.

    A finished task, or one from another (closed) loop — only possible in
    tests — is dropped, so a stale entry can never block touches forever.
    """
    task = _inflight_touches.get(sid)
    if task is None:
        return False
    if task.done() or task.get_loop() is not asyncio.get_running_loop():
        _inflight_touches.pop(sid, None)
        _touch_tasks.discard(task)
        return False
    return True


def _recently_touched(sid: str, now: datetime, interval_seconds: int) -> bool:
    """True when THIS process already wrote ``last_seen`` inside the window.

    Saves a write when the request's SELECT raced the background UPDATE. With
    ``interval_seconds <= 0`` (write every request) it never skips.
    """
    if interval_seconds <= 0:
        return False
    when = _recent_touches.get(sid)
    if when is None:
        return False
    return 0 <= (now - when).total_seconds() < interval_seconds


def _schedule_touch(db_path: str, sid: str, when: datetime) -> None:
    """Start the background touch for ``sid`` unless one is already running."""
    if _touch_in_flight(sid):
        return
    if len(_inflight_touches) >= _MAX_INFLIGHT_TOUCHES:
        return
    task = asyncio.get_running_loop().create_task(_run_touch(db_path, sid, when))
    _touch_tasks.add(task)
    _inflight_touches[sid] = task

    def _done(t: asyncio.Task[None]) -> None:
        _touch_tasks.discard(t)
        if _inflight_touches.get(sid) is t:
            del _inflight_touches[sid]

    task.add_done_callback(_done)


async def drain_session_touches(timeout: Optional[float] = None) -> None:
    """Wait for this loop's pending ``last_seen`` touches; cancel any left at ``timeout``.

    Used by tests (deterministic asserts) and by app shutdown (no "task was
    destroyed but it is pending", no long block). Never raises.
    """
    loop = asyncio.get_running_loop()
    mine = {t for t in _touch_tasks if t.get_loop() is loop and not t.done()}
    if mine:
        _, pending = await asyncio.wait(mine, timeout=timeout)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=1.0)
    # Purge anything finished or owned by another (closed) loop.
    for sid, t in list(_inflight_touches.items()):
        if t.done() or t.get_loop() is not loop:
            _inflight_touches.pop(sid, None)
    for t in list(_touch_tasks):
        if t.done() or t.get_loop() is not loop:
            _touch_tasks.discard(t)


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


def _parse_ts(value: object) -> Optional[datetime]:
    """Parse a stored timestamp (ISO text, or Postgres CURRENT_TIMESTAMP text).

    Naive values are taken as UTC. Anything unparseable returns None, which the
    caller treats as stale — so a strange value costs one write, never a skip.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _touch_due(last_seen: object, now: datetime, interval_seconds: int) -> bool:
    """True when ``last_seen`` is older than the touch window (or unknown)."""
    if interval_seconds <= 0:
        return True
    seen = _parse_ts(last_seen)
    if seen is None:
        return True
    return (now - seen).total_seconds() >= interval_seconds


async def resolve_session(
    db_path: str,
    cookie: str,
    *,
    secret: str,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return the ``user_id`` for a valid, unexpired session cookie, else None.

    Signature is verified before any DB lookup. Access is decided ONLY by the
    row existing and ``expires_at`` (absolute) being in the future.

    ``last_seen`` is informational and written at most once per
    ``settings.SESSION_TOUCH_INTERVAL_SECONDS``: inside the window the resolve
    is a pure read (one SELECT, no UPDATE, no commit). Writing it on every
    request made each signed-in read wait on a commit (FC-008). When a touch
    IS due it runs in the background (``_schedule_touch``): the request never
    waits for it, and one session has at most one touch in flight.

    The row is SELECTed on every call (no session cache), so logout, revoke
    and expiry apply on the very next request.

    ``now`` is an injectable clock for tests; production passes nothing.
    """
    sid = _unsign(cookie, secret)
    if sid is None:
        return None
    now_dt = now if now is not None else datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT user_id, expires_at, last_seen FROM sessions WHERE id = ?", (sid,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now_iso:
            return None
    interval = settings.SESSION_TOUCH_INTERVAL_SECONDS
    if _touch_due(row["last_seen"], now_dt, interval) and not _recently_touched(sid, now_dt, interval):
        _schedule_touch(db_path, sid, now_dt)
    return cast(Optional[str], row["user_id"])


async def revoke_session(db_path: str, cookie: str, *, secret: str) -> Optional[str]:
    """Delete one session and return the user it belonged to (None if unknown).

    Reads the owner BEFORE deleting so the audit trail can say *who* signed out.
    It previously ran the DELETE alone, which meant the one fact worth recording
    was thrown away: measured in production 2026-07-28, `audit_log` held 19 of 59
    rows with no user_id, and while 10 were `magic_link_request` (legitimately
    unattributed — no account exists yet at request time), 4 `logout` and 4
    `session_revoked` had lost a user that was perfectly knowable. "Who logged
    out?" was unanswerable from the table you reach for during an incident.

    SELECT-then-DELETE rather than `DELETE ... RETURNING`: every statement here
    goes through the psycopg shim's translate(), and SELECT + DELETE are already
    exercised everywhere in this codebase, so this cannot depend on how the shim
    handles a RETURNING clause.

    Returns None for a forged, expired or already-revoked cookie — logout is
    called with whatever the browser presents, so that path must degrade quietly.
    """
    sid = _unsign(cookie, secret)
    if sid is None:
        return None
    async with open_db(db_path) as db:
        cur = await db.execute("SELECT user_id FROM sessions WHERE id = ?", (sid,))
        row = await cur.fetchone()
        user_id = cast(Optional[str], row["user_id"]) if row else None
        await db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        await db.commit()
    get_audit_logger().info(
        "session_revoked",
        extra={"event": "session_revoked", "session_id": sid[:8], "user_id": user_id},
    )
    return user_id


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
        return cast(int, cur.rowcount)
