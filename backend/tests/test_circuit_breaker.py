"""Tests for per-source circuit breaker.

Replaces the `newly_empty` flag in `backend/src/main.py`. A breaker trips
OPEN after N consecutive failures; after a cooldown the next call is
HALF_OPEN (one probe) which either closes on success or reopens with a
fresh cooldown on failure. Per pillar_3_batch_3.md §Circuit breakers.

Uses an injectable `clock` function (no freezegun) — the breaker does
not read `time.monotonic()` directly, so tests are deterministic.
"""
import pytest

from src.services.circuit_breaker import (
    CircuitBreaker,
    BreakerRegistry,
    BreakerState,
)


def test_starts_closed():
    breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=300)
    assert breaker.state == BreakerState.CLOSED
    assert breaker.can_proceed() is True


def test_5_failures_trip_to_open():
    breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=300)
    for _ in range(4):
        breaker.record_failure()
        assert breaker.state == BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state == BreakerState.OPEN


def test_open_rejects_call_without_hitting_source():
    """Once OPEN, can_proceed() returns False until cooldown elapses."""
    now = [1000.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=60,
        clock=lambda: now[0],
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == BreakerState.OPEN
    assert breaker.can_proceed() is False


def test_open_transitions_to_half_open_after_cooldown():
    now = [1000.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=60,
        clock=lambda: now[0],
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.can_proceed() is False
    # Advance past cooldown
    now[0] = 1061.0
    # can_proceed() should flip to HALF_OPEN and allow the probe
    assert breaker.can_proceed() is True
    assert breaker.state == BreakerState.HALF_OPEN


def test_half_open_success_closes():
    now = [1000.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=60,
        clock=lambda: now[0],
    )
    breaker.record_failure()
    breaker.record_failure()
    now[0] = 1061.0
    breaker.can_proceed()  # → HALF_OPEN
    breaker.record_success()
    assert breaker.state == BreakerState.CLOSED


def test_half_open_failure_reopens_with_fresh_cooldown():
    now = [1000.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=60,
        clock=lambda: now[0],
    )
    breaker.record_failure()
    breaker.record_failure()
    first_trip_at = now[0]
    now[0] = 1061.0
    breaker.can_proceed()  # → HALF_OPEN
    breaker.record_failure()
    assert breaker.state == BreakerState.OPEN
    # The cooldown should now be measured from the re-trip, not from the
    # original failure — a fresh 60s window.
    assert breaker._opened_at > first_trip_at + 60


def test_registry_scopes_breakers_by_source_name():
    registry = BreakerRegistry(failure_threshold=3, cooldown_seconds=120)
    a = registry.get("source_a")
    b = registry.get("source_b")
    assert a is not b
    a.record_failure()
    a.record_failure()
    a.record_failure()
    assert a.state == BreakerState.OPEN
    assert b.state == BreakerState.CLOSED
    # Registry returns the same object on repeat lookup
    assert registry.get("source_a") is a
