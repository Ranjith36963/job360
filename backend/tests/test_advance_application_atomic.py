"""advance_application atomicity (docs/fable/02 D11).

The Kanban stage move used to be SELECT → UPDATE → INSERT with each statement
auto-committing: a crash between UPDATE and INSERT moved the card but silently
dropped its history row. Now both run in ONE transaction — these tests pin:
  1. happy path: stage move + history row land together,
  2. a failure before COMMIT rolls the stage move back (nothing half-applied),
  3. the connection stays usable after the rollback (no poisoned transaction),
  4. the pre-0014 tolerance still holds: with no history table, the stage
     still moves (savepoint skips just the history).
"""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

from migrations import runner
from src.repositories.database import JobDatabase

UID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def tmp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


async def _mk_db_with_application(path: str) -> tuple[JobDatabase, int]:
    db = JobDatabase(path)
    await db.init_db()
    await runner.up(path)
    title = f"role-{uuid.uuid4().hex[:8]}"
    await db._conn.execute(
        "INSERT INTO jobs (title, company, location, apply_url, source, date_found, "
        "normalized_company, normalized_title, first_seen) "
        "VALUES (?, 'Co', '', ?, 'reed', '2026-01-01', 'co', ?, '2026-01-01')",
        (title, f"https://e/{title}", title),
    )
    cur = await db._conn.execute("SELECT id FROM jobs WHERE normalized_title = ?", (title,))
    job_id = (await cur.fetchone())[0]
    await db._conn.execute(
        "INSERT INTO applications (user_id, job_id, stage, created_at, updated_at) "
        "VALUES (?, ?, 'applied', '2026-01-01', '2026-01-01')",
        (UID, job_id),
    )
    await db._conn.commit()
    return db, job_id


@pytest.mark.asyncio
async def test_advance_writes_stage_and_history_together(tmp_db_path):
    db, job_id = await _mk_db_with_application(tmp_db_path)
    try:
        result = await db.advance_application(job_id, "interview", UID)
        assert result["stage"] == "interview"
        cur = await db._conn.execute(
            "SELECT from_stage, to_stage FROM application_stage_history "
            "WHERE user_id = ? AND job_id = ?",
            (UID, job_id),
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "applied" and rows[0][1] == "interview"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failure_before_commit_rolls_back_the_stage_move(tmp_db_path, monkeypatch):
    """The D11 crash window: if the operation dies before COMMIT, the stage
    move must NOT survive alone. Pre-fix (autocommit per statement) the UPDATE
    was already durable when the crash hit — the exact half-applied state."""
    db, job_id = await _mk_db_with_application(tmp_db_path)
    try:
        real_execute = db._conn.execute

        async def crash_at_commit(sql, params=()):
            if sql.strip().upper() == "COMMIT":
                raise RuntimeError("simulated crash before commit")
            return await real_execute(sql, params)

        monkeypatch.setattr(db._conn, "execute", crash_at_commit)
        with pytest.raises(RuntimeError, match="simulated crash"):
            await db.advance_application(job_id, "offer", UID)
        monkeypatch.setattr(db._conn, "execute", real_execute)

        # Atomicity: the stage is unchanged AND no orphan history row exists.
        cur = await db._conn.execute(
            "SELECT stage FROM applications WHERE user_id = ? AND job_id = ?", (UID, job_id)
        )
        assert (await cur.fetchone())[0] == "applied", "stage move must have rolled back"
        cur = await db._conn.execute(
            "SELECT COUNT(*) FROM application_stage_history WHERE user_id = ? AND job_id = ?",
            (UID, job_id),
        )
        assert (await cur.fetchone())[0] == 0

        # 3. connection not poisoned — a normal advance works right after.
        result = await db.advance_application(job_id, "interview", UID)
        assert result["stage"] == "interview"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_missing_history_table_still_moves_the_stage(tmp_db_path):
    """History-table tolerance (savepoint path): the stage move must survive a
    failing history INSERT. Without the SAVEPOINT, the failed INSERT would abort
    the WHOLE transaction and undo the UPDATE — regressing the long-standing
    graceful-skip behaviour."""
    db, job_id = await _mk_db_with_application(tmp_db_path)
    try:
        await db._conn.execute("DROP TABLE application_stage_history")
        await db._conn.commit()
        result = await db.advance_application(job_id, "interview", UID)
        assert result["stage"] == "interview"
    finally:
        await db.close()
