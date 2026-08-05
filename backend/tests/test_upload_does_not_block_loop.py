"""Parsing an upload must not freeze the server for everyone else.

FOUND IN A REAL BROWSER RUN, not by a unit test. While one person uploaded a CV,
``/api/health`` went 27 ms -> 1.4 s -> 1.6 s -> 5 s timeout -> connection
refused. The uploader's own profile poll was reset by the server and the page
showed "Something went wrong - API error 500" while their upload was in fact
succeeding. Measured directly afterwards, the event loop stalled for **2,399 ms**
on a single 2-page PDF.

The cause: pdfplumber is synchronous and CPU-bound, and the route called it
straight from an ``async def``. While it ran, the loop could not accept a
connection, answer a healthcheck, or serve any other user on that worker. One
upload degraded the whole API.

This is the same failure as the search freeze (PR #123), and it will keep coming
back — the natural way to write the line is the blocking way. So this test does
not check for a spelling of ``to_thread``; it measures the thing that actually
matters, using a stub with NO real PDF so it is deterministic and CI-safe.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.api.routes import profile as profile_routes
from src.services.profile.models import UserProfile

# A stub "parser" that burns wall-clock the way pdfplumber does. Deliberately a
# BUSY WAIT, not time.sleep: sleep releases the GIL and would let the loop run,
# hiding the very bug this guards. Real PDF parsing holds the CPU.
_BLOCK_SECONDS = 0.6


def _fake_blocking_extract(_path: str) -> str:
    end = time.perf_counter() + _BLOCK_SECONDS
    while time.perf_counter() < end:
        pass
    return "Jane Okafor\nRegistered Nurse\n" + ("ICU ALS BLS Epic triage. " * 40)


@pytest.mark.asyncio
async def test_cv_parsing_keeps_the_server_answering_other_requests(monkeypatch):
    """While a CV is parsed, the loop must stay responsive to everyone else.

    The threshold is deliberately loose (250 ms against a 600 ms parse): this
    guards against the loop being BLOCKED, not against ordinary scheduling
    jitter on a busy CI box. A regression here means a full stall, which is
    unmissable — the old code stalled for the entire parse.
    """
    monkeypatch.setattr(profile_routes, "extract_text", _fake_blocking_extract)

    lags: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        """Stand in for every other request in flight: ask for 20 ms of sleep
        and record how much LATER than that we were actually resumed."""
        while not stop.is_set():
            t = time.perf_counter()
            await asyncio.sleep(0.02)
            lags.append((time.perf_counter() - t - 0.02) * 1000)

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)  # let the heartbeat establish a baseline

    profile = UserProfile()
    await profile_routes._capture_cv_raw(b"%PDF-1.4 fake", "cv.pdf", profile)

    stop.set()
    await hb

    assert profile.cv_data.raw_text, "the parse result must still reach the profile"
    worst = max(lags) if lags else 0.0
    assert worst < 250, (
        f"the event loop stalled {worst:.0f} ms during a {_BLOCK_SECONDS*1000:.0f} ms "
        "parse - the upload is blocking every other request again "
        "(wrap the parser in asyncio.to_thread)"
    )


@pytest.mark.asyncio
async def test_the_upload_helper_is_awaitable():
    """_capture_cv_raw must stay a coroutine.

    If someone makes it sync again, the ``await`` at both call sites breaks
    loudly here rather than silently reintroducing the freeze.
    """
    assert asyncio.iscoroutinefunction(profile_routes._capture_cv_raw)
