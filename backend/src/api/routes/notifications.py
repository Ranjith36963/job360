"""Step-1.5 S3-D — paginated notification ledger endpoint.

Exposes ``notification_ledger`` rows (Batch 2 migration 0004) to the
authenticated user. Per CLAUDE.md rule #12 the route reads ``user.id``
from the session, never from a URL parameter — and the database reader
scopes its WHERE clause by that user_id.

The Step-1.5 plan §non-scope explicitly defers a ``body`` column on
``notification_ledger`` so this endpoint surfaces metadata only:
status + timestamp + retry count. The frontend ledger page (Step 2 S4)
can render a meaningful history without that.

Step-3 O-01: added ``job_id``, ``start_time``, ``end_time`` query filters.
Step-3 O-02: added ``GET /notifications/stats`` per-channel aggregation.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.models import NotificationLedgerEntry, NotificationLedgerListResponse
from src.repositories.database import JobDatabase
from src.services.notifications.unsubscribe import verify_token

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationLedgerListResponse)
async def list_notifications(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    channel: Optional[str] = Query(None, description="Filter by channel name"),
    status: Optional[str] = Query(None, description="Filter by status: queued/sent/failed/dlq"),
    # Step-3 O-01 — additional filters
    job_id: Optional[int] = Query(None, description="Filter by job id"),
    start_time: Optional[str] = Query(None, description="ISO-8601 lower bound on created_at"),
    end_time: Optional[str] = Query(None, description="ISO-8601 upper bound on created_at"),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
) -> NotificationLedgerListResponse:
    """Return the caller's most recent notification ledger entries,
    paginated by ``limit`` + ``offset``. Sorted by created_at DESC.

    A 200 with an empty ``notifications`` list is returned when the
    caller has no notifications yet — empty-state UX preferred over 404.

    Optional filters: ``channel``, ``status``, ``job_id``, ``start_time``,
    ``end_time`` (ISO-8601 strings for created_at range).
    """
    rows = await db.get_notification_ledger(
        user_id=user.id,
        limit=limit,
        offset=offset,
        channel=channel,
        status=status,
        job_id=job_id,
        start_time=start_time,
        end_time=end_time,
    )
    total = await db.count_notification_ledger(
        user_id=user.id,
        channel=channel,
        status=status,
        job_id=job_id,
        start_time=start_time,
        end_time=end_time,
    )
    entries = [NotificationLedgerEntry(**row) for row in rows]
    return NotificationLedgerListResponse(
        notifications=entries,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/notifications/stats")
async def notification_stats(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, dict[str, int]]:
    """Step-3 O-02 — per-channel success/failure aggregation.

    Returns ``{channel: {sent: N, failed: M, queued: P, ...}}``.
    Always 200 — empty dict when no ledger rows exist yet.
    """
    return await db.get_notification_ledger_stats(user_id=user.id)

class UnsubscribeRequest(BaseModel):
    token: str


@router.post("/notifications/unsubscribe")
async def unsubscribe(
    body: UnsubscribeRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI DI idiom
) -> dict[str, bool]:
    """Turn ALL notifications off for the user a signed token names (W-23).

    NO SESSION REQUIRED, on purpose. Someone who wants the emails to stop is often
    exactly the person who will not log in to make it happen — and a recipient who
    cannot find the exit presses "spam" instead, which is the worst signal a sending
    domain can collect. The token IS the authorisation.

    Safe to expose unauthenticated because of what it can do: the ONLY outcome is
    silence. It cannot read anything, cannot change an address, and cannot be used to
    reach an account. A leaked token buys an attacker the ability to stop someone's
    email, which the owner reverses by logging in and switching it back on.

    POST, never GET. Email clients and security scanners prefetch links, and a
    state-changing GET would unsubscribe people who never clicked — the same trap the
    magic-link landing page already solves with a confirm button. The emailed URL
    points at a frontend page; that page POSTs here when the human presses the button.

    Idempotent: unsubscribing twice is a success, not an error. A retry, a double-click
    or a second visit to an old email must not produce a scary failure page.
    """
    user_id = verify_token(body.token)
    if user_id is None:
        # One message for forged AND malformed. Distinguishing them would let
        # someone probe which user ids exist.
        raise HTTPException(status_code=400, detail="This unsubscribe link is not valid.")
    await db.set_notifications_enabled(user_id, enabled=False)
    return {"unsubscribed": True}
