"""Pipeline (application tracker) routes for Job360 FastAPI backend.

User-scoped per CLAUDE.md rule #12 — every endpoint requires a
valid session and queries are filtered by user.id.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_db
from src.api.models import (
    PipelineAdvanceRequest,
    PipelineApplication,
    PipelineListResponse,
    PipelineRemindersResponse,
)
from src.repositories.database import JobDatabase

router = APIRouter(tags=["pipeline"])

_VALID_STAGES = {"applied", "outreach", "interview", "offer", "rejected"}


def _to_pipeline_application(row: dict) -> PipelineApplication:
    return PipelineApplication(
        job_id=row["job_id"],
        stage=row["stage"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        notes=row.get("notes", ""),
        title=row.get("title", ""),
        company=row.get("company", ""),
    )


@router.get("/pipeline", response_model=PipelineListResponse)
async def list_pipeline(
    stage: Optional[str] = Query(None),
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    """List caller's tracked job applications, optionally filtered by stage."""
    rows = await db.get_applications(user.id, stage)
    return PipelineListResponse(
        applications=[_to_pipeline_application(r) for r in rows]
    )


@router.get("/pipeline/counts")
async def pipeline_counts(
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    """Return application counts per pipeline stage, scoped to caller."""
    counts = await db.get_application_counts(user.id)
    defaults = {stage: 0 for stage in _VALID_STAGES}
    defaults.update(counts)
    return defaults


@router.get("/pipeline/reminders", response_model=PipelineRemindersResponse)
async def pipeline_reminders(
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    """Return the caller's stale applications (no update in 7+ days)."""
    rows = await db.get_stale_applications(user.id, days=7)
    return PipelineRemindersResponse(
        reminders=[_to_pipeline_application(r) for r in rows]
    )


@router.post("/pipeline/{job_id}", response_model=PipelineApplication)
async def create_application(
    job_id: int,
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    """Add a job to the caller's application pipeline (stage: applied)."""
    job = await db.get_job_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    row = await db.create_application(job_id, user.id)
    return _to_pipeline_application(row)


@router.post("/pipeline/{job_id}/advance", response_model=PipelineApplication)
async def advance_application(
    job_id: int,
    body: PipelineAdvanceRequest,
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    """Advance the caller's application to the specified pipeline stage."""
    if body.stage not in _VALID_STAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid stage '{body.stage}'. Must be one of: {sorted(_VALID_STAGES)}",
        )
    row = await db.advance_application(job_id, body.stage, user.id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Application for job {job_id} not found")
    return _to_pipeline_application(row)
