"""Redis-backed rate-limit path (docs/fable/01).

These run fully offline: a FakeRedis stub stands in for the client so we test
the dispatch + fallback logic, not a live Redis. The Lua scripts themselves are
covered by the offline in-memory path having identical semantics; here we prove
the limiter (a) uses Redis when available, (b) honours its verdicts, and — most
importantly — (c) falls back to in-memory on ANY Redis error so auth never breaks.
"""
from __future__ import annotations

import pytest

from src.core import settings
from src.services.auth import rate_limit


class FakeRedis:
    """Minimal stand-in for a sync redis client used by the limiter."""

    def __init__(self, *, eval_returns=None, raises=False):
        self.calls: list[tuple] = []
        self._eval_returns = list(eval_returns or [])
        self.raises = raises
        self.deleted: list[str] = []

    def eval(self, script, numkeys, *args):  # noqa: A002 — mirrors redis-py name
        self.calls.append((script, args))
        if self.raises:
            raise RuntimeError("redis down")
        return self._eval_returns.pop(0) if self._eval_returns else 1

    def delete(self, *keys):
        if self.raises:
            raise RuntimeError("redis down")
        self.deleted.extend(keys)


@pytest.fixture(autouse=True)
def _clean():
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


def test_check_and_record_uses_redis_verdict(monkeypatch):
    fake = FakeRedis(eval_returns=[1, 0])  # first admit, then deny
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: fake)
    assert rate_limit.check_and_record("k", max_in_window=1, window_seconds=60) is True
    assert rate_limit.check_and_record("k", max_in_window=1, window_seconds=60) is False
    assert len(fake.calls) == 2
    # Uses the request namespace, not the failures one.
    assert fake.calls[0][1][0] == "rl:req:k"


def test_redis_error_falls_back_to_memory(monkeypatch):
    fake = FakeRedis(raises=True)
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: fake)
    # Redis raises → in-memory sliding window takes over and still limits.
    assert rate_limit.check_and_record("k2", max_in_window=1, window_seconds=60) is True
    assert rate_limit.check_and_record("k2", max_in_window=1, window_seconds=60) is False


def test_is_locked_uses_redis_count(monkeypatch):
    fake = FakeRedis(eval_returns=[5])  # 5 failures in window
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: fake)
    assert rate_limit.is_locked("login:a:ip", max_failures=5, window_seconds=900) is True
    fake2 = FakeRedis(eval_returns=[4])  # under threshold
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: fake2)
    assert rate_limit.is_locked("login:a:ip", max_failures=5, window_seconds=900) is False


def test_record_failure_returns_redis_count(monkeypatch):
    fake = FakeRedis(eval_returns=[3])
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: fake)
    assert rate_limit.record_failure("login:b:ip") == 3
    assert fake.calls[0][1][0] == "rl:fail:login:b:ip"


def test_clear_failures_deletes_redis_key(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: fake)
    rate_limit.clear_failures("login:c:ip")
    assert fake.deleted == ["rl:fail:login:c:ip"]


def test_clear_failures_error_falls_back(monkeypatch):
    fake = FakeRedis(raises=True)
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: fake)
    # Seed an in-memory failure, then clear — the Redis delete raises, so the
    # in-memory drop must still run.
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: None)
    rate_limit.record_failure("login:d:ip")
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: fake)
    rate_limit.clear_failures("login:d:ip")
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: None)
    assert rate_limit.is_locked("login:d:ip", max_failures=1, window_seconds=900) is False


def test_flag_off_returns_no_client(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIS", False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    rate_limit._reset_redis_for_tests()
    assert rate_limit._redis_client() is None


def test_flag_on_but_no_url_returns_no_client(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIS", True)
    monkeypatch.delenv("REDIS_URL", raising=False)
    rate_limit._reset_redis_for_tests()
    assert rate_limit._redis_client() is None
