"""The permanent guard against reintroducing an event-loop block.

Background — this bug class has now shipped THREE times:
  * PR #123: run_search scored + deduped inline -> "Lost contact with the
    server while searching".
  * tests/test_upload_does_not_block_loop.py: pdfplumber inline -> 2.4 s stall,
    /api/health went to connection-refused.
  * backfill_feed_from_catalog: a 50,000-row synchronous scoring loop, called on
    EVERY per-user search.

Timing tests catch it only when the fixture is big enough to be slow. This guard
is a CALLEE-SIDE ASSERTION instead: a function decorated ``@cpu_bound`` checks,
every call, whether it is running on the event loop thread. It fires on a 5-row
fixture, it does not care about wall-clock, and it is immune to conftest's
instant-``asyncio.sleep`` monkeypatch.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.utils import loop_guard
from src.utils.loop_guard import LoopBlockError, cpu_bound


@cpu_bound
def _dummy_cpu_work(x: int = 2) -> int:
    return x * 21


@pytest.mark.asyncio
async def test_cpu_bound_raises_on_loop():
    """Calling a @cpu_bound function inline from async code must raise."""
    with pytest.raises(LoopBlockError) as exc:
        _dummy_cpu_work()
    msg = str(exc.value)
    assert "_dummy_cpu_work" in msg, f"message must name the offender, got: {msg}"
    assert "asyncio.to_thread" in msg, f"message must name the fix, got: {msg}"


@pytest.mark.asyncio
async def test_cpu_bound_ok_via_to_thread():
    """The CORRECT call shape must be untouched — no false positive."""
    assert await asyncio.to_thread(_dummy_cpu_work, 2) == 42


def test_cpu_bound_ok_from_plain_sync_code():
    """No running loop at all (CLI, worker thread, sync test) -> just run."""
    assert _dummy_cpu_work(3) == 63


@pytest.mark.asyncio
async def test_cpu_bound_prod_mode_logs_instead_of_raising(caplog, monkeypatch):
    """In production the guard must NEVER 500 a live request.

    It logs an ERROR (and pings Sentry) and then runs the work anyway. A slow
    response beats a broken one.
    """
    monkeypatch.setattr(loop_guard, "STRICT_OVERRIDE", False, raising=False)
    with caplog.at_level(logging.ERROR):
        assert _dummy_cpu_work(2) == 42
    assert any(
        "_dummy_cpu_work" in r.getMessage() and r.levelno >= logging.ERROR
        for r in caplog.records
    ), f"expected an ERROR record naming the offender, got: {[r.getMessage() for r in caplog.records]}"


def test_watchdog_is_disabled_under_tests():
    """MANDATORY safety pin.

    conftest makes ``asyncio.sleep`` instant, so a 100 ms watchdog sampler would
    become a busy-spin that starves the very test it is watching. The autouse
    fixture forces the flag off; this asserts the fixture actually works.
    """
    assert loop_guard.watchdog_enabled() is False
    assert loop_guard.start_loop_watchdog() is None


@pytest.mark.parametrize(
    "t0,t1,interval,expected",
    [
        (10.0, 10.1, 0.1, 0.0),  # slept exactly as asked -> no lag
        (10.0, 11.1, 0.1, 1.0),  # 1 s late -> the loop was blocked 1 s
        (0.0, 0.1, 0.1, 0.0),
    ],
)
def test_measure_lag_math(t0, t1, interval, expected):
    """Lag is overshoot, not elapsed time."""
    assert loop_guard.measure_lag(t0, t1, interval) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_lifespan_starts_and_cancels_the_watchdog(monkeypatch):
    """The production backstop must actually be wired into app startup.

    ``@cpu_bound`` only covers code someone remembered to decorate. The watchdog
    is the catch-all: it measures the loop's own responsiveness and reports any
    real stall to Sentry, whatever caused it. Dead code in a module nobody calls
    would be worse than nothing — it would look like coverage.
    """
    from src.api import main as api_main

    async def _noop():
        return None

    monkeypatch.setattr(api_main, "init_db", _noop)
    monkeypatch.setattr(api_main, "close_db", _noop)

    started: list[str] = []
    forever = asyncio.Event()

    def _fake_start():
        started.append("yes")
        return asyncio.create_task(forever.wait())

    assert hasattr(api_main, "start_loop_watchdog"), (
        "src/api/main.py must import start_loop_watchdog — the production "
        "loop-lag backstop is not wired into app startup at all"
    )
    monkeypatch.setattr(api_main, "start_loop_watchdog", _fake_start)

    async with api_main.lifespan(object()):
        assert started == ["yes"], "lifespan must start the loop watchdog"
        task = next(
            t for t in asyncio.all_tasks() if t.get_coro().__qualname__.startswith("Event.wait")
        )
        assert not task.done()

    await asyncio.sleep(0)
    assert task.cancelled() or task.done(), (
        "the watchdog task must be cancelled on shutdown — a leaked sampler "
        "keeps the loop alive after close"
    )


def test_cpu_bound_rejects_async_def():
    """@cpu_bound on a coroutine function is always a mistake — fail at import."""
    with pytest.raises(TypeError):

        @cpu_bound
        async def _oops() -> None:  # pragma: no cover - never called
            return None
