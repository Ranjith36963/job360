"""Per-User AI CV & Cover Letter routes (docs/peruser_cv_coverletter.md).

Every endpoint is per-user (rule #12/#25): ``require_verified_user`` + all queries
scoped by ``user.id`` (never a user_id from the path/body → no IDOR). Generation is
quota-gated (guardrail #1). The LLM call is done synchronously here (reliable +
testable, no Redis dependency); an ARQ task mirrors it for a future async path.

Test seam: ``llm_extract`` and ``load_profile`` are imported into this module so
tests monkeypatch them here (``monkeypatch.setattr(tailor, "llm_extract", fake)``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, require_verified_user
from src.api.dependencies import get_request_db
from src.core.settings import TAILOR_FREE_PER_MONTH
from src.repositories.database import JobDatabase
from src.services.profile.llm_provider import llm_extract
from src.services.profile.storage import load_profile
from src.services.tailoring import DOC_KINDS, generate_document
from src.services.tailoring.docx import render_docx
from src.services.tailoring.generator import EmptyCVError
from src.services.tailoring.patterns import derive_patterns, summarize_patterns
from src.services.tailoring.pdf import render_pdf
from src.services.tailoring.provenance import annotate_provenance
from src.utils.logger import get_audit_logger

router = APIRouter(tags=["tailor"])


# ── Response / request models ─────────────────────────────────────────────────

class TailoredDocOut(BaseModel):
    doc_kind: str
    ai_draft: str
    polished: str | None = None
    status: str = "draft"
    model: str | None = None
    updated_at: str | None = None
    # Proper nouns in the draft that match nothing in the source CV/job — possible
    # fabrications for the user to review before applying (guardrail #2). Usually empty.
    flagged_terms: list[str] = []


class TailorBundle(BaseModel):
    job_id: int
    documents: list[TailoredDocOut]
    quota_used: int
    quota_limit: int


class TailorSaveRequest(BaseModel):
    # N6 — a tailored CV/cover letter tops out at a few thousand words; cap the
    # edit body so a client can't push a multi-MB blob into the DB unbounded.
    text: str = Field(max_length=50_000)


class ProvenanceSegment(BaseModel):
    text: str
    grounded: bool  # True = grounded in the user's CV/job (their fact); False = AI-added


def _doc_out(row: dict) -> TailoredDocOut:
    return TailoredDocOut(
        doc_kind=row["doc_kind"],
        ai_draft=row.get("ai_draft", ""),
        polished=row.get("polished"),
        status=row.get("status", "draft"),
        model=row.get("model"),
        updated_at=row.get("updated_at"),
        flagged_terms=row.get("flagged_terms", []) or [],
    )


def _check_kind(doc_kind: str) -> None:
    if doc_kind not in DOC_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown doc kind {doc_kind!r}")


async def _bundle(db: JobDatabase, user_id: str, job_id: int) -> TailorBundle:
    rows = await db.get_tailored_docs(user_id, job_id)
    used = await db.count_tailored_usage_month(user_id)
    return TailorBundle(
        job_id=job_id,
        documents=[_doc_out(r) for r in rows],
        quota_used=used,
        quota_limit=TAILOR_FREE_PER_MONTH,
    )


def _load_cv_text(user_id: str) -> str:
    """Full CV text = stored CV + LinkedIn text (the ONLY facts the LLM may use)."""
    profile = load_profile(user_id)  # sync (pgsync shim), fast single-row
    if profile is None:
        return ""
    cv = profile.cv_data
    parts = [getattr(cv, "raw_text", "") or "", getattr(cv, "linkedin_raw_text", "") or ""]
    return "\n\n".join(p for p in parts if p.strip()).strip()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/tailor/{job_id}/generate", response_model=TailorBundle)
async def generate(
    job_id: int,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
):
    """Generate a tailored CV + cover letter for (caller, job). Quota-gated."""
    job = await db.get_job_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Guardrail #1 — paid/capped. No premium plan exists yet, so everyone is on the
    # free monthly cap; a real premium tier will bypass this later.
    used = await db.count_tailored_usage_month(user.id)
    if used >= TAILOR_FREE_PER_MONTH:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Monthly free limit reached ({used}/{TAILOR_FREE_PER_MONTH}). "
                "Tailored documents are a premium feature."
            ),
        )

    cv_text = _load_cv_text(user.id)
    if not cv_text:
        raise HTTPException(
            status_code=400,
            detail="No CV on file — upload your CV on the Profile page before tailoring.",
        )

    fit_reason = await db.get_fit_reason(user.id, job_id)

    for kind in DOC_KINDS:
        # Layer 2 (per-user): the user's own past KEPT docs → 'write like me'.
        examples = await db.get_user_kept_docs(user.id, kind, limit=3)
        # Layer 1 (universal, cold-start only): patterns-only structural guidance,
        # used when the user has no history of their own yet.
        universal = ""
        if not examples:
            universal = summarize_patterns(await db.get_tailoring_patterns(kind), kind)
        try:
            doc = await generate_document(
                doc_kind=kind,
                cv_text=cv_text,
                job_title=job.get("title", ""),
                company=job.get("company", ""),
                job_description=job.get("description", ""),
                fit_reason=fit_reason,
                user_examples=examples,
                universal_patterns=universal,
                llm_extract_fn=llm_extract,
            )
        except EmptyCVError:
            raise HTTPException(status_code=400, detail="No CV text to reshape.")
        except Exception as exc:  # noqa: BLE001 — surface LLM failure clearly, don't 500 silently
            raise HTTPException(status_code=503, detail=f"Generation failed: {exc}")
        await db.upsert_tailored_doc(
            user.id, job_id, kind, doc.document,
            model=doc.model, flagged_terms=doc.flagged_terms,
        )

    await db.record_tailored_usage(user.id, job_id)
    get_audit_logger().info(
        "tailor_generate",
        extra={"user_id": user.id, "job_id": job_id, "quota_used": used + 1},
    )
    return await _bundle(db, user.id, job_id)


@router.get("/tailor/{job_id}", response_model=TailorBundle)
async def get_tailored(
    job_id: int,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
):
    """Return the caller's tailored docs for a job (empty list if none generated)."""
    return await _bundle(db, user.id, job_id)


@router.get("/tailor/{job_id}/{doc_kind}/provenance", response_model=list[ProvenanceSegment])
async def provenance(
    job_id: int,
    doc_kind: str,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
):
    """Per-line provenance for the doc: which lines are the user's OWN facts (grounded
    in their CV + the job) vs lines the AI added. Deterministic, no LLM — shown before
    download so the user can verify what's real (user request / guardrail #2)."""
    _check_kind(doc_kind)
    row = await db.get_tailored_doc(user.id, job_id, doc_kind)
    if row is None:
        raise HTTPException(status_code=404, detail="Nothing to check — generate first.")
    text = row.get("polished") or row.get("ai_draft") or ""
    job = await db.get_job_by_id(job_id)
    source = _load_cv_text(user.id) + "\n" + (job.get("description", "") if job else "")
    return annotate_provenance(text, source)


@router.patch("/tailor/{job_id}/{doc_kind}", response_model=TailoredDocOut)
async def save_edit(
    job_id: int,
    doc_kind: str,
    body: TailorSaveRequest,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
):
    """Save the user's edited/polished version (guardrail #3 — always editable)."""
    _check_kind(doc_kind)
    existing = await db.get_tailored_doc(user.id, job_id, doc_kind)
    if existing is None:
        raise HTTPException(status_code=404, detail="No draft to edit — generate first.")
    row = await db.save_tailored_polished(user.id, job_id, doc_kind, body.text)
    return _doc_out(row)


@router.post("/tailor/{job_id}/{doc_kind}/keep", response_model=TailoredDocOut)
async def keep(
    job_id: int,
    doc_kind: str,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
):
    """Mark KEPT → the learning trigger (§5 learn-from-kept-only)."""
    _check_kind(doc_kind)
    existing = await db.get_tailored_doc(user.id, job_id, doc_kind)
    if existing is None:
        raise HTTPException(status_code=404, detail="Nothing to keep — generate first.")
    row = await db.keep_tailored_doc(user.id, job_id, doc_kind)
    await _learn_universal(db, doc_kind, row)
    get_audit_logger().info(
        "tailor_keep", extra={"user_id": user.id, "job_id": job_id, "doc_kind": doc_kind}
    )
    return _doc_out(row)


@router.post("/tailor/{job_id}/{doc_kind}/download")
async def download(
    job_id: int,
    doc_kind: str,
    fmt: str = "pdf",
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_verified_user),
):
    """Download the polished (or draft) doc as an ATS-friendly PDF or DOCX. Marks it KEPT.

    ``fmt`` = ``pdf`` (default) | ``docx``.

    POST, not GET (docs/fable/01 S6): this endpoint MUTATES — it marks the doc kept
    and feeds `_learn_universal`. A side-effecting GET is both wrong HTTP semantics
    and a CSRF hole: `OriginCheckMiddleware` only guards unsafe methods, and a
    cross-site top-level link click still sends the SameSite=Lax cookie. As POST it
    is Origin-checked like every other mutation. The frontend already fetches this
    as a blob, so the method change is transparent there.
    """
    _check_kind(doc_kind)
    if fmt not in ("pdf", "docx"):
        raise HTTPException(status_code=400, detail="format must be 'pdf' or 'docx'")
    row = await db.get_tailored_doc(user.id, job_id, doc_kind)
    if row is None:
        raise HTTPException(status_code=404, detail="Nothing to download — generate first.")
    text = row.get("polished") or row.get("ai_draft") or ""
    title = "Curriculum Vitae" if doc_kind == "cv" else "Cover Letter"

    if fmt == "docx":
        content = render_docx(text, title=title)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        content = render_pdf(text, title=title)
        media_type = "application/pdf"

    # Downloading counts as 'used' → keep it + learn from it (§5).
    kept = await db.keep_tailored_doc(user.id, job_id, doc_kind)
    await _learn_universal(db, doc_kind, kept or row)

    filename = f"{doc_kind}_{job_id}.{fmt}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _learn_universal(db: JobDatabase, doc_kind: str, row: dict | None) -> None:
    """Layer 1 (§6/§7): store PATTERNS ONLY from a kept doc — never content/PII."""
    if not row:
        return
    text = row.get("polished") or row.get("ai_draft") or ""
    if not text.strip():
        return
    import json as _json

    features = derive_patterns(text, doc_kind)  # structural numbers/labels only
    await db.record_tailoring_pattern(doc_kind, _json.dumps(features))
