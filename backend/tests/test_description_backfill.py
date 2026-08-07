"""BACKFILL phase of the enrichment_sweep cron (2026-08-07). See
workers/tasks.py::_backfill_thin_descriptions and
services/description_backfill.py for the full design.

Pins: highest-value-first selection, the DESCRIPTION_BACKFILL_PER_TICK
budget (including its 0=disabled kill switch), the per-source capability
flag (pre-filtered in SQL), the migration-0029 attempt counter (a job stops
being retried after MAX_BACKFILL_ATTEMPTS — NOT a padded description; see
the rejected-padding note below), per-row fault isolation (one bad row must
never abort the sweep), and a freshly-backfilled job reaching the
enrichment phase in the SAME tick.

REJECTED-APPROACH PIN: an earlier version of this phase padded a
still-thin description with trailing spaces to escape the selection floor
without a migration. That was rejected in manager review — coverage.py's
skill-text predicate counts ANY description over 200 chars as real
coverage with no whitespace check, so padding manufactured a FALSE
"we understand this job" signal.
test_backfill_whitespace_fetch_result_is_not_treated_as_success pins that a
whitespace-only fetch result can never be mistaken for success, and no test
in this file ever asserts on a padded description.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from aioresponses import aioresponses

from migrations import runner
from src.models import Job
from src.repositories import pgsync
from src.repositories.database import JobDatabase

_NOW = datetime.now(timezone.utc).isoformat()

# Real prose, well over the 200-char selection floor once tag-stripped.
_LONG_TEXT = "Deep learning research role using PyTorch and distributed training. " * 5


async def _full_db(path: str) -> JobDatabase:
    db = JobDatabase(path)
    await db.init_db()
    await db.close()
    await runner.up(path)
    db = JobDatabase(path)
    await db.init_db()
    return db


def _seed_user(db_path: str) -> str:
    user_id = str(uuid.uuid4())
    with pgsync.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?,?,?)",
            (user_id, f"{user_id}@t.example", "hash"),
        )
    return user_id


def _mk_job(idx: int, source: str, apply_url: str, description: str = "short.") -> Job:
    # title/company vary per idx — normalized_key() dedups on (company,
    # title), and insert_job upserts on collision, so identical values
    # across rows in the same test would silently collapse to one job.
    return Job(
        title=f"Engineer {idx}", company=f"Co{idx}", apply_url=apply_url,
        source=source, date_found=_NOW, location="London, UK",
        description=description,
    )


async def _seed_feed_row(conn, user_id: str, job_id: int, score: int) -> None:
    await conn.execute(
        "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
        "VALUES (?,?,?,?,'active',?,?)",
        (user_id, job_id, score, "older", _NOW, _NOW),
    )


async def _attempts(conn, job_id: int) -> int:
    cur = await conn.execute(
        "SELECT coalesce(description_backfill_attempts, 0) FROM jobs WHERE id = ?", (job_id,)
    )
    return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_backfill_selects_highest_value_within_budget(tmp_path, monkeypatch):
    """Budget=2 over 3 candidates must pick the two HIGHEST-value jobs, and
    leave the lowest-value one alone (selection order + budget respected).
    Deliberately does NOT touch ENGINE2_ENABLED — backfill is independent
    of the enrichment gate (2026-08-07 manager review)."""
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "DESCRIPTION_BACKFILL_PER_TICK", 2, raising=False)

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        for i in range(3):
            await db.insert_job(_mk_job(
                i, "greenhouse", f"https://boards.greenhouse.io/deepmind/jobs/{100 + i}",
            ))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t.db"))
        cur = await conn.execute("SELECT id FROM jobs ORDER BY id")
        ids = [r[0] for r in await cur.fetchall()]
        for jid, score in zip(ids, (10, 50, 90)):
            await _seed_feed_row(conn, user_id, jid, score)
        await db.commit()

        with aioresponses() as m:
            m.get(
                re.compile(r"https://boards-api\.greenhouse\.io/v1/boards/deepmind/jobs/\d+\?content=true"),
                payload={"content": _LONG_TEXT},
                repeat=True,
            )
            result = await tasks_mod.enrichment_sweep({"db": conn})

        assert result["reason"] == "e2_off", result  # E2 is off by default in tests; backfill still ran
        assert result["attempted"] == 2, result
        assert result["filled"] == 2, result

        cur = await conn.execute("SELECT id, description FROM jobs ORDER BY id")
        rows = {r[0]: r[1] for r in await cur.fetchall()}
        assert "Deep learning research" in rows[ids[1]], "score-50 job must be filled"
        assert "Deep learning research" in rows[ids[2]], "score-90 job must be filled"
        assert rows[ids[0]] == "short.", "lowest-value job must be left untouched (budget=2)"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backfill_skips_disallowed_source(tmp_path, monkeypatch):
    """LinkedIn is OUT by capability flag — pre-filtered in SQL, so it must
    never even be selected, let alone fetched."""
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "DESCRIPTION_BACKFILL_PER_TICK", 10, raising=False)

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        await db.insert_job(_mk_job(0, "linkedin", "https://www.linkedin.com/jobs/view/999"))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t.db"))
        cur = await conn.execute("SELECT id FROM jobs LIMIT 1")
        jid = (await cur.fetchone())[0]
        await _seed_feed_row(conn, user_id, jid, 90)
        await db.commit()

        # No routes registered — aioresponses raises on ANY real HTTP attempt,
        # so this also proves the dispatch never touches the network.
        with aioresponses():
            result = await tasks_mod.enrichment_sweep({"db": conn})

        assert result["attempted"] == 0, result
        assert result["skipped_by_capability"] == 0, "excluded by the SQL IN-clause, never even selected"
        cur = await conn.execute("SELECT description FROM jobs WHERE id = ?", (jid,))
        assert (await cur.fetchone())[0] == "short."
        assert await _attempts(conn, jid) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backfill_attempt_counter_stops_retrying_after_cap(tmp_path, monkeypatch):
    """A job whose fetch can never improve (unmapped Workday tenant — no
    WORKDAY_COMPANIES entry, so no HTTP call is even possible) must stop
    being selected once description_backfill_attempts reaches
    MAX_BACKFILL_ATTEMPTS (3) — a REAL counter (migration 0029), not a
    padded description. description must stay untouched throughout."""
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod
    from src.services.description_backfill import MAX_BACKFILL_ATTEMPTS

    monkeypatch.setattr(settings_mod, "DESCRIPTION_BACKFILL_PER_TICK", 10, raising=False)

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        await db.insert_job(_mk_job(
            0, "workday", "https://nomatch.wd1.myworkdayjobs.com/en-US/job/London/X_R1",
        ))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t.db"))
        cur = await conn.execute("SELECT id FROM jobs LIMIT 1")
        jid = (await cur.fetchone())[0]
        await _seed_feed_row(conn, user_id, jid, 90)
        await db.commit()

        for expected_attempt in range(1, MAX_BACKFILL_ATTEMPTS + 1):
            with aioresponses():
                result = await tasks_mod.enrichment_sweep({"db": conn})
            assert result["attempted"] == 1, (expected_attempt, result)
            assert result["still_thin"] == 1, (expected_attempt, result)
            assert await _attempts(conn, jid) == expected_attempt

            cur = await conn.execute("SELECT description FROM jobs WHERE id = ?", (jid,))
            assert (await cur.fetchone())[0] == "short.", "description must NEVER be padded/modified"

        # One more tick, past the cap: no longer selected at all.
        with aioresponses():
            result = await tasks_mod.enrichment_sweep({"db": conn})
        assert result["attempted"] == 0, result
        assert await _attempts(conn, jid) == MAX_BACKFILL_ATTEMPTS, "counter must stop incrementing too"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backfill_whitespace_fetch_result_is_not_treated_as_success(tmp_path, monkeypatch):
    """PIN: the exact bug the padding approach was rejected for. A fetch that
    returns a long WHITESPACE-ONLY string must not be counted as 'filled',
    must not overwrite the real (if short) description, and must still bump
    the real attempt counter like any other non-improving attempt."""
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "DESCRIPTION_BACKFILL_PER_TICK", 10, raising=False)

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        await db.insert_job(_mk_job(
            0, "greenhouse", "https://boards.greenhouse.io/deepmind/jobs/555", description="short.",
        ))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t.db"))
        cur = await conn.execute("SELECT id FROM jobs LIMIT 1")
        jid = (await cur.fetchone())[0]
        await _seed_feed_row(conn, user_id, jid, 90)
        await db.commit()

        async def _whitespace_only(*a, **kw):
            return " " * 250  # well over MIN_DESCRIPTION_CHARS, but pure whitespace

        with patch("src.services.description_backfill.fetch_description", new=_whitespace_only):
            result = await tasks_mod.enrichment_sweep({"db": conn})

        assert result["filled"] == 0, "whitespace-only text must NEVER count as a successful fill"
        assert result["still_thin"] == 1, result

        cur = await conn.execute("SELECT description FROM jobs WHERE id = ?", (jid,))
        assert (await cur.fetchone())[0] == "short.", (
            "the real (if short) description must be preserved, not overwritten with whitespace"
        )
        assert await _attempts(conn, jid) == 1, "a whitespace result still counts as an attempt"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backfill_fetch_exception_does_not_abort_sweep(tmp_path, monkeypatch):
    """One poison row (fetch raises) must not stop the sweep from finishing
    or from processing the other candidate rows, and must still bump the
    attempt counter so a systemically-broken row is eventually capped."""
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "DESCRIPTION_BACKFILL_PER_TICK", 10, raising=False)

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        for i in range(2):
            await db.insert_job(_mk_job(
                i, "greenhouse", f"https://boards.greenhouse.io/deepmind/jobs/{200 + i}",
            ))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t.db"))
        cur = await conn.execute("SELECT id FROM jobs ORDER BY id")
        ids = [r[0] for r in await cur.fetchall()]
        for jid in ids:
            await _seed_feed_row(conn, user_id, jid, 90)
        await db.commit()

        async def _boom(*a, **kw):
            raise RuntimeError("simulated network failure")

        with patch("src.services.description_backfill.fetch_description", new=_boom):
            result = await tasks_mod.enrichment_sweep({"db": conn})

        assert result["attempted"] == 2, result
        assert result["errors"] == 2, result

        cur = await conn.execute("SELECT id, description FROM jobs ORDER BY id")
        for jid, desc in await cur.fetchall():
            assert desc == "short.", "an errored row must be left untouched, not corrupted"
            assert await _attempts(conn, jid) == 1, "an exception still counts as an attempt"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backfill_disabled_when_budget_zero(tmp_path, monkeypatch):
    """DESCRIPTION_BACKFILL_PER_TICK=0 must disable the phase entirely — no
    SELECT, no fetch, no DB write, no attempt-counter bump."""
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "DESCRIPTION_BACKFILL_PER_TICK", 0, raising=False)

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        await db.insert_job(_mk_job(0, "greenhouse", "https://boards.greenhouse.io/deepmind/jobs/999"))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t.db"))
        cur = await conn.execute("SELECT id FROM jobs LIMIT 1")
        jid = (await cur.fetchone())[0]
        await _seed_feed_row(conn, user_id, jid, 90)
        await db.commit()

        with aioresponses():  # no routes registered — any HTTP call fails loudly
            result = await tasks_mod.enrichment_sweep({"db": conn})

        assert result["attempted"] == 0, result
        assert result["filled"] == 0, result
        assert result["still_thin"] == 0, result
        assert result["skipped_by_capability"] == 0, result
        assert result["errors"] == 0, result

        cur = await conn.execute("SELECT description FROM jobs WHERE id = ?", (jid,))
        assert (await cur.fetchone())[0] == "short."
        assert await _attempts(conn, jid) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backfilled_job_reaches_enrichment_phase_same_tick(tmp_path, monkeypatch):
    """A job whose description the BACKFILL phase just filled must be picked
    up by the REPAIR phase in the SAME enrichment_sweep call — not the next
    tick — because backfill runs first and commits before the repair query.
    This scenario DOES need E2 on (repair/missing-coverage are still gated)."""
    import src.core.settings as settings_mod
    import src.workers.tasks as tasks_mod

    monkeypatch.setattr(settings_mod, "ENGINE2_ENABLED", True, raising=False)
    monkeypatch.setattr(settings_mod, "DESCRIPTION_BACKFILL_PER_TICK", 10, raising=False)
    monkeypatch.setenv("ENRICHMENT_SWEEP_PER_TICK", "5")

    db = await _full_db(str(tmp_path / "t.db"))
    try:
        await db.insert_job(_mk_job(0, "greenhouse", "https://boards.greenhouse.io/deepmind/jobs/321"))
        conn = db._db
        user_id = _seed_user(str(tmp_path / "t.db"))
        cur = await conn.execute("SELECT id FROM jobs LIMIT 1")
        jid = (await cur.fetchone())[0]
        await _seed_feed_row(conn, user_id, jid, 90)
        # A hollow enrichment row — the empty-text artifact the REPAIR phase
        # targets (same shape as test_enrichment_sweep_task.py's repair test).
        await conn.execute(
            "INSERT INTO job_enrichment(job_id, title_canonical, category, visa_sponsorship, "
            "seniority, workplace_type, enriched_at) VALUES (?,?,?,?,?,?,?)",
            (jid, "Engineer", "software_engineering", "unknown", "unknown", "unknown", _NOW),
        )
        await db.commit()

        calls = []

        async def fake_enrich_batch(jobs, semaphore_limit=3, conn=None, skip_existing=True, **kw):
            calls.append((sorted(j.id for j in jobs), skip_existing))
            return [object()] * len(jobs)

        with aioresponses() as m:
            m.get(
                re.compile(r"https://boards-api\.greenhouse\.io/v1/boards/deepmind/jobs/\d+\?content=true"),
                payload={"content": _LONG_TEXT},
            )
            with patch("src.services.job_enrichment.enrich_batch", new=fake_enrich_batch):
                result = await tasks_mod.enrichment_sweep({"db": conn})

        assert result["filled"] == 1, result
        cur = await conn.execute("SELECT description FROM jobs WHERE id = ?", (jid,))
        assert "Deep learning research" in (await cur.fetchone())[0]

        # REPAIR must have run in THIS SAME call, skip_existing=False (overwrite).
        assert any(ids == [jid] and skip is False for ids, skip in calls), calls
    finally:
        await db.close()
