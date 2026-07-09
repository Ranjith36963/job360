"""RequestTimeoutMiddleware — slow handlers 504, fast/exempt ones pass.

The slow-route tests use @pytest.mark.real_sleep because conftest mocks
asyncio.sleep to instant by default (which would make a "slow" route finish
immediately and never trip the timeout).
"""
import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.middleware import RequestTimeoutMiddleware


def _app(timeout: float, exempt: tuple = ()) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RequestTimeoutMiddleware, timeout_seconds=timeout, exempt_prefixes=exempt
    )

    @app.get("/fast")
    async def fast():
        return {"ok": True}

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.3)
        return {"ok": True}

    @app.get("/exempt/slow")
    async def exempt_slow():
        await asyncio.sleep(0.3)
        return {"ok": True}

    return app


async def _get(app: FastAPI, path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


async def test_fast_request_passes():
    resp = await _get(_app(1.0), "/fast")
    assert resp.status_code == 200


@pytest.mark.real_sleep
async def test_slow_request_times_out_504():
    resp = await _get(_app(0.1), "/slow")
    assert resp.status_code == 504
    assert resp.json()["detail"] == "request timed out"


@pytest.mark.real_sleep
async def test_exempt_prefix_is_not_timed_out():
    resp = await _get(_app(0.1, exempt=("/exempt",)), "/exempt/slow")
    assert resp.status_code == 200
