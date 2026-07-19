"""Run history + per-source health endpoints.

Step-3 B-15 introduced ``/runs/recent``.
Phase −2 item D adds ``/runs/source-health`` — a per-source aggregation
across the most recent runs so a small admin page can spot silent
provider failures (LinkedIn scraper returning 0 jobs for 3 runs in a row,
Workday tripping its breaker etc).
"""

from __future__ import annotations

import json
from collections import defaultdict
from statistics import mean
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_request_db
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
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — rule #12
) -> RunsListResponse:
    """Return paginated pipeline run history from run_log, newest first.

    Step-3 B-15.  Requires authentication (rule #12) — the run log is
    operational metadata, not a public catalog surface.
    """
    runs_raw = await db.get_recent_runs(user_id=user.id, limit=limit, offset=offset)
    total = await db.count_recent_runs(user_id=user.id)
    return RunsListResponse(
        runs=[RunEntry.from_row(r) for r in runs_raw],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── Phase −2 item D — per-source health summary ─────────────────────────────


class SourceHealthEntry(BaseModel):
    """Per-source aggregate across the last N runs."""

    source: str
    runs_observed: int           # number of recent runs this source appeared in
    runs_zero_jobs: int          # runs where source returned 0 results
    runs_errored: int            # runs where source had a per_source_errors entry
    avg_duration_seconds: Optional[float] = None
    last_job_count: Optional[int] = None       # most recent run's count
    last_error: Optional[str] = None           # most recent error message if any
    # A simple traffic-light derived in the route — frontend can override.
    health: str  # "ok" | "warning" | "critical"


class SourceHealthResponse(BaseModel):
    sources: list[SourceHealthEntry]
    runs_considered: int


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    """Tolerantly decode a TEXT column that should be a JSON object."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _classify(zero_streak: int, total: int, errored: int) -> str:
    """Map raw stats to a traffic-light.

    Three buckets, evaluated in order — the first match wins:

    1. ``critical`` — at least 3 consecutive zero-job runs at the head
       (most-recent first). Strong signal the source has stopped working.
    2. ``warning`` — either of:
         a. errored-run ratio > 50% across the window (source is failing
            outright most of the time), OR
         b. zero-job ratio ≥ 33% AND at least one zero-run streak at the
            head (source is intermittently silent).
    3. ``ok`` — anything else.

    Note that a *single* errored run that isn't part of a wider pattern
    won't trigger warning by itself — the >50% threshold is a sustained
    failure signal, not a one-off blip. Phase −2 docstring was previously
    misleading on this point; this is the post-review accurate spec.
    Thresholds tunable later (rule #18-style env override is the natural
    extension once we have multiple consumers).
    """
    if zero_streak >= 3:
        return "critical"
    if total > 0 and errored / total > 0.5:
        return "warning"
    if zero_streak >= 1 and total > 0 and zero_streak / total >= 0.33:
        return "warning"
    return "ok"


@router.get("/runs/source-health", response_model=SourceHealthResponse)
async def source_health(
    runs: int = Query(20, ge=1, le=100, description="How many recent runs to aggregate over."),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> SourceHealthResponse:
    """Aggregate per-source job-count + error stats across recent runs.

    Reads existing run_log columns (per_source, per_source_errors,
    per_source_duration) populated by migrations 0000 + 0010 — no new
    schema. Returns one row per source observed in any of the last ``runs``
    pipeline passes.

    Empty response is valid — fresh installs with zero runs return
    ``{"sources": [], "runs_considered": 0}``.
    """
    rows = await db.get_recent_runs(user_id=user.id, limit=runs, offset=0)
    # Aggregate. Rows come back newest-first; "last" stats come from the
    # first occurrence we see.
    seen: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "runs_observed": 0,
            "runs_zero_jobs": 0,
            "runs_errored": 0,
            "durations": [],
            "last_job_count": None,
            "last_error": None,
            "zero_streak": 0,           # current consecutive 0-job streak (newest backwards)
            "_streak_open": True,        # while True, count zero-streak; ends at first non-zero
        }
    )

    for row in rows:
        per_source = _parse_json_dict(row.get("per_source"))
        per_source_errors = _parse_json_dict(row.get("per_source_errors"))
        per_source_duration = _parse_json_dict(row.get("per_source_duration"))

        observed_in_row = set(per_source) | set(per_source_errors) | set(per_source_duration)
        for name in observed_in_row:
            entry = seen[name]
            entry["runs_observed"] += 1

            count = per_source.get(name)
            if isinstance(count, int):
                if entry["last_job_count"] is None:
                    entry["last_job_count"] = count
                if count == 0:
                    entry["runs_zero_jobs"] += 1
                    if entry["_streak_open"]:
                        entry["zero_streak"] += 1
                else:
                    entry["_streak_open"] = False  # close the streak at first non-zero

            err = per_source_errors.get(name)
            if err:
                entry["runs_errored"] += 1
                if entry["last_error"] is None:
                    entry["last_error"] = str(err)[:200]  # bound the payload

            dur = per_source_duration.get(name)
            if isinstance(dur, (int, float)) and dur >= 0:
                entry["durations"].append(float(dur))

    summaries = [
        SourceHealthEntry(
            source=name,
            runs_observed=entry["runs_observed"],
            runs_zero_jobs=entry["runs_zero_jobs"],
            runs_errored=entry["runs_errored"],
            avg_duration_seconds=(round(mean(entry["durations"]), 3) if entry["durations"] else None),
            last_job_count=entry["last_job_count"],
            last_error=entry["last_error"],
            health=_classify(entry["zero_streak"], entry["runs_observed"], entry["runs_errored"]),
        )
        for name, entry in seen.items()
    ]
    # Stable order: critical → warning → ok, then alphabetical inside.
    priority = {"critical": 0, "warning": 1, "ok": 2}
    summaries.sort(key=lambda s: (priority[s.health], s.source))

    return SourceHealthResponse(sources=summaries, runs_considered=len(rows))
