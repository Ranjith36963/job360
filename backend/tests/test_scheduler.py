"""Tests for TieredScheduler — per pillar_3_batch_3.md §"Optimised tiered polling".

Replaces the broken twice-daily cron with per-source intervals:
  - ATS (60s): Greenhouse, Lever, Ashby, etc.
  - Reed (5min)
  - Workday / RSS (15min)
  - Scrapers (60min)

Tests use injectable `clock` — no freezegun, no wall-clock dependency.
"""
import asyncio

import pytest

from src.services.scheduler import (
    TieredScheduler,
    resolve_tier_seconds,
    TIER_INTERVALS_SECONDS,
)
from src.services.circuit_breaker import BreakerRegistry


class _FakeSource:
    def __init__(self, name: str, category: str, payload=None, raise_exc=None):
        self.name = name
        self.category = category
        self.fetch_calls = 0
        self._payload = payload if payload is not None else [object()]
        self._raise = raise_exc

    async def fetch_jobs(self):
        self.fetch_calls += 1
        if self._raise:
            raise self._raise
        return self._payload


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# resolve_tier_seconds
# ---------------------------------------------------------------------------


def test_resolve_tier_by_category_and_name():
    ats = _FakeSource("greenhouse", "ats")
    rss = _FakeSource("jobs_ac_uk", "rss")
    scraper = _FakeSource("linkedin", "scraper")
    reed = _FakeSource("reed", "keyed_api")

    assert resolve_tier_seconds(ats) == TIER_INTERVALS_SECONDS["ats"]
    assert resolve_tier_seconds(rss) == TIER_INTERVALS_SECONDS["rss"]
    assert resolve_tier_seconds(scraper) == TIER_INTERVALS_SECONDS["scrapers"]
    # Reed has an explicit 5-min override (falls under keyed_api otherwise)
    assert resolve_tier_seconds(reed) == TIER_INTERVALS_SECONDS["reed"]


# ---------------------------------------------------------------------------
# Per-interval dispatch
# ---------------------------------------------------------------------------


def test_ats_source_polled_every_60s():
    now = [1000.0]
    src = _FakeSource("greenhouse", "ats")
    sched = TieredScheduler(
        [src],
        breaker_registry=BreakerRegistry(),
        clock=lambda: now[0],
    )

    _run(sched.tick())
    assert src.fetch_calls == 1

    # 59s later: NOT due
    now[0] = 1000.0 + 59
    _run(sched.tick())
    assert src.fetch_calls == 1

    # 60s: due again
    now[0] = 1000.0 + 60
    _run(sched.tick())
    assert src.fetch_calls == 2


def test_scrapers_polled_every_3600s():
    now = [1000.0]
    src = _FakeSource("linkedin", "scraper")
    sched = TieredScheduler([src], BreakerRegistry(), clock=lambda: now[0])

    _run(sched.tick())
    assert src.fetch_calls == 1

    now[0] = 1000.0 + 3599
    _run(sched.tick())
    assert src.fetch_calls == 1

    now[0] = 1000.0 + 3600
    _run(sched.tick())
    assert src.fetch_calls == 2


# ---------------------------------------------------------------------------
# Fairness
# ---------------------------------------------------------------------------


def test_multiple_tiers_do_not_starve():
    """One slow-tier source should not delay another fast-tier source's tick."""
    now = [1000.0]
    ats = _FakeSource("lever", "ats")
    scraper = _FakeSource("linkedin", "scraper")
    sched = TieredScheduler([ats, scraper], BreakerRegistry(), clock=lambda: now[0])

    _run(sched.tick())
    assert ats.fetch_calls == 1
    assert scraper.fetch_calls == 1

    # ATS interval (60s) — scraper not due yet (3600s)
    now[0] = 1000.0 + 60
    _run(sched.tick())
    assert ats.fetch_calls == 2
    assert scraper.fetch_calls == 1

    # ATS due again (120s), scraper still waiting
    now[0] = 1000.0 + 120
    _run(sched.tick())
    assert ats.fetch_calls == 3
    assert scraper.fetch_calls == 1


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------


def test_scheduler_respects_circuit_breaker_open():
    """Breaker OPEN for a source → scheduler does not call .fetch_jobs()."""
    now = [1000.0]
    registry = BreakerRegistry(failure_threshold=2, cooldown_seconds=600,
                                clock=lambda: now[0])
    # Pre-trip the breaker for 'flaky'
    flaky_breaker = registry.get("flaky")
    flaky_breaker.record_failure()
    flaky_breaker.record_failure()
    assert flaky_breaker.can_proceed() is False

    flaky = _FakeSource("flaky", "ats")
    healthy = _FakeSource("greenhouse", "ats")
    sched = TieredScheduler([flaky, healthy], registry, clock=lambda: now[0])

    _run(sched.tick())
    assert flaky.fetch_calls == 0    # skipped
    assert healthy.fetch_calls == 1  # dispatched


def test_scheduler_honors_manual_source_filter():
    """CLI `--source` single-run path bypasses tier windows (run_once=True)."""
    now = [1000.0]
    src = _FakeSource("scrapy", "scraper")  # normally 3600s interval
    sched = TieredScheduler([src], BreakerRegistry(), clock=lambda: now[0])

    _run(sched.tick(force=True))
    _run(sched.tick(force=True))
    # Back-to-back forced ticks both dispatch
    assert src.fetch_calls == 2
