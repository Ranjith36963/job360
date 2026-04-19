"""Jobs list, export, and detail endpoints."""
import csv
import io
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_db
from src.api.models import JobResponse, JobListResponse
from src.repositories.database import JobDatabase

router = APIRouter(tags=["jobs"])


def _compute_bucket(date_found: str) -> str:
    """Return time bucket string from date_found ISO string."""
    if not date_found:
        return "7d"
    try:
        # Parse with or without timezone info
        dt = datetime.fromisoformat(date_found)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        hours_ago = (now - dt).total_seconds() / 3600
        if hours_ago <= 24:
            return "24h"
        elif hours_ago <= 48:
            return "48h"
        elif hours_ago <= 72:
            return "3d"
        elif hours_ago <= 120:
            return "5d"
        else:
            return "7d"
    except (ValueError, TypeError):
        return "7d"


def _row_to_job_response(row: dict, action: str | None = None) -> JobResponse:
    salary = None
    if row.get("salary_min") and row.get("salary_max"):
        salary = f"{int(row['salary_min'])}-{int(row['salary_max'])}"
    elif row.get("salary_min"):
        salary = str(int(row["salary_min"]))
    elif row.get("salary_max"):
        salary = str(int(row["salary_max"]))
    return JobResponse(
        id=row.get("id", 0),
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", ""),
        salary=salary,
        match_score=row.get("match_score", 0),
        source=row.get("source", ""),
        date_found=row.get("date_found", ""),
        apply_url=row.get("apply_url", ""),
        visa_flag=bool(row.get("visa_flag", 0)),
        experience_level=row.get("experience_level", ""),
        action=action,
        bucket=_compute_bucket(row.get("date_found", "")),
    )


@router.get("/jobs/export")
async def export_jobs(
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    """Download all recent jobs as CSV (catalog is shared; auth gates access)."""
    rows = await db.get_recent_jobs(days=7, min_score=0)
    output = io.StringIO()
    headers = [
        "job_title", "company", "location", "salary",
        "match_score", "apply_url", "source", "date_found", "visa_flag",
    ]
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        salary = None
        if row.get("salary_min") and row.get("salary_max"):
            salary = f"{int(row['salary_min'])}-{int(row['salary_max'])}"
        elif row.get("salary_min"):
            salary = str(int(row["salary_min"]))
        elif row.get("salary_max"):
            salary = str(int(row["salary_max"]))
        writer.writerow({
            "job_title": row.get("title", ""),
            "company": row.get("company", ""),
            "location": row.get("location", ""),
            "salary": salary or "",
            "match_score": row.get("match_score", 0),
            "apply_url": row.get("apply_url", ""),
            "source": row.get("source", ""),
            "date_found": row.get("date_found", ""),
            "visa_flag": row.get("visa_flag", 0),
        })
    output.seek(0)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"job360_export_{today}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    hours: Optional[int] = Query(None),
    min_score: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    bucket: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    visa_only: Optional[bool] = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    days = (hours // 24) + 1 if hours else 7
    all_rows = await db.get_recent_jobs(days=days, min_score=min_score or 0)

    # Filter by hours cutoff
    if hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        filtered = []
        for row in all_rows:
            date_str = row.get("date_found", "")
            try:
                dt = datetime.fromisoformat(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    filtered.append(row)
            except (ValueError, TypeError):
                filtered.append(row)
        all_rows = filtered

    # Filter by source
    if source:
        all_rows = [r for r in all_rows if r.get("source", "") == source]

    # Filter by visa_only
    if visa_only:
        all_rows = [r for r in all_rows if bool(r.get("visa_flag", 0))]

    # Filter by bucket
    if bucket:
        all_rows = [r for r in all_rows if _compute_bucket(r.get("date_found", "")) == bucket]

    total = len(all_rows)
    page = all_rows[offset: offset + limit]

    jobs = []
    for row in page:
        job_action = await db.get_action_for_job(row["id"], user.id)
        # Filter by action if specified
        if action is not None and job_action != action:
            continue
        jobs.append(_row_to_job_response(row, job_action))

    filters_applied: dict = {}
    if hours is not None:
        filters_applied["hours"] = hours
    if min_score is not None:
        filters_applied["min_score"] = min_score
    if source:
        filters_applied["source"] = source
    if bucket:
        filters_applied["bucket"] = bucket
    if action:
        filters_applied["action"] = action
    if visa_only:
        filters_applied["visa_only"] = visa_only

    return JobListResponse(jobs=jobs, total=total, filters_applied=filters_applied)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    row = await db.get_job_by_id(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    job_action = await db.get_action_for_job(job_id, user.id)
    return _row_to_job_response(row, job_action)
