"""The common form questions on Memory (owner, 2026-10-26). Rules: ``services/profile/common_questions.py``.

``GET /api/profile/common-questions`` - every question almost every form asks, with the user's saved
answer and whether they verified it. Read-only; the user comes from the session / token (rules #12/#25).
Verifying writes through the existing ``PATCH /api/profile`` (a structured Memory field, or a
``user_info.answers`` record with the row's ``topic`` and ``approved: true``).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.services.profile import common_questions

router = APIRouter(tags=["profile"])


class CommonQuestionOut(BaseModel):
    id: str
    group: Literal["you", "experience", "work", "pay", "form", "equality"]
    question: str
    country: Optional[str] = None
    # Where the answer lives: a structured Memory field (kit key) or a saved answer's topic.
    kit_key: Optional[str] = None
    topic: Optional[str] = None
    answer: Optional[str] = None
    source: Optional[str] = None
    state: Literal["confirmed", "relayed", "inferred", "sensitive", "guessed", "missing"]
    verified: bool
    # Owner 2026-10-26: only two labels, "memory" or "guessed"; None until the assistant drafts it.
    label: Optional[Literal["memory", "guessed"]] = None
    seen: int = 0


class CommonQuestionsSummary(BaseModel):
    total: int
    verified: int
    to_verify: int
    missing: int


class CommonQuestionsOut(BaseModel):
    questions: list[CommonQuestionOut]
    summary: CommonQuestionsSummary


@router.get("/profile/common-questions", response_model=CommonQuestionsOut, dependencies=AUTH_FIRST)
async def list_common_questions(user: CurrentUser = Depends(require_user)) -> dict[str, Any]:  # noqa: B008
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    loaded, overlay = load_profile_with_overlay(user.id)
    rows = common_questions.common_questions(loaded or UserProfile(), overlay)
    return {"questions": rows, "summary": common_questions.summary(rows)}
