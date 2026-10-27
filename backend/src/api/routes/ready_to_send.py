"""Ready to send - the approval cards for "Needs you" (S5d).

One WEB read: every application whose form the assistant filled and the user has
not yet answered, with the CV / letter versions, every answer that will go out
(and where it came from) and the flags that keep a card out of "Send all
unflagged". No MCP twin: the assistant holds the kit and the gate. Scoped by
``user.id`` (rule #12). Logs counts only - never an answer.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.api.routes.applications import BrowserLabelOut
from src.api.routes.profile import settings_view
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import ready as ready_service
from src.services.applications import spine
from src.services.applications.authorship import actor_for
from src.services.browser_agent import routing
from src.services.profile.edits import ProfileEditError
from src.services.profile.models import UserProfile
from src.services.profile.storage import load_profile
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["ready-to-send"])


class ReadyDoc(BaseModel):
    artifact_id: int
    version: int
    saved_at: str
    made_by: str


class ReadyAnswer(BaseModel):
    question: str
    answer: str
    source: str
    key: Optional[str] = None
    saved_by: Optional[str] = None
    saved_at: Optional[str] = None
    # Owner decision 2026-10-18 - the user's own word: their saved answer matches
    # what was typed (verified), or differs (changed_to = what they said instead).
    verified: bool = False
    changed_to: Optional[str] = None
    # Whose act made ``changed_to`` differ (application 122): owner_edit = the user's web session saved
    # it after this fill (the only one the card calls "You changed"); approved_for_employer / saved_answer
    # = memory saved before the fill that the assistant will type. None when ``changed_to`` is None.
    changed_reason: Optional[Literal["owner_edit", "approved_for_employer", "saved_answer"]] = None
    # G023 (slice 11): a sensitive answer the user did not agree to remember - stored as its
    # sha256 only, so ``answer`` is "" (Verify cannot see it; Edit types it again).
    redacted: bool = False
    # Owner 2026-10-26: for a hashed row, the user's own saved answer to edit (None = none saved).
    draft: Optional[str] = None
    # Owner 2026-10-26: ``confirm`` = text written for THIS job (read it each time); ``verify`` = a fact
    # reused on every form (verified once in Memory, then filled). ``ready.answer_check`` decides.
    check: Literal["verify", "confirm"] = "verify"


class ReadyFlag(BaseModel):
    code: str
    text: str
    key: Optional[str] = None
    country: Optional[str] = None


class ReadyCard(BaseModel):
    application_id: int
    form_filled_event_id: int
    filled_at: str
    filled_by: str
    job_title: str
    job_company: str
    job_location: str
    job_country: Optional[str] = None
    brought_by: Optional[str] = None
    brought_at: Optional[str] = None
    fit_score: Optional[int] = None
    fit_by: Optional[str] = None
    # Owner decision 2026-10-10 - which side of the user's own score line.
    line_side: Literal["above", "below", "unknown"] = "unknown"
    cv: Optional[ReadyDoc] = None
    cover_letter: Optional[ReadyDoc] = None
    answers: list[ReadyAnswer]
    flags: list[ReadyFlag]
    # Seconds left on the hold keeping this filled form's Job360 browser open (None = none held).
    held_seconds: Optional[int] = None
    # Is that job's Job360 browser open now? False = it closed (idle or hold over); None = browser off / unknown.
    form_open: Optional[bool] = None
    # Which browser finishes this form: Job360's, unless Job360's browser already failed on it.
    browser: Optional[BrowserLabelOut] = None


class ReadyToSendOut(BaseModel):
    paused: bool
    total: int
    unflagged: int
    items: list[ReadyCard]


@router.get("/ready-to-send", response_model=ReadyToSendOut, dependencies=AUTH_FIRST)
async def ready_to_send(
    application_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(settings.READY_TO_SEND_MAX, ge=0, le=settings.READY_TO_SEND_MAX),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 - FastAPI DI idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ReadyToSendOut:
    """The caller's ready-to-send cards, newest fill first. ``limit=0`` = counts
    only. ``application_id`` narrows it to one (404 when it is not the caller's)."""
    if application_id is not None and await spine.get_owned_application(db, user.id, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    cards = await ready_service.ready_cards(
        db, user.id, limit=settings.READY_TO_SEND_MAX, application_id=application_id
    )
    flagged = sum(1 for c in cards if c["flags"])
    paused = bool(settings_view(load_profile(user.id) or UserProfile(), user.id)["paused"])
    get_audit_logger().info(
        "ready_to_send_read",
        extra={
            "event": "ready_to_send_read", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "result": "ok", "count": len(cards), "flagged": flagged,
        },
    )
    return ReadyToSendOut(
        paused=paused, total=len(cards), unflagged=len(cards) - flagged,
        items=[
            ReadyCard(
                **c, held_seconds=_held(user.id, c["application_id"]), form_open=_open(user.id, c["application_id"]),
                browser=_browser(c.get("apply_url") or ""),
            )
            for c in cards[:limit]
        ],
    )


def _browser(apply_url: str) -> BrowserLabelOut:
    """A ready card's form is filled in Job360's browser first, unless its apply link is on linkedin.com
    (Easy Apply: the user's own browser only, owner 2026-10-26). A form whose browser stopped has an open
    block and is never listed here (``_READY_SQL``); no read."""
    route = routing.browser_for(url=apply_url, queue_kind="application")
    return BrowserLabelOut(kind=route.kind, label=routing.label(route)["label"], reason=route.reason)


def _held(user_id: str, application_id: int) -> Optional[int]:
    """Seconds left on that job's browser hold (in memory, this process; None = none), for Finish it here."""
    if not settings.BROWSER_AGENT_ENABLED:
        return None
    from src.services.browser_agent import agent as browser_agent  # noqa: PLC0415 - lazy

    return browser_agent.held_seconds(browser_agent.get_pool(), user_id, int(application_id))


def _open(user_id: str, application_id: int) -> Optional[bool]:
    """Is that job's browser open now (in memory, this process); None when the browser feature is off."""
    if not settings.BROWSER_AGENT_ENABLED:
        return None
    from src.services.browser_agent import agent as browser_agent  # noqa: PLC0415 - lazy

    return browser_agent.form_open(browser_agent.get_pool(), user_id, int(application_id))


class CvToApproveRow(BaseModel):
    application_id: int
    job_title: str
    job_company: str
    cv: ReadyDoc


class CvToApproveOut(BaseModel):
    items: list[CvToApproveRow]


@router.get("/cv-to-approve", response_model=CvToApproveOut, dependencies=AUTH_FIRST)
async def cv_to_approve(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 - FastAPI DI idiom
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the user's own page; no MCP twin
) -> CvToApproveOut:
    """Approve CV on Needs you (owner decision 2026-10-23): the caller's open applications whose newest CV
    they have not seen yet, from chat or the web. "CV OK" is ``POST /applications/{id}/cv-seen``."""
    rows = await ready_service.cv_to_approve(db, user.id)
    get_audit_logger().info(
        "cv_to_approve_read",
        extra={"event": "cv_to_approve_read", "user_id": safe_log_value(user.id), "result": "ok", "count": len(rows)},
    )
    return CvToApproveOut(items=[CvToApproveRow(**r) for r in rows])


class ConfirmAnswerRequest(BaseModel):
    form_filled_event_id: int = Field(..., ge=1)
    # None = Verify (the typed answer is right); text = Edit (this is the right answer).
    answer: Optional[str] = Field(None, max_length=settings.USER_INFO_ANSWER_MAX_CHARS)


@router.post(
    "/applications/{application_id}/answers/{index}/confirm", response_model=ReadyCard, dependencies=AUTH_FIRST,
)
async def confirm_answer(
    application_id: int,
    index: int,
    body: ConfirmAnswerRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 - FastAPI DI idiom
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> ReadyCard:
    """Verify or Edit one answer on a ready card (owner decision 2026-10-18): it is
    saved as the user's own word in memory, so the assistant uses it next time.
    Session only - an assistant can never vouch for its own answer. Returns the
    card again; 404 / 409 (the fill changed) / 422 write nothing. Logs paths only."""
    if await spine.get_owned_application(db, user.id, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    try:
        paths = await ready_service.confirm_answer(
            db, user.id, actor_for(user), application_id,
            form_filled_event_id=body.form_filled_event_id, index=index, new_text=body.answer,
        )
    except ProfileEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    get_audit_logger().info(
        "ready_answer_confirmed",
        extra={
            "event": "ready_answer_confirmed", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "edited": body.answer is not None,
            "paths": paths, "result": "ok",
        },
    )
    cards = await ready_service.ready_cards(db, user.id, limit=1, application_id=application_id)
    if not cards:  # pragma: no cover - a memory write never takes a card off the list
        raise HTTPException(status_code=404, detail="This form is no longer waiting for you")
    return ReadyCard(**cards[0], browser=_browser(cards[0].get("apply_url") or ""))
