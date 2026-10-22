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
from src.api.routes.profile import settings_view
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import ready as ready_service
from src.services.applications import spine
from src.services.applications.authorship import actor_for
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
    # G023 (slice 11): a sensitive answer the user did not agree to remember - stored as its
    # sha256 only, so ``answer`` is "" (Verify cannot see it; Edit types it again).
    redacted: bool = False


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
        items=[ReadyCard(**c) for c in cards[:limit]],
    )


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
    return ReadyCard(**cards[0])
