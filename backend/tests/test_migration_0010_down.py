"""Step-0 pre-flight (Tier B) — round-trip integration test for migration 0010.

Proves that ``0010_run_log_observability`` can be rolled forward AND back on a
real DB created via ``JobDatabase.init_db()`` without losing the six legacy
``run_log`` columns. The reverse migration rebuilds the table (SQLite DROP
COLUMN pre-3.35 limitation — see the .down.sql header comment), so a concrete
round-trip assertion is the only way to catch a drift between the up + down
pair.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from migrations import runner
from src.repositories import pg
from src.repositories.database import JobDatabase

_0010_STEM = "0010_run_log_observability"
_NEW_COLUMNS = {
    "run_uuid",
    "per_source_errors",
    "per_source_duration",
    "total_duration",
    "user_id",
}
_LEGACY_COLUMNS = (
    "id",
    "timestamp",
    "total_found",
    "new_jobs",
    "sources_queried",
    "per_source",
)


@pytest.fixture
def tmp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


async def _run_0010_down_only(db_path: str) -> None:
    """Apply ``0010_run_log_observability.down.sql`` and remove the ledger row.

    The shared ``runner.down()`` helper reverses the most-recently-applied
    stem; in this test the pre-Batch-2 migrations through 0009 are already
    applied by ``runner.up()``, so calling ``runner.down()`` once is
    equivalent to "reverse 0010". The explicit stem lookup below makes the
    intent obvious (and guards against future migrations being added after
    0010 before this test is updated).
    """
    mdir = runner.MIGRATIONS_DIR
    async with pg.connect(db_path) as db:
        sql = (mdir / f"{_0010_STEM}.down.sql").read_text()
        await db.executescript(sql)
        await db.execute("DELETE FROM _schema_migrations WHERE id = ?", (_0010_STEM,))
        await db.commit()


def _run_log_columns(db_path: str) -> set[str]:
    async def _go() -> set[str]:
        async with pg.connect(db_path) as db:
            cur = await db.execute("PRAGMA table_info(run_log)")
            return {row[1] for row in await cur.fetchall()}

    return asyncio.run(_go())


def test_migration_0010_down_removes_new_columns_and_preserves_legacy_data(
    tmp_db_path: str,
) -> None:
    # 1 — create a tmp DB via JobDatabase.init_db() (legacy 6-column run_log).
    db = JobDatabase(tmp_db_path)
    asyncio.run(db.init_db())
    asyncio.run(db.close())

    # 2 — apply ALL migrations including 0010 via the runner.
    applied = asyncio.run(runner.up(tmp_db_path))
    assert (
        _0010_STEM in applied or _0010_STEM in asyncio.run(runner.status(tmp_db_path))["applied"]
    ), f"migration {_0010_STEM} did not apply"

    # 3 — insert a run_log row populating BOTH legacy + 0010 columns.
    async def _seed() -> None:
        async with pg.connect(tmp_db_path) as conn:
            await conn.execute(
                "INSERT INTO run_log ("
                " timestamp, total_found, new_jobs, sources_queried, per_source,"
                " run_uuid, per_source_errors, per_source_duration,"
                " total_duration, user_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "2026-04-24T00:00:00+00:00",
                    42,
                    7,
                    48,
                    '{"reed": 10}',
                    "run-uuid-sentinel",
                    '{"reed": "timeout"}',
                    '{"reed": 1.23}',
                    9.99,
                    "user-xyz",
                ),
            )
            await conn.commit()

    asyncio.run(_seed())

    # Sanity — new columns exist pre-down.
    pre_cols = _run_log_columns(tmp_db_path)
    assert _NEW_COLUMNS.issubset(pre_cols), f"expected 0010 new columns pre-down, got {pre_cols}"

    # 4 — run migration 0010 down via the runner helpers.
    asyncio.run(_run_0010_down_only(tmp_db_path))

    # 5 — assert new columns are GONE.
    post_cols = _run_log_columns(tmp_db_path)
    for col in _NEW_COLUMNS:
        assert col not in post_cols, f"column {col!r} should be dropped after 0010 down, got {post_cols}"

    # And the legacy 6 columns survive.
    for col in _LEGACY_COLUMNS:
        assert col in post_cols, f"legacy column {col!r} missing after 0010 down, got {post_cols}"

    # 6 — legacy row data survives the table rebuild.
    async def _read_row() -> tuple:
        async with pg.connect(tmp_db_path) as conn:
            cur = await conn.execute(
                "SELECT timestamp, total_found, new_jobs, sources_queried, per_source"
                " FROM run_log ORDER BY id DESC LIMIT 1"
            )
            row = await cur.fetchone()
            assert row is not None
            return tuple(row)

    ts, total, new, queried, per_src = asyncio.run(_read_row())
    assert ts == "2026-04-24T00:00:00+00:00"
    assert total == 42
    assert new == 7
    assert queried == 48
    assert per_src == '{"reed": 10}'

    # 7 — ledger row removed so re-running up() would re-apply 0010 cleanly.
    status = asyncio.run(runner.status(tmp_db_path))
    assert _0010_STEM not in status["applied"]
    assert _0010_STEM in status["pending"]
