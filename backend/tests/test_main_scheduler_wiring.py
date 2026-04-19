"""Tests for TieredScheduler wire-up in run_search (Batch 3.5 Deliverable E).

Replaces the Batch 3 `asyncio.gather` block at main.py:356 with a
scheduler dispatch call. Tests verify:
  1. run_search invokes TieredScheduler.tick exactly once with force=True.
  2. Every registered source's fetch_jobs is called exactly once (one-shot CLI).
  3. A circuit-breaker-OPEN source is skipped — its fetch_jobs never fires.

Tests inject fake sources via `_build_sources` monkeypatch to avoid live
HTTP. They do not touch test_main.py's --ignore'd integration path.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.models import Job
from src.services.circuit_breaker import default_registry as _default_registry
from src.services.circuit_breaker import BreakerState


class _FakeSource:
    """BaseJobSource-compatible fake for scheduler tests."""

    def __init__(self, name: str, category: str = "ats",
                 result: Optional[list] = None, raises: Optional[Exception] = None):
        self.name = name
        self.category = category
        self._result = result if result is not None else []
        self._raises = raises
        self.fetch_call_count = 0

    async def fetch_jobs(self):
        self.fetch_call_count += 1
        if self._raises:
            raise self._raises
        return list(self._result)


def _make_job(title: str = "Test", company: str = "Co") -> Job:
    return Job(
        title=title,
        company=company,
        apply_url="https://x.test/1",
        source="fake",
        date_found=datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def tmp_db_path(tmp_path, monkeypatch):
    """Redirect DB_PATH to a tmp SQLite and run JobDatabase init.

    Defensive: other test files (test_api_idor.py, test_channels_routes.py)
    populate the dependencies._db singleton and the search.py _runs dict;
    we reset both so run_search inside this fixture starts from a known
    clean state.
    """
    from src.core import settings as core_settings
    from src.api import dependencies
    from src.api.routes import search as search_route

    path = tmp_path / "test.db"
    monkeypatch.setattr(core_settings, "DB_PATH", path, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", path, raising=True)
    monkeypatch.setattr(dependencies, "_db", None, raising=False)
    # Reset the module-level _runs dict in search.py (not a leak vector
    # here, but keeps the fixture self-documenting for future readers).
    monkeypatch.setattr(search_route, "_runs", {}, raising=True)
    return str(path)


@pytest.fixture(autouse=True)
def _reset_default_breaker_registry():
    """Isolate tests — default registry is module-global."""
    reg = _default_registry()
    reg._breakers.clear()  # type: ignore[attr-defined]
    yield
    reg._breakers.clear()  # type: ignore[attr-defined]


@pytest.fixture
def fake_profile(monkeypatch):
    """Stub load_profile() so run_search doesn't bail out on 'no profile'.

    Must patch `src.main.load_profile` (the bound reference) — patching
    the storage module alone does NOT work because `main.py` did
    `from src.services.profile.storage import load_profile`.
    """
    from src.services.profile.models import (
        CVData, SearchConfig, UserPreferences, UserProfile,
    )
    from src import main as main_mod

    stub = UserProfile(
        cv_data=CVData(
            raw_text="x", skills=["python"], job_titles=["Engineer"],
        ),
        preferences=UserPreferences(
            target_job_titles=["Engineer"], additional_skills=["python"],
        ),
    )
    # Batch 3.5.2 changed load_profile's signature to take a user_id.
    monkeypatch.setattr(main_mod, "load_profile", lambda uid: stub)
    # `generate_search_config` is also bound in main; return a minimal
    # SearchConfig that won't crash JobScorer init.
    config = SearchConfig(
        job_titles=["Engineer"],
        relevance_keywords=["engineer", "python"],
    )
    monkeypatch.setattr(main_mod, "generate_search_config", lambda p: config)
    return stub


@pytest.mark.asyncio
async def test_run_search_uses_tiered_scheduler(tmp_db_path, fake_profile, monkeypatch):
    """Assert run_search calls TieredScheduler.tick exactly once with force=True."""
    from src.services import scheduler as scheduler_mod
    from src import main as main_mod

    tick_calls: list[dict] = []
    original_tick = scheduler_mod.TieredScheduler.tick

    async def spy_tick(self, now=None, *, force: bool = False):
        tick_calls.append({"force": force, "sources": len(self._sources)})
        return await original_tick(self, now=now, force=force)

    monkeypatch.setattr(scheduler_mod.TieredScheduler, "tick", spy_tick)

    # Replace _build_sources with 2 fakes
    srcs = [
        _FakeSource("fake1", category="ats", result=[_make_job("A")]),
        _FakeSource("fake2", category="rss", result=[_make_job("B")]),
    ]
    monkeypatch.setattr(main_mod, "_build_sources",
                        lambda *a, **kw: srcs)

    stats = await main_mod.run_search(db_path=tmp_db_path, no_notify=True)

    assert len(tick_calls) == 1, f"tick should be called exactly once, got {tick_calls}"
    assert tick_calls[0]["force"] is True
    assert tick_calls[0]["sources"] == 2


@pytest.mark.asyncio
async def test_each_registered_source_called_exactly_once(
    tmp_db_path, fake_profile, monkeypatch
):
    from src import main as main_mod

    srcs = [
        _FakeSource("a", category="ats"),
        _FakeSource("b", category="rss"),
        _FakeSource("c", category="scrapers"),
    ]
    monkeypatch.setattr(main_mod, "_build_sources",
                        lambda *a, **kw: srcs)

    await main_mod.run_search(db_path=tmp_db_path, no_notify=True)

    for s in srcs:
        assert s.fetch_call_count == 1, (
            f"{s.name} fetched {s.fetch_call_count} times, expected 1"
        )


@pytest.mark.asyncio
async def test_breaker_open_source_is_skipped(
    tmp_db_path, fake_profile, monkeypatch
):
    """Pre-trip a breaker for 'flaky' — its fetch_jobs must never fire."""
    from src import main as main_mod

    # Pre-trip breaker for 'flaky'
    reg = _default_registry()
    breaker = reg.get("flaky")
    for _ in range(5):
        breaker.record_failure()
    assert breaker.state == BreakerState.OPEN

    flaky = _FakeSource("flaky", category="ats",
                        raises=RuntimeError("should not be called"))
    healthy = _FakeSource("healthy", category="ats", result=[_make_job("H")])
    monkeypatch.setattr(main_mod, "_build_sources",
                        lambda *a, **kw: [flaky, healthy])

    stats = await main_mod.run_search(db_path=tmp_db_path, no_notify=True)

    assert flaky.fetch_call_count == 0, (
        f"flaky (breaker OPEN) should have been skipped; fetched {flaky.fetch_call_count} times"
    )
    assert healthy.fetch_call_count == 1
    # per_source surface: skipped source should appear as 0 so the summary
    # still covers every registered source, with the breaker state explaining why.
    assert stats["per_source"].get("flaky", 0) == 0
    assert stats["per_source"].get("healthy") == 1
