"""What is due now? - the per-user door the scheduled assistant reads (owner decision 2026-10-23).

``GET /api/schedule/due`` answers which duties (hunt, inbox, batch) are due for THIS user, from
their own settings and the last time each was done; ``POST /api/schedule/done`` stamps one. The
MCP tools ``whats_due`` and ``mark_done`` call these functions (VISION M5: same route the web
uses). Both take the user from the session / token / OAuth subject, never from input (rules
#12/#25), are rate-limited per user, and log only closed values. Rules:
``services/profile/schedule.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications.authorship import actor_for
from src.services.auth import rate_limit
from src.services.profile import schedule
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["schedule"])

Duty = Literal["hunt", "inbox", "batch"]


class DueItemOut(BaseModel):
    duty: Duty
    recipe: Literal["hunt", "daily", "batch"]
    reason: Literal["never_done", "interval_elapsed", "queue_waiting"]


class WhatsDueOut(BaseModel):
    due: list[DueItemOut]
    nothing_due: bool
    next_due_at: dict[str, Optional[str]]
    batch_hold: Optional[str] = None
    last_done_at: dict[str, Optional[str]]
    last_tick_at: Optional[str] = None
    expected_every_hours: int
    checked_at: str


class MarkDoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duty: Duty


class MarkDoneOut(BaseModel):
    duty: Duty
    done_at: str
    last_tick_at: str


def _limit(user: CurrentUser, kind: str, per_hour: int) -> None:
    if per_hour > 0 and not rate_limit.check_and_record(
        f"schedule-{kind}:{user.id}", max_in_window=per_hour, window_seconds=3600
    ):
        raise HTTPException(status_code=429, detail=f"too many schedule {kind}s this hour; try again later")


def _log(event: str, user: CurrentUser, **fields: str) -> None:
    get_audit_logger().info(
        event,
        extra={"event": event, "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
               "result": "ok", **fields},
    )


@router.get("/schedule/due", response_model=WhatsDueOut)
async def whats_due(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> WhatsDueOut:
    """The duties due NOW for the signed-in user, each with its recipe. Read-only: changes no setting
    and stamps nothing."""
    _limit(user, "read", settings.SCHEDULE_READS_MAX_PER_HOUR)
    now = datetime.now(timezone.utc)
    answer = schedule.due_now(await schedule.load_facts(db, user.id, now), now)
    _log("schedule_due_read", user, due=",".join(d["duty"] for d in answer["due"]) or "none")
    return WhatsDueOut(**answer)


@router.post("/schedule/done", response_model=MarkDoneOut)
async def mark_done(
    body: MarkDoneIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> MarkDoneOut:
    """Stamp one duty (hunt | inbox | batch) as done now, and the tick. A slot, never history."""
    _limit(user, "mark", settings.SCHEDULE_MARKS_MAX_PER_HOUR)
    now = datetime.now(timezone.utc)
    await schedule.mark(db, user.id, body.duty, now)
    _log("schedule_duty_done", user, duty=body.duty)
    stamp = now.isoformat(timespec="seconds")
    return MarkDoneOut(duty=body.duty, done_at=stamp, last_tick_at=stamp)
