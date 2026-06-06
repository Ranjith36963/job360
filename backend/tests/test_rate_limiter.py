import asyncio
import time

import pytest

from src.utils.rate_limiter import RateLimiter


def _run(coro):
    return asyncio.run(coro)


def test_basic_acquire_release():
    """RateLimiter should allow acquire and release."""
    async def _test():
        limiter = RateLimiter(concurrent=2, delay=0.0)
        await limiter.acquire()
        limiter.release()
    _run(_test())


def test_context_manager():
    """RateLimiter should work as an async context manager."""
    async def _test():
        limiter = RateLimiter(concurrent=1, delay=0.0)
        async with limiter:
            pass  # Should acquire and release without error
    _run(_test())


def test_concurrent_limit():
    """RateLimiter should block when concurrency limit is reached."""
    async def _test():
        limiter = RateLimiter(concurrent=1, delay=0.0)
        results = []

        async def task(name):
            async with limiter:
                results.append(f"{name}_start")
                await asyncio.sleep(0.05)
                results.append(f"{name}_end")

        await asyncio.gather(task("a"), task("b"))
        # With concurrency=1, tasks must run sequentially
        # a_start, a_end, b_start, b_end (or b first, then a)
        assert len(results) == 4
        # The first task must finish before the second starts
        first_end = results.index(f"{results[0][0]}_end")
        second_start = results.index(f"{results[2][0]}_start")
        assert first_end < second_start

    _run(_test())


@pytest.mark.real_sleep
def test_delay_enforced():
    """RateLimiter should enforce delay between acquires."""
    async def _test():
        delay = 0.1
        limiter = RateLimiter(concurrent=2, delay=delay)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        limiter.release()
        # The delay should have been applied on acquire
        assert elapsed >= delay * 0.9  # Allow small timing tolerance

    _run(_test())


def test_multiple_concurrent_allowed():
    """RateLimiter with concurrent=2 should allow 2 tasks at once."""
    async def _test():
        limiter = RateLimiter(concurrent=2, delay=0.0)
        active = []
        max_active = 0

        async def task():
            nonlocal max_active
            async with limiter:
                active.append(1)
                max_active = max(max_active, len(active))
                await asyncio.sleep(0.05)
                active.pop()

        await asyncio.gather(task(), task(), task())
        assert max_active == 2

    _run(_test())
