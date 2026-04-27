"""Run history endpoint — paginated pipeline run log.

Step-3 B-15.  Surfaces the ``run_log`` table to authenticated callers so
the frontend can render a "pipeline history" view without querying SQLite
directly.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_db
from src.repositories.database import JobDatabase

router = APIRouter(tags=["runs"])


class RunEntry(BaseModel):
    """Single ``run_log`` row exposed over the API.

    Maps every column from the baseline + migration-0010 schema.  Optional
    fields may be NULL on rows inserted before the observability migration
    ran; Pydantic coerces them to None rather than raising.
    """

    id: int
    timestamp: str
    total_found: Optional[int] = None
    new_jobs: Optional[int] = None
    sources_queried: Optional[int] = None
    per_source: Optional[str] = None
    run_uuid: Optional[str] = None
    per_source_errors: Optional[str] = None
    per_source_duration: Optional[str] = None
    total_duration: Optional[float] = None
    user_id: Optional[str] = None

    model_config = ConfigDict(extra="ignore")  # absorb any future columns without breaking

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> RunEntry:
        """Build a RunEntry from a raw DB dict, coercing unknown keys out."""
        known = set(cls.model_fields)
        return cls(**{k: v for k, v in row.items() if k in known})


class RunsListResponse(BaseModel):
    runs: list[RunEntry]
    total: int
    limit: int
    offset: int


@router.get("/runs/recent", response_model=RunsListResponse)
async def recent_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: JobDatabase = Depends(get_db),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — rule #12
):
    """Return paginated pipeline run history from run_log, newest first.

    Step-3 B-15.  Requires authentication (rule #12) — the run log is
    operational metadata, not a public catalog surface.
    """
    runs_raw = await db.get_recent_runs(limit=limit, offset=offset)
    total = await db.count_recent_runs()
    return RunsListResponse(
        runs=[RunEntry.from_row(r) for r in runs_raw],
        total=total,
        limit=limit,
        offset=offset,
    )
