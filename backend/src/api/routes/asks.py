"""Asks - the "Needs you" queue's REST surface (owner plan 2026-10-01).

The user's assistant raises an ask when it would have to guess; the user
answers once (here, or in chat through the ``answer_ask`` MCP tool). Every
route ``Depends(require_user)`` and scopes by ``user.id``; the asker/answerer
(``asked_by`` / ``answered_by``) is derived from the session via ``actor_for``
and never read from a body (``extra="forbid"``).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.routes.applications import AskOut, InboxItemOut
from src.repositories.database import JobDatabase
from src.services import idempotency
from src.services.applications import asks as asks_service
from src.services.applications import blocked as blocked_service
from src.services.applications import inbox_items as inbox_items_service
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError

router = APIRouter(tags=["asks"])


def _raise(exc: SpineError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


class AskQuestionIn(BaseModel):
    """One form question of an ask (0087): the answer the assistant filled in and where it came from.
    Lengths, key shape, the source/answer pairing and the caps are checked in ``asks.validate_questions``."""

    model_config = ConfigDict(extra="forbid")

    key: str
    question: str
    answer: str = ""
    source: Literal["profile", "memory", "cv", "guess", "draft", "none"]
    choices: Optional[list[str]] = None
    remembered_on: Optional[str] = None


class CreateAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: Optional[int] = None
    question: str
    context: str = ""
    # 0087 - what the ask is (a question, or a blocker only the user can clear) and its questions.
    kind: Optional[Literal["question", "captcha", "login", "upload", "safety"]] = None
    questions: Optional[list[AskQuestionIn]] = None
    # Gap roadmap slice 25 — optional idempotency key: the same key + same body returns the
    # FIRST ask (already_existed=true), writing nothing; another body under it is a 409.
    client_key: Optional[str] = None


class CreateAskOut(AskOut):
    # Slice 25 — true when this client_key was already used: the FIRST reply, nothing written.
    already_existed: bool = False


class AnswerAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Required unless `answers` is given (an ask with questions: one answer per key).
    answer: str = ""
    answers: Optional[dict[str, str]] = None
    user_words: str = Field(default="", max_length=2000)


class ListAsksResponse(BaseModel):
    asks: list[AskOut]
    open_count: int


@router.post("/asks", status_code=201, response_model=CreateAskOut, dependencies=AUTH_FIRST)
async def create_ask(
    body: CreateAskRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    async def _write() -> dict[str, Any]:
        try:
            return await asks_service.create_ask(
                db, user.id, body.application_id, body.question, body.context, actor_for(user),
                kind=body.kind or "",
                questions=[q.model_dump(exclude_none=True) for q in body.questions] if body.questions else None,
            )
        except SpineError as exc:
            _raise(exc)
            raise AssertionError("unreachable")  # _raise always raises

    try:
        return await idempotency.run_once(
            # Unset 0087 fields stay out, so a key stored before them still matches its own retry.
            db._db, user.id, "ask_user", body.client_key,
            body.model_dump(exclude={"client_key"} | {k for k in ("kind", "questions") if getattr(body, k) is None}),
            _write,
        )
    except idempotency.IdempotencyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.get("/asks", response_model=ListAsksResponse, dependencies=AUTH_FIRST)
async def list_asks(
    status: str = Query("open"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        rows = await asks_service.list_asks(db, user.id, status, limit=limit, offset=offset)
        finish = await blocked_service.finish_here_ask_ids(db, user.id, rows)
        rows = [{**r, "finish_here": r["id"] in finish} for r in rows]
        return {"asks": rows, "open_count": await asks_service.count_open_asks(db, user.id)}
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.post("/asks/{ask_id}/answer", response_model=AskOut, dependencies=AUTH_FIRST)
async def answer_ask(
    ask_id: int,
    body: AnswerAskRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await asks_service.answer_ask(
            db, user.id, ask_id, body.answer, actor_for(user), body.user_words, answers=body.answers
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.post("/asks/{ask_id}/confirm", response_model=AskOut, dependencies=AUTH_FIRST)
async def confirm_ask(
    ask_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """The user confirms a relayed chat answer. Web session only: an assistant
    can never confirm for the user, so there is deliberately no MCP tool."""
    if actor_for(user) != "web":
        raise HTTPException(status_code=403, detail="only you can confirm this, on Job360")
    try:
        return await asks_service.confirm_ask(db, user.id, ask_id)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


class WithdrawAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Why the ask is out of date; required when an assistant withdraws (owner, 2026-10-23).
    reason: str = Field(default="", max_length=asks_service.ASKS_WITHDRAW_REASON_MAX_CHARS)


@router.post("/asks/{ask_id}/withdraw", response_model=AskOut, dependencies=AUTH_FIRST)
async def withdraw_ask(
    ask_id: int,
    body: Optional[WithdrawAskRequest] = None,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Take an open ask back. The user (web) may withdraw any ask, reason optional. An assistant
    (the MCP ``answer_ask`` ``withdraw``) may withdraw only an ask it asked itself, and must say why
    (owner, 2026-10-23); an ask the user or another assistant raised stays the user's to close."""
    actor = actor_for(user)
    try:
        return await asks_service.withdraw_ask(
            db, user.id, ask_id, actor, body.reason if body else "", own_only=actor != "web"
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


class ListInboxItemsResponse(BaseModel):
    items: list[InboxItemOut]
    open_count: int


class ResolveInboxItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: Optional[int] = None
    dismiss: bool = False


@router.get("/inbox-items", response_model=ListInboxItemsResponse, dependencies=AUTH_FIRST)
async def list_inbox_items(
    status: str = Query("open"),
    limit: int = Query(50, ge=1, le=200),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Feature 10: unmatched mail the assistant stored (open, or all)."""
    try:
        items = await inbox_items_service.list_items(db, user.id, status, limit)
        return {"items": items, "open_count": await inbox_items_service.count_open(db, user.id)}
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # _raise always raises


@router.post("/inbox-items/{item_id}/resolve", response_model=InboxItemOut, dependencies=AUTH_FIRST)
async def resolve_inbox_item(
    item_id: int,
    body: ResolveInboxItemRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """The user's pick: match the email to one of their applications, or dismiss it. Stores the pick
    and writes no event (only the assistant read the mail). No MCP tool: the assistant records the event."""
    try:
        return await inbox_items_service.resolve(
            db, user.id, item_id, application_id=body.application_id, dismiss=body.dismiss, actor=actor_for(user)
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # _raise always raises
