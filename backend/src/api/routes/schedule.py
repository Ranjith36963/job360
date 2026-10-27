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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications.authorship import actor_for
from src.services.auth import rate_limit
from src.services.profile import schedule
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["schedule"])

Duty = Literal["hunt", "inbox", "batch", "reach"]
Outcome = Literal["done", "partial", "failed"]
RunReason = Literal[
    "no_work", "tool_unavailable", "sign_in_needed", "paid_plan_needed", "network_blocked", "rate_limited",
    "needs_user", "error",
]


class DueItemOut(BaseModel):
    duty: Duty
    recipe: Literal["hunt", "daily", "batch", "reach"]
    reason: Literal["never_done", "interval_elapsed", "queue_waiting", "new_applications", "run_now"]
    # Only on `reach`: the applications to find people for this run (newest first).
    application_ids: Optional[list[int]] = None


class WhatsDueOut(BaseModel):
    due: list[DueItemOut]
    nothing_due: bool
    next_due_at: dict[str, Optional[str]]
    batch_hold: Optional[str] = None
    # Owner 2026-10-25: queued jobs that wait on the user (an open ask, a block, a CV not seen yet). When
    # all of them do, `batch_hold` is `waiting_on_you` and the batch is not due.
    waiting_on_you: int = 0
    # Owner 2026-10-25: why the hunt is not due although its time came (closed set: queue_full).
    hunt_hold: Optional[Literal["queue_full"]] = None
    # Owner 2026-10-27: duties due because the user pressed Run now in Job360 (reason `run_now`).
    run_now: list[Duty] = Field(default_factory=list)
    last_done_at: dict[str, Optional[str]]
    last_tick_at: Optional[str] = None
    # Owner 2026-10-25: failed runs in a row per duty (a failing duty waits 2h, 4h, 8h... at most 24h).
    failures_in_row: dict[str, int] = Field(default_factory=dict)
    # Owner 2026-10-25: duties another run has now (not listed in `due`): {duty: taken_at}.
    running: dict[str, Optional[str]] = Field(default_factory=dict)
    # Duties that are due but another run has them now (so `nothing_due` is never silent about it).
    held_by_running: list[Duty] = Field(default_factory=list)
    expected_every_hours: int
    checked_at: str


class MarkDoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duty: Duty
    # Only with `reach`: the applications this run worked (each stamped so it is not listed again).
    application_ids: Optional[list[int]] = Field(default=None, max_length=20)
    # How the run ended (owner 2026-10-25). `failed` needs a `reason`; done/partial may carry one.
    outcome: Outcome = "done"
    reason: Optional[RunReason] = None


class MarkDoneOut(BaseModel):
    duty: Duty
    done_at: str
    last_tick_at: str
    outcome: Outcome = "done"
    failures_in_row: int = 0


class RunProblemOut(BaseModel):
    duty: Duty
    label: str
    failures_in_row: int
    reason: RunReason
    title: str
    what_to_do: str
    last_failed_at: Optional[str] = None


class RunProblemsOut(BaseModel):
    problems: list[RunProblemOut]


class DutyStatusOut(BaseModel):
    duty: Duty
    label: str
    state: Literal["off", "running", "due", "failing", "held", "waiting"]
    # Why a duty that is on does not run now (hunt: queue_full; batch: one of the batch holds).
    hold: Optional[str] = None
    last_run_at: Optional[str] = None
    last_outcome: Optional[Outcome] = None
    last_reason: Optional[RunReason] = None
    failures_in_row: int = 0
    next_due_at: Optional[str] = None


class ScheduleStatusOut(BaseModel):
    duties: list[DutyStatusOut]
    last_tick_at: Optional[str] = None


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


@router.get("/schedule/due", response_model=WhatsDueOut, dependencies=AUTH_FIRST)
async def whats_due(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
    duties: Optional[list[Duty]] = Query(default=None),  # noqa: B008
) -> WhatsDueOut:
    """The duties due NOW for the signed-in user, each with its recipe. Changes no setting. ``duties``
    (a routine that runs one job passes its own) limits the answer AND what it takes to those; a read
    without ``duties`` only looks. An assistant's read with ``duties`` takes each listed duty for this run
    (one run per duty at a time, owner 2026-10-25):
    another run in the next ``DUTY_CLAIM_MINUTES`` sees it under ``running`` / ``held_by_running`` until
    ``mark_done``. The website's own read only looks."""
    _limit(user, "read", settings.SCHEDULE_READS_MAX_PER_HOUR)
    now = datetime.now(timezone.utc)
    answer = schedule.due_now(await schedule.load_facts(db, user.id, now), now)
    if duties:
        wanted = set(duties)
        answer["due"] = [d for d in answer["due"] if d["duty"] in wanted]
        answer["held_by_running"] = [d for d in answer["held_by_running"] if d in wanted]
        answer["nothing_due"] = not answer["due"]
    if duties and actor_for(user) != "web" and answer["due"]:
        got = await schedule.claim(db, user.id, [d["duty"] for d in answer["due"]], now)
        lost = [d["duty"] for d in answer["due"] if d["duty"] not in got]
        answer["due"] = [d for d in answer["due"] if d["duty"] in got]
        answer["nothing_due"] = not answer["due"]
        answer["running"] = {**answer["running"], **{d: now.isoformat(timespec="seconds") for d in lost}}
        answer["held_by_running"] = [*answer["held_by_running"], *lost]
    _log("schedule_due_read", user, due=",".join(d["duty"] for d in answer["due"]) or "none")
    return WhatsDueOut(**answer)


@router.post("/schedule/done", response_model=MarkDoneOut, dependencies=AUTH_FIRST)
async def mark_done(
    body: MarkDoneIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> MarkDoneOut:
    """Stamp one duty (hunt | inbox | batch) as done now, and the tick. A slot, never history."""
    _limit(user, "mark", settings.SCHEDULE_MARKS_MAX_PER_HOUR)
    ids = sorted(set(body.application_ids or []))
    if body.duty == "reach" and not ids:
        raise HTTPException(status_code=422, detail="reach needs application_ids: the applications this run worked")
    if body.duty != "reach" and body.application_ids is not None:
        raise HTTPException(status_code=422, detail="application_ids is only for duty reach")
    if body.outcome == "failed" and body.reason is None:
        raise HTTPException(status_code=422, detail="a failed run needs a reason (one of the closed list)")
    if ids and await schedule.owned_application_ids(db, user.id, ids) != set(ids):
        raise HTTPException(status_code=404, detail="application not found")
    now = datetime.now(timezone.utc)
    await schedule.mark(db, user.id, body.duty, now, ids or None, outcome=body.outcome, reason=body.reason or "")
    _log("schedule_duty_done", user, duty=body.duty, outcome=body.outcome, reason=body.reason or "")
    failures = int((await schedule.load_runs(db, user.id)).get(body.duty, {}).get("failures") or 0)
    if body.duty == "hunt" and body.outcome != "failed":  # Ignition: a finished hunt starts reach and the batch
        from src.services.browser_agent import duty_trigger  # noqa: PLC0415

        await duty_trigger.ignite(user.id, ("reach", "batch"), "hunt_done", db=db)
    stamp = now.isoformat(timespec="seconds")
    return MarkDoneOut(
        duty=body.duty, done_at=stamp, last_tick_at=stamp, outcome=body.outcome, failures_in_row=failures
    )


@router.get("/schedule/problems", response_model=RunProblemsOut, dependencies=AUTH_FIRST)
async def run_problems(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> RunProblemsOut:
    """Owner 2026-10-25: one plain card per duty whose scheduled run failed ``FAILURE_CARD_AT`` times in a
    row, saying what happened and what to do (Home and Needs you show them). Read-only; closed values."""
    runs = await schedule.load_runs(db, user.id)
    return RunProblemsOut(problems=[RunProblemOut(**p) for p in schedule.problems(runs)])


@router.get("/schedule/status", response_model=ScheduleStatusOut, dependencies=AUTH_FIRST)
async def schedule_status(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ScheduleStatusOut:
    """Owner 2026-10-25: the Home status card - for each duty, is it on, when it last ran and how that
    ended, and when it runs next. Read-only (takes no duty); closed values only."""
    _limit(user, "read", settings.SCHEDULE_READS_MAX_PER_HOUR)
    now = datetime.now(timezone.utc)
    facts = await schedule.load_facts(db, user.id, now)
    answer = schedule.due_now(facts, now)
    rows = schedule.status(facts, answer, await schedule.load_runs(db, user.id))
    return ScheduleStatusOut(duties=[DutyStatusOut(**r) for r in rows], last_tick_at=answer["last_tick_at"])
