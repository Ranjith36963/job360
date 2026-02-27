import json
from datetime import datetime, timezone, timedelta

import aiosqlite

from src.models import Job


class JobDatabase:
    def __init__(self, db_path: str):
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init_db(self):
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT DEFAULT '',
                salary_min REAL,
                salary_max REAL,
                description TEXT DEFAULT '',
                apply_url TEXT NOT NULL,
                source TEXT NOT NULL,
                date_found TEXT NOT NULL,
                match_score INTEGER DEFAULT 0,
                visa_flag INTEGER DEFAULT 0,
                normalized_company TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                UNIQUE(normalized_company, normalized_title)
            );
            CREATE TABLE IF NOT EXISTS run_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total_found INTEGER DEFAULT 0,
                new_jobs INTEGER DEFAULT 0,
                per_source TEXT DEFAULT '{}'
            );
        """)
        await self._conn.commit()

    async def get_tables(self) -> list[str]:
        cursor = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def is_job_seen(self, normalized_key: tuple[str, str]) -> bool:
        company, title = normalized_key
        cursor = await self._conn.execute(
            "SELECT 1 FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
            (company, title),
        )
        return await cursor.fetchone() is not None

    async def insert_job(self, job: Job):
        company, title = job.normalized_key()
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT OR IGNORE INTO jobs
            (title, company, location, salary_min, salary_max, description,
             apply_url, source, date_found, match_score, visa_flag,
             normalized_company, normalized_title, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.title, job.company, job.location,
                job.salary_min, job.salary_max, job.description,
                job.apply_url, job.source, job.date_found,
                job.match_score, int(job.visa_flag),
                company, title, now,
            ),
        )
        await self._conn.commit()

    async def count_jobs(self) -> int:
        cursor = await self._conn.execute("SELECT COUNT(*) FROM jobs")
        row = await cursor.fetchone()
        return row[0]

    async def log_run(self, stats: dict):
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO run_log (timestamp, total_found, new_jobs, per_source) VALUES (?, ?, ?, ?)",
            (
                now,
                stats.get("total_found", 0),
                stats.get("new_jobs", 0),
                json.dumps(stats.get("per_source", {})),
            ),
        )
        await self._conn.commit()

    async def get_run_logs(self) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT timestamp, total_found, new_jobs, per_source FROM run_log ORDER BY id DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "timestamp": row[0],
                "total_found": row[1],
                "new_jobs": row[2],
                "per_source": json.loads(row[3]),
            }
            for row in rows
        ]

    async def get_new_jobs_since(self, hours: int = 12) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cursor = await self._conn.execute(
            "SELECT * FROM jobs WHERE first_seen >= ? ORDER BY match_score DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def close(self):
        if self._conn:
            await self._conn.close()
