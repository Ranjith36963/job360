"""Channels & Notifications overhaul — single per-user notification rule.

Two endpoints:
  GET  /settings/notification-rule   get the caller's rule (404 if none exists)
  PUT  /settings/notification-rule   upsert (create or replace) the rule

All routes gate on ``require_user`` (CLAUDE.md rule #12).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_db
from src.api.models import NotificationRule, NotificationRuleUpdate
from src.repositories.database import JobDatabase

router = APIRouter(tags=["notification-rules"])


def _rule_from_row(row: dict) -> NotificationRule:
    """Convert a DB row dict to a NotificationRule response model."""
    return NotificationRule(
        user_id=row["user_id"],
        score_threshold=row["score_threshold"],
        notify_mode=row["notify_mode"],
        interval_hours=row.get("interval_hours", 6),
        daily_send_time=row.get("daily_send_time", "08:00"),
        quiet_hours_start=row.get("quiet_hours_start"),
        quiet_hours_end=row.get("quiet_hours_end"),
        last_sent_at=row.get("last_sent_at"),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get(
    "/settings/notification-rule",
    response_model=NotificationRule,
)
async def get_notification_rule(
    db: JobDatabase = Depends(get_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> NotificationRule:
    """Return the authenticated user's notification rule, or 404 if none set."""
    row = await db.get_user_notification_rule(user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notification rule not found",
        )
    return _rule_from_row(row)


@router.put(
    "/settings/notification-rule",
    response_model=NotificationRule,
)
async def upsert_notification_rule(
    body: NotificationRuleUpdate,
    db: JobDatabase = Depends(get_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> NotificationRule:
    """Create or update the authenticated user's single notification rule.

    Fields not supplied keep their current value (or schema default on first
    create). Uses upsert semantics via UNIQUE(user_id).
    """
    # Load existing rule to merge with update fields
    existing = await db.get_user_notification_rule(user.id) or {}
    data = {
        "score_threshold": existing.get("score_threshold", 60),
        "notify_mode": existing.get("notify_mode", "instant"),
        "interval_hours": existing.get("interval_hours", 6),
        "daily_send_time": existing.get("daily_send_time", "08:00"),
        "quiet_hours_start": existing.get("quiet_hours_start"),
        "quiet_hours_end": existing.get("quiet_hours_end"),
        "enabled": bool(existing.get("enabled", True)),
    }
    update_dict = body.model_dump(exclude_unset=True)
    data.update(update_dict)
    row = await db.save_user_notification_rule(user.id, data)
    return _rule_from_row(row)
