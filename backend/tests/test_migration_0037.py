"""Migration 0037 — the application spine fold (spec.md §Migration fold).

Frozen — not edited to make the implementation pass (see test_application_spine.py's
module docstring for the same contract). Migration `0037_application_spine` does not
exist on this branch yet, so every test below is expected to FAIL: either the new
tables/columns are missing, or `runner.up()` simply never reaches a stem that isn't
on disk. That is the intended RED state.

The fold's whole argument is COUNTS: nothing already in `applications`,
`application_stage_history`, `application_receipts` or `tailored_documents` is
moved or deleted — only copied forward into the new `application_events` /
`application_artifacts` tables and the new `applications` columns. Seeded data
here is chosen to exercise every step of the fold (§Migration fold, steps 2-7).
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from migrations import runner
from src.repositories import pg as _pg

_NOW = "2026-01-01T00:00:00Z"
_USER = "fold-test-user"
_ORPHAN_JOB_ID = 999_001

# The six legacy stages a real `applications` row can hold
# (`routes/pipeline.py::_VALID_STAGES`) — the fold's status backfill (R4,
# reversed) must map every one of them.
_STAGES = ("applied", "outreach", "interview", "offer", "rejected", "ghosted")


async def _seed_user(db: _pg.Connection) -> None:
    await db.execute(
        "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (_USER, "fold-test@example.com", "!", _NOW),
    )


async def _seed_applications(db: _pg.Connection) -> dict[str, int]:
    """One application per legacy stage; returns {stage: job_id}."""
    job_ids: dict[str, int] = {}
    for i, stage in enumerate(_STAGES):
        job_id = 900 + i
        job_ids[stage] = job_id
        await db.execute(
            "INSERT INTO applications (user_id, job_id, stage, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, '', ?, ?)",
            (_USER, job_id, stage, _NOW, _NOW),
        )
    return job_ids


async def _seed_stage_history(db: _pg.Connection, job_id: int) -> None:
    """Two transitions on one application — exercises the 1-row-per-history-row fold."""
    await db.execute(
        "INSERT INTO application_stage_history (job_id, user_id, from_stage, to_stage, transitioned_at, notes) "
        "VALUES (?, ?, NULL, 'applied', ?, '')",
        (job_id, _USER, _NOW),
    )
    await db.execute(
        "INSERT INTO application_stage_history (job_id, user_id, from_stage, to_stage, transitioned_at, notes) "
        "VALUES (?, ?, 'applied', 'interview', ?, 'moved forward')",
        (job_id, _USER, _NOW),
    )


async def _seed_receipts(db: _pg.Connection, matched_job_id: int, orphan_job_id: int) -> None:
    for job_id, note in ((matched_job_id, "matched receipt"), (orphan_job_id, "orphan receipt")):
        await db.execute(
            "INSERT INTO application_receipts "
            "(user_id, job_id, sent_at, job_title, job_company, job_location, job_apply_url, "
            " job_source, job_description, cv_text, cv_origin, cover_letter_text, cover_letter_origin, "
            " profile_version, channel, note, created_at) "
            "VALUES (?, ?, ?, 'Data Engineer', 'Northwind', 'Remote', 'https://x.test/1', 'test', "
            " 'desc', NULL, NULL, NULL, NULL, NULL, '', ?, ?)",
            (_USER, job_id, _NOW, note, _NOW),
        )


async def _seed_tailored_documents(db: _pg.Connection, polished_job_id: int, draft_only_job_id: int) -> None:
    # A polish exists — this row must become TWO artifact versions.
    await db.execute(
        "INSERT INTO tailored_documents "
        "(user_id, job_id, doc_kind, ai_draft, polished, status, model, profile_version, created_at, updated_at) "
        "VALUES (?, ?, 'cv', 'draft text', 'polished text — the edit', 'kept', 'test-model', 1, ?, ?)",
        (_USER, polished_job_id, _NOW, _NOW),
    )
    # No polish — this row must become exactly ONE artifact version.
    await db.execute(
        "INSERT INTO tailored_documents "
        "(user_id, job_id, doc_kind, ai_draft, polished, status, model, profile_version, created_at, updated_at) "
        "VALUES (?, ?, 'cover_letter', 'cover draft only', NULL, 'draft', 'test-model', 1, ?, ?)",
        (_USER, draft_only_job_id, _NOW, _NOW),
    )


async def _counts(db_path: str) -> dict[str, int]:
    tables = ("applications", "application_stage_history", "application_receipts", "tailored_documents")
    out: dict[str, int] = {}
    async with _pg.connect(db_path) as db:
        for t in tables:
            cur = await db.execute(f"SELECT COUNT(*) FROM {t}")  # noqa: S608 — fixed table-name tuple, not user input
            row = await cur.fetchone()
            out[t] = int(row[0])
    return out


async def _has_table(db_path: str, name: str) -> bool:
    async with _pg.connect(db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,))
        return (await cur.fetchone()) is not None


@pytest.fixture
def pre_fold_db_path(tmp_path):
    """Full schema, every migration EXCEPT the fold itself.

    Target pinned to ``0036_oauth`` (today's newest migration) rather than
    left open, so this fixture seeds legacy data BEFORE 0037 exists — the
    moment 0037.up.sql lands, ``runner.up(db_path)`` in each test below is
    what carries it forward, and this fixture's seed data is exactly the
    "before" half of the fold's own count table.
    """
    db_path = str(tmp_path / "pre_fold.db")

    async def _bootstrap() -> None:
        from src.repositories.database import JobDatabase

        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
        await runner.up(db_path, target="0036_oauth")

        async with _pg.connect(db_path) as conn:
            conn.row_factory = _pg.Row
            await _seed_user(conn)
            job_ids = await _seed_applications(conn)
            await _seed_stage_history(conn, job_ids["interview"])
            await _seed_receipts(conn, matched_job_id=job_ids["applied"], orphan_job_id=_ORPHAN_JOB_ID)
            await _seed_tailored_documents(
                conn, polished_job_id=job_ids["applied"], draft_only_job_id=job_ids["outreach"]
            )
            await conn.commit()

    asyncio.run(_bootstrap())
    yield db_path
    with contextlib.suppress(Exception):
        asyncio.run(_pg.drop_schema(db_path))


_STEM = "0037_application_spine"


@pytest.mark.asyncio
async def test_up_down_up_is_clean(pre_fold_db_path):
    # STOP AT 0037. `runner.down()` always reverses the NEWEST applied stem, so
    # a bare `up()` here would make this test assert about whatever migration
    # landed last — it broke the moment 0038 was added. Naming the target keeps
    # the test about 0037 forever (feedback: a test must not encode the merge
    # queue).
    applied = await runner.up(pre_fold_db_path, target=_STEM)
    assert _STEM in applied, f"0037 never applied: {applied}"

    for table in ("application_events", "application_artifacts"):
        assert await _has_table(pre_fold_db_path, table), f"{table} missing after up()"

    reverted = await runner.down(pre_fold_db_path)
    assert reverted == _STEM
    for table in ("application_events", "application_artifacts"):
        assert not await _has_table(pre_fold_db_path, table), f"{table} still present after down()"

    reapplied = await runner.up(pre_fold_db_path, target=_STEM)
    assert reapplied == [_STEM]
    for table in ("application_events", "application_artifacts"):
        assert await _has_table(pre_fold_db_path, table), f"{table} missing after re-up()"


@pytest.mark.asyncio
async def test_no_legacy_row_is_lost(pre_fold_db_path):
    before = await _counts(pre_fold_db_path)
    await runner.up(pre_fold_db_path)
    after = await _counts(pre_fold_db_path)

    assert after["applications"] >= before["applications"], "the fold may only ADD applications rows (orphans)"
    assert after["application_stage_history"] == before["application_stage_history"]
    assert after["application_receipts"] == before["application_receipts"]
    assert after["tailored_documents"] == before["tailored_documents"]

    async with _pg.connect(pre_fold_db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM application_receipts WHERE application_id IS NULL")
        row = await cur.fetchone()
    assert row[0] == 0, "every receipt must come out with a non-NULL application_id"


@pytest.mark.asyncio
async def test_stage_history_becomes_events(pre_fold_db_path):
    async with _pg.connect(pre_fold_db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM application_stage_history")
        history_count = (await cur.fetchone())[0]
    assert history_count > 0, "test fixture sanity: seed data must include stage-history rows"

    await runner.up(pre_fold_db_path)

    async with _pg.connect(pre_fold_db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM application_events WHERE recorded_by = 'migration:0014_history'"
        )
        migrated_count = (await cur.fetchone())[0]
    assert migrated_count == history_count, "one event per stage-history row, no more, no fewer"


@pytest.mark.asyncio
async def test_receipts_get_an_application_id_and_an_event(pre_fold_db_path):
    async with _pg.connect(pre_fold_db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM application_receipts")
        receipt_count = (await cur.fetchone())[0]
    assert receipt_count > 0, "test fixture sanity: seed data must include receipts"

    await runner.up(pre_fold_db_path)

    async with _pg.connect(pre_fold_db_path) as db:
        db.row_factory = _pg.Row
        cur = await db.execute("SELECT application_id FROM application_receipts")
        app_ids = [r["application_id"] for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT COUNT(*) FROM application_events WHERE recorded_by = 'migration:0034_receipts'"
        )
        migrated_events = (await cur.fetchone())[0]

    assert all(a is not None for a in app_ids), "every receipt must be backfilled with an application_id"
    assert migrated_events == receipt_count, "one 'applied' event per receipt, no more, no fewer"


@pytest.mark.asyncio
async def test_a_tailored_doc_with_a_polish_becomes_two_versions(pre_fold_db_path):
    await runner.up(pre_fold_db_path)

    async with _pg.connect(pre_fold_db_path) as db:
        db.row_factory = _pg.Row
        cur = await db.execute(
            "SELECT version_no, made_by, text FROM application_artifacts "
            "WHERE user_id = ? AND kind = 'cv' ORDER BY version_no",
            (_USER,),
        )
        cv_rows = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT version_no FROM application_artifacts WHERE user_id = ? AND kind = 'cover_letter'",
            (_USER,),
        )
        cl_rows = [dict(r) for r in await cur.fetchall()]

    assert [r["version_no"] for r in cv_rows] == [1, 2], "a draft + a polish must fold into TWO versions"
    assert cv_rows[0]["made_by"] == "migration:0023_tailored"
    assert cv_rows[0]["text"] == "draft text", "the draft must survive as its own version, not be overwritten"
    assert cv_rows[1]["made_by"] == "human"
    assert cv_rows[1]["text"] == "polished text — the edit"

    assert [r["version_no"] for r in cl_rows] == [1], "a draft-only doc must fold into exactly ONE version"


@pytest.mark.asyncio
async def test_an_orphan_receipt_gets_its_application_row(pre_fold_db_path):
    await runner.up(pre_fold_db_path)

    async with _pg.connect(pre_fold_db_path) as db:
        db.row_factory = _pg.Row
        cur = await db.execute(
            "SELECT status FROM applications WHERE user_id = ? AND job_id = ?", (_USER, _ORPHAN_JOB_ID)
        )
        row = await cur.fetchone()
    assert row is not None, "a (user, job) present only in receipts must get its own applications row"
    assert row["status"] == "applied", "a receipt means the user got at least that far (spec step 2)"


@pytest.mark.asyncio
async def test_status_backfill_maps_every_legacy_stage(pre_fold_db_path):
    expected = {
        "applied": "applied",
        "outreach": "applied",
        "interview": "interview_scheduled",
        "offer": "offer",
        "rejected": "rejected",
        "ghosted": "ghosted",
    }
    await runner.up(pre_fold_db_path)

    async with _pg.connect(pre_fold_db_path) as db:
        db.row_factory = _pg.Row
        for stage, want in expected.items():
            job_id = 900 + _STAGES.index(stage)
            cur = await db.execute(
                "SELECT status, stage FROM applications WHERE user_id = ? AND job_id = ?", (_USER, job_id)
            )
            row = await cur.fetchone()
            assert row is not None, f"seeded stage {stage!r} row went missing"
            assert row["status"] == want, f"stage {stage!r} backfilled to {row['status']!r}, want {want!r}"
            assert row["stage"] == stage, "the fold must never rewrite the legacy `stage` column"
