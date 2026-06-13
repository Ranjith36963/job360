"""Tests for the Batch 2 migration runner at backend/migrations/runner.py."""

import asyncio
import os
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from migrations import runner


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
    async with aiosqlite.connect(tmp_db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_migrations'")
        row = await cur.fetchone()
    assert row is not None, "_schema_migrations table should exist after first up()"


@pytest.mark.asyncio
async def test_up_applies_all_pending_migrations(tmp_db_path, tmp_migrations_dir):
    applied = await runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir)
    assert applied == ["0001_create_alpha", "0002_create_beta"]
    async with aiosqlite.connect(tmp_db_path) as db:
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
    async with aiosqlite.connect(tmp_db_path) as db:
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

    async with aiosqlite.connect(tmp_db_path) as db:
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
    async with aiosqlite.connect(tmp_db_path) as db:
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
    async with aiosqlite.connect(tmp_db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(user_feed)")
        cols = {row[1] for row in await cur.fetchall()}
    assert {"llm_fit_score", "llm_verdict", "llm_reason", "llm_matched_at"} <= cols


@pytest.mark.asyncio
async def test_0018_adds_profile_version_column(tmp_db_path):
    """Migration 0018 adds profile_version INTEGER column to user_feed."""
    async with aiosqlite.connect(tmp_db_path) as db:
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
    async with aiosqlite.connect(tmp_db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(user_feed)")
        cols = {row[1] for row in await cur.fetchall()}
    assert "profile_version" in cols


def test_split_sql_statements_ignores_semicolon_in_inline_comment():
    """Regression: an inline ``--`` comment containing ``;`` must NOT split the
    statement it trails. 0015's ``-- NULL = not yet used; set on consume`` did,
    producing ``sqlite3.OperationalError: incomplete input`` and breaking every
    migration-applying test fixture.
    """
    import sqlite3

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
    db = sqlite3.connect(":memory:")
    try:
        for s in stmts:
            db.execute(s)
    finally:
        db.close()
