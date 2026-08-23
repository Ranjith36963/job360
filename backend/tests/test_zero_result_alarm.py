"""Zero-result alarm: catch a source that used to work and silently stopped.

The failure this guards against is silent by construction — a 404 makes
_get_json return None, a renamed XML wrapper tag makes the parse loop never
run. The source returns [], nothing raises, the circuit breaker never trips.
nhs_jobs and successfactors each sat at zero for months exactly this way.

The alarm reports REGRESSIONS only. A keyed source with no API key, or a
permanently-dead upstream, is always zero — alarming on those every run is
how an alert becomes noise and stops being read.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.repositories.database import JobDatabase


# Defined locally on purpose. Importing a fixture from another test module
# double-imports it and breaks this repo's per-test Postgres schema isolation.
@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _seed(db: JobDatabase, rows: list[tuple[float, dict]]) -> None:
    """rows = [(hours_ago, {source: count}), ...]

    Raw INSERT rather than log_run() because these tests need control over the
    timestamp — log_run() always stamps "now", which cannot express "this run
    happened before the window".
    """
    async def _go():
        for hours_ago, per_source in rows:
            await db._db.execute(
                "INSERT INTO run_log (timestamp, total_found, new_jobs, "
                "sources_queried, per_source) VALUES (?, ?, ?, ?, ?)",
                (_iso(hours_ago), sum(per_source.values()), 0,
                 len(per_source), json.dumps(per_source)),
            )
        await db._db.commit()
    asyncio.run(_go())


def test_flags_a_source_that_worked_then_went_to_zero(db):
    _seed(db, [
        (100, {"greenhouse": 500, "reed": 20}),   # before the window: worked
        (30, {"greenhouse": 0, "reed": 18}),      # in window: dead
        (5, {"greenhouse": 0, "reed": 22}),       # in window: dead
    ])
    dead = asyncio.run(db.get_silently_dead_sources(hours=48))
    assert "greenhouse" in dead, "a source that worked then stopped must alarm"
    assert dead["greenhouse"] == 500, "reports the historical peak"
    assert "reed" not in dead, "a still-working source must not alarm"


def test_never_flags_a_source_that_has_never_worked(db):
    """The cry-wolf guard: an unconfigured keyed source is always zero."""
    _seed(db, [
        (100, {"careerjet": 0}),
        (30, {"careerjet": 0}),
        (5, {"careerjet": 0}),
    ])
    dead = asyncio.run(db.get_silently_dead_sources(hours=48))
    assert dead == {}, "never-worked sources stay silent, or the alarm becomes noise"


def test_recovery_clears_the_alarm(db):
    """One non-zero run inside the window means it is not dead."""
    _seed(db, [
        (100, {"nhs_jobs": 40}),
        (30, {"nhs_jobs": 0}),
        (5, {"nhs_jobs": 12}),   # recovered
    ])
    assert asyncio.run(db.get_silently_dead_sources(hours=48)) == {}


def test_needs_enough_runs_before_judging(db):
    """One bad run at 3am is not evidence — live APIs flake."""
    _seed(db, [
        (100, {"lever": 90}),
        (5, {"lever": 0}),   # only ONE run inside the window
    ])
    assert asyncio.run(db.get_silently_dead_sources(hours=48, min_runs=2)) == {}
