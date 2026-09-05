"""Bring-a-job: the user pastes the ad, we do everything after the click.

Product rule 4 (`docs/product/VISION.md`): Job360 never sources, ranks or
judges a job. The user's own agent finds the ad and decides whether it fits;
this route is the front door where that ad becomes something we REMEMBER.

Since slice 5 (#483) it stores and nothing else: no score, no shelf gate, no
feed row. `POST /jobs/bring` writes the `jobs` row and births the Application
(`services/applications/spine.py`) — the agent records its own verdict
afterwards with `save_fit`.

Why paste, not a URL fetch, on day one: LinkedIn/Indeed/Workday refuse bots,
and fetching arbitrary user URLs is an SSRF surface that needs its own guard.
A form that fails on the three biggest boards is worse than a form that asks
for a paste. The link is kept as `apply_url` so the receipt can point back.

Storage follows rule #10: the ad goes into the SHARED `jobs` catalog under
`source='user_brought'` (no user_id on `jobs`); "this user is tracking it"
lives in `applications`. Two users pasting the same (company, title) share one
row — `insert_job` is INSERT-OR-IGNORE on the normalized key, and
`existing=True` tells the caller.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.models import JobResponse
from src.core.settings import USER_BROUGHT_SOURCE
from src.repositories.database import JobDatabase
from src.utils.logger import get_audit_logger

router = APIRouter(tags=["bring"])


def job_row_to_response(row: dict[str, Any]) -> JobResponse:
    """Build a `JobResponse` straight off a `jobs` row.

    Was `routes/jobs.py::_row_to_job_response`, which merged the row with a
    per-user feed score and an enrichment LEFT JOIN. Both are gone; what is
    left is a plain projection of the stored ad, which is the whole contract
    now (product rule 4 — nothing here is computed about the user).

    Lives here, not on `JobResponse`, because `models.py` is the API's data
    shapes and knows nothing about DB row keys.
    """
    salary = None
    smin, smax = row.get("salary_min"), row.get("salary_max")
    if smin and smax:
        salary = f"{int(smin)}-{int(smax)}"
    elif smin:
        salary = str(int(smin))
    elif smax:
        salary = str(int(smax))

    return JobResponse(
        id=row.get("id", 0),
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", "") or "",
        salary=salary,
        source=row.get("source", ""),
        date_found=row.get("date_found", "") or "",
        apply_url=row.get("apply_url", "") or "",
        visa_flag=bool(row.get("visa_flag", 0)),
        experience_level=row.get("experience_level", "") or "",
        description=row.get("description") or None,
        posted_at=row.get("posted_at"),
        first_seen_at=row.get("first_seen_at"),
        last_seen_at=row.get("last_seen_at"),
        date_confidence=row.get("date_confidence"),
        deadline=row.get("deadline"),
        deadline_source=row.get("deadline_source"),
    )

# Bounds are guardrails, not product limits: a real ad is 500–8,000 chars; the
# cap stops a pasted PDF dump (or a hostile 50 MB body) from becoming a
# catalog row that every later read has to carry.
_MAX_TEXT = 40_000
_MAX_FIELD = 300


class BringJobRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=_MAX_FIELD)
    company: str = Field(..., min_length=1, max_length=_MAX_FIELD)
    description: str = Field(..., min_length=1, max_length=_MAX_TEXT)
    location: str = Field("", max_length=_MAX_FIELD)
    apply_url: str = Field("", max_length=2_000)

    @field_validator("title", "company", "location", "apply_url")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("title", "company", "description")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # `min_length` runs on the raw value, so "   " passes it and would be
        # stored as an empty title after the strip above. Check after stripping.
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("apply_url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        # A link we later render as <a href> must be a web link — never
        # javascript:, data:, or a bare path the browser would resolve to us.
        if v and not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("apply_url must start with http:// or https://")
        return v


class BringJobResponse(BaseModel):
    job: JobResponse
    existing: bool   # the same (company, title) was already in the catalog
    # spec 2026-09-04-application-spine R1 — an Application is born HERE.
    application_id: int
    status: str


@router.post("/jobs/bring", response_model=BringJobResponse)
async def bring_job(
    body: BringJobRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI DI idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> BringJobResponse:
    """Store the pasted ad and birth the Application for it.

    Nothing is scored: the caller (the user's agent) already decided this ad
    is worth keeping, and product rule 4 forbids us judging fit. Returns the
    stored job plus the `application_id` the caller should work against from
    here on.
    """
    # Lazy import (rule #16) — keeps the spine off the module import path.
    from src.models import Job  # noqa: PLC0415
    from src.services.applications import spine as applications_spine  # noqa: PLC0415
    from src.services.applications.authorship import actor_for  # noqa: PLC0415

    now = datetime.now(timezone.utc).isoformat()
    job = Job(
        title=body.title,
        company=body.company,
        apply_url=body.apply_url,
        source=USER_BROUGHT_SOURCE,
        date_found=now,
        location=body.location,
        description=body.description,
        # The user is reading the ad right now: it is live, and "now" is the
        # most honest posting date we have (low confidence, stated as such).
        posted_at=now,
        date_confidence="low",
        first_seen_at=now,
        last_seen_at=now,
    )
    inserted = await db.insert_job(job)
    await db.commit()
    if not inserted:
        # Same key already in the catalog (a legacy scrape, or an earlier
        # paste). The user is reading this ad right now, so it is LIVE: clear
        # the stale mark a long-dead ghost sweep may have left on the row,
        # otherwise `POST /pipeline/{job_id}` still refuses it as expired.
        await db.update_last_seen(job.normalized_key())
    job_id = await db.get_job_id_by_key(job.normalized_key())
    if job_id is None:
        # insert_job reports "inserted or not", never WHY not; a swallowed
        # insert failure lands here. Say so instead of dying on an assert.
        raise HTTPException(status_code=500, detail="Could not store the job")

    row = await db.get_job_by_id(job_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Could not read the job back")

    # R1/R2 — the Application is born HERE, in the same request: upsert the
    # `applications` row for (user, job) with status='considering', copy the
    # ad onto it (the user's own snapshot, independent of the catalog row),
    # append a `brought` event. Bringing the same job twice reuses the row and appends
    # no second event (birth_application reads-before-inserting).
    birth = await applications_spine.birth_application(
        db, user_id=user.id, job_id=job_id, job_row=dict(row), recorded_by=actor_for(user),
    )

    get_audit_logger().info(
        "job_brought",
        extra={
            "event": "job_brought", "job_id": job_id, "user_id": user.id,
            "existing": not inserted, "status": "ok",
        },
    )
    return BringJobResponse(
        job=job_row_to_response(dict(row)),
        existing=not inserted,
        application_id=birth["application_id"],
        status=birth["status"],
    )
