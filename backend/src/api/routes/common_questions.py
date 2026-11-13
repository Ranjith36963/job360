"""The common form questions on Memory (owner, 2026-10-26). Rules: ``services/profile/common_questions.py``.

``GET /api/profile/common-questions`` - every question almost every form asks, with the user's saved
answer and whether they verified it. Read-only; the user comes from the session / token (rules #12/#25).
Verifying writes through the existing ``PATCH /api/profile`` (a structured Memory field, or a
``user_info.answers`` record with the row's ``topic`` and ``approved: true``).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase
from src.services.profile import common_questions

router = APIRouter(tags=["profile"])


class CommonQuestionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    group: Literal["you", "work", "pay", "form", "equality"]
    question: str
    country: Optional[str] = None
    # Where the answer lives: a structured Memory field (kit key) or a saved answer's topic.
    kit_key: Optional[str] = None
    topic: Optional[str] = None
    answer: Optional[str] = None
    source: Optional[str] = None
    state: Literal["confirmed", "relayed", "inferred", "sensitive", "guessed", "missing"]
    verified: bool
    # Owner 2026-10-26: "memory", "guessed", or "not_known" until the assistant drafts it.
    label: Literal["memory", "guessed", "not_known"] = "not_known"
    seen: int = 0
    # B1: asked on each job's card (not kept in Memory); where an answer is saved (update_profile path);
    # who wrote the newest value and when; how a guess got here; sensitive = only a web write is verified.
    per_job: bool = False
    save_to: Optional[str] = None
    set_by: Optional[str] = None
    set_at: Optional[str] = None
    guess_source: Optional[str] = None
    sensitive: bool = False
    # Two saved answers to this topic disagree (e.g. Yes and No for GB sponsorship): both values, so the
    # user is asked which is right. Empty = no conflict.
    conflict: list[str] = Field(default_factory=list)
    # Answer once (owner 2026-11-02): the text to show filled in ("" = no guess) and where it comes from.
    value: str = ""
    from_: str = Field("", alias="from")


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


class QuestionnaireCountry(BaseModel):
    country: str
    questions: list[CommonQuestionOut]


class QuestionnaireOut(BaseModel):
    countries: list[QuestionnaireCountry]


@router.get("/profile/questionnaire", response_model=QuestionnaireOut, dependencies=AUTH_FIRST)
async def get_questionnaire(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Ask once (owner 2026-11-02): up to 10 common questions per country the user works in or has a queued
    job in, still guessed or not known, most-guessed first (``ask_once.questionnaire``). Read-only."""
    from src.services.applications import spine  # noqa: PLC0415
    from src.services.profile import ask_once  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    loaded, overlay = load_profile_with_overlay(user.id)
    queued = await spine.queued_countries(db, user.id)
    return {"countries": ask_once.questionnaire(loaded or UserProfile(), overlay, queued)}


class RememberItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2000)


class RememberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str = Field(min_length=2, max_length=2)
    items: list[RememberItem] = Field(min_length=1, max_length=10)


class RememberOut(BaseModel):
    saved: list[str]
    # Memory first (plan S2): rows in Memory's families, never saved -> why, naming the page to set it on.
    not_saved: dict[str, str] = Field(default_factory=dict)
    # Answer once (owner 2026-11-06): as RememberRowsOut (routes/asks.py); absent when none.
    memory: Optional[dict[str, dict[str, str]]] = None
    conflicts: Optional[dict[str, str]] = None


@router.post("/profile/questionnaire/remember", response_model=RememberOut, dependencies=AUTH_FIRST,
             response_model_exclude_none=True)
async def remember_questionnaire(
    body: RememberRequest, user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> dict[str, Any]:
    """The owner's own "remember" press on the questionnaire: each answer is saved as their approved answer;
    one in Memory's families (right to work, salary...) goes into Memory for ``country`` (``memory``), or is a
    ``conflict`` with Memory, or is not saved (``not_saved``: set it on that page).
    Session only (no MCP twin): nothing is remembered without that press."""
    from src.services.profile import ask_once  # noqa: PLC0415
    from src.services.profile.edits import ProfileEditError  # noqa: PLC0415

    try:
        out = ask_once.remember(user.id, [i.model_dump() for i in body.items], body.country.upper())
    except ProfileEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if out.get("memory"):  # Answer once: open asks the new Memory facts answer close
        from src.api.routes.profile import _close_asks_memory_answers  # noqa: PLC0415

        await _close_asks_memory_answers(user.id, [m["path"] for m in out["memory"].values()])
    return out


class CoveredAnswerOut(BaseModel):
    id: str
    index: int
    question: str
    answer: str
    country: Optional[str] = None
    topic: Optional[str] = None
    # What Memory says for this family and country (S1's reading); None = Memory says nothing yet.
    memory: Optional[str] = None
    verdict: Literal["same", "different", "memory_empty"]
    saved_by: Optional[Literal["web", "assistant"]] = None


class CoveredAnswersOut(BaseModel):
    answers: list[CoveredAnswerOut]
    profile_edit_id: int


class CoveredDeleteOut(BaseModel):
    deleted: str
    profile_edit_id: int


@router.get("/profile/memory-covered-answers", response_model=CoveredAnswersOut, dependencies=AUTH_FIRST)
async def list_memory_covered_answers(user: CurrentUser = Depends(require_session_user)) -> dict[str, Any]:  # noqa: B008
    """Memory first (plan S3): the user's old saved answers that Memory's families now cover, each with what
    Memory says and a verdict, for the Needs-you card. Session only: the owner cleans these up himself."""
    from src.services.profile import edits as profile_edits  # noqa: PLC0415
    from src.services.profile import memory_cleanup  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    version = profile_edits.current_edit_id(user.id)
    return {"answers": memory_cleanup.covered(load_profile(user.id) or UserProfile()), "profile_edit_id": version}


@router.delete("/profile/memory-covered-answers/{answer_id}", response_model=CoveredDeleteOut, dependencies=AUTH_FIRST)
async def delete_memory_covered_answer(
    answer_id: str, user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> dict[str, Any]:
    """The owner's own Delete on one listed answer: one ``remove_at`` through ``PATCH /profile`` (history and
    ``profile_edit_id`` as usual), guarded by the version read with it (409 if anything wrote since). 404 when
    no listed answer has that id (already gone, or changed). Session only, no MCP twin."""
    from src.api.routes.profile import ProfileEditIn, UpdateProfileRequest, update_profile  # noqa: PLC0415
    from src.services.profile import edits as profile_edits  # noqa: PLC0415
    from src.services.profile import memory_cleanup  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    version = profile_edits.current_edit_id(user.id)
    rows = memory_cleanup.covered(load_profile(user.id) or UserProfile())
    row = next((r for r in rows if r["id"] == answer_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="That saved answer is not on the list any more.")
    body = UpdateProfileRequest(
        edits=[ProfileEditIn(path="user_info.answers", op="remove_at", index=row["index"])],
        expected_profile_version=version,
    )
    resp = await update_profile(body, user)
    return {"deleted": answer_id, "profile_edit_id": resp.profile.profile_edit_id}
