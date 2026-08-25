"""Callee-side guard against blocking the single FastAPI event loop.

Why this exists
---------------
This backend runs one asyncio event loop. Any CPU-heavy synchronous call made
directly from ``async def`` freezes EVERY other user's request while it runs.
The bug has shipped three times already:

* PR #123 — ``run_search`` scored + deduped inline; the UI showed "Lost contact
  with the server while searching" (the search was fine; the loop was busy).
* ``tests/test_upload_does_not_block_loop.py`` — pdfplumber inline; a measured
  2,399 ms stall took ``/api/health`` to connection-refused.
* ``backfill_feed_from_catalog`` — a synchronous 50,000-row scoring loop, run on
  EVERY per-user search.

Each time the fix was the same (`asyncio.to_thread`) and each time it was
reintroduced somewhere else, because the natural way to write the line is the
blocking way. Timing tests only catch it when the fixture happens to be big.

What this does
--------------
``@cpu_bound`` marks a function as "must never run on the loop thread". On every
call it asks ``asyncio.get_running_loop()``:

* no running loop (worker thread via ``to_thread``, CLI, ARQ, sync test) -> run;
* running loop + strict mode (tests, dev) -> raise ``LoopBlockError``;
* running loop + production -> log ERROR + one Sentry message per offender, then
  run anyway (a slow response beats a 500).

It is timing-free and size-free: it fires on a 5-row fixture, and conftest's
instant-``asyncio.sleep`` monkeypatch cannot hide it.

What this does NOT catch (know the bounds)
------------------------------------------
* **Undecorated hot code.** Only decorated entry points are checked; decorating
  a new heavy helper is a review habit. The production loop-lag watchdog
  (``start_loop_watchdog``) is the catch-all backstop.
* **Non-function blocking** — ``time.sleep``, a synchronous socket, a blocking
  DB driver call, a giant ``json.dumps`` written inline.
* **Death by a thousand cuts** — many sub-threshold callbacks that individually
  look fine.
* **GIL contention.** ``to_thread`` restores loop RESPONSIVENESS, not CPU
  throughput. If the work grows heavy enough, a process pool is the next step.

Note for test authors: a pre-existing test that calls a decorated function
inline inside an ``async def`` will now fail. That failure is a real latent
loop-block report — fix it with ``asyncio.to_thread`` (or make the test sync),
never by removing the decorator.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from typing import Any, Callable, Optional, TypeVar, cast

logger = logging.getLogger("job360.utils.loop_guard")

F = TypeVar("F", bound=Callable[..., Any])

class LoopBlockError(RuntimeError):
    """A ``@cpu_bound`` function was called on the event loop thread."""


#: Test hook. ``None`` = decide from the environment. ``True`` = always raise,
#: ``False`` = always log-and-continue (production behaviour). The suite's
#: autouse fixture pins this to ``True`` so strictness never depends on which
#: env vars happen to be set on the box.
STRICT_OVERRIDE: Optional[bool] = None

#: Qualnames already reported to Sentry in this process — the lenient path must
#: not spam the issue stream once per row.
_reported: set[str] = set()


def _is_production() -> bool:
    """Mirror of ``src.api.middleware._is_production``.

    Duplicated on purpose: importing the middleware module from ``src.utils``
    would drag FastAPI into every CLI/worker import path. ``JOB360_ENV`` is dead
    (see CLAUDE.md) — this is the SAME signal HSTS, Sentry and the session-cookie
    ``Secure`` flag already gate on.
    """
    return (
        os.environ.get("APP_ENV", "").lower() == "production"
        or bool(os.environ.get("RAILWAY_ENVIRONMENT", ""))
    )


def _strict() -> bool:
    """Raise (True) or log-and-continue (False) when caught on the loop."""
    if STRICT_OVERRIDE is not None:
        return STRICT_OVERRIDE
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return not _is_production()


def _report(qualname: str) -> None:
    """Production path: always log, ping Sentry once per offender per process."""
    logger.error(
        "event-loop block: %s ran ON the event loop thread; "
        "wrap the call in asyncio.to_thread",
        qualname,
    )
    if qualname in _reported:
        return
    _reported.add(qualname)
    try:
        import sentry_sdk  # noqa: PLC0415 — optional dep, never a hard import

        sentry_sdk.capture_message(
            f"event-loop block: {qualname} ran on the event loop thread",
            level="error",
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the request
        pass


def cpu_bound(fn: F) -> F:
    """Mark ``fn`` as CPU-bound: it must be called off the event loop thread.

    Args:
        fn: a plain (non-async) function doing meaningful synchronous work.

    Returns:
        The same function, wrapped with the on-loop check.

    Raises:
        TypeError: at decoration time if ``fn`` is a coroutine function — a
            coroutine cannot be handed to ``asyncio.to_thread``, so the marker
            would be meaningless.
    """
    if asyncio.iscoroutinefunction(fn):
        raise TypeError(
            f"@cpu_bound cannot decorate the coroutine function {fn.__qualname__!r} — "
            "it marks SYNCHRONOUS work that must be run via asyncio.to_thread"
        )

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop on this thread — the correct path (to_thread / CLI / worker).
            return fn(*args, **kwargs)
        if _strict():
            raise LoopBlockError(
                f"{fn.__qualname__} ran ON the event loop; "
                "wrap the call in asyncio.to_thread"
            )
        _report(fn.__qualname__)
        return fn(*args, **kwargs)

    return cast(F, wrapper)


# --------------------------------------------------------------------------
# Backstop — production loop-lag watchdog
# --------------------------------------------------------------------------

#: Sampler period in seconds. Small enough to see a human-visible stall.
WATCHDOG_INTERVAL = 0.1
#: Lag above this many seconds is reported. Ordinary scheduling jitter is
#: sub-millisecond; 0.5 s means a request was measurably held up.
WATCHDOG_THRESHOLD = 0.5


def watchdog_enabled() -> bool:
    """``LOOP_WATCHDOG_ENABLED`` env flag, default on.

    Read at call time (not import time) so tests and a runtime env change both
    take effect without re-importing the module.
    """
    return os.environ.get("LOOP_WATCHDOG_ENABLED", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def measure_lag(t0: float, t1: float, interval: float = WATCHDOG_INTERVAL) -> float:
    """Lag = how much LONGER than ``interval`` the sleep actually took.

    Split out from the sampler loop so the arithmetic is unit-testable without
    a running loop or real wall-clock.
    """
    return (t1 - t0) - interval


async def _watchdog_loop() -> None:
    """Sample the loop's own responsiveness forever; log every real stall."""
    loop = asyncio.get_running_loop()
    while True:
        t0 = loop.time()
        await asyncio.sleep(WATCHDOG_INTERVAL)
        lag = measure_lag(t0, loop.time())
        if lag > WATCHDOG_THRESHOLD:
            logger.error(
                "event loop blocked for %.2fs (watchdog sampler overshot by %.2fs)",
                lag,
                lag,
            )
            try:
                import sentry_sdk  # noqa: PLC0415

                sentry_sdk.capture_message(
                    f"event loop blocked for {lag:.2f}s", level="error"
                )
            except Exception:  # noqa: BLE001
                pass


def start_loop_watchdog() -> Optional[asyncio.Task[None]]:
    """Start the sampler as a background task; ``None`` when disabled.

    MUST stay disabled under pytest: conftest makes ``asyncio.sleep`` instant,
    which would turn a 100 ms sampler into a busy-spin.
    """
    if not watchdog_enabled():
        return None
    task = asyncio.create_task(_watchdog_loop())
    logger.info("event-loop watchdog started (threshold %.2fs)", WATCHDOG_THRESHOLD)
    return task
