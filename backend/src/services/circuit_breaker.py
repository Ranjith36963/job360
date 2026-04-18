"""Per-source circuit breaker — replaces `newly_empty` health heuristic.

State machine (per pillar_3_batch_3.md §Circuit breakers):

    CLOSED ──N consecutive failures──▶ OPEN
       ▲                                 │
       │                                 │ cooldown elapsed
       │                                 ▼
       └──── success ──── HALF_OPEN ◀───┘
                            │ failure
                            └─────────▶ OPEN (fresh cooldown)

Uses an injectable `clock` function so tests can advance time
deterministically without freezegun.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Callable


class BreakerState(str, Enum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


class CircuitBreaker:
    """One breaker instance per source."""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self.state: BreakerState = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float = 0.0

    def can_proceed(self) -> bool:
        """Return True if a call is allowed. Promotes OPEN→HALF_OPEN on
        cooldown expiry so the caller can make its probe call."""
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.HALF_OPEN:
            return True
        # OPEN
        if self._clock() - self._opened_at >= self.cooldown_seconds:
            self.state = BreakerState.HALF_OPEN
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        if self.state == BreakerState.HALF_OPEN:
            self._trip_open()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._trip_open()

    def _trip_open(self) -> None:
        self.state = BreakerState.OPEN
        self._opened_at = self._clock()


class BreakerRegistry:
    """Lazy per-source CircuitBreaker factory.

    `BreakerRegistry.get(name)` returns a stable instance so every call-
    site that consults the same source_name sees the same state.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, source_name: str) -> CircuitBreaker:
        if source_name not in self._breakers:
            self._breakers[source_name] = CircuitBreaker(
                failure_threshold=self._failure_threshold,
                cooldown_seconds=self._cooldown_seconds,
                clock=self._clock,
            )
        return self._breakers[source_name]

    def names(self) -> list[str]:
        return list(self._breakers.keys())

    def snapshot(self) -> dict[str, BreakerState]:
        return {name: b.state for name, b in self._breakers.items()}


# Module-level default registry — used by main.py::run_search
_DEFAULT_REGISTRY = BreakerRegistry()


def default_registry() -> BreakerRegistry:
    return _DEFAULT_REGISTRY
