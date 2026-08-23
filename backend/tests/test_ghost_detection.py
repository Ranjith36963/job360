"""Tests for the ghost-listing state machine (Pillar 3 Batch 1).

See docs/product/research/pillar_3_batch_1.md §3 "Ghost detection state machine".
"""
import asyncio

import pytest

from src.services.ghost_detection import (
    StalenessState,
    evaluate_job_state,
    should_exclude_from_24h,
    transition,
)

# ---- transition() — pure state function ----


@pytest.mark.parametrize(
    "misses,age_hours,expected",
    [
        (0, 0.0, StalenessState.ACTIVE),
        (0, 240.0, StalenessState.ACTIVE),       # 0 misses always active, regardless of age
        (1, 2.0, StalenessState.ACTIVE),         # 1 miss is noise
        (1, 24.0, StalenessState.ACTIVE),
        (2, 6.0, StalenessState.ACTIVE),         # <12h of absence — still noise
        (2, 12.0, StalenessState.POSSIBLY_STALE),
        (2, 18.0, StalenessState.POSSIBLY_STALE),
        (3, 12.0, StalenessState.POSSIBLY_STALE),  # 3 misses but still <24h
        (3, 24.0, StalenessState.LIKELY_STALE),
        (5, 48.0, StalenessState.LIKELY_STALE),
        (10, 240.0, StalenessState.LIKELY_STALE),
    ],
)
def test_transition_states(misses, age_hours, expected):
    assert transition(misses, age_hours) == expected


# ---- should_exclude_from_24h ----


def test_excludes_likely_stale_and_confirmed():
    assert should_exclude_from_24h(StalenessState.LIKELY_STALE) is True
    assert should_exclude_from_24h(StalenessState.CONFIRMED_EXPIRED) is True


def test_does_not_exclude_active_or_possibly_stale():
    assert should_exclude_from_24h(StalenessState.ACTIVE) is False
    assert should_exclude_from_24h(StalenessState.POSSIBLY_STALE) is False


# ---- evaluate_job_state — integrates with DB row ----


def test_evaluate_job_state_uses_misses_and_last_seen():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    # 3 misses, last_seen 25h ago → LIKELY_STALE
    row = {
        "consecutive_misses": 3,
        "last_seen_at": (now - timedelta(hours=25)).isoformat(),
        "staleness_state": "active",
    }
    assert evaluate_job_state(row, now=now) == StalenessState.LIKELY_STALE


def test_evaluate_job_state_confirmed_expired_sticks():
    """Once a job is confirmed_expired (e.g. 404 on direct URL check), it stays that way."""
    row = {
        "consecutive_misses": 1,
        "last_seen_at": "2020-01-01T00:00:00+00:00",
        "staleness_state": "confirmed_expired",
    }
    assert evaluate_job_state(row) == StalenessState.CONFIRMED_EXPIRED


def test_evaluate_job_state_missing_last_seen_treats_as_active():
    """If last_seen_at is None (fresh job just inserted), keep as active."""
    row = {
        "consecutive_misses": 0,
        "last_seen_at": None,
        "staleness_state": "active",
    }
    assert evaluate_job_state(row) == StalenessState.ACTIVE


# ---- Integration with JobDatabase.mark_missed_for_source ----


@pytest.fixture
def db():
    from src.repositories.database import JobDatabase
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def test_mark_missed_increments_counter(db):
    from src.models import Job

    async def _run():
        job_a = Job(title="A", company="X", apply_url="https://a", source="reed", date_found="2026-04-18T00:00:00+00:00")
        job_b = Job(title="B", company="Y", apply_url="https://b", source="reed", date_found="2026-04-18T00:00:00+00:00")
        await db.insert_job(job_a)
        await db.insert_job(job_b)
        # Second scrape sees only A — B should be marked missed once
        seen = {job_a.normalized_key()}
        missed = await db.mark_missed_for_source("reed", seen)
        assert missed == 1
        # Third scrape sees neither — both should bump again
        missed2 = await db.mark_missed_for_source("reed", set())
        assert missed2 == 2

    asyncio.run(_run())


def test_update_last_seen_resets_misses(db):
    from src.models import Job

    async def _run():
        job = Job(title="A", company="X", apply_url="https://a", source="reed", date_found="2026-04-18T00:00:00+00:00")
        await db.insert_job(job)
        # Simulate two misses
        await db.mark_missed_for_source("reed", set())
        await db.mark_missed_for_source("reed", set())
        # Row now has consecutive_misses = 2
        cursor = await db._conn.execute(
            "SELECT consecutive_misses, staleness_state FROM jobs WHERE apply_url = ?",
            (job.apply_url,),
        )
        row = await cursor.fetchone()
        assert row[0] == 2
        # Re-observe
        await db.update_last_seen(job.normalized_key())
        cursor = await db._conn.execute(
            "SELECT consecutive_misses, staleness_state FROM jobs WHERE apply_url = ?",
            (job.apply_url,),
        )
        row = await cursor.fetchone()
        assert row[0] == 0
        assert row[1] == "active"

    asyncio.run(_run())


# ---- Integration with _ghost_detection_pass in src.main ----


class _FakeSource:
    def __init__(self, name):
        self.name = name


def test_pass_marks_absent_jobs_and_resets_observed(db):
    from src.main import _ghost_detection_pass
    from src.models import Job

    async def _run():
        job_a = Job(title="A", company="X", apply_url="https://a", source="reed",
                    date_found="2026-04-18T00:00:00+00:00")
        job_b = Job(title="B", company="Y", apply_url="https://b", source="reed",
                    date_found="2026-04-18T00:00:00+00:00")
        await db.insert_job(job_a)
        await db.insert_job(job_b)
        # Next scrape sees only A
        source = _FakeSource("reed")
        result = [job_a]
        history = {"reed": [2, 2, 2]}  # rolling avg 2; current 1 = 50%, below 70% threshold
        missed = await _ghost_detection_pass(db, [source], [result], history)
        # Gate kicks in — NO absence sweep
        assert missed == {}
    asyncio.run(_run())


def test_pass_runs_sweep_when_scrape_is_healthy(db):
    from src.main import _ghost_detection_pass
    from src.models import Job

    async def _run():
        job_a = Job(title="A", company="X", apply_url="https://a", source="reed",
                    date_found="2026-04-18T00:00:00+00:00")
        job_b = Job(title="B", company="Y", apply_url="https://b", source="reed",
                    date_found="2026-04-18T00:00:00+00:00")
        await db.insert_job(job_a)
        await db.insert_job(job_b)
        source = _FakeSource("reed")
        # Current scrape sees both → 100% of 2-job rolling avg → well above gate
        history = {"reed": [2, 2, 2]}
        missed = await _ghost_detection_pass(db, [source], [[job_a, job_b]], history)
        assert missed.get("reed", 0) == 0

        # Now A disappears; current = 1; avg still 2; 50% < 70% → skip
        history2 = {"reed": [2, 2, 1]}  # skewed avg = 1.66, 1/1.66 = 0.60 < 0.7
        missed = await _ghost_detection_pass(db, [source], [[job_a]], history2)
        assert missed == {}

        # Empty rolling window → no gate → sweep runs
        missed = await _ghost_detection_pass(db, [source], [[job_a]], {})
        assert missed.get("reed", 0) == 1
    asyncio.run(_run())


def test_pass_skips_failed_sources(db):
    from src.main import _ghost_detection_pass
    from src.models import Job

    async def _run():
        job_a = Job(title="A", company="X", apply_url="https://a", source="reed",
                    date_found="2026-04-18T00:00:00+00:00")
        await db.insert_job(job_a)
        source = _FakeSource("reed")
        # Exception result → must NOT be treated as "job disappeared"
        missed = await _ghost_detection_pass(db, [source], [RuntimeError("oops")], {})
        assert missed == {}
        # None result → same
        missed = await _ghost_detection_pass(db, [source], [None], {})
        assert missed == {}
    asyncio.run(_run())


# --- M6: NULL last_seen_at must not permanently short-circuit to ACTIVE ---

from datetime import datetime, timedelta, timezone  # noqa: E402


def test_m6_null_last_seen_with_misses_and_no_timestamp_goes_stale():
    """M6: a row with NULL last_seen_at AND no first_seen_at, but many misses,
    used to be forced ACTIVE forever (never swept). It must now transition on
    consecutive_misses alone."""
    row = {"staleness_state": "active", "consecutive_misses": 3, "last_seen_at": None}
    assert evaluate_job_state(row) == StalenessState.LIKELY_STALE
    row2 = {"staleness_state": "active", "consecutive_misses": 2, "last_seen_at": None}
    assert evaluate_job_state(row2) == StalenessState.POSSIBLY_STALE


def test_m6_null_last_seen_falls_back_to_first_seen_at():
    """When last_seen_at is NULL, first_seen_at is used as the age proxy."""
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    row = {
        "staleness_state": "active",
        "consecutive_misses": 3,
        "last_seen_at": None,
        "first_seen_at": old,
    }
    # 3 misses + 48h old (via first_seen fallback) => likely stale
    assert evaluate_job_state(row) == StalenessState.LIKELY_STALE


def test_m6_brand_new_job_with_null_last_seen_stays_active():
    """A just-discovered job (recent first_seen_at, NULL last_seen_at, few misses)
    must NOT be wrongly marked stale by the fallback."""
    fresh = datetime.now(timezone.utc).isoformat()
    row = {
        "staleness_state": "active",
        "consecutive_misses": 0,
        "last_seen_at": None,
        "first_seen_at": fresh,
    }
    assert evaluate_job_state(row) == StalenessState.ACTIVE
