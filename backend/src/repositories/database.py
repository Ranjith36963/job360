import json
import os
import re
from datetime import datetime, timedelta, timezone

import aiosqlite

_VALID_COL_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
_VALID_COL_TYPES = {"TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC"}

from src.models import Job  # noqa: E402  # after the regex constants to avoid circular import


class JobDatabase:
    def __init__(self, db_path: str):
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init_db(self):
        # Ensure parent directory exists for fresh clones where data/ isn't yet created.
        parent = os.path.dirname(os.path.abspath(self._path))
        if parent:
            os.makedirs(parent, exist_ok=True)
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
                posted_at TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                last_updated_at TEXT,
                date_confidence TEXT DEFAULT 'low',
                date_posted_raw TEXT,
                consecutive_misses INTEGER DEFAULT 0,
                staleness_state TEXT DEFAULT 'active',
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
            CREATE INDEX IF NOT EXISTS idx_jobs_staleness_state ON jobs(staleness_state);
            CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs(last_seen_at);
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
        """)
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self):
        """Add any missing columns to existing tables (forward-compatible schema migration).

        Runs every init_db(). Safe on both fresh schemas (just created above)
        and on legacy DBs lazy-upgraded in place. Mirrors the forward
        direction of the SQL migrations under backend/migrations/ so tests
        and CLI tools that bypass the external runner still see the full
        schema.
        """
        jobs_migrations = [
            # Pillar 3 Batch 1 — 5-column date model + ghost detection hooks.
            ("posted_at", "TEXT"),
            ("first_seen_at", "TEXT"),
            ("last_seen_at", "TEXT"),
            ("last_updated_at", "TEXT"),
            ("date_confidence", "TEXT DEFAULT 'low'"),
            ("date_posted_raw", "TEXT"),
            ("consecutive_misses", "INTEGER DEFAULT 0"),
            ("staleness_state", "TEXT DEFAULT 'active'"),
            # Step-1.5 S1.1 — per-dim score columns (migration 0011 mirror).
            ("role", "INTEGER DEFAULT 0"),
            ("skill", "INTEGER DEFAULT 0"),
            ("seniority_score", "INTEGER DEFAULT 0"),
            ("experience", "INTEGER DEFAULT 0"),
            ("credentials", "INTEGER DEFAULT 0"),
            ("location_score", "INTEGER DEFAULT 0"),
            ("recency", "INTEGER DEFAULT 0"),
            ("semantic", "INTEGER DEFAULT 0"),
            ("penalty", "INTEGER DEFAULT 0"),
        ]
        run_log_migrations = [
            # Step-0 pre-flight — migration 0010 observability columns.
            # Mirrored here so init_db() alone produces the full run_log
            # schema even when the external migration runner hasn't run.
            ("run_uuid", "TEXT"),
            ("per_source_errors", "TEXT DEFAULT '{}'"),
            ("per_source_duration", "TEXT DEFAULT '{}'"),
            ("total_duration", "REAL"),
            ("user_id", "TEXT"),
        ]

        applications_migrations = [
            # Step-3 B-06 — stage history + interview dates + notes versioning.
            # Mirrors migration 0014_application_history so init_db() alone
            # produces the full applications schema even before the runner runs.
            ("last_advanced_at", "TEXT"),
            ("interview_dates", "TEXT DEFAULT '[]'"),
            ("notes_history", "TEXT DEFAULT '[]'"),
        ]

        await self._add_missing_columns("jobs", jobs_migrations)
        await self._add_missing_columns("run_log", run_log_migrations)
        await self._add_missing_columns("applications", applications_migrations)

        # Ensure application_stage_history table exists (migration 0014).
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS application_stage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                from_stage TEXT,
                to_stage TEXT NOT NULL,
                transitioned_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                notes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_stage_history_job_user
                ON application_stage_history(job_id, user_id);
        """)

        await self._conn.commit()

    async def _add_missing_columns(self, table: str, migrations: list[tuple[str, str]]) -> None:
        """Apply `ALTER TABLE ... ADD COLUMN` for each entry not yet present."""
        if not _VALID_COL_NAME.match(table):
            raise ValueError(f"Invalid migration table name: {table}")
        cursor = await self._conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        for col_name, col_def in migrations:
            if col_name in existing:
                continue
            if not _VALID_COL_NAME.match(col_name):
                raise ValueError(f"Invalid migration column name: {col_name}")
            col_type_word = col_def.split()[0].upper()
            if col_type_word not in _VALID_COL_TYPES:
                raise ValueError(f"Invalid migration column type: {col_type_word}")
            await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

    async def get_tables(self) -> list[str]:
        cursor = await self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
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
        """Insert job, returning True if it was actually inserted (not a duplicate).

        Step-1 B2: lifecycle timestamps (`first_seen_at`, `last_seen_at`) are honoured
        when the caller supplies them on the Job dataclass; they fall back to
        `datetime('now')` only when the Job attribute is None. This mirrors the
        pattern already used for `posted_at`. `scraped_at` equivalent (the
        internal `first_seen` audit column) stays always-now — it's an ingestion
        timestamp, not a lifecycle timestamp.
        """
        company, title = job.normalized_key()
        now = datetime.now(timezone.utc).isoformat()
        first_seen_at = job.first_seen_at if job.first_seen_at is not None else now
        last_seen_at = job.last_seen_at if job.last_seen_at is not None else now
        cursor = await self._conn.execute(
            """INSERT OR IGNORE INTO jobs
            (title, company, location, salary_min, salary_max, description,
             apply_url, source, date_found, match_score, visa_flag,
             experience_level, normalized_company, normalized_title, first_seen,
             posted_at, first_seen_at, last_seen_at, date_confidence,
             date_posted_raw,
             role, skill, seniority_score, experience, credentials,
             location_score, recency, semantic, penalty)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.title,
                job.company,
                job.location,
                job.salary_min,
                job.salary_max,
                job.description,
                job.apply_url,
                job.source,
                job.date_found,
                job.match_score,
                int(job.visa_flag),
                job.experience_level,
                company,
                title,
                now,
                job.posted_at,
                first_seen_at,
                last_seen_at,
                job.date_confidence,
                job.date_posted_raw,
                job.role,
                job.skill,
                job.seniority_score,
                job.experience,
                job.credentials,
                job.location_score,
                job.recency,
                job.semantic,
                job.penalty,
            ),
        )
        return cursor.rowcount > 0

    async def update_last_seen(self, normalized_key: tuple[str, str]) -> None:
        """Mark a job as re-seen this scrape cycle. Resets ghost-detection counters."""
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE jobs SET last_seen_at = ?, consecutive_misses = 0, "
            "staleness_state = 'active' "
            "WHERE normalized_company = ? AND normalized_title = ?",
            (now, normalized_key[0], normalized_key[1]),
        )
        await self._conn.commit()

    async def update_staleness_state(self, job_id: int, new_state: str) -> None:
        """Persist a single job's staleness_state. Step-1.5 S1.5-B helper.

        No commit here — the caller batches commits (see
        :meth:`mark_missed_for_source`) so a multi-job sweep stays atomic.
        """
        await self._conn.execute(
            "UPDATE jobs SET staleness_state = ? WHERE id = ?",
            (new_state, job_id),
        )

    async def mark_missed_for_source(self, source: str, seen_keys: set[tuple[str, str]]) -> int:
        """Increment consecutive_misses for every job of `source` not in `seen_keys`,
        then recompute `staleness_state` via the ghost-detection state machine.

        Scrape-completeness gates (rolling-average checks) are the CALLER's
        responsibility — only call this after a scrape is deemed healthy, per
        pillar_3_batch_1.md §3 Step 1.

        Step-1.5 S1.5-C: prior to this batch the row's misses counter went up
        but its `staleness_state` never advanced past 'active' — the
        :func:`src.services.ghost_detection.transition` function existed but
        was never called from a write path. Now every missed job is run
        through `transition(misses+1, age_hours_since_last_seen)` and the
        resulting state is persisted. CONFIRMED_EXPIRED is treated as sticky
        (set elsewhere by direct-URL verification) — never demoted here.

        Returns the count of jobs marked missed.
        """
        # Lazy import — pure function, no transitive heavy deps, but the
        # import sits inside ``services`` and we keep ``database.py`` free
        # of services-layer top-level imports.
        from src.services.ghost_detection import StalenessState, transition  # noqa: PLC0415

        cursor = await self._conn.execute(
            "SELECT id, normalized_company, normalized_title, "
            "consecutive_misses, last_seen_at, staleness_state "
            "FROM jobs WHERE source = ?",
            (source,),
        )
        rows = await cursor.fetchall()
        now = datetime.now(timezone.utc)
        missed_count = 0
        for row in rows:
            job_id = row[0]
            key = (row[1], row[2])
            if key in seen_keys:
                continue
            current_state = row[5] or StalenessState.ACTIVE.value
            # Sticky: confirmed_expired never demoted by absence sweep.
            if current_state == StalenessState.CONFIRMED_EXPIRED.value:
                await self._conn.execute(
                    "UPDATE jobs SET consecutive_misses = consecutive_misses + 1 WHERE id = ?",
                    (job_id,),
                )
                missed_count += 1
                continue

            new_misses = int(row[3] or 0) + 1
            last_seen = row[4]
            age_hours = 0.0
            if last_seen:
                try:
                    last_seen_dt = datetime.fromisoformat(last_seen)
                    if last_seen_dt.tzinfo is None:
                        last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
                    age_hours = (now - last_seen_dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    age_hours = 0.0
            next_state = transition(new_misses, age_hours).value
            await self._conn.execute(
                "UPDATE jobs SET consecutive_misses = ?, staleness_state = ? " "WHERE id = ?",
                (new_misses, next_state, job_id),
            )
            missed_count += 1
        await self._conn.commit()
        return missed_count

    async def commit(self):
        """Commit pending changes."""
        if self._conn:
            await self._conn.commit()

    async def count_jobs(self) -> int:
        cursor = await self._conn.execute("SELECT COUNT(*) FROM jobs")
        row = await cursor.fetchone()
        return row[0]

    async def log_run(
        self,
        stats: dict,
        *,
        run_uuid: str | None = None,
        per_source_errors: dict | None = None,
        per_source_duration: dict | None = None,
        total_duration: float | None = None,
        user_id: str | None = None,
    ):
        """Insert a run-log row.

        Extra keyword-only params were added by migration 0010
        (``run_uuid``, ``per_source_errors``, ``per_source_duration``,
        ``total_duration``, ``user_id``). All default to ``None`` so legacy
        callers that pass only ``stats`` continue to work unchanged. Dict
        payloads are JSON-encoded; None is stored as SQL NULL for the text
        columns and as NULL for REAL.
        """
        now = datetime.now(timezone.utc).isoformat()
        errors_json = json.dumps(per_source_errors) if per_source_errors is not None else None
        duration_json = json.dumps(per_source_duration) if per_source_duration is not None else None
        await self._conn.execute(
            "INSERT INTO run_log ("
            " timestamp, total_found, new_jobs, sources_queried, per_source,"
            " run_uuid, per_source_errors, per_source_duration,"
            " total_duration, user_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now,
                stats.get("total_found", 0),
                stats.get("new_jobs", 0),
                stats.get("sources_queried", 0),
                json.dumps(stats.get("per_source", {})),
                run_uuid,
                errors_json,
                duration_json,
                total_duration,
                user_id,
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
        cursor = await self._conn.execute("DELETE FROM jobs WHERE first_seen < ?", (cutoff,))
        await self._conn.commit()
        return cursor.rowcount

    async def get_recent_jobs(self, days: int = 7, min_score: int = 0) -> list[dict]:
        """Return jobs from the last `days` with match_score >= min_score.

        Step-1 B9: filters out rows marked ``staleness_state='expired'`` by
        ghost-detection (Pillar-3 Batch-1). NULL is treated as "not yet
        classified" → still served (defence-in-depth until the staleness
        writer lands in Batch S1.5).
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = await self._conn.execute(
            "SELECT * FROM jobs WHERE first_seen >= ? AND match_score >= ? "
            "AND (staleness_state IS NULL OR staleness_state = 'active') "
            "ORDER BY date_found DESC",
            (cutoff, min_score),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_last_source_counts(self, n: int = 5) -> dict[str, list[int]]:
        """Get per-source job counts from the last N runs for health tracking."""
        cursor = await self._conn.execute("SELECT per_source FROM run_log ORDER BY id DESC LIMIT ?", (n,))
        rows = await cursor.fetchall()
        source_history: dict[str, list[int]] = {}
        for row in rows:
            per_source = json.loads(row[0]) if row[0] else {}
            for name, count in per_source.items():
                source_history.setdefault(name, []).append(count)
        return source_history

    # --- User Actions ---
    #
    # Batch 3.5 Deliverable C: every method now takes user_id and scopes
    # queries by it. Schema UNIQUE(user_id, job_id) is from migration
    # 0002_multi_tenant; this layer is the matching read/write surface.

    async def insert_action(self, job_id: int, action: str, user_id: str, notes: str = "") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT INTO user_actions (user_id, job_id, action, notes, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, job_id)
               DO UPDATE SET action = excluded.action,
                             notes = excluded.notes,
                             created_at = excluded.created_at""",
            (user_id, job_id, action, notes, now),
        )
        await self._conn.commit()
        return {"job_id": job_id, "action": action, "notes": notes, "created_at": now}

    async def delete_action(self, job_id: int, user_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM user_actions WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        )
        await self._conn.commit()

    async def get_actions(self, user_id: str) -> list[dict]:
        cursor = await self._conn.execute(
            """SELECT job_id, action, notes, created_at
               FROM user_actions
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )
        return [{"job_id": r[0], "action": r[1], "notes": r[2], "created_at": r[3]} for r in await cursor.fetchall()]

    async def get_action_counts(self, user_id: str) -> dict[str, int]:
        cursor = await self._conn.execute(
            """SELECT action, COUNT(*) FROM user_actions
               WHERE user_id = ? GROUP BY action""",
            (user_id,),
        )
        return {r[0]: r[1] for r in await cursor.fetchall()}

    async def get_action_for_job(self, job_id: int, user_id: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT action FROM user_actions WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    # --- Applications (Pipeline) ---
    #
    # Batch 3.5 Deliverable C: same user_id-scoping treatment as actions.

    async def create_application(self, job_id: int, user_id: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT OR IGNORE INTO applications
               (user_id, job_id, stage, created_at, updated_at)
               VALUES (?, ?, 'applied', ?, ?)""",
            (user_id, job_id, now, now),
        )
        await self._conn.commit()
        return await self._get_application(job_id, user_id)

    async def advance_application(self, job_id: int, stage: str, user_id: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        # Fetch current stage before updating, to record it in history.
        cursor = await self._conn.execute(
            "SELECT stage FROM applications WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        )
        row = await cursor.fetchone()
        from_stage = row[0] if row else None
        await self._conn.execute(
            """UPDATE applications SET stage = ?, updated_at = ?, last_advanced_at = ?
               WHERE user_id = ? AND job_id = ?""",
            (stage, now, now, user_id, job_id),
        )
        # Insert history entry (Step-3 B-06). Gracefully skips if the
        # application_stage_history table doesn't exist yet (e.g. migration
        # 0014 hasn't run) so existing tests remain green.
        try:
            await self._conn.execute(
                """INSERT INTO application_stage_history
                   (job_id, user_id, from_stage, to_stage, transitioned_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (job_id, user_id, from_stage, stage, now),
            )
        except Exception:  # noqa: BLE001, S110
            # Table not yet created (migration 0014 not run) — tolerate gracefully.
            # Logging is omitted intentionally: this is a known transient state
            # during init_db()-only flows (tests) before the runner applies the DDL.
            pass  # noqa: S110
        await self._conn.commit()
        return await self._get_application(job_id, user_id)

    async def _get_application(self, job_id: int, user_id: str) -> dict:
        cursor = await self._conn.execute(
            """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                      j.title, j.company
               FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
               WHERE a.user_id = ? AND a.job_id = ?""",
            (user_id, job_id),
        )
        row = await cursor.fetchone()
        if not row:
            return {}
        return {
            "job_id": row[0],
            "stage": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "notes": row[4] or "",
            "title": row[5] or "",
            "company": row[6] or "",
        }

    async def get_applications(self, user_id: str, stage: str | None = None) -> list[dict]:
        if stage:
            cursor = await self._conn.execute(
                """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                          j.title, j.company
                   FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
                   WHERE a.user_id = ? AND a.stage = ?
                   ORDER BY a.updated_at DESC""",
                (user_id, stage),
            )
        else:
            cursor = await self._conn.execute(
                """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                          j.title, j.company
                   FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
                   WHERE a.user_id = ?
                   ORDER BY a.updated_at DESC""",
                (user_id,),
            )
        return [
            {
                "job_id": r[0],
                "stage": r[1],
                "created_at": r[2],
                "updated_at": r[3],
                "notes": r[4] or "",
                "title": r[5] or "",
                "company": r[6] or "",
            }
            for r in await cursor.fetchall()
        ]

    async def get_application_counts(self, user_id: str) -> dict[str, int]:
        cursor = await self._conn.execute(
            """SELECT stage, COUNT(*) FROM applications
               WHERE user_id = ? GROUP BY stage""",
            (user_id,),
        )
        return {r[0]: r[1] for r in await cursor.fetchall()}

    async def get_stale_applications(self, user_id: str, days: int = 7) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = await self._conn.execute(
            """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                      j.title, j.company
               FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
               WHERE a.user_id = ?
                 AND a.updated_at < ?
                 AND a.stage NOT IN ('offer', 'rejected')
               ORDER BY a.updated_at ASC""",
            (user_id, cutoff),
        )
        return [
            {
                "job_id": r[0],
                "stage": r[1],
                "created_at": r[2],
                "updated_at": r[3],
                "notes": r[4] or "",
                "title": r[5] or "",
                "company": r[6] or "",
            }
            for r in await cursor.fetchall()
        ]

    async def get_job_by_id(self, job_id: int) -> dict | None:
        cursor = await self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))

    # ------------------------------------------------------------------
    # Step-1 B6 — JOIN-once enrichment prefetch.
    #
    # The jobs API surfaces a 13-field enrichment slice on every JobResponse
    # (see src/api/models.py::JobResponse + src/api/routes/jobs.py).
    # Issuing one SELECT per job to load enrichment would N+1-explode on a
    # 100-job list. Instead the route reads jobs LEFT JOIN job_enrichment
    # in a single query — this method encapsulates the column aliasing
    # (every job_enrichment column is prefixed `enr_` to avoid collisions
    # with `experience_level` and `salary` on the jobs side).
    #
    # The `job_enrichment` table is shared catalog (rule #10) — no user_id
    # filter. Per-user state (actions / pipeline) is looked up separately
    # in the route, not joined here.
    # ------------------------------------------------------------------

    _JOBS_ENRICHMENT_JOIN_COLS = (
        "j.*, "
        "je.title_canonical AS enr_title_canonical, "
        "je.category AS enr_category, "
        "je.employment_type AS enr_employment_type, "
        "je.workplace_type AS enr_workplace_type, "
        "je.salary AS enr_salary, "
        "je.required_skills AS enr_required_skills, "
        "je.preferred_skills AS enr_preferred_skills, "
        "je.experience_min_years AS enr_experience_min_years, "
        "je.experience_level AS enr_experience_level, "
        "je.visa_sponsorship AS enr_visa_sponsorship, "
        "je.seniority AS enr_seniority"
    )

    async def get_recent_jobs_with_enrichment(self, days: int = 7, min_score: int = 0) -> list[dict]:
        """Same as :meth:`get_recent_jobs` plus a LEFT JOIN to job_enrichment.

        Returns one row per job; enrichment columns appear with the ``enr_``
        prefix and are ``None`` when no enrichment row exists. Falls back
        to the bare ``SELECT * FROM jobs`` if the enrichment table is
        missing (fresh test DB without migration 0008 — the jobs route
        must keep working). Mirrors the Step-1 B9 staleness filter on
        :meth:`get_recent_jobs` so JobResponse doesn't surface jobs that
        ghost-detection has marked ``expired``.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        # _JOBS_ENRICHMENT_JOIN_COLS is a class constant, not user input — S608 is a false positive here.
        sql = (
            f"SELECT {self._JOBS_ENRICHMENT_JOIN_COLS} "  # noqa: S608
            "FROM jobs j "
            "LEFT JOIN job_enrichment je ON je.job_id = j.id "
            "WHERE j.first_seen >= ? AND j.match_score >= ? "
            "AND (j.staleness_state IS NULL OR j.staleness_state = 'active') "
            "ORDER BY j.date_found DESC"
        )
        try:
            cursor = await self._conn.execute(sql, (cutoff, min_score))
        except aiosqlite.OperationalError:
            return await self.get_recent_jobs(days=days, min_score=min_score)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_job_by_id_with_enrichment(self, job_id: int) -> dict | None:
        """Same as :meth:`get_job_by_id` plus a LEFT JOIN to job_enrichment.

        C-1 fix: mirrors the staleness filter from
        :meth:`get_recent_jobs_with_enrichment` so a single-job lookup
        cannot surface a ghost-detected expired posting that the list
        path correctly hides.
        """
        # _JOBS_ENRICHMENT_JOIN_COLS is a class constant, not user input — S608 is a false positive here.
        sql = (
            f"SELECT {self._JOBS_ENRICHMENT_JOIN_COLS} "  # noqa: S608
            "FROM jobs j "
            "LEFT JOIN job_enrichment je ON je.job_id = j.id "
            "WHERE j.id = ? "
            "AND (j.staleness_state IS NULL OR j.staleness_state = 'active')"
        )
        try:
            cursor = await self._conn.execute(sql, (job_id,))
        except aiosqlite.OperationalError:
            # Fallback for fresh DBs without migration 0008 — still apply
            # the staleness filter so the read path stays consistent.
            cursor = await self._conn.execute(
                "SELECT * FROM jobs WHERE id = ? " "AND (staleness_state IS NULL OR staleness_state = 'active')",
                (job_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Step-1.5 S3-D — notification_ledger reader.
    #
    # ``notification_ledger`` was created by migration 0004 as the per-
    # channel idempotency + retry audit table; until Step 1.5 there was
    # no SELECT-based reader for it. The new GET /notifications endpoint
    # consumes the two helpers below. Both scope by user_id (CLAUDE.md
    # rule #12). Optional ``channel`` / ``status`` filters short-circuit
    # to the user-only WHERE when None.
    # ------------------------------------------------------------------

    async def get_notification_ledger(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        channel: str | None = None,
        status: str | None = None,
        job_id: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[dict]:
        """Return a paginated slice of the user's notification ledger,
        newest first. Empty list when the table is missing (legacy DB
        without migration 0004) — matches the graceful-degrade pattern
        already used in :meth:`get_recent_jobs_with_enrichment`.

        Step-3 O-01: added ``job_id``, ``start_time``, ``end_time`` filters.
        """
        sql = (
            "SELECT id, job_id, channel, status, sent_at, error_message, "
            "retry_count, created_at "
            "FROM notification_ledger "
            "WHERE user_id = ?"
        )
        params: list = [user_id]
        if channel:
            sql += " AND channel = ?"
            params.append(channel)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if job_id is not None:
            sql += " AND job_id = ?"
            params.append(job_id)
        if start_time:
            sql += " AND created_at >= ?"
            params.append(start_time)
        if end_time:
            sql += " AND created_at <= ?"
            params.append(end_time)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            cursor = await self._conn.execute(sql, tuple(params))
        except aiosqlite.OperationalError:
            return []
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def count_notification_ledger(
        self,
        user_id: str,
        channel: str | None = None,
        status: str | None = None,
        job_id: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> int:
        """Return the total count for the same WHERE-clause as
        :meth:`get_notification_ledger`. Used to compute pagination
        ``total`` in NotificationLedgerListResponse.

        Step-3 O-01: added ``job_id``, ``start_time``, ``end_time`` filters.
        """
        sql = "SELECT COUNT(*) FROM notification_ledger WHERE user_id = ?"
        params: list = [user_id]
        if channel:
            sql += " AND channel = ?"
            params.append(channel)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if job_id is not None:
            sql += " AND job_id = ?"
            params.append(job_id)
        if start_time:
            sql += " AND created_at >= ?"
            params.append(start_time)
        if end_time:
            sql += " AND created_at <= ?"
            params.append(end_time)
        try:
            cursor = await self._conn.execute(sql, tuple(params))
        except aiosqlite.OperationalError:
            return 0
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # ── Account management (Step-3 B-11..13) ─────────────────────────────────────

    async def soft_delete_user(self, user_id: str) -> None:
        """Set deleted_at to now — auth middleware rejects soft-deleted users."""
        from datetime import datetime, timezone  # noqa: PLC0415

        await self._conn.execute(
            "UPDATE users SET deleted_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        await self._conn.commit()

    async def update_user_password(self, user_id: str, new_hash: str) -> None:
        """Replace the stored password hash for the given user."""
        await self._conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, user_id),
        )
        await self._conn.commit()

    async def update_user_email(self, user_id: str, new_email: str) -> None:
        """Replace the email address for the given user."""
        await self._conn.execute(
            "UPDATE users SET email = ? WHERE id = ?",
            (new_email, user_id),
        )
        await self._conn.commit()

    # ── Application timeline (Step-3 B-07) ───────────────────────────────────────
    async def get_application_timeline(self, job_id: int, user_id: str) -> list[dict]:
        """Return stage history for a job+user, ordered by transitioned_at ASC."""
        cursor = await self._conn.execute(
            "SELECT * FROM application_stage_history WHERE job_id = ? AND user_id = ? ORDER BY transitioned_at ASC",
            (job_id, user_id),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Application notes update (Step-3 B-08) ───────────────────────────────────
    async def update_application_notes(self, job_id: int, user_id: str, new_notes: str) -> dict | None:
        """Append current notes to notes_history, set notes = new_notes."""
        import json
        from datetime import datetime, timezone

        # Fetch current notes
        cursor = await self._conn.execute(
            "SELECT notes, notes_history FROM applications WHERE job_id = ? AND user_id = ?",
            (job_id, user_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        current_notes = row[0] or ""
        history = json.loads(row[1] or "[]") if row[1] else []
        if current_notes:  # only append if there's something to archive
            history.append({"note": current_notes, "timestamp": datetime.now(timezone.utc).isoformat()})
        await self._conn.execute(
            "UPDATE applications SET notes = ?, notes_history = ?, updated_at = ? WHERE job_id = ? AND user_id = ?",
            (new_notes, json.dumps(history), datetime.now(timezone.utc).isoformat(), job_id, user_id),
        )
        await self._conn.commit()
        # Return updated row
        cursor = await self._conn.execute(
            "SELECT a.*, j.title, j.company "
            "FROM applications a LEFT JOIN jobs j ON a.job_id = j.id "
            "WHERE a.job_id = ? AND a.user_id = ?",
            (job_id, user_id),
        )
        updated = await cursor.fetchone()
        return dict(updated) if updated else None

    # ── Notification rules ───────────────────────────────────────────────────────

    async def get_notification_rules(self, user_id: str) -> list[dict]:
        """Return all notification rules for a user, ordered by channel."""
        try:
            cursor = await self._conn.execute(
                "SELECT * FROM notification_rules WHERE user_id = ? ORDER BY channel",
                (user_id,),
            )
        except aiosqlite.OperationalError:
            return []
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_notification_rule(self, rule_id: int, user_id: str) -> dict | None:
        """Return a single rule by id, scoped by user_id (IDOR guard)."""
        try:
            cursor = await self._conn.execute(
                "SELECT * FROM notification_rules WHERE id = ? AND user_id = ?",
                (rule_id, user_id),
            )
        except aiosqlite.OperationalError:
            return None
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def upsert_notification_rule(self, user_id: str, data: dict) -> dict:
        """INSERT OR REPLACE a notification rule for (user_id, channel).

        Returns the full row including auto-assigned id, created_at, updated_at.
        The UNIQUE(user_id, channel) constraint means a second POST for the same
        channel replaces the existing rule (upsert semantics).
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        channel = data["channel"]
        score_threshold = data.get("score_threshold", 60)
        notify_mode = data.get("notify_mode", "instant")
        quiet_hours_start = data.get("quiet_hours_start")
        quiet_hours_end = data.get("quiet_hours_end")
        digest_send_time = data.get("digest_send_time", "08:00")
        enabled = int(data.get("enabled", True))

        await self._conn.execute(
            """
            INSERT INTO notification_rules
                (user_id, channel, score_threshold, notify_mode,
                 quiet_hours_start, quiet_hours_end, digest_send_time, enabled,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, channel) DO UPDATE SET
                score_threshold  = excluded.score_threshold,
                notify_mode      = excluded.notify_mode,
                quiet_hours_start = excluded.quiet_hours_start,
                quiet_hours_end  = excluded.quiet_hours_end,
                digest_send_time = excluded.digest_send_time,
                enabled          = excluded.enabled,
                updated_at       = excluded.updated_at
            """,
            (
                user_id,
                channel,
                score_threshold,
                notify_mode,
                quiet_hours_start,
                quiet_hours_end,
                digest_send_time,
                enabled,
                now,
                now,
            ),
        )
        await self._conn.commit()
        cursor = await self._conn.execute(
            "SELECT * FROM notification_rules WHERE user_id = ? AND channel = ?",
            (user_id, channel),
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}

    async def update_notification_rule(self, rule_id: int, user_id: str, data: dict) -> dict | None:
        """Partial UPDATE a notification rule. Only updates fields present in data.

        Scoped by (id, user_id) — 404 semantics when not found or not owned.
        Returns the updated row, or None if the rule was not found/owned.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        allowed_fields = {
            "score_threshold",
            "notify_mode",
            "quiet_hours_start",
            "quiet_hours_end",
            "digest_send_time",
            "enabled",
        }
        updates = {k: v for k, v in data.items() if k in allowed_fields and v is not None}
        if "enabled" in data and data["enabled"] is not None:
            updates["enabled"] = int(data["enabled"])
        if not updates:
            # Nothing to change — just return the current row.
            return await self.get_notification_rule(rule_id, user_id)
        set_clause = ", ".join(f"{col} = ?" for col in updates)
        params = list(updates.values()) + [now, rule_id, user_id]
        try:
            cursor = await self._conn.execute(
                f"UPDATE notification_rules SET {set_clause}, updated_at = ? "  # noqa: S608
                "WHERE id = ? AND user_id = ?",
                params,
            )
        except aiosqlite.OperationalError:
            return None
        await self._conn.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get_notification_rule(rule_id, user_id)

    async def delete_notification_rule(self, rule_id: int, user_id: str) -> bool:
        """DELETE a notification rule scoped by (id, user_id). Returns True if deleted."""
        try:
            cursor = await self._conn.execute(
                "DELETE FROM notification_rules WHERE id = ? AND user_id = ?",
                (rule_id, user_id),
            )
        except aiosqlite.OperationalError:
            return False
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_notification_rule_for_channel(self, user_id: str, channel: str) -> dict | None:
        """Return the rule row for a specific (user_id, channel) pair, or None."""
        try:
            cursor = await self._conn.execute(
                "SELECT * FROM notification_rules WHERE user_id = ? AND channel = ?",
                (user_id, channel),
            )
        except aiosqlite.OperationalError:
            return None
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def queue_digest_notification(self, user_id: str, channel: str, job_id: int) -> None:
        """Enqueue a job for the user's digest on the given channel.

        Idempotent — duplicate (user_id, channel, job_id) rows are allowed
        because digests may be queued multiple times before send; dedup happens
        in the digest sender via the sent=0 filter.
        """
        try:
            await self._conn.execute(
                "INSERT INTO user_notification_digests(user_id, channel, job_id) VALUES(?, ?, ?)",
                (user_id, channel, job_id),
            )
            await self._conn.commit()
        except aiosqlite.OperationalError:
            pass  # Table missing on legacy DB — graceful no-op.

    async def get_pending_digests(self, user_id: str, channel: str) -> list[dict]:
        """Return all un-sent digest rows for (user_id, channel)."""
        try:
            cursor = await self._conn.execute(
                "SELECT * FROM user_notification_digests " "WHERE user_id = ? AND channel = ? AND sent = 0",
                (user_id, channel),
            )
        except aiosqlite.OperationalError:
            return []
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_digests_sent(self, user_id: str, channel: str) -> int:
        """Flip sent=1 on all pending digest rows for (user_id, channel).

        Returns the count of rows updated.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            cursor = await self._conn.execute(
                "UPDATE user_notification_digests "
                "SET sent = 1, sent_at = ? "
                "WHERE user_id = ? AND channel = ? AND sent = 0",
                (now, user_id, channel),
            )
        except aiosqlite.OperationalError:
            return 0
        await self._conn.commit()
        return cursor.rowcount

    # ── Notification ledger stats ────────────────────────────────────────────

    async def get_notification_ledger_stats(self, user_id: str) -> dict[str, dict[str, int]]:
        """Aggregate notification_ledger by channel + status for the caller.

        Returns ``{channel: {sent: N, failed: M, queued: P, ...}}``.
        Missing table on legacy DB returns an empty dict — same graceful-degrade
        pattern as the rest of the notification_ledger surface.
        """
        try:
            cursor = await self._conn.execute(
                "SELECT channel, status, COUNT(*) as cnt "
                "FROM notification_ledger "
                "WHERE user_id = ? "
                "GROUP BY channel, status",
                (user_id,),
            )
        except aiosqlite.OperationalError:
            return {}
        rows = await cursor.fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            channel = row[0]
            status = row[1]
            count = int(row[2])
            result.setdefault(channel, {})[status] = count
        return result

    async def close(self):
        if self._conn:
            await self._conn.close()
