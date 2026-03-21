"""User actions on jobs — Liked, Applied, Not Interested."""

from __future__ import annotations

import enum
from datetime import datetime, timezone


class ActionType(enum.Enum):
    liked = "liked"
    applied = "applied"
    not_interested = "not_interested"


class UserActionsDB:
    """Manages user actions on jobs (one action per job at a time)."""

    def __init__(self, conn):
        self._conn = conn

    async def init_table(self):
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('liked', 'applied', 'not_interested')),
                timestamp TEXT NOT NULL,
                notes TEXT DEFAULT '',
                UNIQUE(job_id),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
        """)
        await self._conn.commit()

    async def set_action(self, job_id: int, action: ActionType, notes: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT OR REPLACE INTO user_actions (job_id, action, timestamp, notes) VALUES (?, ?, ?, ?)",
            (job_id, action.value, now, notes),
        )
        # Auto-create application entry when marking as "applied"
        if action == ActionType.applied:
            try:
                from src.pipeline.reminders import compute_next_reminder
                from src.pipeline.tracker import PipelineStage
                next_rem = compute_next_reminder(PipelineStage.applied, now)
                await self._conn.execute(
                    "INSERT OR IGNORE INTO applications "
                    "(job_id, status, date_applied, next_reminder, notes, last_updated) "
                    "VALUES (?, ?, ?, ?, '', ?)",
                    (job_id, "applied", now, next_rem, now),
                )
            except Exception:
                pass  # applications table may not exist yet
        await self._conn.commit()

    async def toggle_action(self, job_id: int, action: ActionType) -> bool:
        """Toggle an action: if same action exists, remove it. Otherwise set it.
        Returns True if the action was set, False if removed."""
        existing = await self.get_action(job_id)
        if existing and existing["action"] == action.value:
            await self.remove_action(job_id)
            return False
        await self.set_action(job_id, action)
        return True

    async def remove_action(self, job_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM user_actions WHERE job_id = ?", (job_id,)
        )
        await self._conn.commit()

    async def get_action(self, job_id: int) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT * FROM user_actions WHERE job_id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_jobs_by_action(self, action: ActionType) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM user_actions WHERE action = ? ORDER BY timestamp DESC",
            (action.value,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_all_actions(self) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM user_actions ORDER BY timestamp DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def count_by_action(self) -> dict[str, int]:
        cursor = await self._conn.execute(
            "SELECT action, COUNT(*) as cnt FROM user_actions GROUP BY action"
        )
        rows = await cursor.fetchall()
        return {row["action"]: row["cnt"] for row in rows}
