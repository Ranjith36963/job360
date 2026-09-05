import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from src.repositories import pg

_VALID_COL_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
# BOOLEAN + JSONB added for migration 0031 (Universal Shelf Step 1):
# salary_is_estimated is a real 3-state nullable bool, and shelf_provenance
# is the one JSON column stored as a native Postgres JSONB rather than the
# JSON-in-TEXT convention every other list/dict column in this file uses —
# see 0032_universal_shelf.up.sql for why.
_VALID_COL_TYPES = {"TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC", "BOOLEAN", "JSONB"}

from src.core.settings import USER_BROUGHT_SOURCE  # noqa: E402
from src.models import Job  # noqa: E402  # after the regex constants to avoid circular import
from src.utils.logger import get_logger  # noqa: E402

_log = get_logger("db.repo")  # job360.db.repo → data/logs/

# Child tables purged alongside a `jobs` row (docs/fable/02 D4 — orphan cleanup).
# The pg shim strips EVERY foreign-key clause, including ON DELETE CASCADE, so the
# DB will never cascade for us; purge_old_jobs must delete these explicitly.
#
# ONLY catalog-DERIVED rows belong here — data that is meaningless once the job is
# gone and that the pipeline can regenerate. `user_feed` dominates the bloat: one
# row per user per job.
#
# Deliberately NOT purged (rule #3 — purging them would be real data loss):
#   applications, application_stage_history, tailored_documents, tailored_usage,
#   user_actions, notification_ledger
# Those are the USER's own records and audit trail. A user's Kanban entry or
# tailored CV must survive the shared catalog aging out, so they may keep an
# orphan job_id by design.
_PURGE_CASCADE_TABLES = (
    "job_enrichment",
    "job_embeddings",
    "user_feed",
    "user_notification_digests",
)


class JobDatabase:
    def __init__(self, db_path: str):
        self._path = db_path
        self._conn: pg.Connection | None = None

    @classmethod
    def from_connection(cls, db_path: str, conn: "pg.Connection") -> "JobDatabase":
        """Build a JobDatabase around an ALREADY-OPEN connection.

        Used by the pooled request path: a connection is borrowed from the pool
        and handed here, so the instance never opens (or closes) one of its own —
        the pool owns the connection's lifecycle. Callers must NOT call
        ``close()`` on the result; returning it to the pool is the caller's job.
        """
        db = cls(db_path)
        db._conn = conn
        return db

    @property
    def _db(self) -> "pg.Connection":
        """The live connection, or a clear error if there isn't one.

        ``_conn`` is legitimately ``Optional`` — it is None before ``connect()``
        and after ``close()`` — but all ~119 query methods used it directly, so
        every one of them was an unguarded ``None`` dereference to the type
        checker (and to production: a missed ``connect()`` surfaced as
        ``AttributeError: 'NoneType' object has no attribute 'execute'``, which
        says nothing about the real cause).

        Going through this property narrows the type in one place instead of 119
        and turns that mystery AttributeError into a message that names the bug.
        Assignment sites, the declaration, and the ``if self._conn:`` guards
        still use ``_conn`` directly — only attribute ACCESS goes through here.
        """
        if self._conn is None:
            raise RuntimeError(
                "JobDatabase has no open connection — call await connect() "
                "(or init_db()) before querying, and don't use it after close()."
            )
        return self._conn

    async def connect(self) -> None:
        """Open a connection WITHOUT running the schema DDL (docs/fable/02).

        The schema + migrations are created once at boot by ``init_db()``. Per-request
        instances just need a live connection, so this skips the (heavy, redundant)
        CREATE TABLE executescript. Used by the per-request ``get_db()`` dependency so
        each request has its OWN connection — psycopg3 forbids sharing one async
        connection across concurrent coroutines, and a fresh connection also self-heals
        after a DB restart (the old single shared connection did neither).
        """
        self._conn = await pg.connect(self._path)
        self._conn.row_factory = pg.Row

    async def init_db(self) -> None:
        # Postgres connection (schema selected from self._path in test mode;
        # always ``public`` in production). No PRAGMAs / WAL / busy_timeout —
        # Postgres handles concurrency natively (no "database is locked").
        self._conn = await pg.connect(self._path)
        self._conn.row_factory = pg.Row
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
                deadline TEXT,
                deadline_source TEXT,
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

    async def _migrate(self) -> None:
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
            # Migration 0020 — application deadline columns.
            ("deadline", "TEXT"),
            ("deadline_source", "TEXT"),
            # Migration 0029 — description-backfill retry counter. Real state
            # (not a padded description) so a thin job stops being re-fetched
            # after MAX_BACKFILL_ATTEMPTS without faking coverage.py's
            # skill-text signal (see 0029's up.sql for the full incident).
            ("description_backfill_attempts", "INTEGER DEFAULT 0"),
            # Migration 0031 — Universal Shelf Step 1 (docs/pillars/
            # UNIVERSAL_SHELF.md §1/§6). Mirrored here so init_db()-only test
            # DBs (that never run the external migration runner) still get
            # the full schema — see that migration's up.sql for the full
            # rationale on each column and why shelf_provenance is JSONB
            # while every other new column here is TEXT.
            ("employment_type", "TEXT"),
            ("workplace_mode", "TEXT"),
            ("seniority", "TEXT"),
            ("category", "TEXT"),
            ("source_tags", "TEXT DEFAULT '[]'"),
            ("visa_status", "TEXT"),
            ("salary_currency", "TEXT"),
            ("salary_period", "TEXT"),
            ("salary_is_estimated", "BOOLEAN"),
            ("salary_min_gbp_annual", "REAL"),
            ("salary_max_gbp_annual", "REAL"),
            ("shelf_provenance", "JSONB NOT NULL DEFAULT '{}'"),
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
            ("matcher_stats", "TEXT DEFAULT '{}'"),  # backlog #9 — LLM judge telemetry
            # Migration 0032 — JOB SOURCE ENRICHMENT spend counter. Same shape
            # as matcher_stats: a whole-blob JSON payload written by
            # services/shelf_enrichment.py so "what did last night cost?" has
            # an answer at all (docs/pillars/UNIVERSAL_SHELF.md §7).
            ("enrichment_stats", "TEXT DEFAULT '{}'"),
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
        await self._db.executescript("""
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

        # Ensure notification_rules + user_notification_digests exist (migration 0012 / 0013).
        # Mirrors the forward direction of the SQL migration files so tests that
        # call init_db() directly (without the external runner) see the full schema.
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS notification_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                score_threshold INTEGER NOT NULL DEFAULT 60,
                notify_mode TEXT NOT NULL DEFAULT 'instant'
                    CHECK (notify_mode IN ('instant', 'daily', 'every_n_hours')),
                interval_hours INTEGER NOT NULL DEFAULT 6,
                daily_send_time TEXT NOT NULL DEFAULT '08:00',
                quiet_hours_start TEXT,
                quiet_hours_end TEXT,
                last_sent_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE(user_id)
            );
            CREATE TABLE IF NOT EXISTS user_notification_digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                queued_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                sent INTEGER NOT NULL DEFAULT 0,
                sent_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_digests_user_channel_pending
                ON user_notification_digests(user_id, channel, sent);
        """)

        # Ensure tailored-document tables exist (migration 0023 — Per-User AI CV &
        # Cover Letter). Mirrors 0023_tailored_documents.up.sql so init_db()-only
        # tests see the full schema. tailoring_patterns has NO user_id/content by
        # design (universal layer = patterns only, spec §7 privacy).
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tailored_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                doc_kind TEXT NOT NULL,
                ai_draft TEXT NOT NULL DEFAULT '',
                polished TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                model TEXT,
                profile_version INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                kept_at TEXT,
                flagged_terms TEXT DEFAULT '[]',
                UNIQUE(user_id, job_id, doc_kind)
            );
            CREATE INDEX IF NOT EXISTS idx_tailored_user_kind_status
                ON tailored_documents(user_id, doc_kind, status);
            CREATE INDEX IF NOT EXISTS idx_tailored_user_job
                ON tailored_documents(user_id, job_id);
            CREATE TABLE IF NOT EXISTS tailored_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_tailored_usage_user_time
                ON tailored_usage(user_id, created_at);
            CREATE TABLE IF NOT EXISTS tailoring_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_kind TEXT NOT NULL,
                features TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_tailoring_patterns_kind
                ON tailoring_patterns(doc_kind);
        """)
        # flagged_terms (migration 0024) — possible-fabrication warnings surfaced to
        # the user. Added here too so DBs created before 0024 pick it up on boot.
        await self._add_missing_columns(
            "tailored_documents", [("flagged_terms", "TEXT DEFAULT '[]'")]
        )

        # Add timezone column to users table if it was created before migration 0012.
        # users table may not exist in all test DB instances (auth tests create it;
        # non-auth tests skip it).  Guard with table existence check.
        cursor = await self._db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if await cursor.fetchone():
            await self._add_missing_columns("users", [("timezone", "TEXT NOT NULL DEFAULT 'UTC'")])

        # Migration 0030 — snapshot_id on user_profile_versions (see that
        # migration's up.sql for the full rationale). Like the table itself,
        # user_profile_versions is created ONLY by the external migration
        # runner (never by the CREATE TABLE block above), so this mirror only
        # needs the column, guarded by the same table-existence check used
        # for users.timezone above — a DB that has the table via the runner
        # but predates 0030 still gets the column on the next init_db() boot.
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_profile_versions'"
        )
        if await cursor.fetchone():
            await self._add_missing_columns("user_profile_versions", [("snapshot_id", "TEXT")])

        await self._db.commit()

    async def _add_missing_columns(self, table: str, migrations: list[tuple[str, str]]) -> None:
        """Apply `ALTER TABLE ... ADD COLUMN` for each entry not yet present."""
        if not _VALID_COL_NAME.match(table):
            raise ValueError(f"Invalid migration table name: {table}")
        cursor = await self._db.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        for col_name, col_def in migrations:
            if col_name in existing:
                continue
            if not _VALID_COL_NAME.match(col_name):
                raise ValueError(f"Invalid migration column name: {col_name}")
            col_type_word = col_def.split()[0].upper()
            if col_type_word not in _VALID_COL_TYPES:
                raise ValueError(f"Invalid migration column type: {col_type_word}")
            await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

    async def get_tables(self) -> list[str]:
        cursor = await self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def is_job_seen(self, normalized_key: tuple[str, str]) -> bool:
        company, title = normalized_key
        cursor = await self._db.execute(
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
        cursor = await self._db.execute(
            """INSERT OR IGNORE INTO jobs
            (title, company, location, salary_min, salary_max, description,
             apply_url, source, date_found, match_score, visa_flag,
             experience_level, normalized_company, normalized_title, first_seen,
             posted_at, first_seen_at, last_seen_at, date_confidence,
             date_posted_raw,
             role, skill, seniority_score, experience, credentials,
             location_score, recency, semantic, penalty,
             deadline, deadline_source,
             employment_type, workplace_mode, seniority, category, source_tags,
             visa_status, salary_currency, salary_period, salary_is_estimated,
             salary_min_gbp_annual, salary_max_gbp_annual,
             shelf_provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                job.deadline,
                job.deadline_source,
                # Universal Shelf (migration 0031). salary_min_gbp_annual /
                # salary_max_gbp_annual are DERIVED by
                # services/shelf_gate.py::_fill_salary — annualised and
                # converted to GBP from the source's own unit sidecars. They
                # stay NULL when the gate could not honestly convert (no
                # amount, or a currency core/fx cannot price), which is the
                # correct rule #29 state for a value nobody could derive.
                job.employment_type,
                job.workplace_mode,
                job.seniority,
                job.category,
                json.dumps(job.source_tags or []),
                job.visa_status,
                job.salary_currency,
                job.salary_period,
                job.salary_is_estimated,
                job.salary_min_gbp_annual,
                job.salary_max_gbp_annual,
                json.dumps(job.shelf_provenance or {}),
            ),
        )
        inserted = cursor.rowcount > 0
        # DESCRIPTION UPGRADE ON RE-FETCH — the ingest half of the text
        # recovery work (docs/pillars/UNIVERSAL_SHELF.md §2 DESCRIPTION).
        #
        # `INSERT OR IGNORE` means a job we already hold is never updated. The
        # first version of this guard (2026-08-06) only filled a description
        # that was EMPTY, which fixed the 0%-description sources but left the
        # much larger problem untouched: a TEASER is not empty. Reed's list
        # endpoint ships a 453-char teaser and its detail endpoint the full
        # ~4,700-char ad; himalayas ships a 187-char `excerpt` beside a 7,299-
        # char `description`. Under empty-only, the recovered full text could
        # NEVER replace the teaser on the 10,579 jobs already stored — the
        # recovery would only ever have helped brand-new postings, and every
        # existing row would have had to age out through the 30-day purge.
        #
        # So: a MATERIALLY LONGER description replaces a shorter one. Both
        # halves of the threshold are load-bearing:
        #   * `+200 chars` — the same floor `shelf_gate.is_stub_description`
        #     uses for "too thin to be a real ad". It stops a re-fetch that
        #     merely gained a cookie banner or a trailing "Apply now" from
        #     rewriting the row.
        #   * `>= 1.2x` — a proportional win, so on an ad that is already
        #     4,000 chars a 200-char footer is not enough; it needs to be a
        #     real upgrade.
        # Together they also stop THRASH: once the long version is stored, the
        # short one can never satisfy either half, so alternating runs (detail
        # fetch inside its budget vs. spent) settle instead of flip-flopping.
        # SHRINKING IS STILL IMPOSSIBLE — an upstream that drops its text can
        # never wipe what we hold.
        if not inserted and job.description:
            new_len = len(job.description)
            await self._db.execute(
                """UPDATE jobs SET description = ?
                   WHERE normalized_company = ? AND normalized_title = ?
                     AND (description IS NULL OR description = ''
                          OR (? >= LENGTH(description) + 200
                              AND ? * 5 >= LENGTH(description) * 6))""",
                (job.description, company, title, new_len, new_len),
            )
        return inserted

    async def update_job_scores(self, job: Job) -> None:
        """Persist a re-scored job's match_score + dim columns to the catalog.

        Used after enrichment, when a job is re-scored with its DB ``id`` set so
        the enrichment dims (seniority/salary/visa/workplace, folded into
        match_score) actually land on the stored row. No-op without ``job.id``.
        """
        job_id = getattr(job, "id", None)
        if job_id is None:
            return
        await self._db.execute(
            """UPDATE jobs SET
                   match_score = ?, role = ?, skill = ?, seniority_score = ?,
                   experience = ?, credentials = ?, location_score = ?,
                   recency = ?, semantic = ?, penalty = ?, visa_flag = ?
               WHERE id = ?""",
            (
                job.match_score,
                job.role,
                job.skill,
                job.seniority_score,
                job.experience,
                job.credentials,
                job.location_score,
                job.recency,
                job.semantic,
                job.penalty,
                int(job.visa_flag),
                job_id,
            ),
        )

    async def update_last_seen(self, normalized_key: tuple[str, str]) -> None:
        """Mark a job as re-seen this scrape cycle. Resets ghost-detection counters."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE jobs SET last_seen_at = ?, consecutive_misses = 0, "
            "staleness_state = 'active' "
            "WHERE normalized_company = ? AND normalized_title = ?",
            (now, normalized_key[0], normalized_key[1]),
        )
        await self._db.commit()

    async def update_staleness_state(self, job_id: int, new_state: str) -> None:
        """Persist a single job's staleness_state. Step-1.5 S1.5-B helper.

        No commit here — the caller batches commits (see
        :meth:`mark_missed_for_source`) so a multi-job sweep stays atomic.
        """
        await self._db.execute(
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

        cursor = await self._db.execute(
            "SELECT id, normalized_company, normalized_title, "
            "consecutive_misses, last_seen_at, staleness_state, first_seen_at "
            "FROM jobs WHERE source = ?",
            (source,),
        )
        rows = await cursor.fetchall()
        now = datetime.now(timezone.utc)
        missed_count = 0
        # M7 — wrap the whole sweep in ONE transaction. Under autocommit each
        # UPDATE committed on its own, so a crash (or a connection drop) part-way
        # through left the source half-swept: some jobs with an incremented
        # consecutive_misses and a new staleness_state, the rest untouched. The
        # next run would then re-increment only the survivors, drifting the two
        # halves apart. A ghost sweep is one logical decision about one source;
        # it should land completely or not at all.
        async with self._db.transaction():
            for row in rows:
                job_id = row[0]
                key = (row[1], row[2])
                if key in seen_keys:
                    continue
                current_state = row[5] or StalenessState.ACTIVE.value
                # Sticky: confirmed_expired never demoted by absence sweep.
                if current_state == StalenessState.CONFIRMED_EXPIRED.value:
                    await self._db.execute(
                        "UPDATE jobs SET consecutive_misses = consecutive_misses + 1 WHERE id = ?",
                        (job_id,),
                    )
                    missed_count += 1
                    continue

                new_misses = int(row[3] or 0) + 1
                # M6 (second path): last_seen_at can be NULL. Pre-fix, age_hours
                # stayed 0.0, so transition(misses, 0.0) could NEVER promote and a
                # repeatedly-missed job stayed ACTIVE forever — the same bug the
                # nightly sweep's evaluate_job_state was fixed for, here in the
                # more-frequent pipeline path. Mirror that fallback: use
                # first_seen_at as the age proxy, and if there is no timestamp at
                # all, let consecutive_misses alone decide.
                last_seen = row[4] or row[6]  # last_seen_at, else first_seen_at
                if not last_seen:
                    if new_misses >= 3:
                        next_state = StalenessState.LIKELY_STALE.value
                    elif new_misses >= 2:
                        next_state = StalenessState.POSSIBLY_STALE.value
                    else:
                        next_state = StalenessState.ACTIVE.value
                else:
                    age_hours = 0.0
                    try:
                        last_seen_dt = datetime.fromisoformat(last_seen)
                        if last_seen_dt.tzinfo is None:
                            last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
                        age_hours = (now - last_seen_dt).total_seconds() / 3600
                    except (ValueError, TypeError):
                        age_hours = 0.0
                    next_state = transition(new_misses, age_hours).value
                await self._db.execute(
                    "UPDATE jobs SET consecutive_misses = ?, staleness_state = ? " "WHERE id = ?",
                    (new_misses, next_state, job_id),
                )
                missed_count += 1
        # (transaction() above commits on exit — no explicit commit here)
        return missed_count

    async def commit(self) -> None:
        """Commit pending changes."""
        if self._conn:
            await self._conn.commit()

    async def count_jobs(self) -> int:
        cursor = await self._db.execute("SELECT COUNT(*) FROM jobs")
        row = await cursor.fetchone()
        return int(row[0])

    async def count_unexpired_jobs_for_source(self, source: str) -> int:
        """How many of ``source``'s jobs are still being served as live.

        Used only for observability: when a source's fetch fails, the absence
        sweep is skipped for it (a failed scrape is not evidence that jobs
        vanished), which means every one of these rows stops ageing and keeps
        being presented as ``active``. Logging the COUNT turns "a source
        failed" into "N live listings stopped ageing", which is the thing a
        user actually feels.
        """
        # THE SAME PREDICATE THE APP SERVES BY, not a looser one.
        # This counted "anything not confirmed_expired", which sweeps in
        # `possibly_stale` and `likely_stale` — rows `get_recent_jobs` does NOT
        # serve (it takes `staleness_state IS NULL OR = 'active'`, database.py
        # :761). So the warning overstated how many LIVE listings had stopped
        # ageing: an instrument built to say what a user actually feels was
        # counting rows no user can see. An instrument must count the way its
        # consumer counts. (CodeRabbit, PR #387.)
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM jobs WHERE source = ? "
            "AND (staleness_state IS NULL OR staleness_state = 'active')",
            (source,),
        )
        row = await cursor.fetchone()
        return int(row[0])

    async def log_run(
        self,
        stats: dict[str, Any],
        *,
        run_uuid: str | None = None,
        per_source_errors: dict[str, Any] | None = None,
        per_source_duration: dict[str, Any] | None = None,
        total_duration: float | None = None,
        user_id: str | None = None,
        matcher_stats: dict[str, Any] | None = None,
    ) -> None:
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
        matcher_json = json.dumps(matcher_stats) if matcher_stats is not None else None
        await self._db.execute(
            "INSERT INTO run_log ("
            " timestamp, total_found, new_jobs, sources_queried, per_source,"
            " run_uuid, per_source_errors, per_source_duration,"
            " total_duration, user_id, matcher_stats"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                matcher_json,
            ),
        )
        await self._db.commit()

    async def get_run_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
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

    async def get_new_jobs_since(self, hours: int = 12) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cursor = await self._db.execute(
            "SELECT * FROM jobs WHERE first_seen >= ? ORDER BY match_score DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def purge_old_jobs(self, days: int = 30) -> int:
        """Delete jobs not seen in the last `days`. Returns count deleted.

        Keys on LIVENESS (last_seen_at), not ingestion (first_seen) — docs/fable/02:
        a posting still live after 30 days should be kept, not deleted-then-re-inserted
        (which reset its score + re-notified). COALESCE falls back to first_seen for
        legacy rows whose last_seen_at is NULL, so nothing accumulates un-purgeable.

        Also deletes the catalog-derived child rows (docs/fable/02 D4). The pg shim
        strips EVERY foreign-key clause — including ``ON DELETE CASCADE`` — so there
        is no DB-level cascade; without this, every purge orphaned rows forever.
        ``user_feed`` is the big one: one row per user per job.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        # R2 amendment (spec 2026-09-04-application-spine, hard rule #3): a
        # `user_brought` row is never a scrape aging out — the user is
        # tracking it, and the application snapshot needs the live catalog
        # row for the detail page. Both the direct DELETE below AND this
        # `stale` subquery (which feeds the cascade-child DELETEs) exclude
        # it, so neither the job row nor its user_feed/enrichment children
        # are purged out from under a brought application.
        stale = (
            "SELECT id FROM jobs WHERE COALESCE(last_seen_at, first_seen) < ? "
            "AND source <> ?"
        )
        # Children first (the subquery needs the jobs rows to still exist).
        #
        # Skip a child table that does not exist IN THIS SCHEMA. EVERY table in
        # _PURGE_CASCADE_TABLES is created by a MIGRATION (job_enrichment is 0008,
        # user_feed is 0011 …), but ``init_db()`` above only creates the legacy
        # pre-Batch-2 tables — migration 0000's header spells this split out. So a
        # DB built by init_db() ALONE, with no runner.up(), is a legitimate state
        # (several tests do exactly that) in which these children are absent, and
        # an unguarded DELETE dies with UndefinedTable. Production always migrates
        # on boot (api/dependencies.py lifespan), so this skips nothing there.
        #
        # ``current_schema()``, NOT to_regclass(): to_regclass resolves through the
        # whole search_path, which in test mode is ``"t_xxx", public``. An
        # init_db-only test in its own ``mem_*``/``t_*`` schema would then resolve
        # ``user_feed`` via the PUBLIC fallback the moment a dev has run
        # ``python main.py`` (default DSN → same Postgres, writes ``public``) — and
        # the DELETE would silently wipe real ``public`` rows whose job_id collides
        # with the test's ids (1–2 in a fresh schema — near-certain). Pinning to the
        # ACTIVE schema means "exists here", never "exists somewhere on the path".
        # Same current_schema() discipline as runner.py's sequence resync. NOT a
        # try/except UndefinedTable: an explicit check can't also swallow a REAL
        # error from the DELETE.
        existing = await self._db.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
        present = {r[0] for r in await existing.fetchall()}
        for table in _PURGE_CASCADE_TABLES:
            if table not in present:
                continue
            await self._db.execute(
                f"DELETE FROM {table} WHERE job_id IN ({stale})",  # noqa: S608 — table name is a module constant, never user input
                (cutoff, USER_BROUGHT_SOURCE),
            )
        cursor = await self._db.execute(
            "DELETE FROM jobs WHERE COALESCE(last_seen_at, first_seen) < ? AND source <> ?",
            (cutoff, USER_BROUGHT_SOURCE),
        )
        await self._db.commit()
        _log.info(
            "purge_old_jobs",
            extra={"event": "purge_old_jobs", "deleted": cursor.rowcount, "days": days, "cutoff": cutoff},
        )
        return cursor.rowcount

    async def get_recent_jobs(self, days: int = 7, min_score: int = 0) -> list[dict[str, Any]]:
        """Return jobs from the last `days` with match_score >= min_score.

        Step-1 B9: filters out rows marked ``staleness_state='expired'`` by
        ghost-detection (Pillar-3 Batch-1). NULL is treated as "not yet
        classified" → still served (defence-in-depth until the staleness
        writer lands in Batch S1.5).
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = await self._db.execute(
            "SELECT * FROM jobs WHERE first_seen >= ? AND match_score >= ? "
            "AND (staleness_state IS NULL OR staleness_state = 'active') "
            "ORDER BY date_found DESC",
            (cutoff, min_score),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_catalog_jobs_for_rescore(self, limit: int = 50000) -> list[dict[str, Any]]:
        """Return the most-recently-found jobs from the shared catalog for rescoring.

        Read-only. Returns a list of plain dicts with the columns that
        ``score_catalog_row`` (``src/services/rescore.py``) needs to reconstruct
        a ``Job`` for scoring. Respects ``limit`` so callers can cap memory use.

        THE LIMIT MUST EXCEED THE CATALOG, or a "full re-score" silently is not
        one. It was 5,000 while the catalog had grown to 6,457, so the oldest
        1,457 jobs were never re-scored when a profile changed: 43 of one real
        user's feed rows still carried scores computed on 2026-07-02 against a
        profile he had since replaced. Those sit on a different scale from
        everything around them, so they sort wrongly and mislead every threshold
        that reads them — and the gap widened every single day.

        purge_old_jobs() caps the catalog at 30 days of live postings, so 50,000
        is far above any real size while still bounding memory if that ever
        changes. The data-invariants detector alarms if the catalog approaches
        it (`rescore_covers_whole_catalog`), so this cannot silently rot again.
        """
        cursor = await self._db.execute(
            "SELECT id, title, company, apply_url, source, date_found, location, "
            "description, salary_min, salary_max, posted_at, date_confidence "
            "FROM jobs ORDER BY date_found DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_last_source_counts(self, n: int = 5) -> dict[str, list[int]]:
        """Get per-source job counts from the last N runs for health tracking."""
        cursor = await self._db.execute("SELECT per_source FROM run_log ORDER BY id DESC LIMIT ?", (n,))
        rows = await cursor.fetchall()
        source_history: dict[str, list[int]] = {}
        for row in rows:
            per_source = json.loads(row[0]) if row[0] else {}
            for name, count in per_source.items():
                source_history.setdefault(name, []).append(count)
        return source_history

    async def get_silently_dead_sources(
        self, hours: int = 48, min_runs: int = 2
    ) -> dict[str, int]:
        """Sources that USED to return jobs but have returned zero for `hours`.

        The whole failure mode this guards against is silent: a 404 makes
        `_get_json` return None, a renamed XML tag makes the parse loop never
        run — the source returns `[]`, nothing raises, and the circuit breaker
        never trips. Two sources sat at zero for months before anyone noticed.

        Deliberately reports only REGRESSIONS — a source that has never
        produced a job (keyed source with no API key, permanently dead
        upstream) is excluded. Alarming on those every run is how an alert
        becomes noise and stops being read.

        Returns {source_name: peak_jobs_seen_historically}.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        cursor = await self._db.execute(
            "SELECT per_source FROM run_log WHERE timestamp >= ? ORDER BY id DESC",
            (cutoff,),
        )
        recent_rows = await cursor.fetchall()
        if len(recent_rows) < min_runs:
            return {}  # not enough evidence in the window to judge

        recent: dict[str, list[int]] = {}
        for row in recent_rows:
            for name, count in (json.loads(row[0]) if row[0] else {}).items():
                recent.setdefault(name, []).append(int(count or 0))

        # Historical peak, so we only flag sources that have actually worked.
        cursor = await self._db.execute("SELECT per_source FROM run_log")
        peak: dict[str, int] = {}
        for row in await cursor.fetchall():
            for name, count in (json.loads(row[0]) if row[0] else {}).items():
                peak[name] = max(peak.get(name, 0), int(count or 0))

        return {
            name: peak.get(name, 0)
            for name, counts in recent.items()
            if len(counts) >= min_runs
            and not any(counts)          # zero in every run in the window
            and peak.get(name, 0) > 0    # but it has produced jobs before
        }

    # --- User Actions ---
    #
    # Batch 3.5 Deliverable C: every method now takes user_id and scopes
    # queries by it. Schema UNIQUE(user_id, job_id) is from migration
    # 0002_multi_tenant; this layer is the matching read/write surface.

    async def insert_action(self, job_id: int, action: str, user_id: str, notes: str = "") -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO user_actions (user_id, job_id, action, notes, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, job_id)
               DO UPDATE SET action = excluded.action,
                             notes = excluded.notes,
                             created_at = excluded.created_at""",
            (user_id, job_id, action, notes, now),
        )
        await self._db.commit()
        return {"job_id": job_id, "action": action, "notes": notes, "created_at": now}

    async def delete_action(self, job_id: int, user_id: str) -> None:
        await self._db.execute(
            "DELETE FROM user_actions WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        )
        await self._db.commit()

    async def get_actions(self, user_id: str) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
            """SELECT job_id, action, notes, created_at
               FROM user_actions
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )
        return [{"job_id": r[0], "action": r[1], "notes": r[2], "created_at": r[3]} for r in await cursor.fetchall()]

    async def get_action_counts(self, user_id: str) -> dict[str, int]:
        cursor = await self._db.execute(
            """SELECT action, COUNT(*) FROM user_actions
               WHERE user_id = ? GROUP BY action""",
            (user_id,),
        )
        return {r[0]: r[1] for r in await cursor.fetchall()}

    async def get_action_for_job(self, job_id: int, user_id: str) -> str | None:
        cursor = await self._db.execute(
            "SELECT action FROM user_actions WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    # --- Applications (Pipeline) ---
    #
    # Batch 3.5 Deliverable C: same user_id-scoping treatment as actions.

    # ── Tailored CV / cover letter (migration 0023) ──────────────────────────

    def _tailored_row_to_dict(self, row: Any) -> dict[str, Any]:
        import json as _json
        try:
            flagged = _json.loads(row[11]) if len(row) > 11 and row[11] else []
        except Exception:  # noqa: BLE001
            flagged = []
        return {
            "user_id": row[0], "job_id": row[1], "doc_kind": row[2],
            "ai_draft": row[3] or "", "polished": row[4], "status": row[5],
            "model": row[6], "profile_version": row[7],
            "created_at": row[8], "updated_at": row[9], "kept_at": row[10],
            "flagged_terms": flagged,
        }

    async def upsert_tailored_doc(
        self, user_id: str, job_id: int, doc_kind: str, ai_draft: str,
        *, model: str | None = None, profile_version: int | None = None,
        flagged_terms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Insert/replace the AI draft for (user, job, kind); resets it to 'draft'.

        Regenerating a doc is a fresh draft — old polished/kept state for THIS
        (user, job, kind) is superseded. KEPT docs for OTHER jobs stay intact, so the
        per-user learning signal is preserved.
        """
        now = datetime.now(timezone.utc).isoformat()
        import json as _json
        # DELETE + INSERT (not `INSERT OR REPLACE` — the Postgres shim doesn't
        # translate `OR REPLACE`, only `OR IGNORE`). A regenerate is a fresh draft.
        # Both statements run in ONE explicit transaction: if the INSERT fails
        # (or the process dies) between the two, the DELETE rolls back so the
        # user's existing tailored document is never lost (M7 data-loss fix).
        async with self._db.transaction():
            await self._db.execute(
                "DELETE FROM tailored_documents WHERE user_id = ? AND job_id = ? AND doc_kind = ?",
                (user_id, job_id, doc_kind),
            )
            await self._db.execute(
                """INSERT INTO tailored_documents
                   (user_id, job_id, doc_kind, ai_draft, polished, status, model,
                    profile_version, created_at, updated_at, kept_at, flagged_terms)
                   VALUES (?, ?, ?, ?, NULL, 'draft', ?, ?, ?, ?, NULL, ?)""",
                (user_id, job_id, doc_kind, ai_draft, model, profile_version, now, now,
                 _json.dumps(flagged_terms or [])),
            )
        await self._db.commit()
        doc = await self.get_tailored_doc(user_id, job_id, doc_kind)
        return doc or {}

    async def get_tailored_doc(self, user_id: str, job_id: int, doc_kind: str) -> dict[str, Any] | None:
        cursor = await self._db.execute(
            """SELECT user_id, job_id, doc_kind, ai_draft, polished, status, model,
                      profile_version, created_at, updated_at, kept_at, flagged_terms
               FROM tailored_documents
               WHERE user_id = ? AND job_id = ? AND doc_kind = ?""",
            (user_id, job_id, doc_kind),
        )
        row = await cursor.fetchone()
        return self._tailored_row_to_dict(row) if row else None

    async def get_tailored_docs(self, user_id: str, job_id: int) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
            """SELECT user_id, job_id, doc_kind, ai_draft, polished, status, model,
                      profile_version, created_at, updated_at, kept_at, flagged_terms
               FROM tailored_documents
               WHERE user_id = ? AND job_id = ? ORDER BY doc_kind""",
            (user_id, job_id),
        )
        return [self._tailored_row_to_dict(r) for r in await cursor.fetchall()]

    async def save_tailored_polished(
        self, user_id: str, job_id: int, doc_kind: str, polished: str
    ) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """UPDATE tailored_documents SET polished = ?, updated_at = ?
               WHERE user_id = ? AND job_id = ? AND doc_kind = ?""",
            (polished, now, user_id, job_id, doc_kind),
        )
        await self._db.commit()
        return await self.get_tailored_doc(user_id, job_id, doc_kind)

    async def keep_tailored_doc(self, user_id: str, job_id: int, doc_kind: str) -> dict[str, Any] | None:
        """Mark KEPT (finalized/downloaded/used) — the only status we learn from (§5)."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """UPDATE tailored_documents SET status = 'kept', kept_at = ?, updated_at = ?
               WHERE user_id = ? AND job_id = ? AND doc_kind = ?""",
            (now, now, user_id, job_id, doc_kind),
        )
        await self._db.commit()
        return await self.get_tailored_doc(user_id, job_id, doc_kind)

    async def count_tailored_usage_month(self, user_id: str) -> int:
        """Generations this calendar month — the quota gate counter (guardrail #1)."""
        start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM tailored_usage WHERE user_id = ? AND created_at >= ?",
            (user_id, start),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    async def record_tailored_usage(self, user_id: str, job_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO tailored_usage (user_id, job_id, created_at) VALUES (?, ?, ?)",
            (user_id, job_id, now),
        )
        await self._db.commit()

    async def get_user_kept_docs(self, user_id: str, doc_kind: str, limit: int = 3) -> list[str]:
        """Layer 2 (per-user, §6): the user's recent KEPT polished docs of this kind —
        few-shot 'write like me' examples. Only KEPT docs (§5 learn-from-kept-only)."""
        cursor = await self._db.execute(
            """SELECT polished FROM tailored_documents
               WHERE user_id = ? AND doc_kind = ? AND status = 'kept'
                 AND polished IS NOT NULL AND polished != ''
               ORDER BY kept_at DESC LIMIT ?""",
            (user_id, doc_kind, limit),
        )
        return [r[0] for r in await cursor.fetchall() if r[0]]

    async def record_tailoring_pattern(self, doc_kind: str, features_json: str) -> None:
        """Layer 1 (universal, §6): store a privacy-scrubbed pattern — NO user_id/content."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO tailoring_patterns (doc_kind, features, created_at) VALUES (?, ?, ?)",
            (doc_kind, features_json, now),
        )
        await self._db.commit()

    async def get_tailoring_patterns(self, doc_kind: str, limit: int = 200) -> list[dict[str, Any]]:
        import json as _json
        cursor = await self._db.execute(
            "SELECT features FROM tailoring_patterns WHERE doc_kind = ? ORDER BY id DESC LIMIT ?",
            (doc_kind, limit),
        )
        out: list[dict[str, Any]] = []
        for r in await cursor.fetchall():
            try:
                out.append(_json.loads(r[0]))
            except Exception:  # noqa: BLE001
                pass
        return out

    async def get_tailored_summary_for_jobs(
        self, user_id: str, job_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """For the Kanban attach: {job_id: {doc_kind: status}} for the given jobs."""
        if not job_ids:
            return {}
        placeholders = ",".join("?" for _ in job_ids)
        cursor = await self._db.execute(
            f"""SELECT job_id, doc_kind, status FROM tailored_documents
                WHERE user_id = ? AND job_id IN ({placeholders})""",
            (user_id, *job_ids),
        )
        result: dict[int, dict[str, Any]] = {}
        for jid, kind, status in await cursor.fetchall():
            result.setdefault(jid, {})[kind] = status
        return result

    async def get_fit_reason(self, user_id: str, job_id: int) -> str:
        """The judge's (E4) 'why it fits' reason for (user, job); '' if none computed.

        Tells the tailoring prompt what to emphasise. Tolerates a missing user_feed
        row (returns '') so tailoring works even before the judge has run.
        """
        try:
            cursor = await self._db.execute(
                "SELECT llm_reason FROM user_feed WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            )
        except Exception:  # noqa: BLE001 — user_feed may not exist in minimal test DBs
            return ""
        row = await cursor.fetchone()
        return (row[0] or "") if row else ""

    async def get_user_feed_verdict(self, user_id: str, job_id: int) -> dict[str, Any]:
        """The judge's (E4) per-user verdict for (user, job): ``llm_fit_score``,
        ``llm_verdict``, ``llm_reason``. Returns an empty dict when the job isn't in
        this user's feed or the judge hasn't run — so the single-job read stays
        None-safe (mirrors the list read's ``get_user_feed_jobs`` llm columns).
        """
        try:
            cursor = await self._db.execute(
                "SELECT llm_fit_score, llm_verdict, llm_reason FROM user_feed "
                "WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            )
        except Exception:  # noqa: BLE001 — user_feed may not exist in minimal test DBs
            return {}
        row = await cursor.fetchone()
        if not row:
            return {}
        return {
            "llm_fit_score": row[0],
            "llm_verdict": row[1],
            "llm_reason": row[2],
        }

    async def create_application(self, job_id: int, user_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT OR IGNORE INTO applications
               (user_id, job_id, stage, created_at, updated_at)
               VALUES (?, ?, 'applied', ?, ?)""",
            (user_id, job_id, now, now),
        )
        await self._db.commit()
        return await self._get_application(job_id, user_id)

    async def advance_application(self, job_id: int, stage: str, user_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        # Fetch current stage before updating, to record it in history.
        cursor = await self._db.execute(
            "SELECT stage FROM applications WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        )
        row = await cursor.fetchone()
        from_stage = row[0] if row else None
        # Stage move + its history row commit together or not at all (docs/
        # fable/02 D11): previously each statement auto-committed, so a crash
        # between them moved the card but silently dropped the history entry.
        # The history INSERT sits behind a SAVEPOINT so the long-standing
        # tolerance for a missing application_stage_history table (init_db-only
        # test flows, pre-0014) skips JUST the history — without the savepoint
        # that error would abort the whole transaction and undo the UPDATE.
        await self._db.execute("BEGIN")
        try:
            await self._db.execute(
                """UPDATE applications SET stage = ?, updated_at = ?, last_advanced_at = ?
                   WHERE user_id = ? AND job_id = ?""",
                (stage, now, now, user_id, job_id),
            )
            await self._db.execute("SAVEPOINT _adv_hist")
            try:
                await self._db.execute(
                    """INSERT INTO application_stage_history
                       (job_id, user_id, from_stage, to_stage, transitioned_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (job_id, user_id, from_stage, stage, now),
                )
                await self._db.execute("RELEASE SAVEPOINT _adv_hist")
            except Exception:  # noqa: BLE001
                # Table not yet created (migration 0014 not run) — keep the
                # stage move, skip only the history row.
                await self._db.execute("ROLLBACK TO SAVEPOINT _adv_hist")
            await self._db.execute("COMMIT")
        except Exception:
            await self._db.execute("ROLLBACK")
            raise
        return await self._get_application(job_id, user_id)

    async def _get_application(self, job_id: int, user_id: str) -> dict[str, Any]:
        cursor = await self._db.execute(
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

    async def get_applications(self, user_id: str, stage: str | None = None) -> list[dict[str, Any]]:
        if stage:
            cursor = await self._db.execute(
                """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                          j.title, j.company
                   FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
                   WHERE a.user_id = ? AND a.stage = ?
                   ORDER BY a.updated_at DESC""",
                (user_id, stage),
            )
        else:
            cursor = await self._db.execute(
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
        cursor = await self._db.execute(
            """SELECT stage, COUNT(*) FROM applications
               WHERE user_id = ? GROUP BY stage""",
            (user_id,),
        )
        return {r[0]: r[1] for r in await cursor.fetchall()}

    async def get_stale_applications(self, user_id: str, days: int = 7) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = await self._db.execute(
            """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                      j.title, j.company
               FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
               WHERE a.user_id = ?
                 AND a.updated_at < ?
                 AND a.stage NOT IN ('offer', 'rejected', 'considering')
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

    async def get_job_id_by_key(self, normalized_key: tuple[str, str]) -> int | None:
        """Resolve a (normalized_company, normalized_title) key to the catalog id.

        `insert_job` returns only "inserted or not"; a caller that needs the
        row id afterwards (bring-a-job, where a user re-bringing an ad that is
        already in the catalog must land on the SAME row) resolves it here.
        """
        cursor = await self._db.execute(
            "SELECT id FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
            (normalized_key[0], normalized_key[1]),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else None

    # ── Application receipts (migration 0034) ─────────────────────────────────
    #
    # APPEND-ONLY. There is deliberately no update_receipt / delete_receipt:
    # a receipt is the frozen record of what the user sent, and the whole point
    # is that nothing later can rewrite it (tests/test_receipts.py pins this).

    _RECEIPT_COLS = (
        "id, user_id, job_id, sent_at, job_title, job_company, job_location, "
        "job_apply_url, job_source, job_description, cv_text, cv_origin, "
        "cover_letter_text, cover_letter_origin, profile_version, channel, note, created_at"
    )

    @staticmethod
    def _receipt_row_to_dict(row: Any) -> dict[str, Any]:
        keys = (
            "id", "user_id", "job_id", "sent_at", "job_title", "job_company",
            "job_location", "job_apply_url", "job_source", "job_description",
            "cv_text", "cv_origin", "cover_letter_text", "cover_letter_origin",
            "profile_version", "channel", "note", "created_at",
        )
        return dict(zip(keys, row))

    async def insert_receipt(
        self,
        *,
        user_id: str,
        job: dict[str, Any],
        cv_text: str | None,
        cv_origin: str | None,
        cover_letter_text: str | None,
        cover_letter_origin: str | None,
        profile_version: int | None,
        channel: str = "",
        note: str = "",
        application_id: int | None = None,
    ) -> dict[str, Any]:
        """Freeze one application: the job row as it reads NOW plus the documents
        as sent. `job` is a `get_job_by_id` dict; its fields are COPIED, never
        referenced, so the receipt survives re-description, expiry and purge.

        `application_id` (spec 2026-09-04-application-spine R8) is set HERE, at
        INSERT time, never by a later UPDATE — tests/test_receipts.py::
        test_receipts_are_append_only greps `backend/src/` for any UPDATE/DELETE
        against `application_receipts` and must stay green.
        """
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """INSERT INTO application_receipts
               (user_id, job_id, sent_at, job_title, job_company, job_location,
                job_apply_url, job_source, job_description, cv_text, cv_origin,
                cover_letter_text, cover_letter_origin, profile_version, channel,
                note, created_at, application_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, int(job["id"]), now,
                job.get("title") or "", job.get("company") or "",
                job.get("location") or "", job.get("apply_url") or "",
                job.get("source") or "", job.get("description") or "",
                cv_text, cv_origin, cover_letter_text, cover_letter_origin,
                profile_version, channel, note, now, application_id,
            ),
        )
        await self._db.commit()
        receipt = await self.get_receipt(user_id, int(cursor.lastrowid or 0))
        assert receipt is not None  # just inserted under this user_id
        return receipt

    async def get_receipt(self, user_id: str, receipt_id: int) -> dict[str, Any] | None:
        """One receipt, scoped by owner (rule #12: a foreign id reads as absent)."""
        cursor = await self._db.execute(
            f"SELECT {self._RECEIPT_COLS} FROM application_receipts "  # noqa: S608 — class constant
            "WHERE user_id = ? AND id = ?",
            (user_id, receipt_id),
        )
        row = await cursor.fetchone()
        return self._receipt_row_to_dict(row) if row else None

    _RECEIPT_SUMMARY_COLS = (
        "id, user_id, job_id, sent_at, job_title, job_company, job_location, "
        "job_apply_url, job_source, cv_text IS NOT NULL, cover_letter_text IS NOT NULL, "
        "profile_version, channel, note, created_at"
    )

    async def list_receipts(
        self, user_id: str, *, job_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """The user's receipts, newest first; optionally only for one job.

        Summary rows only: the three long bodies (`job_description`, `cv_text`,
        `cover_letter_text`) are NOT selected — a receipt can carry 40k chars
        of ad plus a CV, and the list page shows none of it. `has_cv` /
        `has_cover_letter` are computed in SQL. Always bounded by `limit`.
        """
        sql = (
            f"SELECT {self._RECEIPT_SUMMARY_COLS} FROM application_receipts "  # noqa: S608 — class constant
            "WHERE user_id = ?"
        )
        params: list[Any] = [user_id]
        if job_id is not None:
            sql += " AND job_id = ?"
            params.append(job_id)
        sql += " ORDER BY sent_at DESC, id DESC LIMIT ? OFFSET ?"
        params += [max(1, int(limit)), max(0, int(offset))]
        cursor = await self._db.execute(sql, params)
        keys = (
            "id", "user_id", "job_id", "sent_at", "job_title", "job_company",
            "job_location", "job_apply_url", "job_source", "has_cv", "has_cover_letter",
            "profile_version", "channel", "note", "created_at",
        )
        out = []
        for r in await cursor.fetchall():
            d = dict(zip(keys, r))
            d["has_cv"] = bool(d["has_cv"])
            d["has_cover_letter"] = bool(d["has_cover_letter"])
            out.append(d)
        return out

    async def count_receipts(self, user_id: str, *, job_id: int | None = None) -> int:
        """How many receipts `list_receipts` would page through."""
        sql = "SELECT COUNT(*) FROM application_receipts WHERE user_id = ?"
        params: list[Any] = [user_id]
        if job_id is not None:
            sql += " AND job_id = ?"
            params.append(job_id)
        cursor = await self._db.execute(sql, params)
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def get_job_by_id(self, job_id: int) -> dict[str, Any] | None:
        cursor = await self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        # `description` is Optional on the cursor protocol, but a row was just
        # fetched above, so it is always populated here. Assert rather than
        # `or []`: if this ever IS None something is badly wrong, and silently
        # returning {} would hide it.
        assert cursor.description is not None
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

    async def get_recent_jobs_with_enrichment(self, days: int = 7, min_score: int = 0) -> list[dict[str, Any]]:
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
            cursor = await self._db.execute(sql, (cutoff, min_score))
        except pg.OperationalError:
            return await self.get_recent_jobs(days=days, min_score=min_score)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_user_feed_jobs(self, user_id: str, days: int = 7, min_score: int = 0) -> list[dict[str, Any]]:
        """Per-user dashboard read: the user's OWN ``user_feed`` rows joined to
        the shared ``jobs`` catalog (+ enrichment).

        This is what makes the dashboard multi-tenant: John sees only the jobs
        in John's feed, Paul only Paul's. The shared ``jobs`` table is the
        universal pool/cache; ``user_feed`` is the isolated per-user view
        (blueprint §3). Each row's ``match_score`` is the user's feed score
        (not the shared, last-writer-wins ``jobs.match_score``).

        Returns ``[]`` if ``user_feed`` is absent (fresh DB without the Batch-2
        migration) — the caller treats that as "no personalised feed yet".
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        # _JOBS_ENRICHMENT_JOIN_COLS is a class constant, not user input — S608 false positive.
        sql = (
            f"SELECT {self._JOBS_ENRICHMENT_JOIN_COLS}, f.score AS feed_score, "  # noqa: S608
            "f.llm_fit_score AS llm_fit_score, f.llm_verdict AS llm_verdict, "
            "f.llm_reason AS llm_reason "
            "FROM user_feed f "
            "JOIN jobs j ON j.id = f.job_id "
            "LEFT JOIN job_enrichment je ON je.job_id = j.id "
            "WHERE f.user_id = ? AND f.status = 'active' "
            "AND j.first_seen >= ? AND f.score >= ? "
            "AND (j.staleness_state IS NULL OR j.staleness_state = 'active') "
            # Judge outranks funnel: matcher fit when present, else keyword score.
            # All-NULL llm_fit_score (flag off) keeps this identical to the old order.
            "ORDER BY COALESCE(f.llm_fit_score, f.score) DESC, j.date_found DESC"
        )
        try:
            cursor = await self._db.execute(sql, (user_id, cutoff, min_score))
        except pg.OperationalError:
            return []
        rows = await cursor.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            # Surface the per-user feed score as the job's match_score.
            d["match_score"] = d.get("feed_score", d.get("match_score"))
            out.append(d)
        return out

    async def get_job_by_id_with_enrichment(
        self, job_id: int, user_id: str | None = None
    ) -> dict[str, Any] | None:
        """Same as :meth:`get_job_by_id` plus a LEFT JOIN to job_enrichment.

        C-1 fix: mirrors the staleness filter from
        :meth:`get_recent_jobs_with_enrichment` so a single-job lookup
        cannot surface a ghost-detected expired posting that the list
        path correctly hides.

        BUT A JOB THE USER ALREADY ACTED ON IS THEIRS TO OPEN. When ``user_id``
        is given, a job they applied to or acted on is returned even if it has
        since gone stale.

        Found 2026-08-03: a real user's own application (job 56, stage
        "applied") could no longer be opened — clicking your own application and
        getting "not found" reads as data loss, on the highest-intent object in
        the product. Hiding a ghost from BROWSING is right; hiding a person's
        own history from them is not, and staleness is a guess about the
        employer, not a fact about the user's record.
        """
        # _JOBS_ENRICHMENT_JOIN_COLS is a class constant, not user input — S608 is a false positive here.
        own = ""
        params: tuple[Any, ...] = (job_id,)
        if user_id:
            own = (
                " OR EXISTS (SELECT 1 FROM applications a "
                "WHERE a.job_id = j.id AND a.user_id = ?)"
                " OR EXISTS (SELECT 1 FROM user_actions ua "
                "WHERE ua.job_id = j.id AND ua.user_id = ?)"
            )
            params = (job_id, user_id, user_id)
        sql = (
            f"SELECT {self._JOBS_ENRICHMENT_JOIN_COLS} "  # noqa: S608
            "FROM jobs j "
            "LEFT JOIN job_enrichment je ON je.job_id = j.id "
            "WHERE j.id = ? "
            f"AND (j.staleness_state IS NULL OR j.staleness_state = 'active'{own})"
        )
        try:
            cursor = await self._db.execute(sql, params)
        except pg.OperationalError:
            # Fallback for fresh DBs without migration 0008 — still apply
            # the staleness filter so the read path stays consistent.
            fb_own = (
                " OR EXISTS (SELECT 1 FROM applications a "
                "WHERE a.job_id = jobs.id AND a.user_id = ?)"
                " OR EXISTS (SELECT 1 FROM user_actions ua "
                "WHERE ua.job_id = jobs.id AND ua.user_id = ?)"
            ) if user_id else ""
            cursor = await self._db.execute(
                "SELECT * FROM jobs WHERE id = ? "
                f"AND (staleness_state IS NULL OR staleness_state = 'active'{fb_own})",
                (job_id, user_id, user_id) if user_id else (job_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            # `description` is Optional on the cursor protocol, but a row was
            # just fetched above, so it is always populated here. Assert rather
            # than `or []`: if this ever IS None something is badly wrong, and
            # silently returning {} would hide it.
            assert cursor.description is not None
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
    ) -> list[dict[str, Any]]:
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
        params: list[Any] = [user_id]
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
            cursor = await self._db.execute(sql, tuple(params))
        except pg.OperationalError:
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
        params: list[Any] = [user_id]
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
            cursor = await self._db.execute(sql, tuple(params))
        except pg.OperationalError:
            return 0
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # ── Account management (Step-3 B-11..13) ─────────────────────────────────────

    async def soft_delete_user(self, user_id: str) -> None:
        """Set deleted_at to now — auth middleware rejects soft-deleted users."""
        from datetime import datetime, timezone  # noqa: PLC0415

        await self._db.execute(
            "UPDATE users SET deleted_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        await self._db.commit()

    # Table names are internal constants (never user input) — the placeholders
    # below are still parameterized. S608 is a false positive here (ignored for
    # this file in pyproject).
    # NOTE (2026-08-24): `oauth_states` was removed from this tuple when
    # migration 0031 dropped the table. Account deletion iterates these names
    # and issues a DELETE per table — a name here that no longer exists in the
    # schema turns "delete my account" into an UndefinedTable crash (rule #26).
    # The list and the schema must move together, in the same commit.
    _PER_USER_TABLES = (
        "api_tokens", "application_artifacts", "application_contacts", "application_events",
        "application_receipts", "application_stage_history", "applications", "email_verifications",
        "notification_ledger", "notification_rules", "oauth_grants",
        "password_resets", "profile_edits", "sessions", "tailored_documents", "tailored_usage",
        "user_actions", "user_channels", "user_feed",
        "user_notification_digests", "user_profile_versions", "user_profiles",
    )

    # Tables included in a GDPR Article 20 export (docs/fable/05 C7). This is the
    # user's OWN data — what they authored or what we derived about them.
    # Deliberately EXCLUDED from _PER_USER_TABLES: sessions, password_resets,
    # email_verifications. Those hold short-lived security tokens, not portable
    # personal data; exporting them would hand out live credentials.
    # (`oauth_states` used to be on that excluded list too — the table itself is
    # gone as of migration 0031.)
    # `api_tokens` IS exported (the user's own names/dates — "which machines did I
    # connect?") but its `token_hash` is redacted below; the plaintext was never
    # stored, so the export can hand out nothing usable as a credential.
    _EXPORT_TABLES = (
        "api_tokens", "application_artifacts", "application_contacts", "application_events",
        "application_receipts", "applications", "application_stage_history", "audit_log",
        "notification_ledger", "notification_rules", "oauth_grants", "profile_edits", "tailored_documents",
        "tailored_usage", "user_actions", "user_channels", "user_feed",
        "user_profile_versions", "user_profiles",
    )

    # Secret-bearing columns are redacted even inside exported tables — e.g. the
    # Fernet-encrypted channel credentials and the argon2 password hash. The user
    # gets their data, never their (or our) secrets.
    _EXPORT_REDACT_COLUMNS = frozenset({
        "password_hash", "config_encrypted", "credentials_encrypted", "token",
        "token_hash", "secret", "access_token", "refresh_token", "webhook_url",
    })

    @classmethod
    def _scrub_export_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {
            k: ("[redacted]" if k in cls._EXPORT_REDACT_COLUMNS and v is not None else v)
            for k, v in row.items()
        }

    async def export_user_data(self, user_id: str) -> dict[str, Any]:
        """GDPR Article 20 — return everything we hold on this user, as plain data.

        Read-only counterpart to :meth:`hard_delete_user`. Scoped strictly by
        ``user_id`` (rule #12) and never touches the shared catalog (rules #10/#17)
        — a user's export contains their feed/actions/applications, not the whole
        jobs table.

        Secrets are redacted (see ``_EXPORT_REDACT_COLUMNS``) and token tables are
        omitted entirely, so this is safe to hand to the user as a file.
        """
        out: dict[str, Any] = {}
        cur = await self._db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        out["user"] = self._scrub_export_row(dict(row)) if row else None

        incomplete: list[str] = []
        for tbl in self._EXPORT_TABLES:
            try:
                cur = await self._db.execute(
                    f"SELECT * FROM {tbl} WHERE user_id = ?", (user_id,)  # noqa: S608 — name from a module constant
                )
                out[tbl] = [self._scrub_export_row(dict(r)) for r in await cur.fetchall()]
            except Exception as exc:  # noqa: BLE001 — tolerate a table absent in a partial test schema
                # NEVER fail silently: an empty list here is indistinguishable from
                # "you have no rows", so a broken query would hand the user an
                # incomplete Article-20 export while looking successful. Log it and
                # tell the caller which tables could not be read.
                _log.warning(
                    "export_user_data: table unreadable, exported as empty",
                    extra={"event": "export_table_failed", "table": tbl, "error": str(exc)},
                )
                out[tbl] = []
                incomplete.append(tbl)
        if incomplete:
            out["_incomplete_tables"] = incomplete
        return out

    async def hard_delete_user(self, user_id: str) -> None:
        """GDPR Article 17 — irreversibly ERASE all of a user's personal data.

        Deletes the user's rows from every per-user table (CVs, profile versions,
        channel creds, feed, actions, applications, tailored docs, tokens, …),
        anonymises the shared observability ``run_log`` (keeps aggregate ops data,
        severs the personal link), removes the email-keyed ``magic_link_tokens``,
        and drops the ``users`` row last. NEVER touches the shared catalog
        (``jobs``/``job_enrichment``/``job_embeddings``) — rules #10/#17.

        Replaces the old soft-delete (which only set ``deleted_at`` and left CVs +
        embeddings + actions in place, and could be resurrected on a later
        magic-link consume). One fix closes three findings: erasure (fable/05),
        orphaned child rows (fable/02), and soft-delete resurrection (fable/01).
        """
        # magic_link_tokens is keyed by email, not user_id — look it up first.
        cur = await self._db.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        email = row["email"] if row else None

        # Erasure must never SILENTLY half-succeed. Every DELETE below used to be
        # wrapped in `except Exception: pass`, whose stated intent — tolerate a
        # table that does not exist in a partial test schema — is reasonable, but
        # which also swallowed permission errors, FK violations, deadlocks and a
        # dropped connection. The user was then told "your data is deleted" while
        # rows survived. For a GDPR Art.17 erasure that is not a bug, it is a
        # false statement to a data subject.
        #
        # Exception-type filtering alone cannot fix it: pg.py:512 converts
        # UndefinedTable into OperationalError, which is ALSO what a lost
        # connection raises — so "missing table" and "database went away" are
        # indistinguishable by type here.
        #
        # So this verifies the OUTCOME instead of trusting that nothing threw:
        # remember every failure, then prove afterwards that no rows remain.
        # Resolve which tables actually carry a `user_id` column IN THIS SCHEMA
        # first, so "this table has nothing of the user's" is separated from
        # "the delete failed" BEFORE anything is attempted. Without this split,
        # a legacy table (pre-tenancy `user_actions` / `applications`, which get
        # their user_id from a later migration) raises UndefinedColumn on the
        # DELETE and looks identical to a permission error.
        #
        # current_schema() is deliberate, NOT to_regclass(): to_regclass resolves
        # through search_path and would match a same-named table in `public`
        # while the caller operates inside a per-test schema.
        erasable: list[str] = []
        for tbl in self._PER_USER_TABLES:
            cur = await self._db.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = ? AND column_name = 'user_id'",
                (tbl,),
            )
            has_col = await cur.fetchone()
            if has_col and has_col[0]:
                erasable.append(tbl)

        deletion_errors: list[str] = []

        # oauth_tokens / oauth_authorization_codes reference grant_id, not
        # user_id directly, so the generic "has a user_id column" sweep below
        # can never reach them. Migration 0036's DDL declares
        # `ON DELETE CASCADE` for documentation, but `pg.py`'s translate()
        # strips EVERY FK clause before Postgres ever sees it (see that
        # migration's header comment) — so nothing here is cascade-deleted at
        # the database level. Delete them explicitly, via the user's grants,
        # BEFORE oauth_grants itself is erased by the sweep below.
        try:
            await self._db.execute(
                "DELETE FROM oauth_tokens WHERE grant_id IN "  # noqa: S608 — static SQL, no user input
                "(SELECT id FROM oauth_grants WHERE user_id = ?)",
                (user_id,),
            )
            await self._db.execute(
                "DELETE FROM oauth_authorization_codes WHERE grant_id IN "  # noqa: S608
                "(SELECT id FROM oauth_grants WHERE user_id = ?)",
                (user_id,),
            )
        except Exception as exc:  # noqa: BLE001 — a schema without oauth tables yet (partial test DBs)
            deletion_errors.append(f"oauth_tokens/oauth_authorization_codes: {type(exc).__name__}: {exc}")

        for tbl in erasable:
            try:
                await self._db.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (user_id,))
            except Exception as exc:  # noqa: BLE001 — recorded, then verified below
                deletion_errors.append(f"{tbl}: {type(exc).__name__}: {exc}")

        # run_log is shared observability: anonymise rather than delete.
        try:
            await self._db.execute(
                "UPDATE run_log SET user_id = NULL WHERE user_id = ?", (user_id,)
            )
        except Exception:  # noqa: BLE001
            pass

        # audit_log (migration 0025): same retention pattern — the security
        # history (a login happened, from this IP) is kept for legitimate
        # interest, but the personal link is severed. detail never holds email
        # (audit_trail denylists it), so NULLing user_id fully anonymises.
        try:
            await self._db.execute(
                "UPDATE audit_log SET user_id = NULL WHERE user_id = ?", (user_id,)
            )
        except Exception:  # noqa: BLE001
            pass

        if email is not None:
            try:
                await self._db.execute(
                    "DELETE FROM magic_link_tokens WHERE LOWER(email) = LOWER(?)", (email,)
                )
            except Exception:  # noqa: BLE001
                pass

        # The account row itself, last.
        await self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await self._db.commit()

        # ── PROVE the erasure actually happened ─────────────────────────────
        # Outcome check, not an exception check. For every per-user table that
        # EXISTS in this schema, assert zero rows remain for the user. A table
        # that is genuinely absent is skipped — that was the original tolerance
        # and it stays — but a table that exists and still holds rows is a
        # failed erasure and must be loud.
        #
        # current_schema() is deliberate, NOT to_regclass(): to_regclass
        # resolves through search_path and would happily match a same-named
        # table in `public` while the caller is operating inside a per-test
        # schema — checking the wrong table entirely.
        survivors: list[str] = []
        for tbl in erasable:  # only tables that actually hold per-user rows
            try:
                cur = await self._db.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE user_id = ?", (user_id,)
                )
                left = await cur.fetchone()
                if left and left[0]:
                    survivors.append(f"{tbl}={left[0]}")
            except Exception as exc:  # noqa: BLE001 — a failed CHECK is itself a failure
                survivors.append(f"{tbl}: verification failed ({type(exc).__name__})")

        if survivors or deletion_errors:
            raise RuntimeError(
                "account erasure did not complete — data may remain for this user. "
                f"rows still present: {survivors or 'none'}; "
                f"delete errors: {deletion_errors or 'none'}"
            )

    async def update_user_password(self, user_id: str, new_hash: str) -> None:
        """Replace the stored password hash for the given user."""
        await self._db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, user_id),
        )
        await self._db.commit()

    async def update_user_email(self, user_id: str, new_email: str) -> None:
        """Replace the email address for the given user."""
        await self._db.execute(
            "UPDATE users SET email = ? WHERE id = ?",
            (new_email, user_id),
        )
        await self._db.commit()

    # ── Application timeline (Step-3 B-07) ───────────────────────────────────────
    async def get_application_timeline(self, job_id: int, user_id: str) -> list[dict[str, Any]]:
        """Return stage history for a job+user, ordered by transitioned_at ASC."""
        cursor = await self._db.execute(
            "SELECT * FROM application_stage_history WHERE job_id = ? AND user_id = ? ORDER BY transitioned_at ASC",
            (job_id, user_id),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Discovery (Step-3 B-09, B-15) ────────────────────────────────────────────
    async def get_duplicate_jobs(
        self, job_id: int, normalized_company: str, normalized_title: str
    ) -> list[dict[str, Any]]:
        """Return jobs with same normalized key, excluding the given job_id."""
        cursor = await self._db.execute(
            """SELECT id, title, company, source, location, match_score, apply_url, date_found
               FROM jobs
               WHERE normalized_company = ? AND normalized_title = ? AND id != ?
               ORDER BY match_score DESC, date_found DESC""",
            (normalized_company, normalized_title, job_id),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Application notes update (Step-3 B-08) ───────────────────────────────────
    async def update_application_notes(self, job_id: int, user_id: str, new_notes: str) -> dict[str, Any] | None:
        """Append current notes to notes_history, set notes = new_notes."""
        import json
        from datetime import datetime, timezone

        # Fetch current notes
        cursor = await self._db.execute(
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
        await self._db.execute(
            "UPDATE applications SET notes = ?, notes_history = ?, updated_at = ? WHERE job_id = ? AND user_id = ?",
            (new_notes, json.dumps(history), datetime.now(timezone.utc).isoformat(), job_id, user_id),
        )
        await self._db.commit()
        # Return updated row
        cursor = await self._db.execute(
            "SELECT a.*, j.title, j.company "
            "FROM applications a LEFT JOIN jobs j ON a.job_id = j.id "
            "WHERE a.job_id = ? AND a.user_id = ?",
            (job_id, user_id),
        )
        updated = await cursor.fetchone()
        return dict(updated) if updated else None

    # ── Notification rules ───────────────────────────────────────────────────────

    async def get_notification_rules(self, user_id: str) -> list[dict[str, Any]]:
        """Return all notification rules for a user, ordered by channel."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM notification_rules WHERE user_id = ? ORDER BY channel",
                (user_id,),
            )
        except pg.OperationalError:
            return []
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_user_notification_rule(self, user_id: str) -> dict[str, Any] | None:
        """Return the single notification rule for user_id, or None."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM notification_rules WHERE user_id = ?",
                (user_id,),
            )
        except pg.OperationalError:
            return None
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def save_user_notification_rule(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Upsert the single notification rule for user_id. Returns the full row."""
        # #318 — these fallbacks used to be a hardcoded 60/'instant' while the
        # dispatcher defaulted to 30 and the frontend to 60: three numbers for
        # one concept, which is how the old unreachable-threshold bug hid for
        # so long. All three now read the shared, env-driven defaults.
        from src.services.notifications.defaults import (  # noqa: PLC0415
            DEFAULT_NOTIFY_MODE,
            DEFAULT_SCORE_THRESHOLD,
        )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        score_threshold = data.get("score_threshold", DEFAULT_SCORE_THRESHOLD)
        notify_mode = data.get("notify_mode", DEFAULT_NOTIFY_MODE)
        interval_hours = data.get("interval_hours", 6)
        daily_send_time = data.get("daily_send_time", "08:00")
        quiet_hours_start = data.get("quiet_hours_start")
        quiet_hours_end = data.get("quiet_hours_end")
        enabled = int(data.get("enabled", True))

        await self._db.execute(
            """
            INSERT INTO notification_rules
                (user_id, score_threshold, notify_mode, interval_hours,
                 daily_send_time, quiet_hours_start, quiet_hours_end,
                 enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                score_threshold   = excluded.score_threshold,
                notify_mode       = excluded.notify_mode,
                interval_hours    = excluded.interval_hours,
                daily_send_time   = excluded.daily_send_time,
                quiet_hours_start = excluded.quiet_hours_start,
                quiet_hours_end   = excluded.quiet_hours_end,
                enabled           = excluded.enabled,
                updated_at        = excluded.updated_at
            """,
            (
                user_id,
                score_threshold,
                notify_mode,
                interval_hours,
                daily_send_time,
                quiet_hours_start,
                quiet_hours_end,
                enabled,
                now,
                now,
            ),
        )
        await self._db.commit()
        cursor = await self._db.execute(
            "SELECT * FROM notification_rules WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}

    async def set_rule_last_sent(self, user_id: str, ts: str) -> None:
        """Update last_sent_at for the user's notification rule."""
        try:
            await self._db.execute(
                "UPDATE notification_rules SET last_sent_at = ? WHERE user_id = ?",
                (ts, user_id),
            )
            await self._db.commit()
        except pg.OperationalError:
            pass

    async def get_users_with_rules(self) -> list[dict[str, Any]]:
        """Return all enabled notification rules (one per user)."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM notification_rules WHERE enabled = 1"
            )
        except pg.OperationalError:
            return []
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def cleanup_old_digests(self, *, days: int = 30) -> int:
        """Delete sent digest rows older than `days` days. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            cursor = await self._db.execute(
                "DELETE FROM user_notification_digests WHERE sent = 1 AND sent_at < ?",
                (cutoff,),
            )
            await self._db.commit()
            return cursor.rowcount
        except pg.OperationalError:
            return 0

    async def queue_digest_notification(self, user_id: str, channel: str, job_id: int) -> None:
        """Enqueue a job for the user's digest on the given channel.

        Idempotent — duplicate (user_id, channel, job_id) rows are allowed
        because digests may be queued multiple times before send; dedup happens
        in the digest sender via the sent=0 filter.
        """
        try:
            await self._db.execute(
                "INSERT INTO user_notification_digests(user_id, channel, job_id) VALUES(?, ?, ?)",
                (user_id, channel, job_id),
            )
            await self._db.commit()
        except pg.OperationalError:
            pass  # Table missing on legacy DB — graceful no-op.

    async def get_pending_digests(self, user_id: str, channel: str) -> list[dict[str, Any]]:
        """Return all un-sent digest rows for (user_id, channel)."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM user_notification_digests " "WHERE user_id = ? AND channel = ? AND sent = 0",
                (user_id, channel),
            )
        except pg.OperationalError:
            return []
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_digests_sent(self, user_id: str, channel: str) -> int:
        """Flip sent=1 on all pending digest rows for (user_id, channel).

        Returns the count of rows updated.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            cursor = await self._db.execute(
                "UPDATE user_notification_digests "
                "SET sent = 1, sent_at = ? "
                "WHERE user_id = ? AND channel = ? AND sent = 0",
                (now, user_id, channel),
            )
        except pg.OperationalError:
            return 0
        await self._db.commit()
        return cursor.rowcount

    # ── Notification ledger stats ────────────────────────────────────────────

    async def get_notification_ledger_stats(self, user_id: str) -> dict[str, dict[str, int]]:
        """Aggregate notification_ledger by channel + status for the caller.

        Returns ``{channel: {sent: N, failed: M, queued: P, ...}}``.
        Missing table on legacy DB returns an empty dict — same graceful-degrade
        pattern as the rest of the notification_ledger surface.
        """
        try:
            cursor = await self._db.execute(
                "SELECT channel, status, COUNT(*) as cnt "
                "FROM notification_ledger "
                "WHERE user_id = ? "
                "GROUP BY channel, status",
                (user_id,),
            )
        except pg.OperationalError:
            return {}
        rows = await cursor.fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            channel = row[0]
            status = row[1]
            count = int(row[2])
            result.setdefault(channel, {})[status] = count
        return result

    async def get_recent_runs(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return recent pipeline runs scoped to ``user_id``, newest first.

        Per CLAUDE.md rule #12 the run_log is per-user operational metadata —
        rows without a user_id (legacy, pre-Batch-2) are not exposed.
        """
        try:
            cursor = await self._db.execute(
                "SELECT * FROM run_log WHERE user_id = ? "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except pg.OperationalError:
            return []

    async def count_recent_runs(self, user_id: str) -> int:
        """Return run_log row count for the given user."""
        try:
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM run_log WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except pg.OperationalError:
            return 0

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
