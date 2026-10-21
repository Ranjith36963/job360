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

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.routes.applications import ProofOut, ReceiptAnswerOut
from src.repositories.database import JobDatabase
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["receipts"])


class CreateReceiptRequest(BaseModel):
    # Owner decision 2026-10-04 — a closed set for every NEW receipt
    # (settings.APPLICATION_RECEIPT_CHANNELS; '' = not said), checked by the
    # same spine.normalize_receipt_channel the rich receipt route uses.
    channel: str = Field("", max_length=100)
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
    # What the assistant recorded when the user applied (record_application,
    # spine R8) — stored facts read off this same row, never computed. A
    # receipt made without them (the web "I applied" button, rows from before
    # 0037) reads empty / None: no defaults, no placeholders (rule #29).
    application_id: int | None = None
    answers: list[ReceiptAnswerOut] = Field(default_factory=list)
    fields_filled: dict[str, Any] = Field(default_factory=dict)
    confirmation: str | None = None
    cv_version_no: int | None = None
    cover_letter_version_no: int | None = None
    # True iff the receipt names a cover letter artifact; False = no letter was sent.
    cover_letter_sent: bool = False
    # Read-time facts (spine.receipt_summary_facts); "" / None when no form_filled was recorded.
    counts_line: str = ""
    fields_total: int | None = None
    fields_skipped: int = 0
    guessed_count: int = 0
    form_host: str = ""
    recorded_by: str | None = None
    # S3 - '' | same_job | same_role | same_company, the kit the assistant filled from. Set
    # at INSERT; a receipt made without a kit reads '' / None / ''.
    possible_duplicate: str = ""
    kit_event_id: int | None = None
    kit_sha256: str = ""
    # S7 - how well this application is backed up; "none" when there is no application.
    proof: ProofOut = Field(default_factory=ProofOut)


_RECEIPT_BASE_FIELDS = (
    "id", "job_id", "sent_at", "job_title", "job_company", "job_location", "job_apply_url",
    "job_source", "job_description", "cv_text", "cv_origin", "cover_letter_text",
    "cover_letter_origin", "profile_version", "channel", "note",
)


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
    cover_letter_sent: bool = False
    counts_line: str = ""
    form_host: str = ""
    channel: str
    note: str
    proof: ProofOut = Field(default_factory=ProofOut)


class ReceiptListResponse(BaseModel):
    receipts: list[ReceiptSummary]
    total: int


async def _to_receipt(db: JobDatabase, user_id: str, row: dict[str, Any]) -> Receipt:
    """The frozen row plus the details recorded on the SAME
    `application_receipts` row (spine.receipt_details), scoped by owner."""
    from src.services.applications import spine as applications_spine  # noqa: PLC0415

    details = await applications_spine.receipt_details(db, user_id, int(row["id"])) or {}
    return Receipt(**{k: row[k] for k in _RECEIPT_BASE_FIELDS}, **details)


def _to_summary(
    row: dict[str, Any], proof: dict[str, Any] | None = None, letter_sent: bool = False,
    facts: dict[str, Any] | None = None,
) -> ReceiptSummary:
    return ReceiptSummary(
        id=row["id"], job_id=row["job_id"], sent_at=row["sent_at"],
        job_title=row["job_title"], job_company=row["job_company"],
        job_location=row["job_location"], job_apply_url=row["job_apply_url"],
        has_cv=bool(row["has_cv"]), has_cover_letter=bool(row["has_cover_letter"]),
        cover_letter_sent=letter_sent,
        counts_line=(facts or {}).get("counts_line", ""), form_host=(facts or {}).get("form_host", ""),
        channel=row["channel"], note=row["note"], proof=ProofOut(**(proof or {})),
    )


def _sent_text(doc: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """LEGACY (pre-decision-28) `tailored_documents` row → the text the user
    would have sent: their polished edit if they made one, else the draft.
    (None, None) when there is no such row. Nothing writes that table any more.
    """
    if not doc:
        return None, None
    if doc.get("polished"):
        return doc["polished"], "polished"
    if doc.get("ai_draft"):
        return doc["ai_draft"], "ai_draft"
    return None, None


async def _document_sent(
    db: JobDatabase, user_id: str, application: dict[str, Any] | None, job_id: int, kind: str
) -> tuple[str | None, str | None]:
    """The document to freeze: the newest version saved on the application
    spine (what the agent wrote), else a legacy `tailored_documents` row."""
    from src.services.applications import spine as applications_spine  # noqa: PLC0415

    if application is not None:
        artifact = await applications_spine.latest_artifact(
            db, user_id, int(application["id"]), kind
        )
        if artifact and (artifact.get("text") or "").strip():
            return artifact["text"], "artifact"
    return _sent_text(await db.get_tailored_doc(user_id, job_id, kind))


@router.post("/receipts/{job_id}", response_model=Receipt, status_code=201, dependencies=AUTH_FIRST)
async def create_receipt(
    job_id: int,
    body: CreateReceiptRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI DI idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> Receipt:
    """"I applied": freeze the receipt, then mark the job applied on the
    application spine's `applications` row so every surface agrees.
    """
    from src.services.applications import spine as applications_spine  # noqa: PLC0415
    from src.services.profile.storage import current_profile_version_id  # noqa: PLC0415

    try:
        channel = applications_spine.normalize_receipt_channel(body.channel)
    except applications_spine.SpineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    # P0 2026-10-14 — only a job THIS user brought. The catalog is shared
    # (rule #10): upserting an application onto any existing job_id and
    # freezing the catalog row into the receipt handed the caller another
    # user's pasted ad. A job they never brought reads as 404 (no oracle).
    # R8's `application_id` is part of the receipt's own INSERT (below),
    # never a later UPDATE: tests/test_receipts.py::
    # test_receipts_are_append_only greps `backend/src/` for any
    # UPDATE/DELETE against `application_receipts`.
    application = await applications_spine.get_application_by_job(db, user.id, job_id)
    app_row = (
        await applications_spine.get_owned_application(db, user.id, int(application["id"]))
        if application is not None else None
    )
    if application is None or app_row is None or await db.get_job_by_id(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # The ad "as it read" is the caller's own snapshot, never the catalog row.
    job = {
        "id": job_id, "title": app_row.get("job_title") or "", "company": app_row.get("job_company") or "",
        "location": app_row.get("job_location") or "", "apply_url": app_row.get("job_url") or "",
        "source": app_row.get("job_source") or "", "description": app_row.get("job_description_snapshot") or "",
    }

    # Decision 28 (2026-09-21): the documents live in `application_artifacts`
    # — the agent writes them and `save_artifact` versions them. Nothing has
    # written `tailored_documents` since, so that read is a fallback for rows
    # this account made before the change, never the first answer.
    cv_text, cv_origin = await _document_sent(db, user.id, application, job_id, "cv")
    cl_text, cl_origin = await _document_sent(db, user.id, application, job_id, "cover_letter")

    receipt = await db.insert_receipt(
        user_id=user.id,
        job=job,
        cv_text=cv_text,
        cv_origin=cv_origin,
        cover_letter_text=cl_text,
        cover_letter_origin=cl_origin,
        profile_version=current_profile_version_id(user.id),
        channel=channel,
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
        # CodeQL py/log-injection — `user.id` is an internally-issued id, but
        # sanitized defensively since it still originates on a request path.
        extra={
            "event": "receipt_create", "job_id": job_id, "user_id": safe_log_value(user.id),
            "receipt_id": receipt["id"], "has_cv": cv_text is not None,
            "has_cover_letter": cl_text is not None, "status": "ok",
        },
    )
    return await _to_receipt(db, user.id, receipt)


@router.get("/receipts", response_model=ReceiptListResponse, dependencies=AUTH_FIRST)
async def list_receipts(
    job_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ReceiptListResponse:
    rows = await db.list_receipts(user.id, job_id=job_id, limit=limit, offset=offset)
    total = await db.count_receipts(user.id, job_id=job_id)
    from src.services.applications.proof import proof_for  # noqa: PLC0415

    proofs = await proof_for(db, user.id, sorted({int(r["application_id"]) for r in rows if r.get("application_id")}))
    from src.services.applications import spine as applications_spine  # noqa: PLC0415

    sent = await applications_spine.receipt_letter_sent(db, user.id, [int(r["id"]) for r in rows])
    return ReceiptListResponse(
        receipts=[
            _to_summary(
                r, proofs.get(int(r.get("application_id") or 0)), sent.get(int(r["id"]), False),
                await applications_spine.receipt_summary_facts(db, user.id, int(r["id"])),
            )
            for r in rows
        ],
        total=total,
    )


@router.get("/receipts/{receipt_id}", response_model=Receipt, dependencies=AUTH_FIRST)
async def get_receipt(
    receipt_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> Receipt:
    row = await db.get_receipt(user.id, receipt_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return await _to_receipt(db, user.id, row)
