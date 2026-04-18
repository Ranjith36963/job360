"""FeedService — single source of truth for per-user job view.

Both the FastAPI dashboard endpoints AND the notification worker read from
the same ``user_feed`` rows via this service, guaranteeing parity.

Blueprint §3 rationale: "one PostgreSQL table (user_feed) serving both
dashboard and notifications ... both surfaces always see identical data."

Phase 3 ships the read surface. Phase 4 adds ``ingest_job`` (write path).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiosqlite


@dataclass(frozen=True)
class FeedRow:
    id: int
    user_id: str
    job_id: int
    score: int
    bucket: str
    status: str
    notified_at: Optional[str]
    created_at: str
    updated_at: str


def _row(r: aiosqlite.Row) -> FeedRow:
    return FeedRow(
        id=r["id"],
        user_id=r["user_id"],
        job_id=r["job_id"],
        score=r["score"],
        bucket=r["bucket"],
        status=r["status"],
        notified_at=r["notified_at"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


class FeedService:
    """Read + write operations on ``user_feed``.

    Construct with an open ``aiosqlite.Connection`` (not a path) so callers
    control transaction + connection lifecycle. All methods are ``async``.
    """

    def __init__(self, db: aiosqlite.Connection):
        self._db = db
        self._db.row_factory = aiosqlite.Row

    # ----- reads ----------------------------------------------------------

    async def list_for_user(
        self,
        user_id: str,
        *,
        bucket: Optional[str] = None,
        status: str = "active",
        limit: int = 200,
    ) -> list[FeedRow]:
        """Dashboard query: active rows for a user, newest-highest-score first."""
        query = (
            "SELECT * FROM user_feed "
            "WHERE user_id = ? AND status = ?"
        )
        params: list = [user_id, status]
        if bucket is not None:
            query += " AND bucket = ?"
            params.append(bucket)
        query += " ORDER BY score DESC, created_at DESC LIMIT ?"
        params.append(limit)
        cur = await self._db.execute(query, params)
        return [_row(r) for r in await cur.fetchall()]

    async def list_pending_notifications(
        self,
        user_id: str,
        *,
        min_score: int,
        limit: int = 15,
    ) -> list[FeedRow]:
        """Notification worker query: unsent, active, score >= threshold."""
        cur = await self._db.execute(
            """
            SELECT * FROM user_feed
            WHERE user_id = ?
              AND status = 'active'
              AND notified_at IS NULL
              AND score >= ?
            ORDER BY score DESC, created_at DESC
            LIMIT ?
            """,
            (user_id, min_score, limit),
        )
        return [_row(r) for r in await cur.fetchall()]

    # ----- writes ---------------------------------------------------------

    async def mark_notified(self, feed_ids: list[int]) -> None:
        if not feed_ids:
            return
        placeholders = ",".join("?" for _ in feed_ids)
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            f"UPDATE user_feed SET notified_at = ? WHERE id IN ({placeholders})",
            [now, *feed_ids],
        )
        await self._db.commit()

    async def update_status(
        self, user_id: str, job_id: int, status: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE user_feed SET status = ?, updated_at = ? "
            "WHERE user_id = ? AND job_id = ?",
            (status, now, user_id, job_id),
        )
        await self._db.commit()

    async def cascade_stale(self, job_id: int) -> int:
        """Ghost-detection hook: mark a job stale across every user's feed.

        Returns number of rows updated.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            "UPDATE user_feed SET status = 'stale', updated_at = ? WHERE job_id = ? AND status != 'stale'",
            (now, job_id),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def upsert_feed_row(
        self,
        *,
        user_id: str,
        job_id: int,
        score: int,
        bucket: str,
    ) -> int:
        """Insert a feed row or update (score, bucket) if (user, job) already tracked.

        Returns the row id. Idempotent per (user_id, job_id) per UNIQUE constraint.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(user_id, job_id)
            DO UPDATE SET score = excluded.score,
                          bucket = excluded.bucket,
                          updated_at = excluded.updated_at
            """,
            (user_id, job_id, score, bucket, now, now),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT id FROM user_feed WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        )
        row = await cur.fetchone()
        return row["id"] if row else 0
