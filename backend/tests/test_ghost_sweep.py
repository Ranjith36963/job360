"""Step-3 B-14 — nightly_ghost_sweep ARQ task tests.

Tests the ghost-detection sweep task that advances staleness states for
stale jobs, without any live HTTP or Redis connections.

Covers:
  1. test_sweep_active_to_possibly_stale
  2. test_sweep_progression (full chain: active → possibly_stale → likely_stale)
  3. test_update_last_seen_resets_state
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.models import Job
from src.repositories.database import JobDatabase
from src.workers.tasks import nightly_ghost_sweep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOURCE = "reed"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mem_db():
    """In-memory JobDatabase fixture — isolated per test."""
    db = JobDatabase(":memory:")
    _run(db.init_db())
    yield db
    _run(db.close())


def _ctx(db_conn):
    """Build a minimal ARQ ctx dict backed by the raw aiosqlite connection."""
    return {"db": db_conn}


async def _insert_job_with_timestamps(
    db: JobDatabase,
    title: str,
    company: str,
    *,
    consecutive_misses: int = 0,
    last_seen_hours_ago: float = 0.0,
    staleness_state: str = "active",
) -> int:
    """Insert a job and manually set ghost-detection columns."""
    job = Job(
        title=title,
        company=company,
        apply_url=f"https://example.com/{title.replace(' ', '-')}",
        source=_SOURCE,
        date_found="2026-04-26T12:00:00+00:00",
    )
    await db.insert_job(job)

    # Compute last_seen_at based on hours_ago
    now = datetime.now(timezone.utc)
    last_seen = (now - timedelta(hours=last_seen_hours_ago)).isoformat()

    await db._conn.execute(
        """
        UPDATE jobs
           SET consecutive_misses = ?,
               last_seen_at = ?,
               staleness_state = ?
         WHERE title = ? AND company = ?
        """,
        (consecutive_misses, last_seen, staleness_state, title, company),
    )
    await db._conn.commit()

    cursor = await db._conn.execute(
        "SELECT id FROM jobs WHERE title = ? AND company = ? LIMIT 1",
        (title, company),
    )
    row = await cursor.fetchone()
    return row[0]


async def _get_staleness(db: JobDatabase, job_id: int) -> str:
    cursor = await db._conn.execute(
        "SELECT staleness_state FROM jobs WHERE id = ?",
        (job_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row else "unknown"


# ---------------------------------------------------------------------------
# Test 1: active → possibly_stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_active_to_possibly_stale(mem_db):
    """Job with ≥2 misses and ≥12h absence transitions active → possibly_stale."""
    job_id = await _insert_job_with_timestamps(
        mem_db,
        title="Stale Engineer",
        company="OldCorp",
        consecutive_misses=2,
        last_seen_hours_ago=13.0,  # 13h ago — above the 12h threshold
        staleness_state="active",
    )

    # Sanity check: starts as active
    assert await _get_staleness(mem_db, job_id) == "active"

    # Run the sweep with the raw aiosqlite connection (tasks.py uses ctx['db'])
    result = await nightly_ghost_sweep(_ctx(mem_db._conn))

    assert result["evaluated"] >= 1
    assert result["transitioned"] >= 1
    assert await _get_staleness(mem_db, job_id) == "possibly_stale"


# ---------------------------------------------------------------------------
# Test 2: full state progression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_progression(mem_db):
    """Full chain: active → possibly_stale → likely_stale across two sweeps.

    We set the state manually between sweeps to simulate what multiple calls
    to nightly_ghost_sweep would do over time (the sweep only writes the
    *new* evaluated state, not the intermediate ones).
    """
    # Step A — starts active, 2 misses, 13h ago → sweep should move to possibly_stale
    job_id = await _insert_job_with_timestamps(
        mem_db,
        title="Progression Job",
        company="StepCorp",
        consecutive_misses=2,
        last_seen_hours_ago=13.0,
        staleness_state="active",
    )

    result1 = await nightly_ghost_sweep(_ctx(mem_db._conn))
    assert result1["transitioned"] >= 1
    assert await _get_staleness(mem_db, job_id) == "possibly_stale"

    # Simulate more misses + time passing for the second sweep
    await mem_db._conn.execute(
        """
        UPDATE jobs
           SET consecutive_misses = 3,
               last_seen_at = ?,
               staleness_state = 'possibly_stale'
         WHERE id = ?
        """,
        ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), job_id),
    )
    await mem_db._conn.commit()

    result2 = await nightly_ghost_sweep(_ctx(mem_db._conn))
    assert result2["transitioned"] >= 1
    assert await _get_staleness(mem_db, job_id) == "likely_stale"


# ---------------------------------------------------------------------------
# Test 3: update_last_seen resets state — sweep does NOT advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_last_seen_resets_state(mem_db):
    """After update_last_seen() resets misses, sweep does NOT advance staleness."""
    job_id = await _insert_job_with_timestamps(
        mem_db,
        title="Resurrected Role",
        company="Phoenix Ltd",
        consecutive_misses=2,
        last_seen_hours_ago=13.0,
        staleness_state="active",
    )

    # Re-observe the job before the sweep — resets consecutive_misses + last_seen_at
    company_norm, title_norm = Job(
        title="Resurrected Role",
        company="Phoenix Ltd",
        apply_url="https://example.com/Resurrected-Role",
        source=_SOURCE,
        date_found="2026-04-26T12:00:00+00:00",
    ).normalized_key()
    await mem_db.update_last_seen((company_norm, title_norm))

    # Now run the sweep
    result = await nightly_ghost_sweep(_ctx(mem_db._conn))

    # State must still be active (or possibly_stale reset to active); NOT possibly_stale
    state_after = await _get_staleness(mem_db, job_id)
    assert state_after == "active", (
        f"Expected 'active' after re-observe + sweep, got '{state_after}'. " f"Sweep stats: {result}"
    )


# ---------------------------------------------------------------------------
# Test 4: confirmed_expired is sticky — sweep skips it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_skips_confirmed_expired(mem_db):
    """Jobs in confirmed_expired state are not touched by the nightly sweep."""
    job_id = await _insert_job_with_timestamps(
        mem_db,
        title="Dead Job",
        company="Gone Corp",
        consecutive_misses=10,
        last_seen_hours_ago=72.0,
        staleness_state="confirmed_expired",
    )

    result = await nightly_ghost_sweep(_ctx(mem_db._conn))

    # The sweep should not touch this row (confirmed_expired is excluded)
    assert result["transitioned"] == 0
    assert await _get_staleness(mem_db, job_id) == "confirmed_expired"
