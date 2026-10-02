"""Application history lookup."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase

router = APIRouter(tags=["history"])


@router.get("/history/applications")
async def applications_for_user(
    user_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, job_id, status, last_event_at FROM applications WHERE user_id = ? ORDER BY last_event_at DESC",
        (str(user_id),),
    )
    rows = await cur.fetchall()
    return [
        {"id": r[0], "job_id": r[1], "status": r[2], "last_event_at": r[3]}
        for r in rows
    ]
