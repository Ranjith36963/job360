"""Pipeline (application tracker) routes for Job360 FastAPI backend.

User-scoped per CLAUDE.md rule #12 — every endpoint requires a
valid session and queries are filtered by user.id.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.models import (
    ApplicationTimelineResponse,
    PipelineAdvanceRequest,
    PipelineApplication,
    PipelineListResponse,
    PipelineRemindersResponse,
    TimelineEntry,
)
from src.repositories.database import JobDatabase
from src.utils.logger import get_audit_logger

router = APIRouter(tags=["pipeline"])

_VALID_STAGES = {"applied", "outreach", "interview", "offer", "rejected"}


def _to_pipeline_application(row: dict[str, Any]) -> PipelineApplication:
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
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_user),
) -> PipelineListResponse:
    """List caller's tracked job applications, optionally filtered by stage."""
    rows = await db.get_applications(user.id, stage)
    return PipelineListResponse(applications=[_to_pipeline_application(r) for r in rows])


@router.get("/pipeline/counts")
async def pipeline_counts(
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_user),
) -> dict[str, int]:
    """Return application counts per pipeline stage, scoped to caller."""
    counts = await db.get_application_counts(user.id)
    defaults = {stage: 0 for stage in _VALID_STAGES}
    defaults.update(counts)
    return defaults


@router.get("/pipeline/reminders", response_model=PipelineRemindersResponse)
async def pipeline_reminders(
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_user),
) -> PipelineRemindersResponse:
    """Return the caller's stale applications (no update in 7+ days)."""
    rows = await db.get_stale_applications(user.id, days=7)
    return PipelineRemindersResponse(reminders=[_to_pipeline_application(r) for r in rows])


@router.post("/pipeline/{job_id}", response_model=PipelineApplication)
async def create_application(
    job_id: int,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_user),
) -> PipelineApplication:
    """Add a job to the caller's application pipeline (stage: applied).

    R-2: ``get_job_by_id`` does not filter on ``staleness_state`` (only
    ``get_job_by_id_with_enrichment`` carries the C-1 fix). Refuse to
    create a pipeline row for a confirmed-expired listing — return 410
    Gone so the frontend can drop the stale card from the UI.
    """
    job = await db.get_job_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("staleness_state") == "confirmed_expired":
        raise HTTPException(
            status_code=410,
            detail=f"Job {job_id} is no longer accepting applications",
        )
    row = await db.create_application(job_id, user.id)
    get_audit_logger().info(
        "pipeline_create",
        extra={"event": "pipeline_create", "job_id": job_id, "user_id": user.id, "stage": "applied", "status": "ok"},
    )
    return _to_pipeline_application(row)


@router.post("/pipeline/{job_id}/advance", response_model=PipelineApplication)
async def advance_application(
    job_id: int,
    body: PipelineAdvanceRequest,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_user),
) -> PipelineApplication:
    """Advance the caller's application to the specified pipeline stage."""
    if body.stage not in _VALID_STAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid stage '{body.stage}'. Must be one of: {sorted(_VALID_STAGES)}",
        )
    row = await db.advance_application(job_id, body.stage, user.id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Application for job {job_id} not found")
    get_audit_logger().info(
        "pipeline_advance",
        extra={"event": "pipeline_advance", "job_id": job_id, "user_id": user.id, "stage": body.stage, "status": "ok"},
    )
    return _to_pipeline_application(row)


@router.get("/pipeline/{job_id}/timeline", response_model=ApplicationTimelineResponse)
async def get_pipeline_timeline(
    job_id: int,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_user),
) -> ApplicationTimelineResponse:
    """Return the stage history for this application, scoped to caller."""
    rows = await db.get_application_timeline(job_id, user.id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No timeline for job {job_id}")
    return ApplicationTimelineResponse(
        job_id=job_id,
        timeline=[TimelineEntry(**r) for r in rows],
    )


class NotesUpdateRequest(BaseModel):
    notes: str = Field(max_length=2000)


@router.patch("/pipeline/{job_id}/notes", response_model=PipelineApplication)
async def update_notes(
    job_id: int,
    body: NotesUpdateRequest,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_user),
) -> PipelineApplication:
    """Update the latest notes for an application, archiving the previous value."""
    row = await db.update_application_notes(job_id, user.id, body.notes)
    if not row:
        raise HTTPException(status_code=404, detail=f"Application for job {job_id} not found")
    return _to_pipeline_application(row)
