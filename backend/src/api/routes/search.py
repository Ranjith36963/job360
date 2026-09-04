"""Search routes for Job360 FastAPI backend.

Batch 3.5.1: gate both routes with `Depends(require_user)` and scope
each `_runs[run_id]` record to the creating user via a stored
`user_id` field. Cross-user reads return 404 (not 403) — existence
hiding so run_id enumeration gives no oracle.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_deps import CurrentUser, require_user, require_verified_user
from src.api.models import SearchStartResponse, SearchStatusResponse
from src.core import settings
from src.main import run_search
from src.utils.logger import get_audit_logger, get_logger

logger = get_logger("api.search")

router = APIRouter(tags=["search"])


# Hard ceiling on connecting to Redis when starting a search. Notifications are
# best-effort, so a slow/dead queue must never add latency to a user's search.
_ENQUEUE_CONNECT_TIMEOUT_SECONDS = 2


async def _make_notification_enqueue() -> tuple[
    Optional[Callable[..., Awaitable[Any]]],
    Optional[Callable[[], Awaitable[None]]],
]:
    """SI1 — build the ARQ enqueue hook that turns a search into notifications.

    Returns ``(enqueue, close)`` where ``enqueue(function_name, *args)`` queues a
    job for the ARQ worker, or ``(None, None)`` when notifications cannot be
    delivered — no ``REDIS_URL``, arq missing, or Redis unreachable.

    ``(None, None)`` is the SAFE default and the pre-SI1 behaviour: run_search
    then skips the notification step entirely, so a deployment without a worker
    (or a Redis outage) degrades to "search works, nobody is notified" rather
    than failing the search. Conversely, the moment a worker + Redis exist this
    lights up with NO code change.

    A short-lived per-run pool (rather than an app-wide one) is deliberate: a
    search is user-triggered and infrequent, and this avoids sharing one Redis
    connection across concurrent background tasks. arq is imported lazily per
    CLAUDE.md rule #11 (never pay the import cost on an unrelated request).
    """
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        return None, None

    # `pool` is created on FIRST ACTUAL ENQUEUE, not here. Connecting eagerly put
    # Redis on the critical path of every search: a configured-but-dead Redis
    # stalled the run before it started, and most runs never notify anyone at
    # all, so the connection was usually pure waste. Connecting lazily means a
    # dead queue costs nothing until there is genuinely something to send.
    state: dict[str, Any] = {"pool": None, "broken": False}

    async def _enqueue(function_name: str, *args: Any, **kwargs: Any) -> Any:
        """Queue one notification. Never raises — returns None if it can't."""
        if state["broken"]:
            return None
        if state["pool"] is None:
            try:
                from arq import create_pool  # noqa: PLC0415 — lazy import (rule #11)
                from arq.connections import RedisSettings  # noqa: PLC0415

                # Fail fast: arq defaults to conn_retries=5 / conn_retry_delay=1,
                # i.e. ~10s of retrying. wait_for is a hard ceiling on top.
                redis_settings = RedisSettings.from_dsn(redis_url)
                redis_settings.conn_retries = 0
                redis_settings.conn_timeout = _ENQUEUE_CONNECT_TIMEOUT_SECONDS
                state["pool"] = await asyncio.wait_for(
                    create_pool(redis_settings),
                    timeout=_ENQUEUE_CONNECT_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 — Redis down must never fail a search
                state["broken"] = True  # don't retry per-job for the rest of the run
                logger.warning(
                    "SI1: notifications skipped for this run — Redis/arq unavailable (%s)",
                    type(exc).__name__,
                )
                return None
        try:
            return await state["pool"].enqueue_job(function_name, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — a failed send must not fail the search
            logger.warning("SI1: enqueue failed (%s)", type(exc).__name__)
            return None

    async def _close() -> None:
        pool = state["pool"]
        if pool is None:  # never connected — nothing to clean up
            return
        # redis-py 5 renamed close() -> aclose(); support both.
        closer = getattr(pool, "aclose", None) or getattr(pool, "close", None)
        if closer is None:
            return
        result = closer()
        if hasattr(result, "__await__"):
            await result

    return _enqueue, _close

# Module-level in-memory store. Pure-process, not persisted across
# restarts — search runs are ephemeral poll targets. Each record carries
# a `user_id` so GET can reject cross-user reads with a 404.
_runs: dict[str, dict[str, Any]] = {}

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

# Strong references to in-flight background search tasks. Without this the event
# loop's weak reference is the ONLY one, and the garbage collector may reclaim a
# running search. Entries remove themselves via add_done_callback, so this stays
# bounded by the number of concurrently-running searches (already capped per user
# by MAX_CONCURRENT_SEARCHES_PER_USER).
_search_bg_tasks: set[asyncio.Task[None]] = set()


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
) -> SearchStartResponse:
    """Start an async job search run. Returns a run_id to poll for status.

    The run_id record is tagged with `user.id`; only the creating user
    can later read its status.

    Step-1 B12: enforces ``MAX_CONCURRENT_SEARCHES_PER_USER``. If the
    caller already has that many runs with status ``pending``/``running``
    queued, returns HTTP 429. Counting is per-user, so other users are
    unaffected by one user's burst.
    """
    if not settings.SEARCH_UI_ENABLED:
        # R12 — hidden feature, not a security control (S10): the route keeps
        # its existing Depends(require_verified_user) regardless of the flag.
        raise HTTPException(status_code=404, detail="Not Found")

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

    async def _run() -> None:
        # SI1 — build the notification fan-out hook. Returns (None, None) when
        # REDIS_URL is unset or Redis is unreachable, in which case the search
        # runs exactly as before (no notifications). Once a worker + Redis are
        # deployed this lights up automatically — no code change needed.
        enqueue, close_pool = await _make_notification_enqueue()
        try:
            _runs[run_id]["progress"] = "Fetching from sources..."
            # Pass the logged-in user so the pipeline scores against THEIR
            # profile, not the default tenant's (E2E_TEST_REPORT #1).
            # no_notify mirrors enqueue availability: with a live queue we WANT
            # main.run_search to call _enqueue_notifications for the feed rows
            # it writes; without one there is nothing to enqueue to.
            result = await run_search(
                source_filter=source,
                no_notify=enqueue is None,
                user_id=user.id,
                enqueue=enqueue,
            )
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
        finally:
            if close_pool is not None:
                try:
                    await close_pool()
                except Exception:  # noqa: BLE001 — never fail a run on cleanup
                    logger.debug("notification pool close failed", exc_info=True)

    # Hold a STRONG reference to the task. asyncio keeps only a weak one, so a
    # task nobody references "may get garbage collected at any time, even before
    # it is done" (CPython docs). `_run()` is the ENTIRE pipeline — fetch, score,
    # dedup, feed write, notify — so losing it mid-flight stops the search with
    # no exception and no log line, while `_runs[run_id]["status"]` stays
    # "running" forever and the user watches a spinner that will never resolve.
    #
    # profile.py:55-90 already does exactly this for its background re-score,
    # and its comment describes the pattern as a "mirror of search.py" — the fix
    # was written one file over and never applied to the file it was named after.
    task = asyncio.create_task(_run())
    _search_bg_tasks.add(task)
    task.add_done_callback(_search_bg_tasks.discard)
    get_audit_logger().info(
        "search_started",
        extra={"event": "search_started", "run_id": run_id, "user_id": user.id, "source": source},
    )
    return SearchStartResponse(run_id=run_id, status="running")


@router.get("/search/{run_id}/status", response_model=SearchStatusResponse)
async def search_status(
    run_id: str,
    user: CurrentUser = Depends(require_user),  # noqa: B008  # FastAPI dep idiom
) -> SearchStatusResponse:
    """Poll the status of a running or completed search.

    Existence-hiding: unknown run_id OR run owned by a different user
    both return 404 with the same body. An attacker enumerating run_ids
    cannot distinguish "does not exist" from "exists but not mine".
    """
    if not settings.SEARCH_UI_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")

    run = _runs.get(run_id)
    if run is None or run.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="run not found")
    # Strip user_id/created_at from the response payload — they're internal
    # scoping/eviction fields, not part of the public SearchStatusResponse
    # contract.
    payload = {k: v for k, v in run.items() if k not in ("user_id", "created_at")}
    return SearchStatusResponse(run_id=run_id, **payload)
