"""User action endpoints (like, apply, dismiss)."""
from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_db
from src.api.models import ActionRequest, ActionResponse, ActionsListResponse
from src.repositories.database import JobDatabase

router = APIRouter(tags=["actions"])

_VALID_ACTIONS = {"liked", "applied", "not_interested"}


@router.post("/jobs/{job_id}/action", response_model=ActionResponse)
async def set_action(
    job_id: int,
    body: ActionRequest,
    db: JobDatabase = Depends(get_db),
):
    if body.action not in _VALID_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of: {sorted(_VALID_ACTIONS)}",
        )
    row = await db.get_job_by_id(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    await db.insert_action(job_id, body.action, body.notes)
    return ActionResponse(ok=True, job_id=job_id, action=body.action)


@router.delete("/jobs/{job_id}/action", response_model=ActionResponse)
async def delete_action(job_id: int, db: JobDatabase = Depends(get_db)):
    await db.delete_action(job_id)
    return ActionResponse(ok=True, job_id=job_id, action="")


@router.get("/actions", response_model=ActionsListResponse)
async def list_actions(db: JobDatabase = Depends(get_db)):
    rows = await db.get_actions()
    actions = [
        ActionResponse(ok=True, job_id=r["job_id"], action=r["action"])
        for r in rows
    ]
    return ActionsListResponse(actions=actions)


@router.get("/actions/counts")
async def action_counts(db: JobDatabase = Depends(get_db)):
    counts = await db.get_action_counts()
    return {
        "liked": counts.get("liked", 0),
        "applied": counts.get("applied", 0),
        "not_interested": counts.get("not_interested", 0),
    }
