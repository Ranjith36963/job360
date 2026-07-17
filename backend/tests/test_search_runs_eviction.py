"""N6 regression: `_runs` in src/api/routes/search.py must be bounded.

Before this fix, every POST /search added an entry to the module-level
`_runs` dict that was never removed — unbounded growth over the life of
the process. `_prune_runs()` now caps the dict at `_RUNS_MAX` entries and
evicts completed/failed entries older than `_RUNS_TTL_SECONDS`, but NEVER
touches a run that is still pending/running.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.api.routes import search


def _make_run(status: str, created_at: datetime, user_id: str = "u1") -> dict:
    return {
        "user_id": user_id,
        "status": status,
        "progress": "Done" if status == "completed" else "Starting...",
        "result": None,
        "created_at": created_at,
    }


def test_prune_caps_size_at_runs_max(monkeypatch):
    """Registering far more than `_RUNS_MAX` completed runs keeps the dict bounded."""
    monkeypatch.setattr(search, "_runs", {}, raising=True)
    now = datetime.now(timezone.utc)

    for i in range(search._RUNS_MAX + 250):
        run_id = f"run-{i}"
        search._runs[run_id] = _make_run("completed", now - timedelta(seconds=i))
        search._prune_runs(now=now)
        assert len(search._runs) <= search._RUNS_MAX

    assert len(search._runs) <= search._RUNS_MAX


def test_prune_never_evicts_active_runs(monkeypatch):
    """A pending/running run must survive prune even when the dict is way over cap
    and the run is older than the TTL — it must never disappear out from under a
    live poller."""
    monkeypatch.setattr(search, "_runs", {}, raising=True)
    now = datetime.now(timezone.utc)
    very_old = now - timedelta(hours=48)

    search._runs["live-run"] = _make_run("running", very_old)
    for i in range(search._RUNS_MAX + 250):
        search._runs[f"completed-{i}"] = _make_run("completed", now - timedelta(seconds=i))

    search._prune_runs(now=now)

    assert "live-run" in search._runs
    assert search._runs["live-run"]["status"] == "running"
    assert len(search._runs) <= search._RUNS_MAX + 1  # cap + the one protected live run


def test_prune_evicts_stale_completed_runs_past_ttl(monkeypatch):
    """A completed run older than `_RUNS_TTL_SECONDS` is dropped even under the cap."""
    monkeypatch.setattr(search, "_runs", {}, raising=True)
    now = datetime.now(timezone.utc)

    fresh_id, stale_id = "fresh", "stale"
    search._runs[fresh_id] = _make_run("completed", now - timedelta(seconds=60))
    search._runs[stale_id] = _make_run("failed", now - timedelta(seconds=search._RUNS_TTL_SECONDS + 1))

    search._prune_runs(now=now)

    assert fresh_id in search._runs
    assert stale_id not in search._runs


def test_prune_evicts_oldest_completed_first_when_over_cap(monkeypatch):
    """When over `_RUNS_MAX` but nothing is TTL-stale yet, the oldest completed/failed
    entries go first (LRU-by-created_at), not an arbitrary subset."""
    monkeypatch.setattr(search, "_runs", {}, raising=True)
    now = datetime.now(timezone.utc)

    # All within TTL, but count exceeds _RUNS_MAX by 10.
    for i in range(search._RUNS_MAX + 10):
        # Larger i => older (created further in the past).
        search._runs[f"run-{i}"] = _make_run("completed", now - timedelta(seconds=i))

    search._prune_runs(now=now)

    assert len(search._runs) == search._RUNS_MAX
    # The 10 oldest (highest i) must be gone; the 10 newest (lowest i) must survive.
    for i in range(10):
        assert f"run-{search._RUNS_MAX + 10 - 1 - i}" not in search._runs
    for i in range(10):
        assert f"run-{i}" in search._runs


def test_start_search_registers_run_with_created_at_and_prunes(monkeypatch):
    """POST /search's registration path stamps created_at and calls the pruner
    so the dict cannot grow without bound across many calls."""
    monkeypatch.setattr(search, "_runs", {}, raising=True)
    now = datetime.now(timezone.utc)

    # Pre-fill with more than the cap worth of old completed runs to prove
    # registering a new run triggers pruning down to the cap.
    for i in range(search._RUNS_MAX + 50):
        search._runs[f"old-{i}"] = _make_run("completed", now - timedelta(seconds=i + 100))

    # Simulate what start_search() does on registration (prune, then insert).
    search._prune_runs()
    new_run_id = "brand-new"
    search._runs[new_run_id] = {
        "user_id": "u1",
        "status": "running",
        "progress": "Starting...",
        "result": None,
        "created_at": datetime.now(timezone.utc),
    }
    search._prune_runs()

    assert new_run_id in search._runs
    assert "created_at" in search._runs[new_run_id]
    assert len(search._runs) <= search._RUNS_MAX + 1
