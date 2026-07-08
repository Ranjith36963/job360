"""Channels & Notifications overhaul — single per-user notification rule.

Two endpoints:
  GET  /settings/notification-rule   get the caller's rule (null if none exists)
  PUT  /settings/notification-rule   upsert (create or replace) the rule

All routes gate on ``require_user`` (CLAUDE.md rule #12).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_db
from src.api.models import NotificationRule, NotificationRuleUpdate
from src.repositories.database import JobDatabase
from src.utils.logger import get_audit_logger

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
    response_model=Optional[NotificationRule],
)
async def get_notification_rule(
    db: JobDatabase = Depends(get_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> Optional[NotificationRule]:
    """Return the authenticated user's notification rule.

    Returns ``null`` (HTTP 200) when the user has not saved one yet — the client
    treats that as "no rule, use defaults". Settings is a per-user singleton, so
    "not set" is a normal empty-state, not a 404 error (a 404 would surface as a
    console error in the browser even though the client handles it fine).
    """
    row = await db.get_user_notification_rule(user.id)
    if row is None:
        return None
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
    get_audit_logger().info(
        "notification_rule_saved",
        extra={
            "event": "notification_rule_saved",
            "user_id": user.id,
            "notify_mode": data.get("notify_mode"),
            "score_threshold": data.get("score_threshold"),
            "enabled": data.get("enabled"),
        },
    )
    return _rule_from_row(row)
