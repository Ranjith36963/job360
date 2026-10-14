"""Migration 0056 — the sourcing-era score columns leave `jobs`, and stay gone."""

from __future__ import annotations

import uuid

import pytest

from migrations import runner
from src.repositories import pg
from src.repositories.database import JobDatabase

STEM = "0056_drop_jobs_sourcing_columns"
DROPPED = {
    "match_score", "role", "skill", "seniority_score", "experience", "credentials",
    "location_score", "recency", "semantic", "penalty",
    "salary_min_gbp_annual", "salary_max_gbp_annual",
}
KEPT = {"id", "title", "company", "description", "apply_url", "source", "salary_min", "salary_max",
        "normalized_company", "normalized_title", "first_seen", "deadline"}


async def _jobs_columns(path: str) -> set[str]:
    async with pg.connect(path) as conn:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'jobs'"
        )
        return {r[0] for r in await cur.fetchall()}


async def _boot(path: str) -> None:
    db = JobDatabase(path)
    await db.init_db()
    await db.close()


@pytest.mark.asyncio
async def test_migration_0056_up_down(tmp_path):
    path = str(tmp_path / f"m56_{uuid.uuid4().hex[:8]}.db")
    await _boot(path)
    await runner.up(path, target=STEM)

    async with pg.connect(path) as conn:
        await conn.execute(
            "INSERT INTO jobs (title, company, apply_url, source, date_found, normalized_company, "
            "normalized_title, first_seen, salary_min) VALUES ('t', 'c', 'u', 'user_brought', 'd', "
            "'c', 't', 'f', 50000)"
        )

    cols = await _jobs_columns(path)
    assert not (DROPPED & cols), f"0056 left {sorted(DROPPED & cols)}"
    assert KEPT <= cols

    # A reboot must NOT resurrect them (init_db's _migrate runs before the runner).
    await _boot(path)
    assert not (DROPPED & await _jobs_columns(path)), "init_db re-added a dropped column"

    # Down: shape comes back, nullable/defaulted; the row survives both ways.
    assert await runner.down(path) == STEM
    cols = await _jobs_columns(path)
    assert DROPPED <= cols
    async with pg.connect(path) as conn:
        cur = await conn.execute("SELECT salary_min, match_score, salary_min_gbp_annual FROM jobs")
        assert tuple(await cur.fetchone()) == (50000, 0, None)

    # And up again.
    await runner.up(path, target=STEM)
    assert not (DROPPED & await _jobs_columns(path))
    async with pg.connect(path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM jobs")
        assert (await cur.fetchone())[0] == 1
        cur = await conn.execute(
            "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = current_schema() "
            "AND indexname = 'idx_jobs_match_score'"
        )
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_fresh_init_db_has_none_of_the_dropped_columns(tmp_path):
    """init_db alone (no runner) — the schema many tests use — matches a migrated one."""
    path = str(tmp_path / f"m56f_{uuid.uuid4().hex[:8]}.db")
    await _boot(path)
    assert not (DROPPED & await _jobs_columns(path))


def test_migration_0056_drops_exactly_the_twelve():
    from pathlib import Path

    folder = Path(__file__).resolve().parent.parent / "migrations"
    up = (folder / f"{STEM}.up.sql").read_text(encoding="utf-8")
    down = (folder / f"{STEM}.down.sql").read_text(encoding="utf-8")
    for col in DROPPED:
        assert f"DROP COLUMN IF EXISTS {col};" in up
        assert f"ADD COLUMN IF NOT EXISTS {col} " in down
    assert up.count("ALTER TABLE jobs DROP COLUMN") == len(DROPPED)
    assert down.count("ALTER TABLE jobs ADD COLUMN") == len(DROPPED)
