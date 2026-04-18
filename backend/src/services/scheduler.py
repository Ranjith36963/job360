"""Tiered polling scheduler — replaces the broken twice-daily cron.

Per pillar_3_batch_3.md §"Optimised tiered polling":

    ATS          : 60 seconds  (Greenhouse, Lever, Ashby, SmartRecruiters,
                                Workable, Recruitee, Pinpoint, Personio,
                                Rippling, Comeet — unmetered boards)
    Reed         : 5 minutes   (2000 req/hr budget)
    Workday      : 15 minutes  (anti-scraper, conservative)
    RSS feeds    : 15 minutes
    Scrapers     : 60 minutes  (LinkedIn, JobTensor, etc.)
    default      : 60 minutes  (unknown categories)

The scheduler honours the source's `BaseJobSource.category` for tier
selection with name-level overrides. It consults the shared
`BreakerRegistry` before every dispatch so a tripped breaker skips the
source until its cooldown elapses.

This module intentionally does NOT own scoring / dedup / DB writes —
those stay in `main.py::run_search` post-dispatch. The scheduler is a
transport-layer fair-share dispatcher.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Iterable

from src.services.circuit_breaker import BreakerRegistry

logger = logging.getLogger("job360.scheduler")


# Tier → interval seconds. Name-level overrides live in NAME_TIER below.
TIER_INTERVALS_SECONDS: dict[str, float] = {
    "ats":        60.0,
    "reed":       300.0,
    "workday":    900.0,
    "rss":        900.0,
    "scrapers":   3600.0,
    "keyed_api":  3600.0,
    "free_json":  3600.0,
    "other":      3600.0,
    "unknown":    3600.0,
    "default":    3600.0,
}

# Name-level tier override (beats category). Mostly for sources whose
# category says "keyed_api" but which have tighter/looser schedules than
# the default.
NAME_TIER: dict[str, str] = {
    "reed":    "reed",     # 5 min
    "workday": "workday",  # 15 min (anti-scraper)
    # Apprenticeships and teaching_vacancies (Phase F new sources) both
    # honour the default rss/15-min tier via their `category = "rss"`.
}


def resolve_tier_seconds(source) -> float:
    """Return this source's polling interval in seconds."""
    name_override = NAME_TIER.get(source.name)
    if name_override is not None:
        return TIER_INTERVALS_SECONDS[name_override]
    return TIER_INTERVALS_SECONDS.get(
        source.category,
        TIER_INTERVALS_SECONDS["default"],
    )


class TieredScheduler:
    """Per-source interval tracker + async dispatcher.

    `tick()` is the primary integration point:
      * Picks sources whose interval has elapsed since their last tick
      * Consults the breaker registry, skipping OPEN sources
      * Concurrently dispatches `fetch_jobs()` on the due sources
      * Records success/failure into the breaker
      * Returns [(source, result|Exception), ...] for the caller to score

    The CLI single-source path (`python -m src.cli run --source X`) uses
    `tick(force=True)` to bypass intervals — the scheduler is only in the
    way otherwise.
    """

    def __init__(
        self,
        sources: Iterable,
        breaker_registry: BreakerRegistry,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._sources = list(sources)
        self._breakers = breaker_registry
        self._clock = clock or time.monotonic
        self._last_run: dict[str, float] = {}

    def due_sources(self, now: float | None = None) -> list:
        now_val = now if now is not None else self._clock()
        return [s for s in self._sources if self._is_due(s, now_val)]

    def _is_due(self, source, now: float) -> bool:
        last = self._last_run.get(source.name)
        if last is None:
            return True
        return now - last >= resolve_tier_seconds(source)

    async def tick(self, now: float | None = None, *, force: bool = False) -> list:
        """One scheduler pass. Returns [(source, result|Exception), ...]."""
        now_val = now if now is not None else self._clock()
        candidates = self._sources if force else self.due_sources(now_val)

        due = []
        for src in candidates:
            breaker = self._breakers.get(src.name)
            if not breaker.can_proceed():
                # Still mark this as a "tick" so can_proceed doesn't get
                # rechecked on every call at the microsecond level — but
                # don't dispatch.
                self._last_run[src.name] = now_val
                logger.debug("[%s] breaker %s — skipping tick",
                             src.name, breaker.state.value)
                continue
            due.append(src)

        if not due:
            return []

        async def _safe_fetch(src):
            try:
                return await src.fetch_jobs()
            except BaseException as e:  # noqa: BLE001 — we want circuit trips on any failure
                logger.warning("[%s] fetch raised %s", src.name, type(e).__name__)
                return e

        results = await asyncio.gather(*[_safe_fetch(s) for s in due])
        paired = []
        for src, result in zip(due, results):
            self._last_run[src.name] = now_val
            breaker = self._breakers.get(src.name)
            fetch_failed = isinstance(result, BaseException) or result is None or not result
            if fetch_failed:
                breaker.record_failure()
            else:
                breaker.record_success()
            paired.append((src, result))
        return paired

    async def run_forever(self, *, poll_interval: float = 1.0) -> None:
        """Run until cancelled. One tick() per poll_interval wall-clock seconds.

        Not wired to a system service yet — Batch 4 owns the launchability
        story. For now, callers use `tick()` directly from run_search.
        """
        while True:
            try:
                await self.tick()
            except Exception as e:  # keep the loop alive
                logger.exception("Scheduler tick failed: %s", e)
            await asyncio.sleep(poll_interval)
