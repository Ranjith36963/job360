"""Autonomy agreement report (owner plan 2026-11-02): how often the watch-only ``autonomy_shadow`` verdicts
agree with the owner's own web decisions, per kind, against the bar in ``core.settings``.

``GET /api/autonomy/agreement`` is a read and a WEB one (``require_session_user``): the numbers are the
owner's proof, so no assistant token reads them and there is no MCP twin (the parity table has no row).
Scoped by ``user.id`` (rules #12/#25). Counts, ids and verdict codes only: never answer text.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user
from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase
from src.services.applications import agreement

router = APIRouter(tags=["autonomy"])


class KindAgreement(BaseModel):
    cases: int
    agree: int
    agreement_pct: Optional[float] = None
    wrong_facts: int
    bar_met: bool


class Disagreement(BaseModel):
    application_id: int
    kind: str
    question_key: str
    would_clear: bool
    owner: str


class Bar(BaseModel):
    min_cases: int
    min_agree_pct: int


class AgreementOut(BaseModel):
    kinds: dict[str, KindAgreement]
    pending: int
    examples: list[Disagreement]
    bar: Bar


@router.get("/autonomy/agreement", response_model=AgreementOut, dependencies=AUTH_FIRST)
async def autonomy_agreement(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 - FastAPI DI idiom
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> AgreementOut:
    """Per kind (essay, fixed, cv): cases, agree, agreement_pct, wrong_facts, bar_met; ``pending`` = cases with
    no owner decision yet; ``examples`` = up to 5 recent disagreements (ids and codes only)."""
    return AgreementOut(**await agreement.report(db, user.id))
