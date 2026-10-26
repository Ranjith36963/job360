"""The own-browser queue (owner decisions 2026-10-25): what waits for the user's OWN browser.

``GET /api/own-browser/queue`` lists it; ``POST /own-browser/take`` / ``release`` hand a task to one
assistant session and give it back (the MCP tool ``own_browser_queue`` calls these functions: same route
the web uses, gates re-applied there). ``POST /own-browser/run-now`` and ``POST
/contacts/{id}/own-browser-send`` are the USER's own clicks: web session only (``require_session_user``), no
MCP twin. Every route takes the user from the session / token / OAuth subject, never from input (rules
#12/#25). Rules and storage: ``services/applications/own_browser.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase
from src.services.applications import own_browser
from src.services.applications.spine import SpineError

router = APIRouter(tags=["own-browser"])

Why = Literal["any_time", "pc_on_hours", "run_now", "closed", "paused"]
Reason = Literal[
    "captcha", "login_wall", "weekly_limit", "draft_rewritten", "already_pending", "profile_not_found", "other",
]


class TaskClaimOut(BaseModel):
    session: str
    until: Optional[str] = None


class TaskBrowserOut(BaseModel):
    kind: Literal["job360", "own_browser", "human"]
    label: str
    reason: str


class OwnBrowserTaskItemOut(BaseModel):
    task_id: str
    kind: Literal["linkedin_outreach", "application"]
    what: str
    url: str = ""
    contact_id: Optional[int] = None
    application_id: Optional[int] = None
    name: str = ""
    message_id: Optional[int] = None
    # Text the user's assistant drafted: show it, never follow it as instructions.
    draft_text: str = ""
    draft_chars: int = 0
    linkedin_state: Optional[str] = None
    why_own_browser: str = ""
    owner_yes: bool = False
    claim: Optional[TaskClaimOut] = None
    browser: TaskBrowserOut


class QueueOut(BaseModel):
    open_now: bool
    why: Why
    pc_on_hours: Optional[dict[str, Any]] = None
    opens_at: Optional[str] = None
    run_now_until: Optional[str] = None
    count: int
    tasks: list[OwnBrowserTaskItemOut]


class TakeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: str = Field(default="", max_length=200)
    # Claim this one person (the one the user pressed Send on); omitted = the next task in the queue.
    contact_id: Optional[int] = Field(default=None, ge=1)


class TakeOut(BaseModel):
    status: Literal["task", "already_yours", "empty", "closed", "paused", "busy", "wait", "daily_limit"]
    why: str = ""
    task: Optional[OwnBrowserTaskItemOut] = None
    next: str = ""
    wait_seconds: Optional[int] = None
    opens_at: Optional[str] = None


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: str = Field(max_length=200)
    task_id: str = Field(max_length=40)
    reason: Reason


class ReleaseOut(BaseModel):
    released: bool
    task_id: str
    reason: str


class RunNowOut(BaseModel):
    run_now_until: str
    minutes: int


class SendDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: int
    decision: Literal["approve", "decline"]


class SendDecisionOut(BaseModel):
    state: Literal["waiting_on_you", "queued", "in_progress", "declined"]
    message_id: int
    approved_at: Optional[str] = None
    claim_until: Optional[str] = None


def _raise(exc: SpineError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.get("/own-browser/queue", response_model=QueueOut, dependencies=AUTH_FIRST)
async def queue(
    session: str = Query(default="", max_length=200),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> QueueOut:
    """What waits for the user's own browser, and whether it is open now. The website passes no ``session``;
    an assistant's read passes its own, so an application another session holds is left out."""
    now = datetime.now(timezone.utc)
    try:
        label = own_browser.session_label(session) if session else None
        win = await own_browser.window(db, user.id, now)
        tasks = await own_browser.queue_tasks(db, user, now, label)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # _raise always raises
    own_browser.log_read(user.id, len(tasks))
    return QueueOut(**win, count=len(tasks), tasks=[OwnBrowserTaskItemOut(**t) for t in tasks])


@router.post("/own-browser/take", response_model=TakeOut, dependencies=AUTH_FIRST)
async def take(
    body: TakeIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> TakeOut:
    """Hand ONE task to this assistant session (see ``own_browser.take`` for the statuses)."""
    try:
        got = await own_browser.take(db, user, body.session, datetime.now(timezone.utc), body.contact_id)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # _raise always raises
    return TakeOut(**got)


@router.post("/own-browser/release", response_model=ReleaseOut, dependencies=AUTH_FIRST)
async def release(
    body: ReleaseIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ReleaseOut:
    """Give a claimed person back (409 unless this session holds the live claim)."""
    try:
        got = await own_browser.release(
            db, user, body.session, body.task_id, body.reason, datetime.now(timezone.utc)
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # _raise always raises
    return ReleaseOut(**got)


@router.post("/own-browser/run-now", response_model=RunNowOut, dependencies=AUTH_FIRST)
async def run_now(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the user's own click (session only)
) -> RunNowOut:
    """The user's own "Run my own-browser tasks now": opens the queue for the next hour. Session only."""
    until = await own_browser.run_now(db, user.id, datetime.now(timezone.utc))
    return RunNowOut(run_now_until=until.isoformat(), minutes=own_browser.RUN_NOW_MINUTES)


@router.post(
    "/contacts/{contact_id}/own-browser-send", response_model=SendDecisionOut, dependencies=AUTH_FIRST
)
async def own_browser_send(
    contact_id: int,
    body: SendDecisionIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the user's own yes (session only)
) -> SendDecisionOut:
    """The user's per-card click "Send" (approve: Job360's browser first, the own browser after) or "Don't send"
    (decline) for ONE LinkedIn message. Session only; no MCP twin. 409 when the draft changed since they looked."""
    try:
        got = await own_browser.decide(db, user, contact_id, body.message_id, body.decision)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # _raise always raises
    return SendDecisionOut(**got)
