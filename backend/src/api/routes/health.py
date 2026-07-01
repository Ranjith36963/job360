"""Health, status, and sources endpoints."""

import json
import logging
import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.api.dependencies import get_db
from src.api.models import (
    HealthResponse,
    LivezResponse,
    ReadyzChecks,
    ReadyzResponse,
    SourceInfo,
    SourcesResponse,
    StatusResponse,
)
from src.core.tenancy import DEFAULT_TENANT_ID
from src.main import SOURCE_REGISTRY
from src.repositories.database import JobDatabase
from src.services.profile.storage import profile_exists

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="1.0.0")


@router.get("/livez", response_model=LivezResponse)
async def livez():
    """Liveness probe — returns 200 as long as the process is running.

    No dependency checks. Kubernetes/Railway liveness must not fail because
    the DB momentarily blipped; that is what /readyz is for.
    """
    return LivezResponse(status="alive")


@router.get("/readyz")
async def readyz():
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
            import redis.asyncio as aioredis  # type: ignore[import]  # noqa: PLC0415

            r = aioredis.from_url(redis_url, socket_connect_timeout=2)
            await r.ping()
            await r.aclose()
            checks["redis"] = "ok"
        except ImportError:
            # redis package not installed; treat as skipped rather than error
            # (ARQ brings it in — if ARQ isn't in the venv, Redis isn't used).
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
async def status(db: JobDatabase = Depends(get_db)):
    jobs_total = await db.count_jobs()
    run_logs = await db.get_run_logs(limit=1)
    last_run = run_logs[0] if run_logs else None

    sources_active = 0
    if last_run and last_run.get("per_source"):
        per_source = last_run["per_source"]
        if isinstance(per_source, str):
            per_source = json.loads(per_source)
        sources_active = sum(1 for v in per_source.values() if v > 0)

    return StatusResponse(
        jobs_total=jobs_total,
        last_run=last_run,
        sources_total=len(SOURCE_REGISTRY),
        sources_active=sources_active,
        # Public /health endpoint reports "has the single-tenant deployment
        # been set up?". Checking DEFAULT_TENANT_ID preserves CLI-era semantics
        # — per-user existence checks belong inside authenticated routes.
        profile_exists=profile_exists(DEFAULT_TENANT_ID),
    )


@router.get("/sources", response_model=SourcesResponse)
async def sources():
    source_list = [SourceInfo(name=name, type="free", health={}) for name in sorted(SOURCE_REGISTRY.keys())]
    return SourcesResponse(sources=source_list)
