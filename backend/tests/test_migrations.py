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
    (d / "0001_create_alpha.up.sql").write_text(
        "CREATE TABLE alpha (id INTEGER PRIMARY KEY, name TEXT);"
    )
    (d / "0001_create_alpha.down.sql").write_text("DROP TABLE alpha;")
    (d / "0002_create_beta.up.sql").write_text(
        "CREATE TABLE beta (id INTEGER PRIMARY KEY, val INTEGER);"
    )
    (d / "0002_create_beta.down.sql").write_text("DROP TABLE beta;")
    return d


@pytest.mark.asyncio
async def test_migrations_table_created_on_first_run(tmp_db_path, tmp_migrations_dir):
    await runner.up(tmp_db_path, migrations_dir=tmp_migrations_dir)
    async with aiosqlite.connect(tmp_db_path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_migrations'"
        )
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
