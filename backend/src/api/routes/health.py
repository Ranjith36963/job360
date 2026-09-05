"""Health and status endpoints."""

import logging
import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.api.dependencies import get_request_db
from src.api.models import (
    HealthResponse,
    LivezResponse,
    ReadyzChecks,
    ReadyzResponse,
    StatusResponse,
)
from src.core.tenancy import DEFAULT_TENANT_ID
from src.repositories.database import JobDatabase
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
    """Readiness probe — checks real dependencies before accepting traffic.

    Checks:
      * db   — trivial ``SELECT 1`` through the pg connection layer.
      * redis — ping via redis-py. Skipped (not failed) when REDIS_URL unset.

    Returns 200 ``{status:"ready", ...}`` when all checks pass,
    503 ``{status:"not ready", ...}`` when any required check fails.
    """
    checks: dict[str, str] = {}
    healthy = True

    # --- Postgres check ---------------------------------------------------
    try:
        from src.repositories import pg

        async with pg.connect() as conn:
            await conn.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as exc:
        logger.warning("readyz: db check failed: %s", exc)
        checks["db"] = "error"
        healthy = False

    # --- Redis check ------------------------------------------------------
    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        checks["redis"] = "skipped"
    else:
        try:
            import redis.asyncio as aioredis  # noqa: PLC0415

            # redis-py ships py.typed but `from_url` itself carries no
            # annotations, so strict mode flags the call, not the import.
            r = aioredis.from_url(redis_url, socket_connect_timeout=2)  # type: ignore[no-untyped-call]
            await r.ping()
            await r.aclose()
            checks["redis"] = "ok"
        except ImportError:
            # redis package not installed; treat as skipped rather than error.
            # Redis is optional here — it backs the shared auth rate limiter
            # (services/auth/rate_limit.py), which falls back to an in-process
            # window when it is absent.
            logger.info("readyz: redis-py not installed, skipping Redis check")
            checks["redis"] = "skipped"
        except Exception as exc:
            logger.warning("readyz: redis check failed: %s", exc)
            checks["redis"] = "error"
            healthy = False

    status_code = 200 if healthy else 503
    body = ReadyzResponse(
        status="ready" if healthy else "not ready",
        checks=ReadyzChecks(**checks),
    )
    return JSONResponse(content=body.model_dump(), status_code=status_code)


@router.get("/status", response_model=StatusResponse)
async def status(db: JobDatabase = Depends(get_request_db)) -> StatusResponse:
    """How much is stored, and has this deployment been set up at all.

    Slice 5 (#483) took the source counters and the last-run block off this
    response: nothing fetches jobs any more, so there is no run to report.
    """
    return StatusResponse(
        jobs_total=await db.count_jobs(),
        # Public /health endpoint reports "has the single-tenant deployment
        # been set up?". Checking DEFAULT_TENANT_ID preserves CLI-era semantics
        # — per-user existence checks belong inside authenticated routes.
        profile_exists=profile_exists(DEFAULT_TENANT_ID),
    )
