"""Step-3 B-02 — per-user per-channel notification rule CRUD.

Four endpoints:
  GET    /settings/notification-rules          list caller's rules
  POST   /settings/notification-rules          create or upsert (by user+channel)
  PATCH  /settings/notification-rules/{rule_id} partial update
  DELETE /settings/notification-rules/{rule_id} delete (IDOR-safe)

All routes:
  * gate on ``require_user`` — user.id is the only accepted identity source
    (CLAUDE.md rule #12).
  * PATCH/DELETE scope their WHERE clause with ``AND user_id = ?`` so a caller
    cannot read or modify another user's rules (IDOR guard).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_db
from src.api.models import (
    NotificationRule,
    NotificationRuleCreate,
    NotificationRuleListResponse,
    NotificationRuleUpdate,
)
from src.repositories.database import JobDatabase

router = APIRouter(tags=["notification-rules"])


def _rule_from_row(row: dict) -> NotificationRule:
    """Convert a DB row dict to a NotificationRule response model."""
    return NotificationRule(
        id=row["id"],
        user_id=row["user_id"],
        channel=row["channel"],
        score_threshold=row["score_threshold"],
        notify_mode=row["notify_mode"],
        quiet_hours_start=row.get("quiet_hours_start"),
        quiet_hours_end=row.get("quiet_hours_end"),
        digest_send_time=row.get("digest_send_time"),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get(
    "/settings/notification-rules",
    response_model=NotificationRuleListResponse,
)
async def list_notification_rules(
    db: JobDatabase = Depends(get_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> NotificationRuleListResponse:
    """Return all notification rules for the authenticated user."""
    rows = await db.get_notification_rules(user.id)
    return NotificationRuleListResponse(rules=[_rule_from_row(r) for r in rows])


@router.post(
    "/settings/notification-rules",
    response_model=NotificationRule,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_rule(
    body: NotificationRuleCreate,
    db: JobDatabase = Depends(get_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> NotificationRule:
    """Create or upsert a notification rule for the given channel.

    If a rule already exists for (user, channel), it is replaced in-place
    with the new settings (upsert semantics via UNIQUE(user_id, channel)).
    """
    row = await db.upsert_notification_rule(user.id, body.model_dump())
    return _rule_from_row(row)


@router.patch(
    "/settings/notification-rules/{rule_id}",
    response_model=NotificationRule,
)
async def update_notification_rule(
    rule_id: int,
    body: NotificationRuleUpdate,
    db: JobDatabase = Depends(get_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> NotificationRule:
    """Partially update a notification rule.

    Only fields supplied in the request body are changed. Returns 404 when
    the rule does not exist or belongs to a different user.
    """
    row = await db.update_notification_rule(rule_id, user.id, body.model_dump())
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notification rule not found",
        )
    return _rule_from_row(row)


@router.delete(
    "/settings/notification-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification_rule(
    rule_id: int,
    db: JobDatabase = Depends(get_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> None:
    """Delete a notification rule.

    Returns 404 when the rule does not exist or belongs to a different user.
    """
    deleted = await db.delete_notification_rule(rule_id, user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notification rule not found",
        )
