"""Personal API tokens — a credential for agents, not a session.

A token is ``j360_`` + 43 url-safe chars (32 random bytes = 256 bits). We store
``sha256(token)`` only; the plaintext is returned once by :func:`mint` and never
again. Lookup is by hash, so a wrong guess costs one indexed SELECT and leaks no
timing. Revocation flips ``revoked_at`` and takes effect on the next request —
there is no cache to wait out.

Design: docs/plans/2026-09-03-mcp-server/spec.md (R1, R2, security guardrails).
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.repositories import pg
from src.repositories.db_retry import open_db
from src.utils.logger import get_audit_logger

TOKEN_PREFIX = "j360_"  # noqa: S105 — a public display prefix, not a secret
PREFIX_DISPLAY_CHARS = 12
# `last_used_at` is a hint for the user ("is this token still in use?"), not an
# audit trail; writing it on every call would turn each MCP request into a
# write. Once per window is plenty.
LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class TokenOwner:
    """What a valid bearer resolves to."""

    token_id: int
    user_id: str
    email: str
    email_verified: bool


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def count_active(db_path: str, user_id: str) -> int:
    """Number of unrevoked tokens the user holds (for the per-user cap)."""
    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL",
            (user_id,),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def mint(db_path: str, *, user_id: str, name: str) -> dict[str, Any]:
    """Create a token. Returns the row fields PLUS ``token`` — the only time it exists in plaintext."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    prefix = token[:PREFIX_DISPLAY_CHARS]
    created_at = _now().isoformat()
    async with open_db(db_path) as db:
        cur = await db.execute(
            """
            INSERT INTO api_tokens(user_id, name, token_hash, prefix, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, name, _hash(token), prefix, created_at),
        )
        token_id = cur.lastrowid
        await db.commit()
    get_audit_logger().info(
        "api_token_create",
        extra={"event": "api_token_create", "user_id": user_id, "token_id": token_id, "prefix": prefix},
    )
    return {
        "id": int(token_id),
        "name": name,
        "prefix": prefix,
        "created_at": created_at,
        "last_used_at": None,
        "token": token,
    }


async def list_active(db_path: str, user_id: str) -> list[dict[str, Any]]:
    """The user's live tokens — never the hash, never the secret."""
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            """
            SELECT id, name, prefix, created_at, last_used_at
            FROM api_tokens
            WHERE user_id = ? AND revoked_at IS NULL
            ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["name"],
            "prefix": r["prefix"],
            "created_at": str(r["created_at"]),
            "last_used_at": str(r["last_used_at"]) if r["last_used_at"] else None,
        }
        for r in rows
    ]


async def revoke(db_path: str, *, user_id: str, token_id: int) -> bool:
    """Soft-revoke one of the user's tokens. False when it is not theirs or already gone."""
    async with open_db(db_path) as db:
        cur = await db.execute(
            """
            UPDATE api_tokens SET revoked_at = ?
            WHERE id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (_now().isoformat(), token_id, user_id),
        )
        changed = cur.rowcount
        await db.commit()
    if changed:
        get_audit_logger().info(
            "api_token_revoke",
            extra={"event": "api_token_revoke", "user_id": user_id, "token_id": token_id},
        )
    return bool(changed)


async def resolve(db_path: str, token: str) -> Optional[TokenOwner]:
    """Map a presented bearer to its owner, or None. Revoked / deleted users → None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            """
            SELECT t.id AS token_id, t.last_used_at,
                   u.id AS user_id, u.email, u.email_verified_at
            FROM api_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ? AND t.revoked_at IS NULL AND u.deleted_at IS NULL
            """,
            (_hash(token),),
        )
        row = await cur.fetchone()
        if not row:
            return None
        last_used = _parse_ts(row["last_used_at"])
        now = _now()
        if last_used is None or now - last_used >= LAST_USED_WRITE_INTERVAL:
            await db.execute(
                "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                (now.isoformat(), row["token_id"]),
            )
            await db.commit()
    return TokenOwner(
        token_id=int(row["token_id"]),
        user_id=row["user_id"],
        email=row["email"],
        email_verified=row["email_verified_at"] is not None,
    )
