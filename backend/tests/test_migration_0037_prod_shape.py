"""Migration 0037 vs the shape that killed its first prod deploy (2026-09-04).

The fold's "no orphans" step inserted one applications row PER TIMESTAMP because
`SELECT DISTINCT user_id, job_id, ..., created_at` keeps a row for every distinct
timestamp, and `WHERE NOT EXISTS` cannot see sibling rows inserted by the same
statement. Prod had two tailored_documents for one (user, job) with different
`created_at` values — `UniqueViolation: applications_new_user_id_job_id_key`,
`Application startup failed`, deploy dead. These tests seed exactly that shape
in ALL THREE source tables. Fixtures are local to this module on purpose —
importing them across modules breaks schema-per-test isolation (conftest law).
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from migrations import runner
from src.repositories import pg as _pg

_USER = "prod-shape-user"
_JOB = 910_001
_T1 = "2026-08-01T10:00:00Z"
_T2 = "2026-08-02T11:00:00Z"


@pytest.fixture
def dup_key_db_path(tmp_path):
    """Every migration up to 0036, then multiple legacy rows per (user, job).

    No applications row exists for the key — the fold's step 2 must create
    exactly one from the three competing sources.
    """
    db_path = str(tmp_path / "dup_key.db")

    async def _bootstrap() -> None:
        from src.repositories.database import JobDatabase

        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
        await runner.up(db_path, target="0036_oauth")

        async with _pg.connect(db_path) as conn:
            await conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (_USER, "prod-shape@example.com", "!", _T1),
            )
            # Two tailored documents, same (user, job), DIFFERENT created_at —
            # the exact prod shape. Different doc_kind per row: the table has
            # UNIQUE(user_id, job_id, doc_kind), so prod's duplicate key came
            # from a cv + cover_letter pair for one job.
            for ts, kind in ((_T1, "cv"), (_T2, "cover_letter")):
                await conn.execute(
                    "INSERT INTO tailored_documents "
                    "(user_id, job_id, doc_kind, ai_draft, polished, status, model, profile_version, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, ?, 'draft', NULL, 'draft', 'm', 1, ?, ?)",
                    (_USER, _JOB, kind, ts, ts),
                )
            # Two receipts for the same key, different created_at (the table
            # has no UNIQUE on the pair — re-applying is legal).
            for ts in (_T1, _T2):
                await conn.execute(
                    "INSERT INTO application_receipts "
                    "(user_id, job_id, sent_at, job_title, job_company, created_at) "
                    "VALUES (?, ?, ?, 't', 'c', ?)",
                    (_USER, _JOB, ts, ts),
                )
            # Two stage-history transitions for the same key, different times.
            await conn.execute(
                "INSERT INTO application_stage_history "
                "(job_id, user_id, from_stage, to_stage, transitioned_at, notes) "
                "VALUES (?, ?, NULL, 'applied', ?, '')",
                (_JOB, _USER, _T1),
            )
            await conn.execute(
                "INSERT INTO application_stage_history "
                "(job_id, user_id, from_stage, to_stage, transitioned_at, notes) "
                "VALUES (?, ?, 'applied', 'interview', ?, '')",
                (_JOB, _USER, _T2),
            )
            await conn.commit()

    asyncio.run(_bootstrap())
    yield db_path
    with contextlib.suppress(Exception):
        asyncio.run(_pg.drop_schema_for(db_path))


@pytest.mark.asyncio
async def test_duplicate_keys_across_timestamps_fold_into_one_application(dup_key_db_path):
    """The prod crash, replayed: runner.up() must not raise, and the six
    competing legacy rows must produce exactly ONE applications row carrying
    the OLDEST timestamp."""
    await runner.up(dup_key_db_path)

    async with _pg.connect(dup_key_db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*), MIN(created_at) FROM applications WHERE user_id = ? AND job_id = ?",
            (_USER, _JOB),
        )
        count, created_at = await cur.fetchone()
    assert count == 1, "one (user, job) key must fold into exactly one application"
    assert created_at == _T1, "the folded application must keep the oldest legacy timestamp"


@pytest.mark.asyncio
async def test_migration_is_rerunnable_after_the_fold(dup_key_db_path):
    """A second runner.up() over the folded database applies nothing and
    raises nothing — the guard against a crash-looped deploy half-applying."""
    await runner.up(dup_key_db_path)
    applied_again = await runner.up(dup_key_db_path)
    assert applied_again == [] or applied_again is None or applied_again == 0 or not applied_again, (
        "re-running the chain must be a no-op"
    )
