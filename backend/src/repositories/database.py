import re
from datetime import datetime, timezone
from typing import Any

from src.repositories import pg

_VALID_COL_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
# BOOLEAN + JSONB added for migration 0031 (Universal Shelf Step 1):
# salary_is_estimated is a real 3-state nullable bool, and shelf_provenance
# is the one JSON column stored as a native Postgres JSONB rather than the
# JSON-in-TEXT convention every other list/dict column in this file uses —
# see 0032_universal_shelf.up.sql for why.
_VALID_COL_TYPES = {"TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC", "BOOLEAN", "JSONB"}

from src.models import Job  # noqa: E402  # after the regex constants to avoid circular import
from src.utils.logger import get_logger  # noqa: E402

_log = get_logger("db.repo")  # job360.db.repo → data/logs/


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
            CREATE INDEX IF NOT EXISTS idx_jobs_date_found ON jobs(date_found);
            CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
            CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score);
            CREATE INDEX IF NOT EXISTS idx_jobs_staleness_state ON jobs(staleness_state);
            CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs(last_seen_at);
            -- `user_actions` is dropped by migration 0040 (mission sweep) — no
            -- product code reads or writes it any more. It stays HERE, though,
            -- because migration 0002_multi_tenant's rebuild pattern (create
            -- user_actions_new, copy FROM user_actions, drop, rename) assumes
            -- the table already exists; without it a fresh boot 500s on
            -- `relation "user_actions" does not exist` before it ever reaches
            -- the migration that retires it. init_db() creates the legacy
            -- scaffolding every migration after it expects; 0040 is the one
            -- that actually removes it from the schema.
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
        applications_migrations = [
            # Step-3 B-06 — stage history + interview dates + notes versioning.
            # Mirrors migration 0014_application_history so init_db() alone
            # produces the full applications schema even before the runner runs.
            ("last_advanced_at", "TEXT"),
            ("interview_dates", "TEXT DEFAULT '[]'"),
            ("notes_history", "TEXT DEFAULT '[]'"),
        ]

        await self._add_missing_columns("jobs", jobs_migrations)
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
        # Slice 5 (#483): the score, dim and shelf columns are no longer
        # written. Nothing computes them any more — the columns stay on the
        # table (dropping them is its own migration) but a brought ad leaves
        # them at their defaults rather than storing invented zeroes.
        cursor = await self._db.execute(
            """INSERT OR IGNORE INTO jobs
            (title, company, location, salary_min, salary_max, description,
             apply_url, source, date_found, visa_flag,
             experience_level, normalized_company, normalized_title, first_seen,
             posted_at, first_seen_at, last_seen_at, date_confidence,
             date_posted_raw, deadline, deadline_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                job.deadline,
                job.deadline_source,
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

    async def update_last_seen(self, normalized_key: tuple[str, str]) -> None:
        """Mark a job as seen NOW, clearing the stale marks a long-dead ghost
        sweep may have left on it.

        Its old caller (the scrape cycle) is gone. `POST /jobs/bring` is the
        only one left: re-bringing an ad that matches a legacy scraped row must
        make that row usable again, because `POST /pipeline/{job_id}` still
        refuses a `confirmed_expired` one — the last read of that column.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE jobs SET last_seen_at = ?, consecutive_misses = 0, "
            "staleness_state = 'active' "
            "WHERE normalized_company = ? AND normalized_title = ?",
            (now, normalized_key[0], normalized_key[1]),
        )
        await self._db.commit()

    async def commit(self) -> None:
        """Commit pending changes."""
        if self._conn:
            await self._conn.commit()

    async def count_jobs(self) -> int:
        cursor = await self._db.execute("SELECT COUNT(*) FROM jobs")
        row = await cursor.fetchone()
        return int(row[0])

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
        "oauth_grants",
        "password_resets", "profile_edits", "sessions", "tailored_documents", "tailored_usage",
        "user_profile_versions", "user_profiles",
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
        "oauth_grants", "profile_edits", "tailored_documents",
        "tailored_usage",
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
        removes the email-keyed ``magic_link_tokens``, and drops the ``users``
        row last. NEVER touches the shared ``jobs`` catalog — rule #10.

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

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
