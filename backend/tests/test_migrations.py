"""Tests for the Batch 2 migration runner at backend/migrations/runner.py."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from migrations import runner
from src.repositories import pg


@pytest.fixture
def tmp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def tmp_migrations_dir(tmp_path: Path) -> Path:
    """Isolated migrations directory with two toy migrations."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "0001_create_alpha.up.sql").write_text("CREATE TABLE alpha (id INTEGER PRIMARY KEY, name TEXT);")
    (d / "0001_create_alpha.down.sql").write_text("DROP TABLE alpha;")
    (d / "0002_create_beta.up.sql").write_text("CREATE TABLE beta (id INTEGER PRIMARY KEY, val INTEGER);")
    (d / "0002_create_beta.down.sql").write_text("DROP TABLE beta;")
    return d


@pytest.mark.asyncio
async def test_migrations_table_created_on_first_run(tmp_db_path, tmp_migrations_dir):
    await runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir)
    async with pg.connect(tmp_db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_migrations'")
        row = await cur.fetchone()
    assert row is not None, "_schema_migrations table should exist after first up()"


@pytest.mark.asyncio
async def test_up_applies_all_pending_migrations(tmp_db_path, tmp_migrations_dir):
    applied = await runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir)
    assert applied == ["0001_create_alpha", "0002_create_beta"]
    async with pg.connect(tmp_db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in await cur.fetchall()}
    assert "alpha" in tables
    assert "beta" in tables


@pytest.mark.asyncio
async def test_up_is_idempotent(tmp_db_path, tmp_migrations_dir):
    first = await runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir)
    second = await runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir)
    assert first == ["0001_create_alpha", "0002_create_beta"]
    assert second == []  # nothing new to apply


@pytest.mark.asyncio
async def test_down_reverses_last_migration(tmp_db_path, tmp_migrations_dir):
    await runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir)
    reverted = await runner.down(tmp_db_path, migrations_dir=tmp_migrations_dir)
    assert reverted == "0002_create_beta"
    async with pg.connect(tmp_db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in await cur.fetchall()}
    assert "alpha" in tables
    assert "beta" not in tables


@pytest.mark.asyncio
async def test_status_lists_applied_and_pending(tmp_db_path, tmp_migrations_dir):
    # Apply only 0001 manually by partial-up
    await runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir, target="0001_create_alpha")
    status = await runner.status(tmp_db_path, migrations_dir=tmp_migrations_dir)
    assert status["applied"] == ["0001_create_alpha"]
    assert status["pending"] == ["0002_create_beta"]


@pytest.mark.asyncio
async def test_identity_sequence_resynced_after_id_copy(tmp_db_path, tmp_path):
    """docs/fable/02 D3 — a migration that copies explicit ids must leave the
    identity sequence pointing past MAX(id).

    Postgres does not advance a serial/identity sequence when the id is supplied
    explicitly (which every table-rebuild migration does: `INSERT INTO x_new
    (id, ...) SELECT id, ...`). Without a setval the sequence still sits at 1, so
    the next natural INSERT collides with an existing row (UniqueViolation).
    """
    d = tmp_path / "m_seq"
    d.mkdir()
    (d / "0001_seqfix.up.sql").write_text(
        "CREATE TABLE seq_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);\n"
        # Copy rows in WITH their ids, exactly like the rebuild migrations do.
        "INSERT INTO seq_probe (id, name) VALUES (1, 'a');\n"
        "INSERT INTO seq_probe (id, name) VALUES (2, 'b');\n"
        "INSERT INTO seq_probe (id, name) VALUES (3, 'c');"
    )
    (d / "0001_seqfix.down.sql").write_text("DROP TABLE IF EXISTS seq_probe;")

    await runner.up(tmp_db_path, migrations_dir=d)

    # The next natural insert (no explicit id) must NOT collide with id 1/2/3.
    async with pg.connect(tmp_db_path) as db:
        await db.execute("INSERT INTO seq_probe (name) VALUES ('d')")
        await db.commit()
        cur = await db.execute("SELECT COUNT(*) FROM seq_probe")
        assert (await cur.fetchone())[0] == 4
        cur = await db.execute("SELECT MAX(id) FROM seq_probe")
        assert (await cur.fetchone())[0] == 4, "sequence did not resume after MAX(id)"
        await db.execute("DROP TABLE IF EXISTS seq_probe")
        await db.commit()


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_atomically(tmp_db_path, tmp_path):
    """A migration that fails partway leaves NO partial state (docs/fable/02).

    Transactional migrations: statement 1 creates a table, statement 2 is
    invalid (targets a table that doesn't exist). The whole migration must roll
    back — the first statement's table must NOT survive, and the migration must
    NOT be recorded as applied. Before the fix (autocommit per statement),
    statement 1 committed and the table leaked.
    """
    d = tmp_path / "m_atomic"
    d.mkdir()
    (d / "0001_partial.up.sql").write_text(
        "CREATE TABLE atomic_probe_tbl (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO definitely_no_such_table_xyz (id) VALUES (1);"
    )
    (d / "0001_partial.down.sql").write_text("DROP TABLE IF EXISTS atomic_probe_tbl;")

    with pytest.raises(Exception):
        await runner.up(tmp_db_path, migrations_dir=d)

    # The good statement's table must have been rolled back with the bad one.
    async with pg.connect(tmp_db_path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='atomic_probe_tbl'"
        )
        row = await cur.fetchone()
    assert row is None, "partial migration table should have been rolled back, not committed"

    # And the migration must not be recorded as applied — it's still pending.
    status = await runner.status(tmp_db_path, migrations_dir=d)
    assert "0001_partial" in status["pending"]
    assert "0001_partial" not in status["applied"]


@pytest.mark.asyncio
async def test_concurrent_up_is_race_safe(tmp_db_path, tmp_migrations_dir):
    """Step-1 B11: two concurrent up() calls against the same DB must not
    crash on the `_schema_migrations` UNIQUE(id) constraint.

    API + ARQ worker both call runner.up() on boot. If they race, both read
    the applied set, both decide a migration is pending, both execute the
    SQL, both INSERT — second INSERT hits UNIQUE(id) and the process crashes.

    Expectation: neither coroutine raises; each migration recorded exactly
    once in `_schema_migrations`.
    """
    results = await asyncio.gather(
        runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir),
        runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir),
        return_exceptions=True,
    )
    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"concurrent up() raised: {exceptions!r}"

    async with pg.connect(tmp_db_path) as db:
        cur = await db.execute("SELECT id, COUNT(*) FROM _schema_migrations GROUP BY id")
        rows = await cur.fetchall()
    dup_rows = [r for r in rows if r[1] != 1]
    assert dup_rows == [], f"duplicate migration rows: {dup_rows!r}"
    applied_ids = {r[0] for r in rows}
    assert applied_ids == {"0001_create_alpha", "0002_create_beta"}


@pytest.mark.asyncio
async def test_0017_adds_llm_verdict_columns(tmp_db_path):
    """Migration 0017 adds the four per-user LLM-matcher columns to user_feed."""
    # Pre-Batch-2 baseline tables must exist before the runner fires (same
    # pattern as test_feed_service.py / conftest._bootstrap_async_db).
    async with pg.connect(tmp_db_path) as db:
        await db.executescript(
            """
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
            CREATE TABLE IF NOT EXISTS run_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                source TEXT NOT NULL,
                jobs_found INTEGER NOT NULL DEFAULT 0,
                jobs_new INTEGER NOT NULL DEFAULT 0,
                duration_s REAL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                salary_min INTEGER,
                salary_max INTEGER,
                description TEXT,
                apply_url TEXT,
                source TEXT,
                date_found TEXT,
                match_score INTEGER DEFAULT 0,
                normalized_key TEXT UNIQUE,
                visa_flag INTEGER DEFAULT 0,
                date_posted TEXT
            );
            """
        )
        await db.commit()
    # Apply all real migrations (0000 -> latest).
    await runner.up(tmp_db_path)
    async with pg.connect(tmp_db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(user_feed)")
        cols = {row[1] for row in await cur.fetchall()}
    assert {"llm_fit_score", "llm_verdict", "llm_reason", "llm_matched_at"} <= cols


@pytest.mark.asyncio
async def test_0018_adds_profile_version_column(tmp_db_path):
    """Migration 0018 adds profile_version INTEGER column to user_feed."""
    async with pg.connect(tmp_db_path) as db:
        await db.executescript(
            """
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
            CREATE TABLE IF NOT EXISTS run_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                source TEXT NOT NULL,
                jobs_found INTEGER NOT NULL DEFAULT 0,
                jobs_new INTEGER NOT NULL DEFAULT 0,
                duration_s REAL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                salary_min INTEGER,
                salary_max INTEGER,
                description TEXT,
                apply_url TEXT,
                source TEXT,
                date_found TEXT,
                match_score INTEGER DEFAULT 0,
                normalized_key TEXT UNIQUE,
                visa_flag INTEGER DEFAULT 0,
                date_posted TEXT
            );
            """
        )
        await db.commit()
    await runner.up(tmp_db_path)
    async with pg.connect(tmp_db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(user_feed)")
        cols = {row[1] for row in await cur.fetchall()}
    assert "profile_version" in cols


@pytest.mark.asyncio
async def test_0019_adds_oauth_states_table_and_channel_columns(tmp_db_path):
    """Migration 0019 adds connection_status + target_label to user_channels
    and creates the oauth_states table with the expected columns."""
    async with pg.connect(tmp_db_path) as db:
        await db.executescript(
            """
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
            CREATE TABLE IF NOT EXISTS run_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                source TEXT NOT NULL,
                jobs_found INTEGER NOT NULL DEFAULT 0,
                jobs_new INTEGER NOT NULL DEFAULT 0,
                duration_s REAL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                salary_min INTEGER,
                salary_max INTEGER,
                description TEXT,
                apply_url TEXT,
                source TEXT,
                date_found TEXT,
                match_score INTEGER DEFAULT 0,
                normalized_key TEXT UNIQUE,
                visa_flag INTEGER DEFAULT 0,
                date_posted TEXT
            );
            """
        )
        await db.commit()
    await runner.up(tmp_db_path)
    async with pg.connect(tmp_db_path) as conn:
        # user_channels gains the two new columns.
        cur = await conn.execute("PRAGMA table_info(user_channels)")
        channel_cols = {row[1] for row in await cur.fetchall()}
        assert "connection_status" in channel_cols, (
            "user_channels missing connection_status after migration 0019"
        )
        assert "target_label" in channel_cols, (
            "user_channels missing target_label after migration 0019"
        )

        # oauth_states table exists with all expected columns.
        cur2 = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='oauth_states'"
        )
        assert await cur2.fetchone() is not None, "oauth_states table was not created"

        cur3 = await conn.execute("PRAGMA table_info(oauth_states)")
        oauth_cols = {row[1] for row in await cur3.fetchall()}
        assert {"state", "user_id", "channel_type", "created_at"} <= oauth_cols, (
            f"oauth_states missing expected columns; got {oauth_cols}"
        )


@pytest.mark.asyncio
async def test_0020_adds_deadline_columns(tmp_db_path):
    """Migration 0020 adds deadline + deadline_source TEXT columns to jobs."""
    async with pg.connect(tmp_db_path) as db:
        await db.executescript(
            """
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
            CREATE TABLE IF NOT EXISTS run_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                source TEXT NOT NULL,
                jobs_found INTEGER NOT NULL DEFAULT 0,
                jobs_new INTEGER NOT NULL DEFAULT 0,
                duration_s REAL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                salary_min INTEGER,
                salary_max INTEGER,
                description TEXT,
                apply_url TEXT,
                source TEXT,
                date_found TEXT,
                match_score INTEGER DEFAULT 0,
                normalized_key TEXT UNIQUE,
                visa_flag INTEGER DEFAULT 0,
                date_posted TEXT
            );
            """
        )
        await db.commit()
    await runner.up(tmp_db_path)
    async with pg.connect(tmp_db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(jobs)")
        cols = {row[1] for row in await cur.fetchall()}
    assert "deadline" in cols, "jobs table missing 'deadline' column after migration 0020"
    assert "deadline_source" in cols, "jobs table missing 'deadline_source' column after migration 0020"


def test_split_sql_statements_ignores_semicolon_in_inline_comment():
    """Regression: an inline ``--`` comment containing ``;`` must NOT split the
    statement it trails. 0015's ``-- NULL = not yet used; set on consume`` did,
    producing ``pgsync.OperationalError: incomplete input`` and breaking every
    migration-applying test fixture.
    """
    from src.repositories import pgsync

    sql = (
        "CREATE TABLE t (\n"
        "    id INTEGER PRIMARY KEY,\n"
        "    used_at TEXT  -- NULL = not yet used; set on consume\n"
        ");\n"
        "CREATE INDEX idx_t ON t(id);\n"
    )
    stmts = runner._split_sql_statements(sql)
    assert len(stmts) == 2, f"expected 2 statements, got {len(stmts)}: {stmts!r}"
    assert stmts[0].startswith("CREATE TABLE t")
    assert "used_at TEXT" in stmts[0]
    assert "set on consume" not in stmts[0]  # inline comment stripped
    assert stmts[1].startswith("CREATE INDEX")
    # The real bug only surfaced at execute time — prove the split is runnable.
    db = pgsync.connect(":memory:")
    try:
        for s in stmts:
            db.execute(s)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_up_rolls_back_partial_migration_on_failure(tmp_db_path, tmp_path):
    """Finding H2: a migration whose body fails mid-way must roll back ENTIRELY
    and NOT be recorded as applied — no half-applied migration marked "done".

    The up.sql here creates ``good_tbl`` (succeeds) then inserts into a missing
    table (fails). Under the per-migration transaction the CREATE must roll back
    and the stem must be absent from ``_schema_migrations``.
    """
    d = tmp_path / "m_partial"
    d.mkdir()
    (d / "0001_partial.up.sql").write_text(
        "CREATE TABLE good_tbl (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table_xyz (id) VALUES (1);\n"
    )
    (d / "0001_partial.down.sql").write_text("DROP TABLE IF EXISTS good_tbl;")

    with pytest.raises(Exception):
        await runner.up(tmp_db_path, migrations_dir=d)

    async with pg.connect(tmp_db_path) as db:
        cur = await db.execute("SELECT id FROM _schema_migrations")
        recorded = {r[0] for r in await cur.fetchall()}
        assert "0001_partial" not in recorded, "half-applied migration was recorded as done"

        cur2 = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='good_tbl'"
        )
        assert await cur2.fetchone() is None, "partial CREATE TABLE was not rolled back"
