"""Health, status, and sources endpoints."""
import json

from fastapi import APIRouter, Depends

from src.api.dependencies import get_db
from src.api.models import HealthResponse, StatusResponse, SourceInfo, SourcesResponse
from src.core.tenancy import DEFAULT_TENANT_ID
from src.main import SOURCE_REGISTRY
from src.services.profile.storage import profile_exists
from src.repositories.database import JobDatabase

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="1.0.0")


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
    source_list = [
        SourceInfo(name=name, type="free", health={})
        for name in sorted(SOURCE_REGISTRY.keys())
    ]
    return SourcesResponse(sources=source_list)
