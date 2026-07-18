"""Search routes for Job360 FastAPI backend.

Batch 3.5.1: gate both routes with `Depends(require_user)` and scope
each `_runs[run_id]` record to the creating user via a stored
`user_id` field. Cross-user reads return 404 (not 403) — existence
hiding so run_id enumeration gives no oracle.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_deps import CurrentUser, require_user, require_verified_user
from src.api.models import SearchStartResponse, SearchStatusResponse
from src.core import settings
from src.main import run_search
from src.utils.logger import get_audit_logger, get_logger

logger = get_logger("api.search")

router = APIRouter(tags=["search"])

# Module-level in-memory store. Pure-process, not persisted across
# restarts — search runs are ephemeral poll targets. Each record carries
# a `user_id` so GET can reject cross-user reads with a 404.
_runs: dict[str, dict] = {}

# Statuses that count toward the per-user concurrent cap. A run that has
# transitioned to `completed` or `failed` no longer holds compute budget,
# so it must NOT count.
_ACTIVE_STATUSES = frozenset({"pending", "running"})

# N6: `_runs` is unbounded in-memory state — every POST /search adds an
# entry that was never removed. Bound it with a cap + TTL so a long-lived
# process can't leak memory. Only completed/failed entries are ever
# evicted — a run that is still pending/running is NEVER dropped, no
# matter how old, so a live poll target can't disappear out from under
# the client.
_RUNS_MAX = 500
_RUNS_TTL_SECONDS = 3600


def _active_run_count_for_user(user_id: str) -> int:
    """Count runs in `_runs` owned by `user_id` that are still in flight."""
    return sum(1 for run in _runs.values() if run.get("user_id") == user_id and run.get("status") in _ACTIVE_STATUSES)


def _prune_runs(now: Optional[datetime] = None) -> None:
    """Bound `_runs` by TTL + max size. Never evicts an active run.

    Pass 1: drop completed/failed entries older than `_RUNS_TTL_SECONDS`.
    Pass 2: if still over `_RUNS_MAX`, drop the oldest completed/failed
    entries (by `created_at`) until at/under the cap. Entries with status
    in `_ACTIVE_STATUSES` are never touched by either pass.
    """
    now = now or datetime.now(timezone.utc)

    stale_ids = [
        run_id
        for run_id, run in _runs.items()
        if run.get("status") not in _ACTIVE_STATUSES
        and (now - run.get("created_at", now)).total_seconds() > _RUNS_TTL_SECONDS
    ]
    for run_id in stale_ids:
        del _runs[run_id]

    overflow = len(_runs) - _RUNS_MAX
    if overflow > 0:
        evictable = sorted(
            (run_id for run_id, run in _runs.items() if run.get("status") not in _ACTIVE_STATUSES),
            key=lambda run_id: _runs[run_id].get("created_at", now),
        )
        for run_id in evictable[:overflow]:
            del _runs[run_id]


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

    _prune_runs()

    run_id = uuid.uuid4().hex[:12]
    _runs[run_id] = {
        "user_id": user.id,
        "status": "running",
        "progress": "Starting...",
        "result": None,
        "created_at": datetime.now(timezone.utc),
    }

    async def _run():
        try:
            _runs[run_id]["progress"] = "Fetching from sources..."
            # Pass the logged-in user so the pipeline scores against THEIR
            # profile, not the default tenant's (E2E_TEST_REPORT #1).
            #
            # SI1 activation (OWNER DEPLOY STEP): notifications are wired in
            # main.run_search via its ``enqueue`` hook + _enqueue_notifications,
            # but they are INERT here on purpose — this HTTP path passes
            # no_notify=True and no enqueue, so nothing fires without a worker.
            # To turn on per-search notifications once a Railway worker + Redis
            # exist: build an ARQ enqueue (e.g. redis.enqueue_job) and call
            # run_search(..., no_notify=False, enqueue=<enqueue_job>). The code
            # is ready; only the deploy-side Redis/worker wiring is missing.
            result = await run_search(source_filter=source, no_notify=True, user_id=user.id)
            _runs[run_id].update(status="completed", progress="Done", result=result)
            get_audit_logger().info(
                "search_completed",
                extra={"event": "search_completed", "run_id": run_id, "user_id": user.id, "status": "ok"},
            )
        except Exception as e:
            logger.exception("search run %s failed for user=%s", run_id, user.id)
            _runs[run_id].update(status="failed", progress="Search failed, please try again.")
            get_audit_logger().warning(
                "search_failed",
                extra={"event": "search_failed", "run_id": run_id, "user_id": user.id, "error": str(e)},
            )

    asyncio.create_task(_run())
    get_audit_logger().info(
        "search_started",
        extra={"event": "search_started", "run_id": run_id, "user_id": user.id, "source": source},
    )
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
    # Strip user_id/created_at from the response payload — they're internal
    # scoping/eviction fields, not part of the public SearchStatusResponse
    # contract.
    payload = {k: v for k, v in run.items() if k not in ("user_id", "created_at")}
    return SearchStatusResponse(run_id=run_id, **payload)
