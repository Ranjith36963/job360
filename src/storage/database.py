import json
from datetime import datetime, timezone, timedelta

import aiosqlite

from src.models import Job
from src.storage.user_actions import UserActionsDB
from src.pipeline.tracker import ApplicationTracker


SCHEMA_VERSION = 6  # Bump when schema changes


class JobDatabase:
    def __init__(self, db_path: str):
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._user_actions: UserActionsDB | None = None
        self._applications: ApplicationTracker | None = None

    async def _get_schema_version(self) -> int:
        """Get current schema version from DB (0 if table doesn't exist)."""
        try:
            cursor = await self._conn.execute(
                "SELECT version FROM schema_version ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    async def _set_schema_version(self, version: int):
        """Record a schema version."""
        await self._conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()

    async def _run_migrations(self, current: int):
        """Run migrations from current version to SCHEMA_VERSION."""
        if current < 1:
            # v1: base schema (jobs, run_log, user_actions, applications)
            pass  # Created in init_db below

        if current < 2:
            # v2: schema_version table itself
            pass  # Created in init_db below

        if current < 3:
            # v3: recompute normalized_title to strip codes/parens (aligned with dedup)
            await self._migrate_v3_normalized_titles()

        if current < 4:
            # v4: add job_type column
            await self._migrate_v4_job_type()

        if current < 5:
            # v5: add match_data column (JSON score breakdown)
            await self._migrate_v5_match_data()

        if current < 6:
            # v6: add embedding column + FTS5 virtual table
            await self._migrate_v6_fts5_and_embeddings()

        await self._set_schema_version(SCHEMA_VERSION)

    async def _migrate_v3_normalized_titles(self):
        """Recompute normalized_title for all rows using updated Job.normalized_key()."""
        from src.models import _TRAILING_CODE_RE, _PAREN_RE
        cursor = await self._conn.execute("SELECT id, title FROM jobs")
        rows = await cursor.fetchall()
        for row in rows:
            title = row[1].strip() if row[1] else ""
            title = _TRAILING_CODE_RE.sub('', title)
            title = _PAREN_RE.sub('', title)
            title = title.strip().lower()
            await self._conn.execute(
                "UPDATE jobs SET normalized_title = ? WHERE id = ?",
                (title, row[0]),
            )
        await self._conn.commit()

    async def _migrate_v4_job_type(self):
        """Add job_type column to jobs table."""
        try:
            await self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN job_type TEXT DEFAULT ''"
            )
            await self._conn.commit()
        except Exception:
            pass  # Column may already exist

    async def _migrate_v5_match_data(self):
        """Add match_data column to jobs table (JSON score breakdown)."""
        try:
            await self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN match_data TEXT DEFAULT ''"
            )
            await self._conn.commit()
        except Exception:
            pass  # Column may already exist

    async def _migrate_v6_fts5_and_embeddings(self):
        """Add embedding column and FTS5 virtual table for hybrid retrieval."""
        # Add embedding column
        try:
            await self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN embedding TEXT DEFAULT ''"
            )
            await self._conn.commit()
        except Exception:
            pass  # Column may already exist

        # Create FTS5 virtual table (content-less, synced manually)
        try:
            await self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
                    title, company, description,
                    content='jobs',
                    content_rowid='id'
                )
            """)
            await self._conn.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("FTS5 creation failed: %s", e)

        # Populate FTS5 index from existing jobs
        try:
            await self._conn.execute("""
                INSERT OR IGNORE INTO jobs_fts(rowid, title, company, description)
                SELECT id, title, company, description FROM jobs
            """)
            await self._conn.commit()
        except Exception:
            pass

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
                job_type TEXT DEFAULT '',
                match_data TEXT DEFAULT '',
                embedding TEXT DEFAULT '',
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

            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('liked', 'applied', 'not_interested')),
                timestamp TEXT NOT NULL,
                notes TEXT DEFAULT '',
                UNIQUE(job_id),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

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
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL
            );
        """)
        await self._conn.commit()

        # Run migrations if needed
        current = await self._get_schema_version()
        if current < SCHEMA_VERSION:
            await self._run_migrations(current)

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
             experience_level, job_type, match_data, embedding,
             normalized_company, normalized_title, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.title, job.company, job.location,
                job.salary_min, job.salary_max, job.description,
                job.apply_url, job.source, job.date_found,
                job.match_score, int(job.visa_flag),
                job.experience_level, job.job_type, job.match_data,
                getattr(job, 'embedding', ''),
                company, title, now,
            ),
        )
        await self._conn.commit()
        # Sync FTS5 index — use normalized key lookup (company, title defined above)
        try:
            id_cur = await self._conn.execute(
                "SELECT id FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
                (company, title),
            )
            id_row = await id_cur.fetchone()
            if id_row:
                await self._conn.execute(
                    "INSERT OR IGNORE INTO jobs_fts(rowid, title, company, description) VALUES (?, ?, ?, ?)",
                    (id_row[0], job.title, job.company, job.description),
                )
                await self._conn.commit()
        except Exception:
            pass  # FTS5 may not be available

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

    @property
    def user_actions(self) -> UserActionsDB:
        if self._user_actions is None:
            self._user_actions = UserActionsDB(self._conn)
        return self._user_actions

    @property
    def applications(self) -> ApplicationTracker:
        if self._applications is None:
            self._applications = ApplicationTracker(self._conn)
        return self._applications

    @property
    def retriever(self):
        """Lazy-loaded HybridRetriever for FTS5 + vector search."""
        if not hasattr(self, '_retriever') or self._retriever is None:
            from src.filters.hybrid_retriever import HybridRetriever
            self._retriever = HybridRetriever(self._conn)
        return self._retriever

    async def search_jobs(self, query: str, profile_embedding=None, limit: int = 50) -> list[dict]:
        """Hybrid search combining FTS5 + vector similarity via RRF."""
        return await self.retriever.search_with_details(
            query, profile_embedding=profile_embedding, limit=limit
        )

    async def get_job_by_id(self, job_id: int) -> dict | None:
        cursor = await self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def close(self):
        if self._conn:
            await self._conn.close()
