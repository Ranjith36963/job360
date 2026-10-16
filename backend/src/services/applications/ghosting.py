"""Automatic ``ghosted`` (feedback loop slice 1, owner decisions #1 and #4).

An application whose status is still exactly ``applied`` and whose newest
status-family event is older than ``settings.GHOSTED_AFTER_DAYS`` gets ONE
``ghosted`` event through the normal spine append (never raw SQL), recorded by
``system``. Idempotent: the status becomes ``ghosted`` so a second sweep finds
nothing. A later employer reply moves it off ``ghosted`` by the ordinary replay
rule (status.replay_status). Called only at the start of doctor (which reports
the ids in ``ghosted_now``); a failure there is logged and never breaks it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from src.core import settings
from src.services.applications import spine
from src.services.applications.stats import _SIMULATED_SQL
from src.services.applications.status import _event_when
from src.utils.logger import get_audit_logger, get_logger, safe_log_value

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase

log = get_logger(__name__)

SYSTEM_ACTOR = "system"


async def sweep_ghosted(db: JobDatabase, user_id: str, now: Optional[datetime] = None) -> list[int]:
    """Mark the user's silent ``applied`` applications ``ghosted``; return the new ids."""
    now = now or datetime.now(timezone.utc)
    days = settings.GHOSTED_AFTER_DAYS
    cutoff = now - timedelta(days=days)
    # `brought` is when the ad was saved, not news from the employer or the user's own applying.
    types = tuple(t for t in settings.APPLICATION_STATUS_EVENT_TYPES if t != "brought")
    marks = ", ".join("?" for _ in types)
    cur = await db._db.execute(
        "SELECT e.application_id, e.occurred_at, e.recorded_at "  # noqa: S608 - placeholders only
        "FROM application_events e JOIN applications a ON a.id = e.application_id "
        "WHERE a.user_id = ? AND a.status = 'applied' AND e.user_id = ? "
        f"AND e.event_type IN ({marks}) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        f"AND NOT {_SIMULATED_SQL.format(app='a.id')}",
        (user_id, user_id, *types, user_id, settings.SIMULATION_EVENT_TYPE),
    )
    newest: dict[int, datetime] = {}
    for row in await cur.fetchall():
        when = _event_when({"occurred_at": row["occurred_at"], "recorded_at": row["recorded_at"]})
        if when is None:
            continue
        app_id = int(row["application_id"])
        if app_id not in newest or when > newest[app_id]:
            newest[app_id] = when
    due = sorted(app_id for app_id, when in newest.items() if when < cutoff)
    marked: list[int] = []
    for app_id in due[: settings.GHOSTED_SWEEP_MAX]:
        try:
            await spine.append_event(
                db,
                user_id=user_id,
                application_id=app_id,
                event_type="ghosted",
                occurred_at=now.isoformat(),
                recorded_by=SYSTEM_ACTOR,
                detail=f"No answer {days} days after applying — marked automatically",
                payload={"auto": True, "days": days},
            )
            marked.append(app_id)
        except Exception:  # noqa: BLE001 - one bad row must not stop the rest
            log.warning(
                "ghost_sweep_event_failed",
                extra={"event": "ghost_sweep_event_failed", "user_id": safe_log_value(user_id),
                       "application_id": app_id},
                exc_info=True,
            )
    get_audit_logger().info(
        "ghost_sweep",
        extra={"event": "ghost_sweep", "user_id": safe_log_value(user_id),
               "candidates": len(due), "marked": len(marked)},
    )
    return marked
