"""Search routes for Job360 FastAPI backend.

Batch 3.5.1: gate both routes with `Depends(require_user)` and scope
each `_runs[run_id]` record to the creating user via a stored
`user_id` field. Cross-user reads return 404 (not 403) — existence
hiding so run_id enumeration gives no oracle.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_deps import CurrentUser, require_user, require_verified_user
from src.api.models import SearchStartResponse, SearchStatusResponse
from src.core import settings
from src.main import run_search

router = APIRouter(tags=["search"])

# Module-level in-memory store. Pure-process, not persisted across
# restarts — search runs are ephemeral poll targets. Each record carries
# a `user_id` so GET can reject cross-user reads with a 404.
_runs: dict[str, dict] = {}

# Statuses that count toward the per-user concurrent cap. A run that has
# transitioned to `completed` or `failed` no longer holds compute budget,
# so it must NOT count.
_ACTIVE_STATUSES = frozenset({"pending", "running"})


def _active_run_count_for_user(user_id: str) -> int:
    """Count runs in `_runs` owned by `user_id` that are still in flight."""
    return sum(1 for run in _runs.values() if run.get("user_id") == user_id and run.get("status") in _ACTIVE_STATUSES)


@router.post("/search", response_model=SearchStartResponse)
async def start_search(
    source: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_verified_user),  # noqa: B008 — #15: verified email required
):
    """Start an async job search run. Returns a run_id to poll for status.

    The run_id record is tagged with `user.id`; only the creating user
    can later read its status.

    Step-1 B12: enforces ``MAX_CONCURRENT_SEARCHES_PER_USER``. If the
    caller already has that many runs with status ``pending``/``running``
    queued, returns HTTP 429. Counting is per-user, so other users are
    unaffected by one user's burst.
    """
    if _active_run_count_for_user(user.id) >= settings.MAX_CONCURRENT_SEARCHES_PER_USER:
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent searches; wait for one to finish before starting another.",
        )

    run_id = uuid.uuid4().hex[:12]
    _runs[run_id] = {
        "user_id": user.id,
        "status": "running",
        "progress": "Starting...",
        "result": None,
    }

    async def _run():
        try:
            _runs[run_id]["progress"] = "Fetching from sources..."
            # Pass the logged-in user so the pipeline scores against THEIR
            # profile, not the default tenant's (E2E_TEST_REPORT #1).
            result = await run_search(source_filter=source, no_notify=True, user_id=user.id)
            _runs[run_id].update(status="completed", progress="Done", result=result)
        except Exception as e:
            _runs[run_id].update(status="failed", progress=str(e))

    asyncio.create_task(_run())
    return SearchStartResponse(run_id=run_id, status="running")


@router.get("/search/{run_id}/status", response_model=SearchStatusResponse)
async def search_status(
    run_id: str,
    user: CurrentUser = Depends(require_user),  # noqa: B008  # FastAPI dep idiom
):
    """Poll the status of a running or completed search.

    Existence-hiding: unknown run_id OR run owned by a different user
    both return 404 with the same body. An attacker enumerating run_ids
    cannot distinguish "does not exist" from "exists but not mine".
    """
    run = _runs.get(run_id)
    if run is None or run.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="run not found")
    # Strip user_id from the response payload — it's an internal scoping
    # field, not part of the public SearchStatusResponse contract.
    payload = {k: v for k, v in run.items() if k != "user_id"}
    return SearchStatusResponse(run_id=run_id, **payload)
