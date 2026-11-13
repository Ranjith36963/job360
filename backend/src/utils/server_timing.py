"""A route's step timings, read off the ``Server-Timing`` header (the same idea as ``blockers.SUBSTEPS``).

The route sets ``TIMINGS`` to a dict; any code awaited under it times its parts with a ``Lap``, so no
function signature changes. A part that repeats (per-row work) ADDS to its key: the value is the sum.
Nothing asks for timings = every ``Lap`` call is a no-op. Durations and fixed step names only.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Optional

TIMINGS: ContextVar[Optional[dict[str, float]]] = ContextVar("server_timings", default=None)


class Lap:
    """Times the parts of one function into ``TIMINGS`` (a no-op when nobody asked)."""

    def __init__(self) -> None:
        self._into = TIMINGS.get()
        self._at = time.perf_counter()

    def __call__(self, part: str) -> None:
        now = time.perf_counter()
        if self._into is not None:
            self._into[part] = round(self._into.get(part, 0.0) + (now - self._at) * 1000, 1)
        self._at = now

    def skip(self) -> None:
        """Start the next part now: the time since the last mark was timed by someone else (a nested step)."""
        self._at = time.perf_counter()


def header_value(parts: dict[str, float], total_ms: float) -> str:
    """``name;dur=ms`` for every part, then ``total``: the standard ``Server-Timing`` value."""
    return ", ".join([f"{name};dur={ms}" for name, ms in parts.items()] + [f"total;dur={total_ms}"])
