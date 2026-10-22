"""Asks - the "Needs you" queue's REST surface (owner plan 2026-10-01).

The user's assistant raises an ask when it would have to guess; the user
answers once (here, or in chat through the ``answer_ask`` MCP tool). Every
route ``Depends(require_user)`` and scopes by ``user.id``; the asker/answerer
(``asked_by`` / ``answered_by``) is derived from the session via ``actor_for``
and never read from a body (``extra="forbid"``).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.routes.applications import AskOut
from src.repositories.database import JobDatabase
from src.services import idempotency
from src.services.applications import asks as asks_service
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError

router = APIRouter(tags=["asks"])


def _raise(exc: SpineError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


class CreateAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: Optional[int] = None
    question: str
    context: str = ""
    # Gap roadmap slice 25 — optional idempotency key: the same key + same body returns the
    # FIRST ask (already_existed=true), writing nothing; another body under it is a 409.
    client_key: Optional[str] = None


class CreateAskOut(AskOut):
    # Slice 25 — true when this client_key was already used: the FIRST reply, nothing written.
    already_existed: bool = False


class AnswerAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
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
                db, user.id, body.application_id, body.question, body.context, actor_for(user)
            )
        except SpineError as exc:
            _raise(exc)
            raise AssertionError("unreachable")  # _raise always raises

    try:
        return await idempotency.run_once(
            db._db, user.id, "ask_user", body.client_key, body.model_dump(exclude={"client_key"}), _write
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
        return await asks_service.answer_ask(db, user.id, ask_id, body.answer, actor_for(user), body.user_words)
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


@router.post("/asks/{ask_id}/withdraw", response_model=AskOut, dependencies=AUTH_FIRST)
async def withdraw_ask(
    ask_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Take an open ask back. Web only - there is deliberately no MCP tool."""
    try:
        return await asks_service.withdraw_ask(db, user.id, ask_id, actor_for(user))
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover
