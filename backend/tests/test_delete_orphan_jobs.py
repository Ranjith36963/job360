"""scripts/delete_orphan_jobs.py — orphan `jobs` cleanup (counts only, guarded).

An orphan is a catalog row no `job_id` column anywhere points at. The script
must count them without writing, refuse to delete without both flags, and never
delete a row something still references.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from scripts import delete_orphan_jobs as doj
from src.repositories import pgsync
from src.repositories.database import JobDatabase

OLD = "2025-01-01T00:00:00+00:00"


@pytest.fixture
def orphan_db(tmp_path):
    from migrations import runner

    db_path = str(tmp_path / f"orphans_{uuid.uuid4().hex[:8]}.db")

    async def _boot():
        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
        await runner.up(db_path)

    asyncio.run(_boot())
    conn = pgsync.connect(db_path)
    yield conn._raw  # psycopg connection, search_path = this test's schema
    conn._raw.close()


def _job(raw, n: int, *, source: str = "reed", seen: str = OLD) -> int:
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (title, company, apply_url, source, date_found, normalized_company, "
            "normalized_title, first_seen, first_seen_at, last_seen_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (f"Title {n}", f"Co {n}", f"https://x.example/{n}", source, seen,
             f"co{n}", f"title{n}", seen, seen, seen),
        )
        return int(cur.fetchone()[0])


def _seed(raw) -> dict[str, int]:
    """3 referenced (application, receipt-less tailored_usage, stage history), 4 orphans, 1 young orphan."""
    uid = uuid.uuid4().hex
    ids = {
        "app": _job(raw, 1),
        "usage": _job(raw, 2),
        "history": _job(raw, 3),
        "o1": _job(raw, 4, source="reed"),
        "o2": _job(raw, 5, source="reed"),
        "o3": _job(raw, 6, source="adzuna"),
        "o4": _job(raw, 7, source="user_brought"),
        "young": _job(raw, 8, source="user_brought", seen="2999-01-01T00:00:00+00:00"),
    }
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'x')",
                    (uid, f"{uid}@example.com"))
        cur.execute(
            "INSERT INTO applications (user_id, job_id, status, stage, job_title, job_company, "
            "job_location, job_url, job_source, job_description_snapshot, snapshot_at, created_at, "
            "updated_at, last_event_at) VALUES (%s, %s, 'considering', 'considering', 't', 'c', '', "
            "'', 'reed', '', %s, %s, %s, %s)",
            (uid, ids["app"], OLD, OLD, OLD, OLD),
        )
        cur.execute("INSERT INTO tailored_usage (user_id, job_id) VALUES (%s, %s)", (uid, ids["usage"]))
        cur.execute(
            "INSERT INTO application_stage_history (job_id, user_id, to_stage) VALUES (%s, %s, 'applied')",
            (ids["history"], uid),
        )
    return ids


def _ids(raw) -> set[int]:
    with raw.cursor() as cur:
        cur.execute("SELECT id FROM jobs")
        return {int(r[0]) for r in cur.fetchall()}


def test_referencing_tables_are_discovered(orphan_db):
    with orphan_db.cursor() as cur:
        tables = set(doj.referencing_tables(cur))
    assert {"applications", "application_receipts", "application_stage_history",
            "tailored_documents", "tailored_usage"} <= tables
    assert "jobs" not in tables


def test_delete_orphan_jobs_dry_run_counts(orphan_db, capsys):
    ids = _seed(orphan_db)
    with orphan_db.cursor() as cur:
        s = doj.summarize(cur)
    assert s["total"] == 8
    assert s["referenced"] == 3
    assert s["orphans"] == 4
    assert s["too_young"] == 1
    assert dict(s["by_source"]) == {"reed": 2, "adzuna": 1, "user_brought": 1}
    assert s["oldest_orphan"] == OLD and s["newest_orphan"] == OLD

    assert doj.main([], conn=orphan_db) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out and "orphan jobs        : 4" in out
    assert "Title" not in out and "Co 4" not in out, "job text must never be printed"
    assert _ids(orphan_db) == set(ids.values()), "dry run wrote something"


def test_delete_orphan_jobs_keeps_referenced(orphan_db, capsys):
    ids = _seed(orphan_db)
    assert doj.main(["--execute", "--i-have-a-backup"], conn=orphan_db) == 0
    left = _ids(orphan_db)
    assert left == {ids["app"], ids["usage"], ids["history"], ids["young"]}
    # Idempotent: a second run finds nothing and deletes nothing.
    assert doj.delete_orphans(orphan_db) == 0
    assert _ids(orphan_db) == left


def test_delete_orphan_jobs_batches(orphan_db):
    _seed(orphan_db)
    assert doj.delete_orphans(orphan_db, batch_size=1) == 4


def test_delete_orphan_jobs_requires_backup_flag(orphan_db, capsys):
    ids = _seed(orphan_db)
    assert doj.main(["--execute"], conn=orphan_db) == 2
    assert "--i-have-a-backup" in capsys.readouterr().err
    assert doj.main(["--execute", "--dry-run", "--i-have-a-backup"], conn=orphan_db) == 2
    assert _ids(orphan_db) == set(ids.values()), "a refused run deleted rows"
