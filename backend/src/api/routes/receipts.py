"""Application receipts — "what did I send X?" answered exactly, forever.

One click at "I applied" freezes the job as it read and the CV / cover letter
as they stood (migration 0034). After that nothing rewrites the record:
re-tailoring (which DELETE+INSERTs `tailored_documents`), the ad expiring, or
the catalog purging the row all leave the receipt untouched.

This is slice one of the career-ops pivot (docs/plans/
2026-08-27-exponential-product-research.md §8) and the data behind the first
MCP tool. Routes are per-user (rule #12) and there is no PATCH or DELETE by
design — see tests/test_receipts.py::test_receipts_are_append_only.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase
from src.utils.logger import get_audit_logger

router = APIRouter(tags=["receipts"])


class CreateReceiptRequest(BaseModel):
    channel: str = Field("", max_length=100)   # "company site", "LinkedIn", "email"…
    note: str = Field("", max_length=2_000)


class Receipt(BaseModel):
    id: int
    job_id: int
    sent_at: str
    job_title: str
    job_company: str
    job_location: str
    job_apply_url: str
    job_source: str
    job_description: str
    cv_text: str | None
    cv_origin: str | None
    cover_letter_text: str | None
    cover_letter_origin: str | None
    profile_version: int | None
    channel: str
    note: str


class ReceiptSummary(BaseModel):
    """List row: everything but the three long bodies."""
    id: int
    job_id: int
    sent_at: str
    job_title: str
    job_company: str
    job_location: str
    job_apply_url: str
    has_cv: bool
    has_cover_letter: bool
    channel: str
    note: str


class ReceiptListResponse(BaseModel):
    receipts: list[ReceiptSummary]
    total: int


def _to_receipt(row: dict[str, Any]) -> Receipt:
    return Receipt(**{k: row[k] for k in Receipt.model_fields})


def _to_summary(row: dict[str, Any]) -> ReceiptSummary:
    return ReceiptSummary(
        id=row["id"], job_id=row["job_id"], sent_at=row["sent_at"],
        job_title=row["job_title"], job_company=row["job_company"],
        job_location=row["job_location"], job_apply_url=row["job_apply_url"],
        has_cv=bool(row["has_cv"]), has_cover_letter=bool(row["has_cover_letter"]),
        channel=row["channel"], note=row["note"],
    )


def _sent_text(doc: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """The document the user would actually have sent: their polished edit if
    they made one, else the AI draft. (None, None) when there is no document.
    """
    if not doc:
        return None, None
    if doc.get("polished"):
        return doc["polished"], "polished"
    if doc.get("ai_draft"):
        return doc["ai_draft"], "ai_draft"
    return None, None


@router.post("/receipts/{job_id}", response_model=Receipt, status_code=201)
async def create_receipt(
    job_id: int,
    body: CreateReceiptRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI DI idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> Receipt:
    """"I applied": freeze the receipt, then mark the job applied in BOTH
    existing per-user tables (`user_actions` for the card, `applications` for
    the pipeline) so every surface agrees.
    """
    from src.services.applications import spine as applications_spine  # noqa: PLC0415
    from src.services.profile.storage import current_profile_version_id  # noqa: PLC0415

    job = await db.get_job_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    cv_text, cv_origin = _sent_text(await db.get_tailored_doc(user.id, job_id, "cv"))
    cl_text, cl_origin = _sent_text(await db.get_tailored_doc(user.id, job_id, "cover_letter"))

    # Mark applied on the existing surfaces FIRST — `create_application` is an
    # upsert (INSERT OR IGNORE), so the application row (and its id) exists
    # before the receipt does. R8's `application_id` is then part of the
    # receipt's own INSERT (below), never a later UPDATE:
    # tests/test_receipts.py::test_receipts_are_append_only greps
    # `backend/src/` for any UPDATE/DELETE against `application_receipts`.
    await db.insert_action(job_id, "applied", user.id)
    await db.create_application(job_id, user.id)
    application = await applications_spine.get_application_by_job(db, user.id, job_id)

    receipt = await db.insert_receipt(
        user_id=user.id,
        job=job,
        cv_text=cv_text,
        cv_origin=cv_origin,
        cover_letter_text=cl_text,
        cover_letter_origin=cl_origin,
        profile_version=current_profile_version_id(user.id),
        channel=body.channel.strip(),
        note=body.note.strip(),
        application_id=application["id"] if application else None,
    )

    # R8 — write-through to the spine: append the `applied` event. The
    # route's OWN shape and behaviour are unchanged for the caller
    # (constraint 4).
    if application is not None:
        await applications_spine.write_through_legacy_receipt(
            db, user_id=user.id, application_id=application["id"],
            receipt_id=receipt["id"], sent_at=receipt["sent_at"], note=body.note.strip(),
        )

    get_audit_logger().info(
        "receipt_create",
        extra={
            "event": "receipt_create", "job_id": job_id, "user_id": user.id,
            "receipt_id": receipt["id"], "has_cv": cv_text is not None,
            "has_cover_letter": cl_text is not None, "status": "ok",
        },
    )
    return _to_receipt(receipt)


@router.get("/receipts", response_model=ReceiptListResponse)
async def list_receipts(
    job_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ReceiptListResponse:
    rows = await db.list_receipts(user.id, job_id=job_id, limit=limit, offset=offset)
    total = await db.count_receipts(user.id, job_id=job_id)
    return ReceiptListResponse(receipts=[_to_summary(r) for r in rows], total=total)


@router.get("/receipts/{receipt_id}", response_model=Receipt)
async def get_receipt(
    receipt_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> Receipt:
    row = await db.get_receipt(user.id, receipt_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return _to_receipt(row)
