"""One idempotency store for the write tools (gap roadmap slice 10, migration 0068).

A write that takes a ``request_id`` keeps one ``idempotency_keys`` row per
(user, tool, key): the sha256 of the canonical JSON request body and the reply
it gave. ``lookup`` before the write: the same key with the same body returns
that FIRST reply (the caller writes nothing); the same key with a different
body raises ``IdempotencyError`` (409 ``idempotency_key_reused``). ``store``
runs INSIDE the caller's transaction, so the guarded write and its key commit
or roll back together; the ``UNIQUE (user_id, tool, key)`` index makes a
concurrent duplicate fail its whole transaction (``pg.IntegrityError``), and
the caller then ``lookup``s again to return the winner's reply.

``record_application`` is the first user (``request_id``). Slice 25 adds
``run_once`` for the five other write tools (``client_key``: save_artifact,
record_event, bring_job, add_contact, ask_user). Scoped by ``user_id``
(rule #12): one user's key never answers another's.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from src.repositories import pg
from src.utils.logger import get_audit_logger, safe_log_value

KEY_MAX_CHARS = 128
KEY_REUSED = "idempotency_key_reused"


class IdempotencyError(Exception):
    """A bad or reused key; a route turns it into ``HTTPException(status_code, detail)``."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def check_key(key: str, field: str = "request_id") -> str:
    """1..128 printable characters, kept exactly as given (never trimmed)."""
    if not isinstance(key, str) or not 1 <= len(key) <= KEY_MAX_CHARS or not key.isprintable():
        raise IdempotencyError(422, f"{field} must be 1-{KEY_MAX_CHARS} printable characters")
    return key


def body_sha256(body: Any) -> str:
    """sha256 of the canonical JSON (sorted keys, no spaces) of the request body."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def lookup(
    conn: pg.Connection, user_id: str, tool: str, key: str, body: Any, field: str = "request_id"
) -> Optional[dict[str, Any]]:
    """The stored reply for this key, or ``None`` when the key is new.
    Raises ``IdempotencyError(409)`` when the key was used with a different body;
    ``field`` names the key in that message."""
    cur = await conn.execute(
        "SELECT body_sha256, result_json FROM idempotency_keys WHERE user_id = ? AND tool = ? AND key = ?",
        (user_id, tool, key),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    if row["body_sha256"] != body_sha256(body):
        get_audit_logger().info(
            KEY_REUSED,
            extra={"event": KEY_REUSED, "user_id": safe_log_value(user_id), "tool": tool, "result": "refused"},
        )
        raise IdempotencyError(
            409, f"{KEY_REUSED}: this {field} was already used for a different {tool} request; use a new one"
        )
    result: dict[str, Any] = json.loads(row["result_json"])
    return result


async def store(
    conn: pg.Connection, user_id: str, tool: str, key: str, body: Any, result: dict[str, Any]
) -> None:
    """Save the reply for this key. Call it inside the write's own transaction."""
    await conn.execute(
        "INSERT INTO idempotency_keys (user_id, tool, key, body_sha256, result_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id, tool, key, body_sha256(body), json.dumps(result, default=str),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


async def run_once(
    conn: pg.Connection,
    user_id: str,
    tool: str,
    key: Optional[str],
    body: Any,
    write: Callable[[], Awaitable[dict[str, Any]]],
    *,
    atomic: bool = True,
) -> dict[str, Any]:
    """Run ``write`` at most once per ``client_key`` (gap roadmap slice 25).

    No key: ``write()`` exactly as before. A key seen with the same ``body``:
    the FIRST reply with ``already_existed: True``, nothing written. A key seen
    with another body: ``IdempotencyError(409)``. Otherwise ``write`` and the
    key row commit in ONE transaction (``atomic``); a racing duplicate fails on
    the UNIQUE key, rolls back whole, and gets the winner's reply. ``atomic=False``
    (bring_job, whose read-only checks swallow their own errors) stores the key
    right after the write instead; its own title+company dedupe covers the gap.
    """
    if key is None:
        return await write()
    check_key(key, "client_key")
    stored = await lookup(conn, user_id, tool, key, body, field="client_key")
    if stored is None:
        try:
            if atomic:
                async with conn.transaction():
                    result = {"already_existed": False, **(await write())}
                    await store(conn, user_id, tool, key, body, result)
            else:
                result = {"already_existed": False, **(await write())}
                await store(conn, user_id, tool, key, body, result)
            return result
        except pg.IntegrityError:
            stored = await lookup(conn, user_id, tool, key, body, field="client_key")
            if stored is None:
                raise
    get_audit_logger().info(
        "client_key_replayed",
        extra={"event": "client_key_replayed", "user_id": safe_log_value(user_id), "tool": tool, "result": "ok"},
    )
    return {**stored, "already_existed": True}
