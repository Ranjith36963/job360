"""Application tracker — manages job applications through pipeline stages."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from src.pipeline.reminders import compute_next_reminder


class PipelineStage(enum.Enum):
    applied = "applied"
    outreach_week1 = "outreach_week1"
    outreach_week2 = "outreach_week2"
    outreach_week3 = "outreach_week3"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


STAGE_ORDER = [
    PipelineStage.applied,
    PipelineStage.outreach_week1,
    PipelineStage.outreach_week2,
    PipelineStage.outreach_week3,
    PipelineStage.interview,
    PipelineStage.offer,
]

TERMINAL_STAGES = {PipelineStage.offer, PipelineStage.rejected, PipelineStage.withdrawn}


class ApplicationTracker:
    """Manages job applications through the pipeline."""

    def __init__(self, conn):
        self._conn = conn

    async def init_table(self):
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'applied',
                date_applied TEXT NOT NULL,
                next_reminder TEXT,
                contact_name TEXT DEFAULT '',
                contact_email TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                last_updated TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
        """)
        await self._conn.commit()

    async def create_application(self, job_id: int, notes: str = "") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        next_rem = compute_next_reminder(PipelineStage.applied, now)
        await self._conn.execute(
            """INSERT OR IGNORE INTO applications
            (job_id, status, date_applied, next_reminder, notes, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, PipelineStage.applied.value, now, next_rem, notes, now),
        )
        await self._conn.commit()
        return await self.get_application(job_id)

    async def advance_stage(self, job_id: int, new_stage: PipelineStage) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        next_rem = compute_next_reminder(new_stage, now)
        await self._conn.execute(
            "UPDATE applications SET status = ?, next_reminder = ?, last_updated = ? WHERE job_id = ?",
            (new_stage.value, next_rem, now, job_id),
        )
        await self._conn.commit()
        return await self.get_application(job_id)

    async def get_application(self, job_id: int) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT * FROM applications WHERE job_id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_applications(self) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM applications ORDER BY last_updated DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_applications_by_stage(self, stage: PipelineStage) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM applications WHERE status = ? ORDER BY last_updated DESC",
            (stage.value,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_due_reminders(self) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._conn.execute(
            "SELECT * FROM applications WHERE next_reminder IS NOT NULL AND next_reminder <= ? ORDER BY next_reminder",
            (now,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def count_by_stage(self) -> dict[str, int]:
        cursor = await self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM applications GROUP BY status"
        )
        rows = await cursor.fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    async def delete_application(self, job_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM applications WHERE job_id = ?", (job_id,)
        )
        await self._conn.commit()

    async def update_notes(self, job_id: int, notes: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE applications SET notes = ?, last_updated = ? WHERE job_id = ?",
            (notes, now, job_id),
        )
        await self._conn.commit()

    async def update_contact(self, job_id: int, name: str = "", email: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE applications SET contact_name = ?, contact_email = ?, last_updated = ? WHERE job_id = ?",
            (name, email, now, job_id),
        )
        await self._conn.commit()
