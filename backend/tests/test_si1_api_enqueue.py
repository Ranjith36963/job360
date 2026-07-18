"""SI1 (API half) — the search route must build a real notification enqueue
when Redis is available, and degrade safely when it isn't.

The pipeline half (main.run_search's ``enqueue`` hook + _enqueue_notifications)
is already tested in test_si1_notification_wiring.py. This pins the ACTIVATION:
without Redis the search behaves exactly as it did pre-SI1 (no notifications,
no failure); with Redis it hands run_search a working enqueue so feed rows
actually produce notifications.

Connection is LAZY — established on the first real enqueue, never at search
start. See test_no_connection_when_nothing_is_enqueued for why that matters.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.search import _make_notification_enqueue


@pytest.mark.asyncio
async def test_no_redis_url_returns_no_enqueue():
    """No REDIS_URL → (None, None): pre-SI1 behaviour, search still works."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REDIS_URL", None)
        enqueue, close = await _make_notification_enqueue()
    assert enqueue is None
    assert close is None


@pytest.mark.asyncio
async def test_blank_redis_url_returns_no_enqueue():
    with patch.dict(os.environ, {"REDIS_URL": "   "}, clear=False):
        enqueue, close = await _make_notification_enqueue()
    assert enqueue is None
    assert close is None


@pytest.mark.asyncio
async def test_no_connection_when_nothing_is_enqueued():
    """Building the hook must not touch Redis. This is the load-bearing one.

    Regression (caught by CI, not by my local run): the first cut connected
    eagerly here, so a configured-but-dead Redis stalled the search before it
    started — arq retries 5x with a 1s delay, ~10s per search. It broke
    test_api_idor.py::test_search_failed_progress_hides_internal_error, whose
    2.5s poll window expired while we sat retrying a Redis that CI configures
    (ci-offline.yml sets REDIS_URL) but does not run.

    Most searches notify nobody, so the eager connection was usually waste too.
    """
    calls = []

    async def _tracked(*a, **kw):
        calls.append(a)
        return MagicMock()

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6399"}, clear=False):
        with patch("arq.create_pool", _tracked):
            enqueue, close = await _make_notification_enqueue()
            assert enqueue is not None, "hook should exist — it just shouldn't connect yet"
            await close()  # closing without ever connecting must be safe

    assert calls == [], "must not connect to Redis until something is actually enqueued"


@pytest.mark.asyncio
async def test_dead_redis_degrades_without_raising(caplog):
    """A Redis outage must NEVER fail a search — the enqueue just returns None."""
    import logging

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6399"}, clear=False):
        with patch("arq.create_pool", side_effect=ConnectionError("no redis")):
            enqueue, close = await _make_notification_enqueue()
            with caplog.at_level(logging.WARNING):
                result = await enqueue("send_notification", "user-1", 42, "instant")
            await close()

    assert result is None, "a dead queue yields None, not an exception"
    assert any("SI1" in r.getMessage() for r in caplog.records), (
        "a skipped notification run should be visible in the logs"
    )


@pytest.mark.asyncio
async def test_dead_redis_only_attempts_connection_once():
    """After one failure, remaining jobs in the run must not each retry."""
    attempts = []

    async def _failing(*a, **kw):
        attempts.append(1)
        raise ConnectionError("no redis")

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6399"}, clear=False):
        with patch("arq.create_pool", _failing):
            enqueue, _ = await _make_notification_enqueue()
            for _ in range(5):
                assert await enqueue("send_notification", "u", 1, "instant") is None

    assert len(attempts) == 1, f"should give up after one failure, tried {len(attempts)}x"


@pytest.mark.asyncio
async def test_redis_available_forwards_exact_args():
    """With Redis up, args reach enqueue_job exactly as _enqueue_notifications sends them."""
    fake_pool = MagicMock()
    fake_pool.enqueue_job = AsyncMock(return_value="job-id")
    fake_pool.aclose = AsyncMock()

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}, clear=False):
        with patch("arq.create_pool", AsyncMock(return_value=fake_pool)):
            enqueue, close = await _make_notification_enqueue()
            result = await enqueue("send_notification", "user-1", 42, "instant")
            await close()

    assert result == "job-id"
    fake_pool.enqueue_job.assert_awaited_once_with("send_notification", "user-1", 42, "instant")
    fake_pool.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_settings_fail_fast():
    """Pin the fail-fast connection settings.

    Asserted on the settings rather than elapsed time on purpose: conftest
    mocks asyncio.sleep to instant, so a timing assertion would pass for the
    wrong reason.
    """
    captured = {}

    async def _capture(settings, *a, **kw):
        captured["settings"] = settings
        return MagicMock(enqueue_job=AsyncMock(), aclose=AsyncMock())

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}, clear=False):
        with patch("arq.create_pool", _capture):
            enqueue, _ = await _make_notification_enqueue()
            await enqueue("send_notification", "u", 1, "instant")

    settings = captured["settings"]
    assert settings.conn_retries == 0, "must not retry — notifications are best-effort"
    assert settings.conn_timeout <= 5, f"connect timeout too generous: {settings.conn_timeout}s"


@pytest.mark.asyncio
async def test_close_falls_back_to_sync_close():
    """redis-py <5 exposes close() not aclose(); the closer must handle both."""
    fake_pool = MagicMock(spec=["enqueue_job", "close"])
    fake_pool.enqueue_job = AsyncMock()
    fake_pool.close = MagicMock()  # sync close

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}, clear=False):
        with patch("arq.create_pool", AsyncMock(return_value=fake_pool)):
            enqueue, close = await _make_notification_enqueue()
            await enqueue("send_notification", "u", 1, "instant")  # force connect
            await close()  # must not raise on a sync close()

    fake_pool.close.assert_called_once()
