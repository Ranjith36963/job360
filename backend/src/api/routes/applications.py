"""The application spine's REST surface (docs/plans/2026-09-04-application-
spine/spec.md, §Tool contracts). Every route here is also an MCP tool
(``src/api/mcp_server.py``) calling the SAME function — one API for every
surface.

Auth: every route ``Depends(require_user)`` (session cookie, personal
``j360_…`` token, or OAuth ``j360a_…`` bearer — S1). None of these routes
spend an LLM call, so none is ``require_verified_user``.

Route-declaration order matters: ``/applications/export`` is declared BEFORE
``/applications/{application_id}`` (spec §Tool contracts) so a stray literal
path can never be swallowed by the dynamic one.

``recorded_by`` / ``made_by`` are NEVER read from a request body — every
request model here sets ``model_config = ConfigDict(extra="forbid")``, so a
body carrying either field is rejected by Pydantic with 422 before this
module's code ever runs (S3).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.models import JobResponse
from src.api.routes.bring import job_row_to_response
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import contacts as contacts_service
from src.services.applications import spine
from src.services.applications import stats as stats_service
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError

router = APIRouter(tags=["applications"])


def _raise(exc: SpineError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


# ── Request models ───────────────────────────────────────────────────────────


class SaveArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    text: str
    label: str = Field("", max_length=100)
    model: Optional[str] = Field(None, max_length=200)


class SaveFitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: Optional[int] = Field(None, ge=0, le=100)
    verdict: Optional[str] = Field(None, max_length=200)
    gaps: Optional[list[str]] = Field(None, max_length=50)
    reasoning: Optional[str] = None

    def clamp_reasoning(self) -> Optional[str]:
        if self.reasoning is None:
            return None
        cap = settings.APPLICATION_FIT_REASONING_MAX_CHARS
        if len(self.reasoning) > cap:
            raise SpineError(422, f"reasoning exceeds APPLICATION_FIT_REASONING_MAX_CHARS ({cap} chars)")
        return self.reasoning


class RecordEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: Optional[str] = None
    corrects_event_id: Optional[int] = None

    def clamp_detail(self) -> str:
        cap = settings.APPLICATION_EVENT_DETAIL_MAX_CHARS
        if len(self.detail) > cap:
            raise SpineError(422, f"detail exceeds APPLICATION_EVENT_DETAIL_MAX_CHARS ({cap} chars)")
        return self.detail


class ReceiptAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., max_length=500)
    answer: str = Field(..., max_length=settings.APPLICATION_RECEIPT_ANSWER_MAX_CHARS)


class RecordApplicationReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field("", max_length=100)
    note: str = Field("", max_length=2_000)
    confirmation: str = Field("", max_length=200)
    answers: list[ReceiptAnswer] = Field(default_factory=list, max_length=settings.APPLICATION_RECEIPT_ANSWERS_MAX)
    fields_filled: dict[str, Any] = Field(default_factory=dict)
    cv_artifact_id: Optional[int] = None
    cover_letter_artifact_id: Optional[int] = None
    applied_at: Optional[str] = None

    def clamp_fields_filled(self) -> dict[str, Any]:
        cap = settings.APPLICATION_RECEIPT_FIELDS_MAX_BYTES
        size = len(json.dumps(self.fields_filled).encode("utf-8"))
        if size > cap:
            raise SpineError(422, f"fields_filled exceeds APPLICATION_RECEIPT_FIELDS_MAX_BYTES ({cap} bytes)")
        return self.fields_filled


class AddContactRequest(BaseModel):
    """Slice 4 (docs/plans/2026-09-05-contacts-stats/spec.md §Tool contracts).

    No length/shape constraints declared here (unlike ``SaveArtifactRequest``'s
    ``label``): every cap is a live ``settings`` value a test can monkeypatch,
    so ``contacts.add_contact`` checks them at call time — the same pattern
    ``SaveFitRequest.clamp_reasoning``/``RecordEventRequest.clamp_detail`` use.
    ``added_by`` is deliberately ABSENT — ``extra="forbid"`` rejects it (S2).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    role: str = ""
    email: str = ""
    linkedin_url: str = ""
    notes: str = ""
    occurred_at: Optional[str] = None


# ── Response models ──────────────────────────────────────────────────────────
#
# Every route here carries `response_model` so the generated `api-types.ts`
# (and its drift gate) sees a real shape — same reasoning as oauth.py's
# comment above `ConsentRequestResponse`. Field types follow the rule the
# request models already use: `Optional[X]` with NO default means the key is
# ALWAYS present but may be `null`; `Optional[X] = None` means the key can be
# ABSENT from the response entirely (openapi-typescript renders the first as
# `X | null` and the second as an optional key).


class ApplicationJobOut(BaseModel):
    job_title: str
    job_company: str
    job_location: str
    job_url: str
    job_source: str
    job_description_snapshot: str
    snapshot_at: Optional[str]
    catalog_present: bool


class ApplicationFitOut(BaseModel):
    score: Optional[int]
    verdict: Optional[str]
    gaps: list[str]
    reasoning: Optional[str]
    recorded_by: str
    recorded_at: str


class ApplicationEventOut(BaseModel):
    """The timeline shape (``list_events_for_display``) — used by
    ``get_application`` and ``export_history``. Carries ``superseded``;
    ``whats_new``'s events do not (see ``WhatsNewEventOut``)."""

    id: int
    event_type: str
    detail: str
    payload: dict[str, Any]
    occurred_at: str
    recorded_at: str
    recorded_by: str
    corrects_event_id: Optional[int]
    superseded: bool


class ApplicationArtifactOut(BaseModel):
    """The ``get_application`` artifacts-list shape: ``text`` is ALWAYS a key
    (null unless ``with_artifact_text=true`` and under the byte cap)."""

    id: int
    kind: str
    version_no: int
    made_by: str
    model: Optional[str]
    profile_version: Optional[int]
    label: str
    chars: int
    created_at: str
    text: Optional[str]
    truncated: bool


class ApplicationArtifactRowOut(BaseModel):
    """``get_application_artifact`` — one version in full. No ``truncated``:
    this route always returns the real row, never a capped read."""

    id: int
    kind: str
    version_no: int
    text: str
    made_by: str
    model: Optional[str]
    profile_version: Optional[int]
    label: str
    chars: int
    created_at: str


class ApplicationReceiptOut(BaseModel):
    """``get_application``'s receipts list — never carries the receipt text
    (that call site never passes ``include_text``; see
    ``ApplicationReceiptExportOut`` for the ``export_history`` shape)."""

    id: int
    sent_at: str
    channel: str
    confirmation: str
    cv_artifact_id: Optional[int]
    cover_letter_artifact_id: Optional[int]
    note: str


class ApplicationReceiptExportOut(ApplicationReceiptOut):
    cv_text: Optional[str] = None
    cover_letter_text: Optional[str] = None


class ContactOut(BaseModel):
    """A contact row (``contacts.list_contacts`` / ``add_contact``'s return).
    Same shape everywhere a contact appears — detail, export, the add response."""

    id: int
    application_id: int
    name: str
    role: str
    email: str
    linkedin_url: str
    notes: str
    added_by: str
    created_at: str


class ApplicationDetailOut(BaseModel):
    id: int
    job_id: int
    status: str
    created_at: str
    updated_at: str
    last_event_at: Optional[str]
    job: ApplicationJobOut
    fit: Optional[ApplicationFitOut]
    artifacts: list[ApplicationArtifactOut]
    events: list[ApplicationEventOut]
    receipts: list[ApplicationReceiptOut]
    contacts: list[ContactOut]


class ApplicationSummaryOut(BaseModel):
    id: int
    job_id: int
    job_title: str
    job_company: str
    status: str
    last_event_at: Optional[str]
    events: int
    artifacts: dict[str, int]
    receipts: int


class ListApplicationsResponse(BaseModel):
    applications: list[ApplicationSummaryOut]
    total: int


class SaveArtifactResponse(BaseModel):
    artifact_id: int
    kind: str
    version_no: int
    chars: int
    made_by: str
    model: Optional[str]
    profile_version: Optional[int]
    created_at: str
    event_id: int


class SaveFitResponse(BaseModel):
    application_id: int
    fit: ApplicationFitOut
    event_id: int


class RecordEventResponse(BaseModel):
    event_id: int
    event_type: str
    occurred_at: str
    recorded_at: str
    recorded_by: str
    status: str


class RecordApplicationReceiptResponse(BaseModel):
    receipt_id: int
    sent_at: str
    cv_artifact_id: Optional[int]
    cv_version_no: Optional[int]
    cover_letter_artifact_id: Optional[int]
    channel: str
    confirmation: str
    url: str
    event_id: int


class WhatsNewEventOut(BaseModel):
    """``whats_new``'s raw event rows — no ``superseded`` (unlike
    ``ApplicationEventOut``); carries ``application_id`` instead."""

    id: int
    application_id: int
    event_type: str
    detail: str
    payload: dict[str, Any]
    occurred_at: str
    recorded_at: str
    recorded_by: str
    corrects_event_id: Optional[int]


class WhatsNewApplicationOut(BaseModel):
    id: int
    job_title: str
    job_company: str
    status: str
    last_event_at: Optional[str]


class WhatsNewResponse(BaseModel):
    now: str
    since: str
    events: list[WhatsNewEventOut]
    applications: list[WhatsNewApplicationOut]
    next_since: str
    next_after_id: Optional[int]
    truncated: bool


class ExportArtifactOut(BaseModel):
    """``export_history``'s artifact METADATA (not the full row): ``text`` is
    only a key at all when ``include_text=true`` — hence the default."""

    id: int
    kind: str
    version_no: int
    made_by: str
    model: Optional[str]
    profile_version: Optional[int]
    label: str
    chars: int
    created_at: str
    text: Optional[str] = None


class ExportApplicationOut(BaseModel):
    id: int
    job_id: int
    status: str
    job_title: str
    job_company: str
    created_at: str
    updated_at: str
    last_event_at: Optional[str]
    events: list[ApplicationEventOut]
    artifacts: list[ExportArtifactOut]
    receipts: list[ApplicationReceiptExportOut]
    contacts: list[ContactOut]


class ProfileEditExportOut(BaseModel):
    """One row of ``export_history``'s top-level ``profile_edits`` — EVERY
    row, including a clear (``value: null``); it is the history, unlike
    ``GET /profile``'s ``agent_edits`` (the live, non-cleared overlay only)."""

    path: str
    value: Any
    set_by: str
    set_at: str


class ExportHistoryResponse(BaseModel):
    applications: list[ExportApplicationOut]
    truncated: bool
    bytes: int
    profile_edits: list[ProfileEditExportOut]
    # S3 — true when the edit history was cut at EXPORT_HISTORY_MAX_PROFILE_EDITS
    # (the newest N are kept, rendered oldest-first).
    profile_edits_truncated: bool = False
    next_since: Optional[str] = None


class AddContactResponse(BaseModel):
    contact: ContactOut
    already_existed: bool
    event_id: Optional[int]


class StatsOverallOut(BaseModel):
    brought: int
    applied: int
    replied: int
    interview: int
    offer: int
    rejected: int
    reply_rate: Optional[float]
    interview_rate: Optional[float]
    offer_rate: Optional[float]


class StatsCvVersionGroupOut(BaseModel):
    # The normalised grouping key (`lower(trim(label))`) — what the tie-break
    # in the group order sorts on, and a stable handle for the caller. `label`
    # is the display spelling from the group's earliest application.
    key: Optional[str]
    label: Optional[str]
    profile_versions: list[int]
    brought: int
    applied: int
    replied: int
    interview: int
    offer: int
    rejected: int
    reply_rate: Optional[float]
    interview_rate: Optional[float]
    offer_rate: Optional[float]


class StatsRoleGroupOut(BaseModel):
    # See StatsCvVersionGroupOut.key — same normalised key, `role` is display.
    key: Optional[str]
    role: Optional[str]
    brought: int
    applied: int
    replied: int
    interview: int
    offer: int
    rejected: int
    reply_rate: Optional[float]
    interview_rate: Optional[float]
    offer_rate: Optional[float]


class StatsResponse(BaseModel):
    since: Optional[str]
    overall: StatsOverallOut
    by_cv_version: list[StatsCvVersionGroupOut]
    by_role: list[StatsRoleGroupOut]
    groups_truncated: bool
    # S6 — true when only the newest STATS_MAX_APPLICATIONS applications were
    # counted; the numbers describe that window, not the whole history.
    applications_truncated: bool
    computed_at: str


# ── Routes — order matters: /export before /{application_id} ────────────────


@router.get("/applications/export", response_model=ExportHistoryResponse)
async def export_history(
    since: Optional[str] = Query(None),
    include_text: bool = Query(False),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await spine.export_history(db, user.id, since=since, include_text=include_text)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover — _raise always raises


@router.get("/applications/stats", response_model=StatsResponse)
async def stats(
    since: Optional[str] = Query(None),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await stats_service.compute_stats(db, user.id, since)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.get("/applications", response_model=ListApplicationsResponse)
async def list_applications(
    status: Optional[str] = Query(None),
    updated_since: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    return await spine.list_applications(
        db, user.id, status=status, updated_since=updated_since, limit=limit, offset=offset
    )


@router.get("/applications/job/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> JobResponse:
    """Read back an ad THIS user brought, by its `job_id`.

    Slice 5 (#483) deleted the public `GET /api/jobs/{id}`. That route served
    the shared catalog to anyone with an id, which stopped being defensible
    the moment `jobs` held nothing but ads individual people pasted (S1). This
    is its per-user replacement: the row is returned only when the caller has
    an application for it, so an id they never brought reads as 404, never as
    somebody else's paste.

    Declared BEFORE `/applications/{application_id}` for the same reason
    `/applications/export` is — a literal segment must not be swallowed by the
    dynamic one.
    """
    if await spine.get_application_by_job(db, user.id, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    row = await db.get_job_by_id(job_id)
    if row is None:  # pragma: no cover — an application always has its job row
        raise HTTPException(status_code=404, detail="Job not found")
    return job_row_to_response(dict(row))


@router.get("/applications/{application_id}", response_model=ApplicationDetailOut)
async def get_application(
    application_id: int,
    with_artifact_text: bool = Query(False),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    detail = await spine.get_application_detail(
        db, user.id, application_id, with_artifact_text=with_artifact_text
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="application not found")
    return detail


@router.get(
    "/applications/{application_id}/artifacts/{artifact_id}", response_model=ApplicationArtifactRowOut
)
async def get_application_artifact(
    application_id: int,
    artifact_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    row = await spine.get_artifact(db, user.id, application_id, artifact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return row


@router.post(
    "/applications/{application_id}/contacts",
    response_model=AddContactResponse,
    # The runtime status is set on `response` below (201 create / 200 replay),
    # which FastAPI cannot infer — so the 201 is declared here by hand.
    # Without it the schema promised 200 ONLY, and every generated client
    # (frontend `api-types.ts` included) treated a successful create as an
    # undocumented response.
    responses={201: {"model": AddContactResponse, "description": "Contact created"}},
)
async def add_contact(
    application_id: int,
    body: AddContactRequest,
    response: Response,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """201 for a new contact, 200 (``already_existed: true``) for the same
    email seen again on this application (R2) — the status code is set
    dynamically since the same call can legitimately answer either."""
    try:
        result = await contacts_service.add_contact(
            db, user.id, application_id, actor_for(user),
            name=body.name, role=body.role, email=body.email, linkedin_url=body.linkedin_url,
            notes=body.notes, occurred_at=body.occurred_at,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover
    response.status_code = 200 if result["already_existed"] else 201
    return result


@router.post(
    "/applications/{application_id}/artifacts", status_code=201, response_model=SaveArtifactResponse
)
async def save_artifact(
    application_id: int,
    body: SaveArtifactRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await spine.save_artifact(
            db, user_id=user.id, application_id=application_id, kind=body.kind, text=body.text,
            made_by=actor_for(user), label=body.label, model=body.model,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.put("/applications/{application_id}/fit", response_model=SaveFitResponse)
async def save_fit(
    application_id: int,
    body: SaveFitRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        reasoning = body.clamp_reasoning()
        return await spine.save_fit(
            db, user_id=user.id, application_id=application_id, recorded_by=actor_for(user),
            score=body.score, verdict=body.verdict, gaps=body.gaps, reasoning=reasoning,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.post(
    "/applications/{application_id}/events", status_code=201, response_model=RecordEventResponse
)
async def record_event(
    application_id: int,
    body: RecordEventRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        spine.validate_event_type(body.event_type)
        detail = body.clamp_detail()
        payload = spine.validate_payload(body.payload)
        occurred_at = spine.parse_occurred_at(body.occurred_at)
        app_row = await spine.get_owned_application(db, user.id, application_id)
        if app_row is None:
            raise SpineError(404, "application not found")
        return await spine.append_event(
            db, user_id=user.id, application_id=application_id, event_type=body.event_type,
            detail=detail, payload=payload, occurred_at=occurred_at, recorded_by=actor_for(user),
            corrects_event_id=body.corrects_event_id,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.post(
    "/applications/{application_id}/receipt",
    status_code=201,
    response_model=RecordApplicationReceiptResponse,
)
async def record_application_receipt(
    application_id: int,
    body: RecordApplicationReceiptRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        fields_filled = body.clamp_fields_filled()
        return await spine.record_receipt(
            db, user_id=user.id, application_id=application_id, recorded_by=actor_for(user),
            channel=body.channel, note=body.note, confirmation=body.confirmation,
            answers=[a.model_dump() for a in body.answers], fields_filled=fields_filled,
            cv_artifact_id=body.cv_artifact_id, cover_letter_artifact_id=body.cover_letter_artifact_id,
            applied_at=body.applied_at,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.get("/whats-new", response_model=WhatsNewResponse)
async def whats_new(
    since: Optional[str] = Query(None),
    after_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    return await spine.whats_new(db, user.id, since=since, after_id=after_id, limit=limit)
