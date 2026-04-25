"""Step-1.5 S3-D — paginated notification ledger endpoint.

Exposes ``notification_ledger`` rows (Batch 2 migration 0004) to the
authenticated user. Per CLAUDE.md rule #12 the route reads ``user.id``
from the session, never from a URL parameter — and the database reader
scopes its WHERE clause by that user_id.

The Step-1.5 plan §non-scope explicitly defers a ``body`` column on
``notification_ledger`` so this endpoint surfaces metadata only:
status + timestamp + retry count. The frontend ledger page (Step 2 S4)
can render a meaningful history without that.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_db
from src.api.models import NotificationLedgerEntry, NotificationLedgerListResponse
from src.repositories.database import JobDatabase

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationLedgerListResponse)
async def list_notifications(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    channel: Optional[str] = Query(None, description="Filter by channel name"),
    status: Optional[str] = Query(None, description="Filter by status: queued/sent/failed/dlq"),
    db: JobDatabase = Depends(get_db),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
):
    """Return the caller's most recent notification ledger entries,
    paginated by ``limit`` + ``offset``. Sorted by created_at DESC.

    A 200 with an empty ``notifications`` list is returned when the
    caller has no notifications yet — empty-state UX preferred over 404.
    """
    rows = await db.get_notification_ledger(
        user_id=user.id,
        limit=limit,
        offset=offset,
        channel=channel,
        status=status,
    )
    total = await db.count_notification_ledger(
        user_id=user.id,
        channel=channel,
        status=status,
    )
    entries = [NotificationLedgerEntry(**row) for row in rows]
    return NotificationLedgerListResponse(
        notifications=entries,
        total=total,
        limit=limit,
        offset=offset,
    )
