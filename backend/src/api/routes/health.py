"""Health and status endpoints."""

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from src.api.models import (
    HealthResponse,
    LivezResponse,
    StatusResponse,
)
from src.core import settings
from src.core.tenancy import DEFAULT_TENANT_ID
from src.repositories import pg, pool
from src.services.profile.storage import profile_exists

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok", version="1.0.0")


@router.get("/livez", response_model=LivezResponse)
async def livez() -> LivezResponse:
    """Liveness probe — returns 200 as long as the process is running.

    No dependency checks. Kubernetes/Railway liveness must not fail because
    the DB momentarily blipped; that is what /readyz is for.
    """
    return LivezResponse(status="alive")


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness probe: can the request path get a DB connection right now?

    Production borrows a connection from the SAME pool ``get_request_db`` uses
    (``repositories/pool.py``), waiting at most ``READYZ_POOL_TIMEOUT_S``, and
    runs ``SELECT 1`` under the same bound. A pinned pool (every connection
    held) or an unreachable database answers 503, which is what Railway's deploy
    healthcheck (``backend/railway.json``) and the uptime probe need to see.

    200 ``{"status": "ready", "pool": {size, idle, used, waiting, max}}``
    503 ``{"status": "not_ready", "reason": "pool_timeout" | "db_error"}``

    Test mode has no request-path pool (``get_request_db`` opens a fresh
    connection per request there), so the probe opens one the same way and
    ``pool`` is null. Redis is no longer checked: the service was deleted
    2026-09-02 and the auth rate limiter falls back without it.
    """
    timeout = settings.READYZ_POOL_TIMEOUT_S
    pool_stats: dict[str, int] | None = None
    try:
        if pg.TEST_MODE:
            async with pg.connect() as conn:
                await asyncio.wait_for(conn.execute("SELECT 1"), timeout)
        else:
            request_pool = await pool.get_pool()
            async with pool.acquire(request_pool, timeout=timeout) as conn:
                await asyncio.wait_for(conn.execute("SELECT 1"), timeout)
            pool_stats = pool.stats(request_pool)
    except PoolTimeout as exc:
        return _not_ready("pool_timeout", exc)
    except Exception as exc:  # any DB failure must answer 503, never a 500
        return _not_ready("db_error", exc)
    return JSONResponse(content={"status": "ready", "pool": pool_stats}, status_code=200)


def _not_ready(reason: str, exc: BaseException) -> JSONResponse:
    # The body carries only the reason; the log adds the error class. Neither
    # carries the DSN or the driver's message (which can name the host).
    logger.warning(
        "readyz_not_ready",
        extra={"event": "readyz_not_ready", "reason": reason, "error": type(exc).__name__},
    )
    return JSONResponse(content={"status": "not_ready", "reason": reason}, status_code=503)


@router.get("/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    """Has this deployment been set up at all.

    Slice 5 (#483) took the source counters and the last-run block off this
    response; the public-audit fix then removed ``jobs_total`` — a public
    endpoint must not reveal how many ads the shared catalog holds.
    """
    return StatusResponse(
        # Public /health endpoint reports "has the single-tenant deployment
        # been set up?". Checking DEFAULT_TENANT_ID preserves CLI-era semantics
        # — per-user existence checks belong inside authenticated routes.
        profile_exists=profile_exists(DEFAULT_TENANT_ID),
    )
