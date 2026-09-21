"""The tailored CV / cover letter the AGENT wrote — read it, edit it, render it.

Decision 28 (2026-09-21, slice A): Job360 has no brain of its own. The user's
agent reads ``get_profile`` + ``get_job``, writes the tailored CV and cover
letter itself, and saves the text with ``save_artifact``. Job360 versions it,
shows which lines are grounded in the user's own CV, takes a human edit as a
NEW version, and renders DOCX / PDF. **Nothing here calls an LLM** — the
generator, its prompts, the monthly quota and ``POST /tailor/{job_id}/generate``
went with decision 28.

The documents therefore live in ONE place: ``application_artifacts``, through
the spine (M3 — every version kept forever, nothing rewritten). These routes
are readers and renderers over that history; the only write is a human edit,
and it is a new version like any other.

Every endpoint is per-user (rule #12/#25): ``require_verified_user`` + the
application is resolved from (caller, job_id), never from a user id in the
path or body → no IDOR.

Test seam: ``load_profile`` is imported into this module so tests monkeypatch
it here (``monkeypatch.setattr(tailor, "load_profile", fake)``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, require_verified_user
from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase
from src.services.applications import spine as applications_spine
from src.services.applications.spine import SpineError
from src.services.profile.storage import load_profile
from src.services.tailoring import DOC_KINDS
from src.services.tailoring.docx import render_docx
from src.services.tailoring.patterns import derive_patterns
from src.services.tailoring.pdf import render_pdf
from src.services.tailoring.provenance import annotate_provenance
from src.utils.logger import get_audit_logger, get_logger

router = APIRouter(tags=["tailor"])

logger = get_logger("api.tailor")


# ── Response / request models ─────────────────────────────────────────────────

class TailoredDocOut(BaseModel):
    """One saved version of a tailored document — the newest of its kind."""

    doc_kind: str
    text: str
    artifact_id: int
    version_no: int
    # Who wrote it, as the spine recorded it at save time: the agent's own name
    # (MCP `save_artifact`) or "human" (an edit made here). No LLM provider.
    made_by: str
    updated_at: str | None = None


class TailorBundle(BaseModel):
    job_id: int
    application_id: int
    documents: list[TailoredDocOut]


class TailorSaveRequest(BaseModel):
    # N6 — a tailored CV/cover letter tops out at a few thousand words; cap the
    # edit body so a client can't push a multi-MB blob into the DB unbounded.
    # The spine caps it again at APPLICATION_ARTIFACT_MAX_CHARS.
    text: str = Field(max_length=50_000)


class ProvenanceSegment(BaseModel):
    text: str
    grounded: bool  # True = grounded in the user's CV/job (their fact); False = added


def _doc_out(kind: str, row: dict[str, Any]) -> TailoredDocOut:
    return TailoredDocOut(
        doc_kind=kind,
        text=row.get("text") or "",
        artifact_id=int(row["id"]),
        version_no=int(row["version_no"]),
        made_by=row.get("made_by") or "",
        updated_at=row.get("created_at"),
    )


def _check_kind(doc_kind: str) -> None:
    if doc_kind not in DOC_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown doc kind {doc_kind!r}")


async def _application_id(db: JobDatabase, user_id: str, job_id: int) -> int:
    """The caller's application for this job. 404 when they never brought it —
    the same answer a job belonging to somebody else gives (no IDOR oracle)."""
    row = await applications_spine.get_application_by_job(db, user_id, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No application for job {job_id}.")
    return int(row["id"])


async def _latest(db: JobDatabase, user_id: str, application_id: int, kind: str) -> dict[str, Any]:
    row = await applications_spine.latest_artifact(db, user_id, application_id, kind)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No {kind} saved for this application yet — ask your agent to write one "
                "and save it (MCP `save_artifact`)."
            ),
        )
    return row


def _load_cv_text(user_id: str) -> str:
    """The user's own CV text (stored CV + LinkedIn) — the ground truth the
    provenance view highlights against."""
    profile = load_profile(user_id)  # sync (pgsync shim), fast single-row
    if profile is None:
        return ""
    cv = profile.cv_data
    parts = [getattr(cv, "raw_text", "") or "", getattr(cv, "linkedin_raw_text", "") or ""]
    return "\n\n".join(p for p in parts if p.strip()).strip()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/tailor/{job_id}", response_model=TailorBundle)
async def get_tailored(
    job_id: int,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
) -> TailorBundle:
    """The newest saved CV and cover letter for this job (empty list if the
    agent has saved none yet). Older versions stay readable on the application
    page — ``GET /applications/{id}``."""
    application_id = await _application_id(db, user.id, job_id)
    documents = []
    for kind in DOC_KINDS:
        row = await applications_spine.latest_artifact(db, user.id, application_id, kind)
        if row is not None:
            documents.append(_doc_out(kind, row))
    return TailorBundle(job_id=job_id, application_id=application_id, documents=documents)


@router.get("/tailor/{job_id}/{doc_kind}/provenance", response_model=list[ProvenanceSegment])
async def provenance(
    job_id: int,
    doc_kind: str,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
) -> list[dict[str, Any]]:
    """Per-line provenance for the newest saved version: which lines are the
    user's OWN facts (grounded in their CV + the job ad) and which were added.
    Deterministic, no LLM — shown before download so the user can verify what is
    real (guardrail #2)."""
    _check_kind(doc_kind)
    application_id = await _application_id(db, user.id, job_id)
    row = await _latest(db, user.id, application_id, doc_kind)
    job = await db.get_job_by_id(job_id)
    source = _load_cv_text(user.id) + "\n" + (job.get("description", "") if job else "")
    return annotate_provenance(row.get("text") or "", source)


@router.patch("/tailor/{job_id}/{doc_kind}", response_model=TailoredDocOut)
async def save_edit(
    job_id: int,
    doc_kind: str,
    body: TailorSaveRequest,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
) -> TailoredDocOut:
    """Save the user's own edit as a NEW version (guardrail #3 — always
    editable). Nothing is overwritten: the agent's version and every earlier
    edit stay readable forever (M3), and the edit is stamped ``made_by="human"``."""
    _check_kind(doc_kind)
    application_id = await _application_id(db, user.id, job_id)
    try:
        saved = await applications_spine.save_artifact(
            db, user_id=user.id, application_id=application_id, kind=doc_kind,
            text=body.text, made_by="human",
        )
    except SpineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return TailoredDocOut(
        doc_kind=doc_kind,
        text=body.text,
        artifact_id=int(saved["artifact_id"]),
        version_no=int(saved["version_no"]),
        made_by="human",
        updated_at=saved.get("created_at"),
    )


@router.post("/tailor/{job_id}/{doc_kind}/keep", response_model=TailoredDocOut)
async def keep(
    job_id: int,
    doc_kind: str,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
) -> TailoredDocOut:
    """"This is the one I'm using" — the learning trigger (§5 learn-from-kept-only).

    Writes nothing to the document: the version history IS the record (decision
    26 — no Keep flag on an artifact). All it does is record the STRUCTURE of the
    kept text in the universal patterns store (§7 privacy — never content)."""
    _check_kind(doc_kind)
    application_id = await _application_id(db, user.id, job_id)
    row = await _latest(db, user.id, application_id, doc_kind)
    await _learn_universal(db, doc_kind, row.get("text") or "")
    get_audit_logger().info(
        "tailor_keep", extra={"user_id": user.id, "job_id": job_id, "doc_kind": doc_kind}
    )
    return _doc_out(doc_kind, row)


@router.post("/tailor/{job_id}/{doc_kind}/download")
async def download(
    job_id: int,
    doc_kind: str,
    fmt: str = "pdf",
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
) -> Response:
    """Download the newest saved version as an ATS-friendly PDF or DOCX.

    ``fmt`` = ``pdf`` (default) | ``docx``.

    POST, not GET (docs/fable/01 S6): this endpoint MUTATES — it feeds
    `_learn_universal`. A side-effecting GET is both wrong HTTP semantics and a
    CSRF hole: `OriginCheckMiddleware` only guards unsafe methods, and a
    cross-site top-level link click still sends the SameSite=Lax cookie. As POST
    it is Origin-checked like every other mutation. The frontend already fetches
    this as a blob, so the method is transparent there.
    """
    _check_kind(doc_kind)
    if fmt not in ("pdf", "docx"):
        raise HTTPException(status_code=400, detail="format must be 'pdf' or 'docx'")
    application_id = await _application_id(db, user.id, job_id)
    row = await _latest(db, user.id, application_id, doc_kind)
    text = row.get("text") or ""
    title = "Curriculum Vitae" if doc_kind == "cv" else "Cover Letter"

    # Rendering is synchronous and CPU-bound (fpdf2 / python-docx build the
    # whole document in memory). Inline it froze the single event loop for every
    # other request for the length of the render — same bug class as PR #123 and
    # the CV-upload stall. @cpu_bound on render_pdf/render_docx reports an
    # inline call — raising in tests and dev, but in production only logging an
    # ERROR + one Sentry message before rendering anyway. So the
    # `asyncio.to_thread` calls below are what actually keep it off the loop.
    if fmt == "docx":
        content = await asyncio.to_thread(render_docx, text, title=title)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        content = await asyncio.to_thread(render_pdf, text, title=title)
        media_type = "application/pdf"

    # Downloading counts as 'used' → learn from it (§5).
    await _learn_universal(db, doc_kind, text)

    filename = f"{doc_kind}_{job_id}.{fmt}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _learn_universal(db: JobDatabase, doc_kind: str, text: str) -> None:
    """Layer 1 (§6/§7): store PATTERNS ONLY from a used doc — never content/PII."""
    if not text.strip():
        return
    import json as _json

    features = derive_patterns(text, doc_kind)  # structural numbers/labels only
    await db.record_tailoring_pattern(doc_kind, _json.dumps(features))
