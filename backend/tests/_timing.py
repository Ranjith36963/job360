"""Timing helper for the wall-clock "is linear" tests.

Noise on a busy machine (pytest -n auto on a CI runner) only ever ADDS time, so the
minimum over a few repeats is the honest cost of the code. Budgets and ratios in the
tests stay exactly as written; only the measurement is made steadier.
"""
from __future__ import annotations

import time
from typing import Callable, Tuple, TypeVar

T = TypeVar("T")


def best_of(fn: Callable[[], T], repeats: int = 3) -> Tuple[T, float]:
    """Call fn() `repeats` times; return (last result, fastest wall time in seconds)."""
    best, result = float("inf"), None
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - t0)
    return result, best  # type: ignore[return-value]
