import json
import re
from datetime import datetime, timezone, timedelta

import aiosqlite

_VALID_COL_NAME = re.compile(r'^[a-z_][a-z0-9_]{0,63}$')
_VALID_COL_TYPES = {'TEXT', 'INTEGER', 'REAL', 'BLOB', 'NUMERIC'}

from src.models import Job


class JobDatabase:
    def __init__(self, db_path: str):
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init_db(self):
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
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
                experience_level TEXT DEFAULT '',
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
                sources_queried INTEGER DEFAULT 0,
                per_source TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_date_found ON jobs(date_found);
            CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
            CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score);
        """)
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self):
        """Add any missing columns to existing tables (forward-compatible schema migration)."""
        cursor = await self._conn.execute("PRAGMA table_info(jobs)")
        existing = {row[1] for row in await cursor.fetchall()}

        # Define columns added after initial schema.
        # Format: (column_name, column_definition)
        migrations = [
            # Add future columns here, e.g.:
            # ("salary_currency", "TEXT DEFAULT ''"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                if not _VALID_COL_NAME.match(col_name):
                    raise ValueError(f"Invalid migration column name: {col_name}")
                col_type_word = col_def.split()[0].upper()
                if col_type_word not in _VALID_COL_TYPES:
                    raise ValueError(f"Invalid migration column type: {col_type_word}")
                await self._conn.execute(
                    f"ALTER TABLE jobs ADD COLUMN {col_name} {col_def}"
                )
        if migrations:
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

    async def insert_job(self, job: Job) -> bool:
        """Insert job, returning True if it was actually inserted (not a duplicate)."""
        company, title = job.normalized_key()
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._conn.execute(
            """INSERT OR IGNORE INTO jobs
            (title, company, location, salary_min, salary_max, description,
             apply_url, source, date_found, match_score, visa_flag,
             experience_level, normalized_company, normalized_title, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.title, job.company, job.location,
                job.salary_min, job.salary_max, job.description,
                job.apply_url, job.source, job.date_found,
                job.match_score, int(job.visa_flag),
                job.experience_level, company, title, now,
            ),
        )
        return cursor.rowcount > 0

    async def commit(self):
        """Commit pending changes."""
        if self._conn:
            await self._conn.commit()

    async def count_jobs(self) -> int:
        cursor = await self._conn.execute("SELECT COUNT(*) FROM jobs")
        row = await cursor.fetchone()
        return row[0]

    async def log_run(self, stats: dict):
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO run_log (timestamp, total_found, new_jobs, sources_queried, per_source) VALUES (?, ?, ?, ?, ?)",
            (
                now,
                stats.get("total_found", 0),
                stats.get("new_jobs", 0),
                stats.get("sources_queried", 0),
                json.dumps(stats.get("per_source", {})),
            ),
        )
        await self._conn.commit()

    async def get_run_logs(self, limit: int = 100) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT timestamp, total_found, new_jobs, per_source FROM run_log ORDER BY id DESC LIMIT ?",
            (limit,),
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

    async def purge_old_jobs(self, days: int = 30) -> int:
        """Delete jobs where first_seen is older than `days` ago. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = await self._conn.execute(
            "DELETE FROM jobs WHERE first_seen < ?", (cutoff,)
        )
        await self._conn.commit()
        return cursor.rowcount

    async def get_recent_jobs(self, days: int = 7, min_score: int = 0) -> list[dict]:
        """Return jobs from the last `days` with match_score >= min_score."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = await self._conn.execute(
            "SELECT * FROM jobs WHERE first_seen >= ? AND match_score >= ? ORDER BY date_found DESC",
            (cutoff, min_score),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_last_source_counts(self, n: int = 5) -> dict[str, list[int]]:
        """Get per-source job counts from the last N runs for health tracking."""
        cursor = await self._conn.execute(
            "SELECT per_source FROM run_log ORDER BY id DESC LIMIT ?", (n,)
        )
        rows = await cursor.fetchall()
        source_history: dict[str, list[int]] = {}
        for row in rows:
            per_source = json.loads(row[0]) if row[0] else {}
            for name, count in per_source.items():
                source_history.setdefault(name, []).append(count)
        return source_history

    async def close(self):
        if self._conn:
            await self._conn.close()
