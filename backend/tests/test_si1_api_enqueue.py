"""SI1 (API half) — the search route must build a real notification enqueue
when Redis is available, and degrade safely when it isn't.

The pipeline half (main.run_search's ``enqueue`` hook + _enqueue_notifications)
is already tested in test_si1_notification_wiring.py. This pins the ACTIVATION:
without Redis the search behaves exactly as it did pre-SI1 (no notifications,
no failure); with Redis it hands run_search a working enqueue so feed rows
actually produce notifications.
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
async def test_redis_unreachable_degrades_safely(caplog):
    """A Redis outage must NEVER fail the search — it just skips notifying."""
    import logging

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}, clear=False):
        with patch("arq.create_pool", side_effect=ConnectionError("no redis")):
            with caplog.at_level(logging.WARNING):
                enqueue, close = await _make_notification_enqueue()

    assert enqueue is None, "must not hand run_search a broken enqueue"
    assert close is None
    assert any("SI1" in r.getMessage() for r in caplog.records), (
        "a skipped notification run should be visible in the logs"
    )


@pytest.mark.asyncio
async def test_redis_available_returns_working_enqueue():
    """With Redis up, we return the pool's enqueue_job + a working closer."""
    fake_pool = MagicMock()
    fake_pool.enqueue_job = AsyncMock(return_value="job-id")
    fake_pool.aclose = AsyncMock()

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}, clear=False):
        with patch("arq.create_pool", AsyncMock(return_value=fake_pool)):
            enqueue, close = await _make_notification_enqueue()

    assert enqueue is fake_pool.enqueue_job
    assert close is not None

    # The enqueue must accept the exact shape _enqueue_notifications uses.
    await enqueue("send_notification", "user-1", 42, "instant")
    fake_pool.enqueue_job.assert_awaited_once_with("send_notification", "user-1", 42, "instant")

    await close()
    fake_pool.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_falls_back_to_sync_close():
    """redis-py <5 exposes close() not aclose(); the closer must handle both."""
    fake_pool = MagicMock(spec=["enqueue_job", "close"])
    fake_pool.enqueue_job = AsyncMock()
    fake_pool.close = MagicMock()  # sync close

    with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}, clear=False):
        with patch("arq.create_pool", AsyncMock(return_value=fake_pool)):
            _, close = await _make_notification_enqueue()

    await close()  # must not raise on a sync close()
    fake_pool.close.assert_called_once()
