"""The application spine's REST surface (docs/plans/2026-09-04-application-
spine/spec.md, §Tool contracts). Every route here is also an MCP tool
(``src/api/mcp_server.py``) calling the SAME function — one API for every
surface — with one deliberate exception: the read-only artifact diff
(slice 8, ``GET …/artifacts/{artifact_id}/diff``) is web-only by rule M2
(the agent already holds both texts), pinned by
``tests/test_artifact_diff.py::test_no_mcp_tool_diffs_an_artifact``.

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

import contextlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

from src.api.auth_deps import (
    AUTH_FIRST,
    CurrentUser,
    email_verification_satisfied,
    require_session_user,
    require_user,
)
from src.api.dependencies import get_request_db
from src.api.models import AutoEmailCheckResponse, FormCheckOut, FormCheckRequest, JobResponse, LessonsResponse
from src.api.routes.bring import application_job_response
from src.core import settings
from src.repositories.database import JobDatabase
from src.services import idempotency
from src.services.applications import auto_emails as auto_emails_service
from src.services.applications import blocked as blocked_service
from src.services.applications import contacts as contacts_service
from src.services.applications import diff as diff_service
from src.services.applications import interview_payloads, queue_preflight, spine
from src.services.applications import kit as kit_service
from src.services.applications import lessons as lessons_service
from src.services.applications import proof as proof_service
from src.services.applications import ready as ready_service
from src.services.applications import stats as stats_service
from src.services.applications import visa as visa_service
from src.services.applications.authorship import actor_for
from src.services.applications.fit_record import fit_view
from src.services.applications.spine import SpineError
from src.services.profile import assistant_settings as settings_rules
from src.services.profile.models import UserProfile
from src.services.profile.storage import load_profile
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["applications"])


def _raise(exc: SpineError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


_OUTREACH_EVENT_TYPES = ("outreach_sent", "outreach_replied")
OUTREACH_NEEDS_CONTACT = "outreach events need contact_id — call add_contact first and pass its contact_id"


def _outreach_needs_contact(tool: str, user_id: str, **ids: Any) -> SpineError:
    """The refusal for outreach written without a person: it would leave an
    orphan row and never reach the contact's message_count. Logs ids only."""
    get_audit_logger().info(
        "outreach_needs_contact",
        extra={"event": "outreach_needs_contact", "tool": tool, "user_id": safe_log_value(user_id), **ids},
    )
    return SpineError(422, OUTREACH_NEEDS_CONTACT)


# ── Request models ───────────────────────────────────────────────────────────


class SaveArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    text: str
    label: str = Field("", max_length=100)
    model: Optional[str] = Field(None, max_length=200)
    # Owner decision, 2026-09-25 — a message DRAFTED for a person. When given,
    # `kind` must be "outreach" and the text becomes a numbered version in
    # that contact's own outreach ledger (`contact_outreach`), not a new
    # `application_artifacts` row — a version is not news, so it appends no
    # timeline event (unlike every other artifact kind). The contact must be
    # linked to THIS route's `application_id` (URL segment) — a cold contact's
    # message goes through `POST /api/contacts/{contact_id}/outreach` instead,
    # which the `save_artifact` MCP tool calls directly when it is given a
    # `contact_id` but no `application_id`.
    contact_id: Optional[int] = None
    channel: Optional[str] = None
    # Owner decision 2026-10-04 — the ASSISTANT's own ATS check of this
    # document (Job360 never computes one). Only kinds in
    # APPLICATION_ARTIFACT_ATS_KINDS (cv, cover_letter) may carry it — any
    # other kind is a 422 (spine.validate_ats). `strict` so "70" or true is
    # refused, not coerced. Notes are capped at call time by
    # APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS (a live setting), control
    # characters removed.
    ats_score: Optional[int] = Field(None, ge=0, le=100, strict=True)
    ats_notes: Optional[str] = None


class FitAxisIn(BaseModel):
    """One axis of the fit picture (2026-09-20): the agent's own name for a
    dimension and two 0..100 numbers — how much the role asks on it and how
    much the seeker brings. Bounds on the count and the name length are live
    settings checked by ``spine.validate_axes``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    role: int = Field(..., ge=0, le=100)
    you: int = Field(..., ge=0, le=100)


class FitItemIn(BaseModel):
    """One gap / ad requirement with an optional hard|soft marker (slice 5).
    The kind set and the text cap are live settings checked by
    ``fit_record.clean_items`` (so a bad kind is a 422 naming the set)."""

    model_config = ConfigDict(extra="forbid")

    text: str
    kind: Optional[str] = None


class SaveFitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: Optional[int] = Field(None, ge=0, le=100)
    # With a score this is the REASON (1..FIT_SCORE_REASON_MAX_CHARS, checked by
    # fit_record); unscored it stays optional, at most 200 characters.
    verdict: Optional[str] = Field(None, max_length=200)
    # Who/what produced the score: one of settings.FIT_SCORE_SOURCES.
    score_source: Optional[str] = Field(None, max_length=20)
    gaps: Optional[list[Union[str, FitItemIn]]] = Field(None, max_length=50)
    jd_requirements: Optional[list[Union[str, FitItemIn]]] = Field(None, max_length=settings.FIT_JD_REQUIREMENTS_MAX)
    archetype: Optional[str] = Field(None, max_length=80)
    remote_policy: Optional[str] = Field(None, max_length=20)
    team_size: Optional[int] = Field(None, strict=True)
    via: Optional[str] = Field(None, max_length=20)
    reasoning: Optional[str] = None
    # The fit picture — `None`/`[]` = no chart (rule #29); part of the slot.
    axes: Optional[list[FitAxisIn]] = Field(None, max_length=50)
    # Slice 7 (#514) — the agent's visa reading, optional. `None` = not
    # judged this time (the slot is left alone); the closed set / alpha-2 /
    # length rules live in services/applications/visa.py.
    visa_signal: Optional[str] = Field(None, max_length=32)
    visa_detail: Optional[str] = Field(None, max_length=4_000)
    visa_country: Optional[str] = Field(None, max_length=8)

    def clamp_reasoning(self) -> Optional[str]:
        if self.reasoning is None:
            return None
        cap = settings.APPLICATION_FIT_REASONING_MAX_CHARS
        if len(self.reasoning) > cap:
            raise SpineError(422, f"reasoning exceeds APPLICATION_FIT_REASONING_MAX_CHARS ({cap} chars)")
        return self.reasoning


class EventSource(BaseModel):
    """Slice 6 (docs/plans/2026-09-07-email-evidence/spec.md §Tool contracts)
    — the email an event came from. No length caps declared here: every cap
    is a live ``settings`` value ``spine.validate_source`` checks at call
    time, the same reasoning ``AddContactRequest``'s docstring gives."""

    model_config = ConfigDict(extra="forbid")

    kind: str = "email"
    message_id: str
    sender: str = ""
    subject: str = ""
    received_at: Optional[str] = None


class RecordEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: Optional[str] = None
    corrects_event_id: Optional[int] = None
    source: Optional[EventSource] = None
    scheduled_at: Optional[str] = None
    # Owner decision, 2026-09-25: omitted (`None`) leaves the slot untouched;
    # `""` clears it; `YYYY-MM-DD` sets it. Works on any event_type — the
    # daily-check agent sets it on a plain `note` as often as on a status
    # event. `spine.parse_follow_up_on` does the real validation.
    follow_up_on: Optional[str] = None
    # Owner decision, 2026-09-25 — a `contact_id` here means "record this as
    # OUTREACH for that person too, in the same transaction": `event_type`
    # must then be `outreach_sent`/`outreach_replied` and `channel` is
    # required. The contact must be linked to THIS route's `application_id`
    # (a cold contact reads as 422 — there is no job to scope this call by;
    # use `POST /api/contacts/{contact_id}/outreach` for a cold contact).
    contact_id: Optional[int] = None
    channel: Optional[str] = None

    def clamp_detail(self) -> str:
        cap = settings.APPLICATION_EVENT_DETAIL_MAX_CHARS
        if len(self.detail) > cap:
            raise SpineError(422, f"detail exceeds APPLICATION_EVENT_DETAIL_MAX_CHARS ({cap} chars)")
        return self.detail


class ReceiptAnswer(BaseModel):
    """One answer on a receipt. The SAME shape the ``form_filled`` event takes
    (``kit.ANSWER_SOURCES`` is the one source list); ``source`` and ``key`` are
    optional here, stored when given and returned on read."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., max_length=500)
    answer: str = Field(..., max_length=settings.APPLICATION_RECEIPT_ANSWER_MAX_CHARS)
    source: Optional[str] = None
    key: Optional[str] = Field(None, min_length=1, max_length=kit_service.ANSWER_KEY_MAX)

    @field_validator("source")
    @classmethod
    def _known_source(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in kit_service.ANSWER_SOURCES:
            raise ValueError("answer source must be one of: " + ", ".join(kit_service.ANSWER_SOURCES))
        return value


class ReceiptAnswerOut(BaseModel):
    """The READ shape of an answer — deliberately without the request caps.
    A receipt is append-only history; if APPLICATION_RECEIPT_ANSWER_MAX_CHARS
    is ever lowered, older rows must still read back, not 500 the page.
    ``source`` / ``key`` are absent on receipts recorded before they existed."""

    question: str
    answer: str
    source: Optional[str] = None
    key: Optional[str] = None

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: Any):  # type: ignore[no-untyped-def]  # un-annotated keeps the OpenAPI schema typed
        """An answer stored without a source/key reads back without them (not as nulls)."""
        data = handler(self)
        return {k: v for k, v in data.items() if v is not None}


class RecordApplicationReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field("", max_length=100)
    note: str = Field("", max_length=2_000)
    confirmation: str = Field("", max_length=200)
    answers: list[ReceiptAnswer] = Field(default_factory=list, max_length=settings.APPLICATION_RECEIPT_ANSWERS_MAX)
    fields_filled: dict[str, Any] = Field(default_factory=dict)
    cv_artifact_id: Optional[int] = None
    cover_letter_artifact_id: Optional[int] = None
    # ISO-8601 with a timezone (parse_occurred_at); malformed = 422 naming applied_at.
    applied_at: Optional[str] = None
    # Idempotency key (1-128 printable chars): the same one again returns the FIRST
    # receipt with already_existed=true and writes nothing; a different body = 409.
    request_id: Optional[str] = None

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
    # Owner decision 2026-10-04 — where this person was found; closed set
    # CONTACT_FOUND_VIA, checked by contacts._validate_found_via at call time.
    found_via: Optional[str] = None


# ── Response models ──────────────────────────────────────────────────────────
#
# Every route here carries `response_model` so the generated `api-types.ts`
# (and its drift gate) sees a real shape — same reasoning as oauth.py's
# comment above `ConsentRequestResponse`. Field types follow the rule the
# request models already use: `Optional[X]` with NO default means the key is
# ALWAYS present but may be `null`; `Optional[X] = None` means the key can be
# ABSENT from the response entirely (openapi-typescript renders the first as
# `X | null` and the second as an optional key).


class PayAdvertisedOut(BaseModel):
    """Pay exactly as the ad states it (job_checks.pay_facts). Never annualised:
    ``period`` is null when the ad does not say; ``currency`` is "UNKNOWN" when
    no ISO code / unambiguous symbol sits beside the amount."""

    amount: str
    evidence: str
    period: Optional[str] = None
    currency: str
    min: Optional[int] = None
    max: Optional[int] = None
    equity: bool = False


class ApplicationJobOut(BaseModel):
    job_title: str
    job_company: str
    job_location: str
    job_url: str
    job_source: str
    job_description_snapshot: str
    snapshot_at: Optional[str]
    catalog_present: bool
    # Owner decision 2026-10-04 — the user's own facts about the job (null
    # when unset, rule #29): ISO alpha-2 country, remote, where it was found.
    country: Optional[str] = None
    remote: Optional[bool] = None
    found_on: Optional[str] = None
    ad_posted_at: Optional[str] = None
    employer_published_at: Optional[str] = None
    # Pay as advertised in the stored ad text (null = the ad states none).
    pay_advertised: Optional[PayAdvertisedOut] = None


class FitAxisOut(BaseModel):
    name: str
    role: int
    you: int


class FitItemOut(BaseModel):
    text: str
    kind: Optional[str] = None


class ApplicationFitOut(BaseModel):
    score: Optional[int]
    verdict: Optional[str]
    gaps: list[str]
    reasoning: Optional[str]
    score_source: Optional[str] = None
    gaps_detail: list[FitItemOut] = []
    jd_requirements: list[FitItemOut] = []
    archetype: Optional[str] = None
    remote_policy: Optional[str] = None
    team_size: Optional[int] = None
    via: Optional[str] = None
    # The fit picture's axes — `[]` when the agent gave none (drawn as nothing).
    axes: list[FitAxisOut] = []
    recorded_by: str
    recorded_at: str


class AlignmentOut(BaseModel):
    """``GET …/alignment`` (2026-09-20) — the fit picture: the agent's stored
    verdict beside which of the candidate's own skills occur in the stored
    ad text. Two stored facts drawn together; nothing computed about the
    candidate (VISION rule 4)."""

    fit: Optional[ApplicationFitOut]
    skills_in_ad: list[str]
    skills_not_in_ad: list[str]
    skills_total: int
    ad_chars: int


class ApplicationVisaOut(BaseModel):
    """Slice 7 — fact 1 (the agent's reading of the ad) plus the ONE
    comparison against fact 2 (the candidate's countries). ``needs_sponsorship``
    is ``null`` whenever either side is silent (rule #29)."""

    signal: str
    detail: str
    country: str
    recorded_by: str
    recorded_at: str
    needs_sponsorship: Optional[bool]


class EventSourceOut(BaseModel):
    """Slice 6 — the ``source`` shape every reader emits (or ``null``, when
    the event carries none)."""

    kind: str
    message_id: str
    sender: str
    subject: str
    received_at: Optional[str]


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
    source: Optional[EventSourceOut]
    scheduled_at: Optional[str]


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
    # Owner decision 2026-10-04 — the assistant's ATS opinion on this version
    # (null when none was given).
    ats_score: Optional[int] = None
    ats_notes: Optional[str] = None


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
    ats_score: Optional[int] = None
    ats_notes: Optional[str] = None


class ArtifactDiffBaseOut(BaseModel):
    """Slice 8 — what the target version is compared against: the profile's
    stored CV (``profile``), another version of the same kind (``artifact``),
    or nothing (``none`` — a first version of a non-cv kind)."""

    source: str
    artifact_id: Optional[int] = None
    version_no: Optional[int] = None
    label: str


class ArtifactDiffTargetOut(BaseModel):
    artifact_id: int
    version_no: int
    made_by: str
    model: Optional[str]
    created_at: str
    applied: bool


class ArtifactDiffLineOut(BaseModel):
    op: str
    text: str


class ArtifactDiffOut(BaseModel):
    """``GET …/artifacts/{artifact_id}/diff`` — read-only; the web paints it.
    ``applied`` is the receipt's word, not a button's (VISION decision 26)."""

    kind: str
    base: ArtifactDiffBaseOut
    target: ArtifactDiffTargetOut
    lines: list[ArtifactDiffLineOut]
    added: int
    removed: int
    truncated: bool


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
    # True iff cover_letter_artifact_id is set; False = "Cover letter: not sent".
    cover_letter_sent: bool = False
    cv_version_no: Optional[int] = None
    cover_letter_version_no: Optional[int] = None
    # Read-time facts (spine.receipt_summary_facts), never stored: "N of M fields · S skipped · G guessed".
    # counts_line is "" and fields_total None when no form_filled event was recorded (no invented M).
    counts_line: str = ""
    fields_total: Optional[int] = None
    fields_skipped: int = 0
    guessed_count: int = 0
    form_host: str = ""
    note: str
    # What was actually sent (R8) — stored since 0037, readable since 2026-09-11.
    answers: list[ReceiptAnswerOut] = Field(default_factory=list)
    fields_filled: dict[str, Any] = Field(default_factory=dict)


class ApplicationReceiptExportOut(ApplicationReceiptOut):
    cv_text: Optional[str] = None
    cover_letter_text: Optional[str] = None


class ContactEditOut(BaseModel):
    """One value a contact field has ever held — the base row's own value is
    entry 0, oldest first, so ``[-1]`` is always current."""

    value: str
    recorded_at: str
    recorded_by: str


class OutreachEntryOut(BaseModel):
    """One row of a contact's outreach ledger — a drafted message VERSION
    (``entry="message"``, ``version_no`` set) or a ``sent``/``reply`` mark
    (``version_no`` null — unversioned, a person can be sent to or replied
    to many times)."""

    id: int
    contact_id: int
    entry: str
    channel: str
    text: str
    version_no: Optional[int]
    occurred_at: str
    recorded_at: str
    recorded_by: str
    source_message_id: str


class ContactOutreachOut(BaseModel):
    messages: list[OutreachEntryOut]
    sent: list[OutreachEntryOut]
    replies: list[OutreachEntryOut]
    message_count: int
    last_sent: Optional[OutreachEntryOut]
    replied: bool
    last_reply: Optional[OutreachEntryOut]


class ContactOut(BaseModel):
    """A contact row (``contacts.list_contacts`` / ``add_contact``'s return).
    Same shape everywhere a contact appears — detail, export, the add
    response. ``application_id`` is ``null`` for a cold (job-less) contact
    (owner decision, 2026-09-25). ``edit_history``/``outreach`` carry the
    append-only overlays added the same day."""

    id: int
    application_id: Optional[int]
    name: str
    role: str
    email: str
    linkedin_url: str
    notes: str
    # Owner decision 2026-10-04 — closed set CONTACT_FOUND_VIA; null = unset.
    found_via: Optional[str] = None
    added_by: str
    created_at: str
    edit_history: dict[str, list[ContactEditOut]] = Field(default_factory=dict)
    outreach: ContactOutreachOut


class NextStepOut(BaseModel):
    """What to do next on this application — a state machine over the stored
    record (status, fit, CV versions, receipt, interview date, lesson), never
    a judgement of the job. ``code`` is the closed vocabulary an agent
    branches on; ``label`` is the sentence the web shows."""

    code: str
    label: str


class AskOut(BaseModel):
    """One "Needs you" ask (see ``services/applications/asks.py``)."""

    id: int
    application_id: Optional[int]
    job_title: Optional[str]
    job_company: Optional[str]
    question: str
    context: str
    asked_by: str
    asked_at: str
    answer: Optional[str]
    answered_by: Optional[str]
    answered_at: Optional[str]
    # True = the user answered on Job360; False = the assistant recorded
    # the user's chat answer.
    answered_by_user: bool
    # The user's exact chat words when the assistant relayed them ("" = none).
    user_words: str = ""
    # True = an assistant (not the web) recorded an answer AND the user's words.
    relayed_from_chat: bool = False
    # "you" = web answer or relayed chat answer; "assistant" = it answered alone.
    said_by: Literal["you", "assistant"] = "assistant"
    # When the user confirmed the relayed answer on the web (None = not yet).
    confirmed_at: Optional[str] = None
    withdrawn_at: Optional[str]
    status: str


class ProofOut(BaseModel):
    """How well an application is backed up (S7): ``level`` is the strongest of
    email > text > screenshot_only, else none - the one shape every surface reuses."""

    has_text: bool = False
    # The two halves of ``has_text``: pasted thank-you page text, and a receipt confirmation (ID / reference).
    has_page_text: bool = False
    has_confirmation: bool = False
    has_email: bool = False
    # Earliest email proof: the email's received time, else when it was recorded. None without one.
    email_seen_at: str | None = None
    screenshots: int = 0
    level: Literal["none", "screenshot_only", "text", "email"] = "none"
    # Plain words for what is stored, e.g. "confirmation id + email" or "claimed, unverified".
    proof_label: str = "claimed, unverified"


class ProofMissingOut(BaseModel):
    """An applied job with no proof yet; ``question``/``context`` go straight into ask_user."""

    application_id: int
    company: str
    applied_at: str
    question: str
    context: str


class KitHoldOut(BaseModel):
    """Another assistant session is on this application (weak spot #3, 2026-10-15).
    ``session`` is the other holder's own label (data, never markup; ``""`` = none
    given). ``unsure``: the caller passed no ``session`` and its own assistant read
    the kit under a label - maybe another session of the same assistant (``detail``)."""

    held_by: str
    session: str = ""
    since: str
    until: str
    unsure: bool = False
    detail: Optional[str] = None


class ApplicationDetailOut(BaseModel):
    id: int
    job_id: int
    status: str
    created_at: str
    updated_at: str
    last_event_at: Optional[str]
    job: ApplicationJobOut
    fit: Optional[ApplicationFitOut]
    visa: ApplicationVisaOut
    artifacts: list[ApplicationArtifactOut]
    events: list[ApplicationEventOut]
    interview_at: Optional[str]
    receipts: list[ApplicationReceiptOut]
    contacts: list[ContactOut]
    # "Needs you" - every ask about this job, open first.
    asks: list[AskOut]
    # 2026-09-20 — what to do next, read off the stored state (a state machine
    # over the record, never a judgement of the job). `code` is the closed
    # vocabulary an agent branches on; `label` is the sentence the web shows.
    next_step: NextStepOut
    # Owner decision, 2026-09-25 — the follow-up date (in the caller's own
    # timezone), and whether it has arrived. `null`/`false` when unset.
    follow_up_on: Optional[str]
    follow_up_due: bool
    # S7 - proof of application (email > text > screenshot_only > none).
    proof: ProofOut
    # Gmail check slice 2026-10-15 - the ATS sender domains to search (`[]` = not a
    # known ATS) and the time up to which this job's mail was read (null = never).
    email_senders: list[str] = []
    inbox_checked_at: Optional[str] = None
    # Weak spot #3 wave 2 - another assistant session's live kit hold and the headline stage.
    hold: Optional[KitHoldOut] = None
    stage: str = ""
    # Weak spot #1b (c): derived from `form_filled` + a receipt, never stored. null = no form_filled.
    form_state: Optional[Literal["filled", "submitted"]] = None


class ApplicationSummaryOut(BaseModel):
    id: int
    job_id: int
    job_title: str
    job_company: str
    # 2026-09-24 — the list card's title fallback needs the ad link's host
    # when both job_title and job_company are empty.
    job_url: str = ""
    # Owner decision, 2026-09-25 — the wide row's location + visa line.
    job_location: str = ""
    status: str
    last_event_at: Optional[str]
    events: int
    artifacts: dict[str, int]
    receipts: int
    # Owner decision, 2026-09-25 — the row's "Sent <date>" tag; the latest
    # receipt, batched with the count above so they can never disagree.
    last_receipt_at: Optional[str] = None
    # Owner decision, 2026-09-25 — the row's interview badge, same value
    # `next_step` already reads internally, now exposed directly.
    interview_at: Optional[str] = None
    # Slice 7 — enough for the card's badge without a profile read.
    visa_signal: str = "unknown"
    visa_country: str = ""
    needs_sponsorship: Optional[bool] = None
    # Owner decision, 2026-09-25 — the row's fit bar + one-line verdict.
    # `None`/"" when no fit has been judged yet (rule #29 — stay silent,
    # never default).
    fit_score: Optional[int] = None
    fit_verdict: str = ""
    # Owner decision 2026-10-10 - which side of the USER's own score line the fit
    # score is on; "unknown" when there is no line or no fit yet.
    line_side: Literal["above", "below", "unknown"] = "unknown"
    # Owner decision 2026-10-19 - the user's own job priority of this job's place;
    # None when not chosen or the place is unknown (rule #29).
    priority: Optional[Literal["highest", "high", "medium", "low", "off"]] = None
    # 2026-09-24 — the list card's "Next:" line; same state machine and same
    # fields `get_application`'s `next_step` reads, batched for the page.
    next_step: NextStepOut
    # Owner decision, 2026-09-25 — same pair as ApplicationDetailOut, so the
    # list's "Due" chip and amber tag need no per-row detail fetch.
    follow_up_on: Optional[str] = None
    follow_up_due: bool = False
    # Owner decision 2026-10-04 — same three job facts as the detail's `job`.
    country: Optional[str] = None
    remote: Optional[bool] = None
    found_on: Optional[str] = None
    ad_posted_at: Optional[str] = None
    employer_published_at: Optional[str] = None
    proof: ProofOut
    # Gmail check slice 2026-10-15 - same pair as ApplicationDetailOut.
    email_senders: list[str] = []
    inbox_checked_at: Optional[str] = None
    # Weak spot #3 wave 2 - same pair as ApplicationDetailOut, batched for the page.
    hold: Optional[KitHoldOut] = None
    stage: str = ""
    # Batch queue (owner decision 2026-10-18): when it was queued and its 1-based place in the
    # user's whole open queue; both null when it is not on the queue.
    queued_at: Optional[str] = None
    queue_position: Optional[int] = None
    # Where the job came from (owner ask 2026-10-18): ai_hunt / user_link / user_paste, else
    # user (brought on the web) / ai (an assistant brought it).
    origin: str = ""
    # Bulk-apply Q1: derived problems of a queued row (queued=true only; null otherwise).
    preflight: Optional[dict[str, Any]] = None


class ListApplicationsResponse(BaseModel):
    applications: list[ApplicationSummaryOut]
    total: int
    # Bulk-apply Q2: the whole open queue at a glance (queued=true only; null otherwise).
    queue_summary: Optional[dict[str, Any]] = None


class InboxCheckedIn(BaseModel):
    """The assistant read Gmail for these applications up to ``checked_at`` (now when empty)."""

    application_ids: list[int] = Field(min_length=1)
    checked_at: str = ""


class InboxCheckedOut(BaseModel):
    application_ids: list[int]
    inbox_checked_at: str


class SaveArtifactResponse(BaseModel):
    # Owner decision, 2026-09-25 — a `contact_id` save writes no
    # `application_artifacts` row at all (its identity lives in
    # `contact_outreach` instead); `artifact_id` then carries that ledger
    # row's own id and `event_id` is `null` (a message version is not news,
    # so no timeline event exists to name).
    artifact_id: int
    kind: str
    version_no: int
    chars: int
    made_by: str
    model: Optional[str]
    profile_version: Optional[int]
    created_at: str
    event_id: Optional[int]
    contact_id: Optional[int] = None
    ats_score: Optional[int] = None
    ats_notes: Optional[str] = None
    # Deterministic save-time checks (services/applications/cv_checks.py): claims
    # not found in the profile + PDF problems. Advisory - a save is never refused.
    checks: list[dict[str, str]] = []


class SaveFitLineOut(BaseModel):
    """Where the saved fit lands against the USER's own score line (owner
    decision 2026-10-10): the line (null = not set), the side, the switch that
    side uses (null when the side is unknown) and one plain next-step sentence."""

    score_line: Optional[int] = None
    side: Literal["above", "below", "unknown"]
    switch: Optional[Literal["ask", "auto", "skip"]] = None  # skip: below the line, drop the job
    next: str


class SaveFitResponse(BaseModel):
    application_id: int
    fit: ApplicationFitOut
    line: SaveFitLineOut
    # Slice 7 — present only when the call carried a visa reading.
    visa: Optional[ApplicationVisaOut] = None
    event_id: int


class RecordEventResponse(BaseModel):
    # `null` only for a `contact_id` call whose `source` was already recorded
    # (the outreach ledger row existed — no NEW event was ever appended for
    # it, so there is nothing to name). Every other call — including every
    # call that predates `contact_id` — always gets a real id.
    event_id: Optional[int]
    event_type: str
    occurred_at: str
    recorded_at: str
    recorded_by: str
    status: str
    already_existed: bool
    scheduled_at: Optional[str]
    # Non-fatal notes: a deduped message_id recorded as another type, (owner
    # decision 2026-10-14) a reply/interview/offer with no `applied` event yet,
    # or a repeat `applied` stored as the submission confirmation (weak spot #4).
    warnings: list[str] = Field(default_factory=list)
    # Owner decision, 2026-09-25 — the CURRENT slot value after this call:
    # the new value when this call set/cleared it, the unchanged value when
    # it didn't touch it (omitted `follow_up_on`, or a duplicate source that
    # wrote nothing at all — S corollary of R2).
    follow_up_on: Optional[str]


class RecordApplicationReceiptResponse(BaseModel):
    receipt_id: int
    sent_at: str
    cv_artifact_id: Optional[int]
    cv_version_no: Optional[int]
    cover_letter_artifact_id: Optional[int]
    # False when no letter was named: the receipt says no cover letter was sent.
    cover_letter_sent: bool = False
    cover_letter_version_no: Optional[int] = None
    channel: str
    # What the caller sent, verbatim (trimmed); '' when none.
    channel_given: str = ""
    # Plain facts, e.g. an unknown channel stored as 'other'. Never a block.
    warnings: list[str] = []
    confirmation: str
    url: str
    event_id: int
    # S3 - '' | same_job | same_role | same_company. A flag, never a block.
    possible_duplicate: str = ""
    # True when this request_id was already recorded: the FIRST receipt, nothing written.
    already_existed: bool = False


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
    source: Optional[EventSourceOut]
    scheduled_at: Optional[str]


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
    # Always the user's open asks, whatever `since` says.
    open_asks: list[AskOut]
    # S7 - applied jobs with no proof after PROOF_NO_PROOF_AFTER_DAYS and no ask yet.
    proof_missing: list[ProofMissingOut]


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
    ats_score: Optional[int] = None
    ats_notes: Optional[str] = None


class ExportApplicationOut(BaseModel):
    id: int
    job_id: int
    status: str
    job_title: str
    job_company: str
    created_at: str
    updated_at: str
    last_event_at: Optional[str]
    country: Optional[str] = None
    remote: Optional[bool] = None
    found_on: Optional[str] = None
    ad_posted_at: Optional[str] = None
    employer_published_at: Optional[str] = None
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


class SettingRequestExportOut(BaseModel):
    """One setting-change request in the export: waiting or decided."""

    id: int
    path: str
    value: Any = None
    requested_by: str
    requested_at: str
    expires_at: str
    status: str
    decision: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None


class KitSeenOut(BaseModel):
    """Who saw / approved a document, where (web | chat) and when."""

    by: str
    where: str
    at: str


class KitFileOut(BaseModel):
    """A short-lived download link. The token is in ``url`` and exists nowhere else."""

    url: str
    expires_at: str
    downloads_left: int
    filename: str
    mime: str
    size: int


class KitDocumentOut(BaseModel):
    artifact_id: int
    version: int
    label: str
    text: str
    sha256: str
    chars: int
    # `text` is cut to KIT_DOC_TEXT_MAX_CHARS; sha256 / chars are those of the FULL text.
    text_truncated: Optional[bool] = None
    text_more: Optional[str] = None
    seen: Optional[KitSeenOut] = None
    approved: Optional[KitSeenOut] = None
    # Plain facts found when this version was saved (cv_checks.py); [] = none.
    checks: list[dict[str, str]] = []
    file: KitFileOut


class KitAnswerOut(BaseModel):
    key: str
    value: Any
    source: Literal["memory", "profile", "approved_text"]
    saved_at: Optional[str] = None
    label: Optional[str] = None
    company: Optional[str] = None
    topic: Optional[str] = None
    # Weak spot #6: who stands behind it. confirmed = the user's (web, user_said, approved
    # wording); relayed = an assistant saved the user's chat word, not yet confirmed on the web;
    # inferred = an assistant worked it out; sensitive = equality / sanctions / visa.
    state: Literal["confirmed", "relayed", "inferred", "sensitive"]
    # Set when state is confirmed and the stored row has a time (the reference's confirmedAt).
    confirmed_at: Optional[str] = None
    # Sensitive items only: when the user agreed to REMEMBER it (server-stamped). Still show it
    # to the user and confirm on each form before use.
    remembered_at: Optional[str] = None


class KitAnswersSummaryOut(BaseModel):
    confirmed: int = 0
    relayed: int = 0
    inferred: int = 0
    sensitive: int = 0


class KitAnswersOut(BaseModel):
    contact: list[KitAnswerOut] = Field(default_factory=list)
    right_to_work: list[KitAnswerOut] = Field(default_factory=list)
    logistics: list[KitAnswerOut] = Field(default_factory=list)
    salary: list[KitAnswerOut] = Field(default_factory=list)
    languages: list[KitAnswerOut] = Field(default_factory=list)
    equality: list[KitAnswerOut] = Field(default_factory=list)
    approved_text: list[KitAnswerOut] = Field(default_factory=list)


class KitMissingOut(BaseModel):
    key: str
    why: str


class KitIdOut(BaseModel):
    id: int
    sha256: str
    generated_at: str


class KitJobOut(BaseModel):
    job_id: Optional[int] = None
    title: str
    company: str
    location: str
    country: Optional[str] = None
    remote: Optional[bool] = None
    apply_url: str
    found_on: Optional[str] = None


class KitApplicationOut(BaseModel):
    id: int
    status: str
    follow_up_on: Optional[str] = None
    submit_override: Optional[str] = None


class KitSameJobOut(BaseModel):
    application_id: Optional[int] = None
    status: str
    applied_at: str


class KitDuplicateOut(BaseModel):
    same_job: Optional[KitSameJobOut] = None
    same_company_30d: int
    flag: str
    cleared: Optional[KitSeenOut] = None


class KitKnownAccountOut(BaseModel):
    recorded_by: str
    recorded_at: str


class KitAccountSiteOut(BaseModel):
    host: Optional[str] = None
    likely_needs_account: bool
    known_account: Optional[KitKnownAccountOut] = None


class ApplicationKitOut(BaseModel):
    """Everything an assistant needs to fill ONE application form (S3). Binds to
    THIS application only: its latest CV and cover letter, never another job's."""

    kit: KitIdOut
    job: KitJobOut
    application: KitApplicationOut
    cv: Optional[KitDocumentOut] = None
    cv_none_reason: Optional[str] = None
    cover_letter: Optional[KitDocumentOut] = None
    cover_letter_none_reason: Optional[str] = None
    answers: KitAnswersOut
    answers_summary: KitAnswersSummaryOut
    answers_omitted: Optional[int] = None
    missing: list[KitMissingOut]
    settings: dict[str, Any]
    duplicate: KitDuplicateOut
    hold: Optional[KitHoldOut] = None
    account_site: KitAccountSiteOut
    autofill: Literal["allow", "deny", "unset"]
    # Plain facts to check with the user (e.g. expected_below_current) - never a block.
    warnings: list[dict[str, str]] = []
    instructions: list[str]


class ControlsCvOut(BaseModel):
    artifact_id: int
    version: int
    sha256: str
    seen: Optional[KitSeenOut] = None
    approved: Optional[KitSeenOut] = None


class ControlsAutofillOut(BaseModel):
    mode: Literal["allow", "deny", "unset"]
    by: Optional[str] = None
    where: Optional[str] = None
    at: Optional[str] = None


class ControlsBlockedOut(BaseModel):
    """S6: the assistant's open ``blocked`` record (no later ``unblocked``)."""

    reason: str
    reason_label: str
    step: str
    page_host: str
    detail: str
    by: str
    at: str
    ask_id: Optional[int] = None


class ApplicationControlsOut(BaseModel):
    """The state behind the four human-in-the-loop buttons on the application
    page, each with who / where / when, plus an open blocked record."""

    application_id: int
    cv: Optional[ControlsCvOut] = None
    declined: Optional[KitSeenOut] = None
    autofill: ControlsAutofillOut
    duplicate: KitDuplicateOut
    blocked: Optional[ControlsBlockedOut] = None


class CvSeenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Default: the latest CV. A named id must still BE the latest one.
    artifact_id: Optional[int] = None


class AutofillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["allow", "deny"]


class TypeFirstAnswer(BaseModel):
    question: str
    answer: str


class SubmitCheckResponse(BaseModel):
    """The answer to "may I press submit on this application?" - one gate
    (``settings_rules.may_submit``). ``submit``: go ahead; ``ask``: fill the form,
    stop before submit and ask the user yes for this one; ``stop``: do not submit.
    ``reason`` is a closed code; ``detail`` is one plain sentence."""

    application_id: int
    decision: Literal["submit", "ask", "stop"]
    reason: str
    detail: str
    # Owner decision 2026-10-19: answers the user changed on the web after the form was
    # filled. Type each one into its field (replacing what is there) BEFORE any submit.
    type_first: list[TypeFirstAnswer] = Field(default_factory=list)
    # Bulk-apply Q2: what is left of the user's own limits (null = no cap).
    daily_left: Optional[int] = None
    company_left: Optional[int] = None


class OutreachCheckResponse(BaseModel):
    """The answer to "may I press Send on this outreach email?" - one gate
    (``settings_rules.may_send_outreach``). ``send``: press Send in Gmail, then
    record ``outreach_sent``; ``ask``: save a Gmail draft and ask_user; ``stop``:
    do not send. ``sent_today`` / ``daily_max``: recorded sends today vs the cap."""

    decision: Literal["send", "ask", "stop"]
    reason: str
    detail: str
    sent_today: int
    daily_max: int


class ExportHistoryResponse(BaseModel):
    applications: list[ExportApplicationOut]
    truncated: bool
    bytes: int
    profile_edits: list[ProfileEditExportOut]
    # S3 — true when the edit history was cut at EXPORT_HISTORY_MAX_PROFILE_EDITS
    # (the newest N are kept, rendered oldest-first).
    profile_edits_truncated: bool = False
    # The user's standing instructions to their assistant, as they read now.
    # Empty = none (rule #29); their history is in `profile_edits`.
    assistant_notes: list[str] = []
    # Owner decision 2026-10-08 (S2) - the queue of setting changes the user's
    # assistants asked for (waiting and decided), oldest first.
    assistant_setting_requests: list[SettingRequestExportOut] = []
    next_since: Optional[str] = None
    # Owner decision, 2026-09-25 — cold (job-less) contacts. Paged by their
    # OWN cursor (`unlinked_after_id`/`unlinked_next_after_id`), independent
    # of `since` (bug fix, coordinator review 2026-09-26: the old code
    # inferred "first page" from `since` being empty, which silently dropped
    # cold contacts on a caller's genuine first call if it happened to pass
    # one, and could never page past a byte cutoff at all). Pass
    # `include_unlinked=false` on a follow-up call once you already hold
    # every cold contact. Bounded against the same byte budget as everything
    # else in this export.
    unlinked_contacts: list[ContactOut] = []
    unlinked_contacts_truncated: bool = False
    # Bug fix (coordinator review, 2026-09-26) — the cold-contact cursor,
    # independent of `next_since` (applications). Present only when more
    # cold contacts remain to be paged in.
    unlinked_next_after_id: Optional[int] = None


class AddContactResponse(BaseModel):
    contact: ContactOut
    # Top-level copy of contact.id: the id every outreach call needs.
    contact_id: int
    already_existed: bool
    event_id: Optional[int]


class AddPersonRequest(AddContactRequest):
    """``POST /api/contacts`` — a job-less (cold) contact when
    ``application_id`` is omitted, a linked one otherwise (owner decision,
    2026-09-25). Kept as a SEPARATE model from ``AddContactRequest`` (used by
    the per-application route, where the id comes off the URL) so that route
    keeps refusing an ``application_id`` in its body (S3 — the id there is
    never caller-supplied)."""

    application_id: Optional[int] = None


class UpdateContactRequest(BaseModel):
    """``PATCH /api/contacts/{contact_id}`` — every field optional; only the
    ones given are appended to ``contact_edits`` (owner decision, 2026-09-25:
    contacts ARE editable, old values kept). No length caps declared here —
    same reasoning as ``AddContactRequest``: ``contacts.update_contact``
    checks live ``settings`` values at call time."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    notes: Optional[str] = None
    # Owner decision 2026-10-04 — closed set CONTACT_FOUND_VIA; "" clears it.
    found_via: Optional[str] = None


class RecordOutreachRequest(BaseModel):
    """``POST /api/contacts/{contact_id}/outreach`` — the one shared door
    ``save_artifact``/``record_event`` (with a ``contact_id``) also call
    (M5 parity)."""

    model_config = ConfigDict(extra="forbid")

    entry: str
    channel: str
    text: str = ""
    occurred_at: Optional[str] = None
    source: Optional[EventSource] = None
    # Owner decision, 2026-09-25 — only meaningful on entry="sent", and only
    # for a contact linked to a job; `contacts.record_outreach` enforces both.
    follow_up_on: Optional[str] = None
    # A caller going through /applications/{id}/events can only ever touch a
    # contact linked to THAT SAME application — set by the wrapper below,
    # never by the request body itself.
    application_id: Optional[int] = None


class RecordOutreachResponse(BaseModel):
    outreach: OutreachEntryOut
    already_existed: bool
    event_id: Optional[int]
    follow_up_on: Optional[str]


class PersonJobOut(BaseModel):
    application_id: int
    job_title: str
    job_company: str


class PersonOut(BaseModel):
    """``list_people``'s no-``contact_id`` shape — one row per PERSON
    (grouped by lower(email) else linkedin_url), aggregated across every
    underlying ``application_contacts`` row that shares that identity."""

    contact_ids: list[int]
    name: str
    role: str
    email: str
    linkedin_url: str
    notes: str
    found_via: Optional[str] = None
    jobs: list[PersonJobOut]
    message_count: int
    last_sent: Optional[OutreachEntryOut]
    replied: bool
    last_reply: Optional[OutreachEntryOut]


class PersonFullOut(ContactOut):
    """``list_people(contact_id=...)``'s shape — the one row's own full
    record (never merged with another row that happens to share an email)."""

    jobs: list[PersonJobOut]


class ListPeopleResponse(BaseModel):
    people: Optional[list[PersonOut]] = None
    person: Optional[PersonFullOut] = None
    # Bug fix (coordinator review, 2026-09-26) — true when the no-`contact_id`,
    # no-`email` listing was cut at LIST_PEOPLE_MAX (newest kept). Always
    # `false` for the `contact_id`/`email` reads (never paged).
    truncated: bool = False


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
    # True when fewer than STATS_SMALL_SAMPLE_N applications: a hint, not a finding.
    small_sample: bool = False
    # Owner decision 2026-10-14 — replied/interview/offer above count only
    # applications that ALSO have an `applied` event (so no rate exceeds 1);
    # this is how many reached a later stage with no `applied` recorded.
    replies_without_applied: int = 0
    # Applications marked with a `simulation` event: dropped from every count.
    simulations_excluded: int = 0


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
    # True when fewer than STATS_SMALL_SAMPLE_N applications: a hint, not a finding.
    small_sample: bool = False


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
    # True when fewer than STATS_SMALL_SAMPLE_N applications: a hint, not a finding.
    small_sample: bool = False


class StatsKeyedGroupOut(BaseModel):
    """Owner decision 2026-10-04 — ``by_country`` / ``by_job_source`` /
    ``by_channel``: same counts and rates as ``by_role``. ``key`` is the
    canonical value (an ISO alpha-2 code or ``"remote"``; a closed-set
    member); ``null`` = unset, its ``label`` is "Not set"."""

    key: Optional[str]
    label: str
    brought: int
    applied: int
    replied: int
    interview: int
    offer: int
    rejected: int
    reply_rate: Optional[float]
    interview_rate: Optional[float]
    offer_rate: Optional[float]
    # True when fewer than STATS_SMALL_SAMPLE_N applications: a hint, not a finding.
    small_sample: bool = False


class StatsContactFoundViaGroupOut(BaseModel):
    """Owner decision 2026-10-04 — per current contact ``found_via``: how
    many contacts, how many had an outreach marked sent / a reply recorded,
    and ``reply_rate`` = contacts with both / contacts with a sent mark
    (``null`` when nothing was sent)."""

    key: Optional[str]
    label: str
    contacts: int
    outreach_sent: int
    outreach_replied: int
    reply_rate: Optional[float]


class StatsScoreLineOut(BaseModel):
    """Owner decision 2026-10-10 - counts of the user's applications above /
    below THEIR OWN score line (``line`` null = not set) and how many have no
    fit score to place (or there is no line). Counts only."""

    line: Optional[int] = None
    above: int
    below: int
    unscored: int


class StatsFunnelOut(BaseModel):
    n: int
    ever_applied: int
    ever_replied: int
    ever_interview: int
    ever_offer: int
    ever_hired: int
    reply_rate: Optional[float] = None
    interview_rate: Optional[float] = None
    offer_rate: Optional[float] = None
    small_sample: bool
    without_applied: int
    note: str
    truncated: Optional[bool] = None
    truncated_hint: Optional[str] = None


class StatsHopOut(BaseModel):
    key: str
    from_: str = Field(alias="from")
    to: str
    n: int
    median_days: Optional[float] = None
    p75_days: Optional[float] = None
    insufficient_data: bool
    same_day_excluded: int
    still_waiting: int

    model_config = ConfigDict(populate_by_name=True)


class StatsVelocityOut(BaseModel):
    hops: list[StatsHopOut]
    min_n: int
    note: str
    truncated: Optional[bool] = None
    truncated_hint: Optional[str] = None


class StatsBandOut(BaseModel):
    band: str
    min: Optional[int] = None
    max: Optional[int] = None
    n: int
    interviews: int
    offers: int
    interview_rate: Optional[float] = None
    offer_rate: Optional[float] = None


class StatsVerdictOut(BaseModel):
    kind: str
    text: str


class StatsFeedbackQuoteOut(BaseModel):
    application_id: int
    company: str
    fit_score: Optional[int] = None
    verbatim_feedback: str


class StatsCalibrationOut(BaseModel):
    bands: list[StatsBandOut]
    min_band_n: int
    resolved: int
    in_flight: int
    unscored: int
    ghosted_counted_negative: int
    verdict: StatsVerdictOut
    feedback: list[StatsFeedbackQuoteOut]
    note: str
    truncated: Optional[bool] = None
    truncated_hint: Optional[str] = None


class StatsCompanyFactOut(BaseModel):
    application_id: int
    kind: str  # "silent" | "answered"
    stale: bool
    applied_on: Optional[str] = None
    silent_days: Optional[int] = None
    checkins_sent: Optional[int] = None
    confidence: Optional[str] = None
    outcome: Optional[str] = None
    on: Optional[str] = None
    note: Optional[str] = None


class StatsCompanyCardOut(BaseModel):
    key: str
    company: str
    label: str
    applications: int
    facts: list[StatsCompanyFactOut]
    facts_truncated: bool
    truncated_hint: Optional[str] = None


class StatsCompanyOut(BaseModel):
    cards: list[StatsCompanyCardOut]
    silence_window_days: int
    stale_after_days: int
    truncated: bool
    applications_truncated: bool = False
    since_ignored: bool
    explanation: str
    truncated_hint: Optional[str] = None


class StatsLessonSideOut(BaseModel):
    n: int
    replied: int
    interview: int
    reply_rate: Optional[float] = None
    interview_rate: Optional[float] = None
    small_sample: bool
    still_waiting: int


class StatsLessonEffectRowOut(BaseModel):
    event_id: int
    application_id: int
    lesson: str
    agreed_on: Optional[str] = None
    before: StatsLessonSideOut
    after: StatsLessonSideOut
    comparable: bool


class StatsLessonEffectOut(BaseModel):
    lessons: list[StatsLessonEffectRowOut]
    note: str
    truncated: Optional[bool] = None
    truncated_hint: Optional[str] = None


class StatsFollowupsOut(BaseModel):
    available: bool
    checkins_sent: Optional[int] = None
    feedback_asked: Optional[int] = None
    feedback_followups_sent: Optional[int] = None
    applications_with_checkin: Optional[int] = None
    applied_without_checkin: Optional[int] = None
    rejections_without_ask: Optional[int] = None
    skipped: Optional[dict[str, int]] = None
    checkin_days: Optional[int] = None
    truncated: Optional[bool] = None
    truncated_hint: Optional[str] = None


class StatsPatternRowOut(BaseModel):
    """One group of the ``patterns`` view (counts of the user's own sent applications)."""

    key: Optional[str] = None
    label: str
    submitted: int
    positive: int
    negative: int
    awaiting: int
    no_answer: int
    decided: int
    conversion_rate: Optional[float] = None
    decided_rate: Optional[float] = None
    small_sample: bool


class StatsPatternSignalOut(BaseModel):
    dimension: str
    kind: str
    key: str
    label: str
    submitted: int
    decided: int
    positive: int
    decided_rate: Optional[float] = None
    points_vs_overall: Optional[int] = None
    clear_lead: bool
    text: str


class StatsReasonOut(BaseModel):
    reason: str
    count: int
    application_ids: list[int]


class StatsPatternsOut(BaseModel):
    """``stats(view="patterns")``. ``available`` false = below the gate (``text`` says
    how far); every other key is then null."""

    available: bool
    submitted: int
    threshold: int
    text: Optional[str] = None
    withdrawn_excluded: Optional[int] = None
    overall: Optional[StatsPatternRowOut] = None
    by_archetype: Optional[list[StatsPatternRowOut]] = None
    by_remote_policy: Optional[list[StatsPatternRowOut]] = None
    by_team_size: Optional[list[StatsPatternRowOut]] = None
    by_via: Optional[list[StatsPatternRowOut]] = None
    signals: Optional[list[StatsPatternSignalOut]] = None
    rejection_reasons: Optional[list[StatsReasonOut]] = None
    reason_base: Optional[int] = None
    groups_truncated: Optional[bool] = None
    truncated_hint: Optional[str] = None
    note: Optional[str] = None


class StatsSkillGapOut(BaseModel):
    key: str
    gap: str
    applications: int
    low_fit: int
    hard: int
    soft: int
    unmarked: int
    weighted: float
    tier: str
    application_ids: list[int]


class StatsSkillGapsOut(BaseModel):
    """``stats(view="skill_gaps")``. ``available`` false = fewer than the threshold
    of scored fits; every other key is then null."""

    available: bool
    scored: int
    threshold: Optional[int] = None
    text: Optional[str] = None
    fits_read: Optional[int] = None
    total_low_fit: Optional[int] = None
    low_fit_score: Optional[int] = None
    gaps: Optional[list[StatsSkillGapOut]] = None
    excluded_as_known: Optional[int] = None
    truncated: Optional[bool] = None
    truncated_hint: Optional[str] = None
    note: Optional[str] = None


class StatsSiteOut(BaseModel):
    host: str
    n: int
    forms_filled: int
    applied: int
    blocked: dict[str, int]
    blocked_total: int


class StatsSitesOut(BaseModel):
    """``stats(view="sites")``: counts per form host. Facts only, never a ranking."""

    hosts: int
    sites: list[StatsSiteOut]
    truncated: bool
    applications_without_host: int
    note: str


class StatsResponse(BaseModel):
    since: Optional[str]
    overall: StatsOverallOut
    by_cv_version: list[StatsCvVersionGroupOut]
    by_role: list[StatsRoleGroupOut]
    by_country: list[StatsKeyedGroupOut]
    by_job_source: list[StatsKeyedGroupOut]
    by_channel: list[StatsKeyedGroupOut]
    by_contact_found_via: list[StatsContactFoundViaGroupOut]
    score_line: StatsScoreLineOut
    groups_truncated: bool
    # S6 — true when only the newest STATS_MAX_APPLICATIONS applications were
    # counted; the numbers describe that window, not the whole history.
    applications_truncated: bool
    # `stats(view=...)` adds ONE block (or all of them); the rest stay null.
    funnel: Optional[StatsFunnelOut] = None
    velocity: Optional[StatsVelocityOut] = None
    calibration: Optional[StatsCalibrationOut] = None
    company: Optional[StatsCompanyOut] = None
    lesson_effect: Optional[StatsLessonEffectOut] = None
    followups: Optional[StatsFollowupsOut] = None
    # Feedback loop slice 4 (interview side): each is null unless its stats view was asked for.
    digest: Optional[dict[str, Any]] = None
    friction: Optional[dict[str, Any]] = None
    assessments: Optional[dict[str, Any]] = None
    # Slice 5 - one block per view; null unless the call asked for it.
    patterns: Optional[StatsPatternsOut] = None
    skill_gaps: Optional[StatsSkillGapsOut] = None
    sites: Optional[StatsSitesOut] = None
    computed_at: str


# ── Routes — order matters: /export before /{application_id} ────────────────


@router.get("/applications/export", response_model=ExportHistoryResponse, dependencies=AUTH_FIRST)
async def export_history(
    since: Optional[str] = Query(None),
    include_text: bool = Query(False),
    include_unlinked: bool = Query(True),
    unlinked_after_id: Optional[int] = Query(None),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await spine.export_history(
            db, user.id, since=since, include_text=include_text,
            include_unlinked=include_unlinked, unlinked_after_id=unlinked_after_id,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover — _raise always raises


@router.get("/applications/lessons", response_model=LessonsResponse)
async def list_lessons(
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Slice 9 (#516) — every "flag for next time" lesson across the caller's
    applications, newest first (spec R1 door 1). Read-only; a lesson is
    written through ``record_event`` (type ``lesson``) like any other event.
    ``get_profile`` carries the last PROFILE_LESSONS_MAX of the same list."""
    if limit > settings.LESSONS_PAGE_MAX:
        raise HTTPException(
            status_code=422, detail=f"limit must be at most LESSONS_PAGE_MAX ({settings.LESSONS_PAGE_MAX})"
        )
    rows, total = lessons_service.list_lessons(user.id, limit=limit, offset=offset)
    return {"lessons": rows, "total": total}


@router.get("/applications/stats", response_model=StatsResponse, dependencies=AUTH_FIRST)
async def stats(
    since: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await stats_service.compute_stats(db, user.id, since, view=view, company=company)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.get("/applications", response_model=ListApplicationsResponse, dependencies=AUTH_FIRST)
async def list_applications(
    status: Optional[str] = Query(None),
    updated_since: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    # Owner decision, 2026-09-25 — "what's due" / "gone quiet", the two doors
    # the daily-check prompt closes its run with.
    due: bool = Query(False),
    quiet_days: Optional[int] = Query(None, ge=1, le=settings.APPLICATION_QUIET_DAYS_MAX),
    # Weak spot #3 wave 2 - only applications another assistant session holds right now.
    in_flight: bool = Query(False),
    # Batch queue (2026-10-18) - only the open queue, first in first out (rows carry `queue_position`).
    queued: bool = Query(False),
    # Owner decision 2026-10-19 - the user's own job priority of the job's place.
    priority: Optional[str] = Query(None, max_length=20),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await spine.list_applications(
            db, user.id, status=status, updated_since=updated_since, limit=limit, offset=offset,
            due=due, quiet_days=quiet_days, in_flight=in_flight, queued=queued, priority=priority,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.post("/applications/inbox-checked", response_model=InboxCheckedOut, dependencies=AUTH_FIRST)
async def mark_inbox_checked(
    body: InboxCheckedIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Gmail check slice (2026-10-15): move each application's ``inbox_checked_at``
    cursor forward. The assistant read the mail; Job360 only remembers how far
    (VISION rule 5). All or nothing; never rewinds a newer cursor."""
    from src.services.applications.inbox import mark_checked  # noqa: PLC0415

    try:
        return await mark_checked(db, user.id, body.application_ids, body.checked_at or None)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.get("/applications/job/{job_id}", response_model=JobResponse, dependencies=AUTH_FIRST)
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
    mine = await spine.get_application_by_job(db, user.id, job_id)
    if mine is None:
        raise HTTPException(status_code=404, detail="Job not found")
    app_row = await spine.get_owned_application(db, user.id, int(mine["id"]))
    row = await db.get_job_by_id(job_id)
    if row is None or app_row is None:  # pragma: no cover — an application always has its job row
        raise HTTPException(status_code=404, detail="Job not found")
    # P0 2026-10-14 — the text, link and location are THIS user's snapshot,
    # never the shared catalog row (which holds whoever pasted the key first).
    return application_job_response(app_row, dict(row))


@router.get("/applications/{application_id}", response_model=ApplicationDetailOut, dependencies=AUTH_FIRST)
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
    "/applications/{application_id}/artifacts/{artifact_id}", response_model=ApplicationArtifactRowOut,
    dependencies=AUTH_FIRST,
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


@router.post("/applications/{application_id}/artifacts/{artifact_id}/download", dependencies=AUTH_FIRST)
async def download_application_artifact(
    application_id: int,
    artifact_id: int,
    fmt: str = Query("pdf", description="`pdf` or `docx`."),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> Response:
    """Render ONE stored CV / cover-letter version as PDF or DOCX (web-only —
    the agent already holds the text). POST like the tailor download so it is
    Origin-checked; it writes nothing. Foreign application/artifact -> 404."""
    import asyncio

    from src.services.tailoring.cv_render import render_document_pdf
    from src.services.tailoring.docx import render_docx

    if fmt not in ("pdf", "docx"):
        raise HTTPException(status_code=400, detail="format must be 'pdf' or 'docx'")
    row = await spine.get_artifact(db, user.id, application_id, artifact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    kind = row["kind"]
    if kind not in ("cv", "cover_letter"):
        raise HTTPException(status_code=400, detail="only a cv or cover_letter can be downloaded")
    app_row = await spine.get_owned_application(db, user.id, application_id) or {}
    text = row.get("text") or ""
    title = "Curriculum Vitae" if kind == "cv" else "Cover Letter"
    # Rendering is sync + CPU-bound: keep it off the event loop (as tailor.py).
    if fmt == "docx":
        content = await asyncio.to_thread(render_docx, text, title=title)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        content = await render_document_pdf(user.id, kind, text)
        media_type = "application/pdf"
    stem = await kit_service.download_stem(
        user.id, str(app_row.get("job_company") or ""), kind, int(row["version_no"])
    )
    # S3 - the user's own download is "the user saw this CV" (one mark per version).
    # Only a signed-in web session counts; a bearer call writes nothing, and a stale
    # version (not the latest) is simply not marked - the download still works.
    if actor_for(user) == "web":
        try:
            await kit_service.record_decision(
                db, user, application_id, "cv_seen",
                {"artifact_id": row["id"], "sha256": kit_service.sha256_text(text), "where": "web"},
                once_per_version=True,
            )
        except SpineError:
            pass
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'},
    )


@router.get(
    "/applications/{application_id}/artifacts/{artifact_id}/diff", response_model=ArtifactDiffOut,
    dependencies=AUTH_FIRST,
)
async def diff_application_artifact(
    application_id: int,
    artifact_id: int,
    against: str = Query(
        "",
        description="`profile` (the stored CV text) or another artifact id of the same kind. "
        "Default: `profile` for a cv, the previous version otherwise.",
    ),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Slice 8 (#515) — original vs tailored, read-only (spec R1). No Keep:
    the version the receipt names is the applied one (decision 26)."""
    target = await spine.get_artifact(db, user.id, application_id, artifact_id)
    if target is None:
        raise HTTPException(status_code=404, detail="artifact not found")

    base: dict[str, Any]
    base_text: str
    if against == "profile" or (against == "" and target["kind"] == "cv"):
        base = {"source": "profile", "label": "Original CV"}
        base_text = diff_service.load_profile_cv_text(user.id)
    elif against == "":
        prev = await diff_service.previous_version(db, application_id, target["kind"], target["version_no"])
        if prev is None:
            base, base_text = {"source": "none", "label": "Nothing before this"}, ""
        else:
            base = {
                "source": "artifact", "artifact_id": prev["id"], "version_no": prev["version_no"],
                "label": f"v{prev['version_no']}",
            }
            base_text = prev["text"] or ""
    else:
        # `isascii()` too: `str.isdigit()` accepts Unicode digits such as "²"
        # that `int()` rejects — without it that request is a 500, not a 422.
        # The length bound keeps a 100-digit "id" away from psycopg.
        if not against.isascii() or not against.isdigit() or len(against) > 18:
            raise HTTPException(status_code=422, detail="against must be 'profile' or an artifact id")
        other = await spine.get_artifact(db, user.id, application_id, int(against))
        if other is None or other["kind"] != target["kind"] or other["id"] == target["id"]:
            raise HTTPException(status_code=404, detail="artifact not found")
        base = {
            "source": "artifact", "artifact_id": other["id"], "version_no": other["version_no"],
            "label": f"v{other['version_no']}",
        }
        base_text = other["text"] or ""

    result = diff_service.diff_lines(base_text, target["text"] or "")
    return {
        "kind": target["kind"],
        "base": base,
        "target": {
            "artifact_id": target["id"], "version_no": target["version_no"], "made_by": target["made_by"],
            "model": target.get("model"), "created_at": target["created_at"],
            "applied": await diff_service.is_applied(db, application_id, target["id"]),
        },
        **result,
    }


@router.get("/applications/{application_id}/alignment", response_model=AlignmentOut, dependencies=AUTH_FIRST)
async def application_alignment(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """The fit picture (2026-09-20). Read-only; web only — an agent already
    holds the profile and the ad (rule M2), so there is no MCP tool."""
    from src.services.applications import alignment  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415 — rule #16, heavy stack

    app_row = await spine.get_owned_application(db, user.id, application_id)
    if app_row is None:
        raise HTTPException(status_code=404, detail="application not found")
    fit = fit_view(app_row)
    ad_text = app_row.get("job_description_snapshot") or ""
    profile = load_profile(user.id)
    skills = alignment.candidate_skills(profile) if profile is not None else []
    found, missing = alignment.skills_in_text(skills, ad_text)
    return {
        "fit": fit,
        "skills_in_ad": found,
        "skills_not_in_ad": missing,
        # THE one skill count (skill_tiering.profile_skills) — the same number
        # the profile page and get_profile show. A skill too short to search
        # safely (a lone "R") still counts as one of the candidate's skills.
        "skills_total": len(skills),
        "ad_chars": len(ad_text),
    }


@router.post(
    "/applications/{application_id}/contacts",
    response_model=AddContactResponse,
    # The runtime status is set on `response` below (201 create / 200 replay),
    # which FastAPI cannot infer — so the 201 is declared here by hand.
    # Without it the schema promised 200 ONLY, and every generated client
    # (frontend `api-types.ts` included) treated a successful create as an
    # undocumented response.
    responses={201: {"model": AddContactResponse, "description": "Contact created"}},
    dependencies=AUTH_FIRST,
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
            notes=body.notes, occurred_at=body.occurred_at, found_via=body.found_via,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover
    response.status_code = 200 if result["already_existed"] else 201
    return {**result, "contact_id": result["contact"]["id"]}


@router.post(
    "/contacts",
    response_model=AddContactResponse,
    responses={201: {"model": AddContactResponse, "description": "Contact created"}},
    dependencies=AUTH_FIRST,
)
async def add_person(
    body: AddPersonRequest,
    response: Response,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Owner decision, 2026-09-25 — a person with no job yet (cold
    networking), or a linked one when ``application_id`` is given. Same
    idempotency/cap/rate-limit rules as the per-application route, just
    scoped to the USER instead of an application when there is none."""
    try:
        result = await contacts_service.add_contact(
            db, user.id, body.application_id, actor_for(user),
            name=body.name, role=body.role, email=body.email, linkedin_url=body.linkedin_url,
            notes=body.notes, occurred_at=body.occurred_at, found_via=body.found_via,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover
    response.status_code = 200 if result["already_existed"] else 201
    return {**result, "contact_id": result["contact"]["id"]}


@router.patch("/contacts/{contact_id}", response_model=ContactOut, dependencies=AUTH_FIRST)
async def update_contact(
    contact_id: int,
    body: UpdateContactRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Owner decision, 2026-09-25 — contacts ARE editable, old values kept:
    every provided field appends a ``contact_edits`` row (S12 — the base row
    is never touched); the response is the CURRENT view (base + latest edit
    per field) plus the full history. A foreign/unknown id reads 404 (S2)."""
    try:
        return await contacts_service.update_contact(
            db, user.id, contact_id, actor_for(user),
            name=body.name, role=body.role, email=body.email, linkedin_url=body.linkedin_url,
            notes=body.notes, found_via=body.found_via,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.post(
    "/contacts/{contact_id}/outreach",
    response_model=RecordOutreachResponse,
    responses={201: {"model": RecordOutreachResponse, "description": "Outreach recorded"}},
    dependencies=AUTH_FIRST,
)
async def record_outreach(
    contact_id: int,
    body: RecordOutreachRequest,
    response: Response,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """The one shared door for a contact's outreach ledger — a drafted
    message VERSION, or a ``sent``/``reply`` mark. ``save_artifact``
    (``kind="outreach"`` + ``contact_id``) and ``record_event``
    (``outreach_sent``/``outreach_replied`` + ``contact_id``) both delegate to
    the SAME service function this route calls (M5 parity) — this route is
    the one door that works for a COLD contact too, since it carries no
    ``application_id`` in its URL."""
    try:
        source = spine.validate_source(body.source.model_dump() if body.source else None)
        result = await contacts_service.record_outreach(
            db, user.id, contact_id, actor_for(user),
            entry=body.entry, channel=body.channel, text=body.text, occurred_at=body.occurred_at,
            source_message_id=source["message_id"] if source else "",
            follow_up_on=body.follow_up_on, application_id_hint=body.application_id,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover
    response.status_code = 200 if result["already_existed"] else 201
    return result


@router.get(
    "/contacts/{contact_id}/outreach-check", response_model=OutreachCheckResponse, dependencies=AUTH_FIRST
)
async def outreach_check(
    contact_id: int,
    message_id: int = Query(...),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """THE one gate before pressing Send on an outreach email (feature 10).

    Reads the user's settings, this contact's facts and the saved message
    version ``message_id`` (the ``contact_outreach`` row ``save_artifact
    kind="outreach"`` created), plus today's recorded sends, then answers
    through ``settings_rules.may_send_outreach`` - the same function for this
    route and the MCP ``check_outreach`` tool. Read-only: it records nothing
    except one audit line. 404 for a contact that is not the caller's (rule #12)."""
    try:
        contact = await contacts_service.get_owned_contact(db, user.id, contact_id)
        if contact is None:
            raise SpineError(404, "contact not found")
        now = datetime.now(timezone.utc)
        profile = load_profile(user.id) or UserProfile()
        cfg = settings_rules.effective(profile.assistant_settings)
        facts = await contacts_service.outreach_gate_facts(db, user.id, contact, message_id)
        counts = await settings_rules.load_outreach_counts(db, user.id, now, facts.company or None)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover
    verdict = settings_rules.may_send_outreach(
        cfg, facts, counts, now, inbox_mode=profile.preferences.daily_check or ""
    )
    # Never the email address, the message text or the company name: ids and closed codes only.
    get_audit_logger().info(
        "outreach_check",
        extra={
            "event": "outreach_check", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "contact_id": contact_id, "message_id": message_id,
            "decision": verdict.decision, "reason": verdict.reason, "result": "ok",
        },
    )
    return {
        "decision": verdict.decision, "reason": verdict.reason, "detail": verdict.detail,
        "sent_today": counts.sent_today, "daily_max": settings.OUTREACH_AUTO_SENDS_MAX_PER_DAY,
    }


@router.get(
    "/applications/{application_id}/auto-email-check", response_model=AutoEmailCheckResponse,
    dependencies=AUTH_FIRST,
)
async def auto_email_check(
    application_id: int,
    kind: str = Query("", max_length=40),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """THE one gate before an automatic employer email (feedback loop slice 2).

    ``kind``: checkin | feedback_request | feedback_followup. Answers ``send`` / ``ask`` / ``stop`` with a
    closed ``reason`` through ``auto_emails.decide`` (the same function for this route and the MCP
    ``check_auto_email`` tool). ``send`` only means the gate is open. Read-only apart from one audit line.
    404 for an application that is not the caller's (rule #12)."""
    now = datetime.now(timezone.utc)
    facts = await auto_emails_service.load_facts(db, user.id, application_id, now)
    if facts is None:
        raise HTTPException(status_code=404, detail="application not found")
    profile = load_profile(user.id) or UserProfile()
    cfg = settings_rules.effective(profile.assistant_settings)
    verdict = auto_emails_service.decide(kind, facts, cfg, now)
    since = auto_emails_service.due_since(kind, facts)
    # Ids and closed codes only: never an address, a company or any text.
    get_audit_logger().info(
        "auto_email_check",
        extra={
            "event": "auto_email_check", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "application_id": safe_log_value(application_id),
            "kind": safe_log_value(kind, max_len=40), "decision": safe_log_value(verdict.decision, max_len=20),
            "reason": safe_log_value(verdict.reason, max_len=60), "result": "ok",
        },
    )
    return {
        "decision": verdict.decision, "reason": verdict.reason, "detail": verdict.detail,
        "sent_today": facts.sent_today, "daily_max": settings.AUTO_EMAILS_MAX_PER_DAY,
        "due_since": since.isoformat() if since else "",
    }


@router.get("/people", response_model=ListPeopleResponse, dependencies=AUTH_FIRST)
async def list_people(
    contact_id: Optional[int] = Query(None),
    email: Optional[str] = Query(None),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """No ``contact_id`` — every person the user has added, grouped by
    lower(email) else linkedin_url (the same recruiter on two jobs is one
    person here). With ``contact_id`` — that ONE row's own full record
    (message versions, sent/reply marks, detail-edit history), never merged."""
    try:
        return await contacts_service.list_people(db, user.id, contact_id=contact_id, email=email)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.post(
    "/applications/{application_id}/artifacts", status_code=201, response_model=SaveArtifactResponse,
    dependencies=AUTH_FIRST,
)
async def save_artifact(
    application_id: int,
    body: SaveArtifactRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        if body.contact_id is None and body.kind == "outreach":
            raise _outreach_needs_contact("save_artifact", user.id, application_id=application_id)
        if body.contact_id is not None:
            # Owner decision, 2026-09-25 — a message DRAFTED for a person:
            # writes a numbered version into that contact's own outreach
            # ledger (contacts.record_outreach), never an application_artifacts
            # row. The contact must be linked to THIS application (a cold
            # contact's message goes through the MCP tool's other branch,
            # which calls POST /api/contacts/{contact_id}/outreach directly).
            if body.kind != "outreach":
                raise SpineError(422, "kind must be 'outreach' when contact_id is given")
            # An outreach message is never an ATS document — refuse rather
            # than silently drop the opinion.
            spine.validate_ats(body.kind, body.ats_score, body.ats_notes)
            if not body.channel:
                raise SpineError(422, "channel is required when contact_id is given")
            result = await contacts_service.record_outreach(
                db, user.id, body.contact_id, actor_for(user),
                entry="message", channel=body.channel, text=body.text,
                application_id_hint=application_id,
            )
            outreach = result["outreach"]
            return {
                "artifact_id": outreach["id"], "kind": "outreach", "version_no": outreach["version_no"] or 0,
                "chars": len(outreach["text"]), "made_by": outreach["recorded_by"], "model": body.model,
                "profile_version": None, "created_at": outreach["recorded_at"], "event_id": result["event_id"],
                "contact_id": body.contact_id,
            }
        return await spine.save_artifact(
            db, user_id=user.id, application_id=application_id, kind=body.kind, text=body.text,
            made_by=actor_for(user), label=body.label, model=body.model,
            ats_score=body.ats_score, ats_notes=body.ats_notes,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


def _items_out(items: Optional[list[Union[str, FitItemIn]]]) -> Optional[list[Any]]:
    """Request items -> the plain str / dict shapes ``fit_record.clean_items`` reads."""
    if items is None:
        return None
    return [i.model_dump() if isinstance(i, FitItemIn) else i for i in items]


@router.put("/applications/{application_id}/fit", response_model=SaveFitResponse, dependencies=AUTH_FIRST)
async def save_fit(
    application_id: int,
    body: SaveFitRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        reasoning = body.clamp_reasoning()
        saved = await spine.save_fit(
            db, user_id=user.id, application_id=application_id, recorded_by=actor_for(user),
            score=body.score, verdict=body.verdict, gaps=_items_out(body.gaps), reasoning=reasoning,
            visa_signal=body.visa_signal, visa_detail=body.visa_detail, visa_country=body.visa_country,
            axes=[a.model_dump() for a in body.axes] if body.axes is not None else None,
            score_source=body.score_source, jd_requirements=_items_out(body.jd_requirements),
            archetype=body.archetype, remote_policy=body.remote_policy, team_size=body.team_size, via=body.via,
        )
        cfg = settings_rules.effective((load_profile(user.id) or UserProfile()).assistant_settings)
        return {**saved, "line": settings_rules.line_block(body.score, cfg)}
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


class SetVisaRequest(BaseModel):
    """Slice 7 — the human door (a person at the browser has no agent to
    read the ad for them). Same three fields, same rules as bring_job /
    save_fit; no MCP tool calls this (an agent uses save_fit)."""

    model_config = ConfigDict(extra="forbid")

    visa_signal: str = Field(..., max_length=32)
    visa_detail: Optional[str] = Field(None, max_length=4_000)
    visa_country: Optional[str] = Field(None, max_length=8)


class SetVisaResponse(BaseModel):
    application_id: int
    visa: ApplicationVisaOut


@router.put("/applications/{application_id}/visa", response_model=SetVisaResponse, dependencies=AUTH_FIRST)
async def set_visa(
    application_id: int,
    body: SetVisaRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await visa_service.set_visa_signal(
            db, user_id=user.id, application_id=application_id, recorded_by=actor_for(user),
            signal=body.visa_signal, detail=body.visa_detail, country=body.visa_country,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


class UpdateJobFactsRequest(BaseModel):
    """Owner decision 2026-10-04 — set or fix the job's facts after
    bring_job: ISO alpha-2 ``country``, ``remote``, ``found_on`` (closed set
    JOB_FOUND_ON). Only the keys SENT change; an explicit ``null`` (or "")
    clears one (rule #29: unset is null, never a default). Validated by
    ``job_facts.validate_job_facts`` — the same rules bring_job uses."""

    model_config = ConfigDict(extra="forbid")

    country: Optional[str] = Field(None, max_length=8)
    remote: Optional[bool] = Field(None, strict=True)
    found_on: Optional[str] = Field(None, max_length=40)
    # Ad dates, YYYY-MM-DD, not in the future ("" / null clears one).
    posted_at: Optional[str] = Field(None, max_length=10)
    employer_published_at: Optional[str] = Field(None, max_length=10)
    # The employer's working link when the brought one is dead. Replaces ONLY
    # this user's applications.job_url (never the shared jobs row); "" is 422.
    apply_url: Optional[str] = Field(None, max_length=2048)


class JobFactsOut(BaseModel):
    application_id: int
    country: Optional[str]
    remote: Optional[bool]
    found_on: Optional[str]
    ad_posted_at: Optional[str] = None
    employer_published_at: Optional[str] = None


@router.patch("/applications/{application_id}/job", response_model=JobFactsOut, dependencies=AUTH_FIRST)
async def update_job_facts(
    application_id: int,
    body: UpdateJobFactsRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """The web job page's door, and the ``update_job`` MCP tool's route.
    Writes the caller's OWN application row only (never the shared ``jobs``
    catalog — hard rule #10); a foreign/unknown id reads 404 (S2)."""
    from src.services.applications import job_facts  # noqa: PLC0415

    given = {k: getattr(body, k) for k in body.model_fields_set}
    try:
        return await job_facts.set_job_facts(db, user_id=user.id, application_id=application_id, given=given)
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.get(
    "/applications/{application_id}/kit", response_model=ApplicationKitOut, dependencies=AUTH_FIRST
)
async def get_application_kit(
    application_id: int,
    response: Response,
    include_text: bool = Query(True),
    session: str = Query(""),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """The application kit (S3): the CV and cover letter of THIS application, every
    stored answer with its source, what is missing for the job's country, fresh
    30-minute file links, the duplicate / hold / account-site / autofill state and
    the gate's preview. Writes one ``kit_read`` event (the timeline shows who read
    it and when) and one link row per document; the file tokens appear only in this
    response. 404 for an application that is not the caller's; 429 over the
    hourly cap. Document ``text`` is cut to ``KIT_DOC_TEXT_MAX_CHARS`` (``text_truncated`` /
    ``text_more``; ``include_text=false`` sends none); ``approved_text`` keeps the newest
    ``KIT_APPROVED_TEXT_MAX`` (``answers_omitted``). ``session`` is the assistant
    session's own short label (``kit.clean_session``; 422 otherwise): it is stored on
    the ``kit_read`` so ``hold`` can tell two sessions of one assistant apart.
    ``Cache-Control: no-store``."""
    response.headers["Cache-Control"] = "no-store"
    try:
        label = kit_service.clean_session(session)
        return await kit_service.build_kit(
            db, user, application_id, now=datetime.now(timezone.utc), include_text=include_text, session=label,
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.post(
    "/applications/{application_id}/kit/form-check", response_model=FormCheckOut,
    response_model_exclude_none=True, dependencies=AUTH_FIRST,
)
async def check_application_form(
    application_id: int,
    body: FormCheckRequest,
    response: Response,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Which questions on the LIVE form already have a saved answer. The assistant reads
    the form, lists every question (optional ones too, ``required=false``) and, for each,
    the kit ``key`` it plans to use. Job360 checks that key really exists in THIS
    application's kit (an ``approved_text`` answer also matches by its question); with no
    key it matches the normalized wording of the question against the user's approved
    answers. No fuzzy matching, no NLP. ``answered`` carries the value and its source
    (``confirmed: false`` = inferred, confirm with the user); ``missing`` is what to ask
    the user, once. Each question may also carry the live control's ``field_type``, ``options``,
    ``limit`` and ``limit_unit``: a saved value outside ``options`` is a gap, one over ``limit``
    is flagged ``over_limit`` (never cut), sensitive questions are flagged ``needs_confirmation``
    and knock-out questions are listed in ``knockouts`` with ``stop_for_user``. Read-only: no event is
    written. 404 for an application that is not the
    caller's. ``Cache-Control: no-store``."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return await kit_service.check_form(
            db, user, application_id,
            questions=[q.model_dump() for q in body.questions], form_url=body.form_url,
            now=datetime.now(timezone.utc),
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


async def _controls(db: JobDatabase, user: CurrentUser, application_id: int) -> dict[str, Any]:
    """The decision state for one application (404 when it is not the caller's)."""
    app_row = await spine.get_owned_application(db, user.id, application_id)
    if app_row is None:
        raise HTTPException(status_code=404, detail="application not found")
    state = await kit_service.controls_state(db, user.id, app_row, datetime.now(timezone.utc))
    return {"application_id": application_id, **state}


@router.get(
    "/applications/{application_id}/controls", response_model=ApplicationControlsOut, dependencies=AUTH_FIRST
)
async def get_application_controls(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """What the four buttons on the application page show: is the latest CV seen /
    approved (who, where, when), did the user say don't send, the autofill choice,
    and the duplicate warning. Read-only."""
    return await _controls(db, user, application_id)


async def _web_decision(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, payload: dict[str, Any],
    *, once_per_version: bool = False, fingerprint: Optional[dict[str, Any]] = None,
) -> None:
    """One button click: prove the application is the caller's, then write the
    event through the same rules the chat path uses."""
    if await spine.get_owned_application(db, user.id, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    try:
        await kit_service.record_decision(
            db, user, application_id, event_type, payload, once_per_version=once_per_version,
            fingerprint=fingerprint,
        )
    except SpineError as exc:
        _raise(exc)


async def _latest_cv_or_409(db: JobDatabase, user: CurrentUser, application_id: int) -> dict[str, Any]:
    cv = await spine.latest_artifact(db, user.id, application_id, "cv")
    if cv is None:
        raise HTTPException(status_code=409, detail="no CV saved for this application")
    return cv


@router.post(
    "/applications/{application_id}/cv-seen", response_model=ApplicationControlsOut, status_code=201,
    dependencies=AUTH_FIRST,
)
async def mark_cv_seen(
    application_id: int,
    body: CvSeenRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> dict[str, Any]:
    """"I've checked this CV" - the user's own click records ``cv_seen`` (where=web)
    for the latest CV, once per version. Session only; no MCP twin."""
    cv = await _latest_cv_or_409(db, user, application_id)
    artifact_id = body.artifact_id if body.artifact_id is not None else cv["id"]
    row = await spine.get_artifact(db, user.id, application_id, artifact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    await _web_decision(
        db, user, application_id, "cv_seen",
        {"artifact_id": row["id"], "sha256": kit_service.sha256_text(row["text"]), "where": "web"},
        once_per_version=True,
    )
    return await _controls(db, user, application_id)


# Statuses at which "Send this one" / "Don't send" no longer make sense: the
# application is already applied (or further along), or closed. The pre-submit
# approval step only exists while the status is still `considering`.
_SEND_DECISION_CLOSED_STATUSES = frozenset({
    "applied", "replied", "interview_requested", "interview_scheduled", "interview_done",
    "offer", "rejected", "withdrawn", "ghosted", "hired", "offer_declined",
})


async def _refuse_if_already_applied(db: JobDatabase, user: CurrentUser, application_id: int) -> None:
    """Defence in depth for the web buttons: 404 for a foreign id, 409 once the
    application is applied or later (or closed). Nothing is recorded."""
    row = await spine.get_owned_application(db, user.id, application_id)
    if row is None:
        raise HTTPException(status_code=404, detail="application not found")
    if row.get("status") in _SEND_DECISION_CLOSED_STATUSES:
        raise HTTPException(status_code=409, detail="already applied")


@router.post(
    "/applications/{application_id}/send/approve", response_model=ApplicationControlsOut, status_code=201,
    dependencies=AUTH_FIRST,
)
async def approve_send(
    application_id: int,
    artifact_id: Optional[int] = Query(None, ge=1),
    form_filled_event_id: Optional[int] = Query(None, ge=1),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> dict[str, Any]:
    """"Send this one" - the user says yes to sending this application with the
    latest CV. Records ``cv_seen`` (if not already) then ``submit_approved``, both
    where=web. A CV edited later makes the yes stop matching; slice 8: so does a new
    letter, fill, form page or assistant profile edit, or SUBMIT_APPROVAL_TTL_MINUTES passing
    (``fingerprint`` on the event; check_submit asks ``approval_stale``). Clears an earlier
    "don't send". S5d: ``artifact_id`` / ``form_filled_event_id`` name the CV and
    the fill the user was looking at; when either is no longer the newest, 409 and
    nothing is recorded (the yes must name what the user saw)."""
    await _refuse_if_already_applied(db, user, application_id)
    cv = await _latest_cv_or_409(db, user, application_id)
    if artifact_id is not None and int(cv["id"]) != artifact_id:
        raise HTTPException(status_code=409, detail="the CV changed since you looked - look again")
    if form_filled_event_id is not None:
        if await ready_service.newest_fill_id(db, user.id, application_id) != form_filled_event_id:
            raise HTTPException(status_code=409, detail="the filled form changed since you looked - look again")
    # Owner decision 2026-10-19: an answer the user edited does not block the yes - submit-check hands
    # the assistant those answers (`type_first`) to type into the form before it submits.
    payload = {"artifact_id": cv["id"], "sha256": kit_service.sha256_text(cv["text"]), "where": "web"}
    # Gap roadmap slice 8: the yes is bound to what the card showed (letter, fill, page, the
    # assistant's profile edits) and to time; check_submit asks `approval_stale` once any of it moves.
    stamp = await kit_service.approval_fingerprint(db, user.id, application_id)
    await _web_decision(db, user, application_id, "cv_seen", payload, once_per_version=True)
    await _web_decision(db, user, application_id, "submit_approved", payload, fingerprint=stamp)
    return await _controls(db, user, application_id)


@router.post(
    "/applications/{application_id}/send/decline", response_model=ApplicationControlsOut, status_code=201,
    dependencies=AUTH_FIRST,
)
async def decline_send(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> dict[str, Any]:
    """"Don't send" - the gate answers ``stop`` / ``user_declined`` until a later
    "Send this one"."""
    await _refuse_if_already_applied(db, user, application_id)
    await _web_decision(db, user, application_id, "submit_declined", {"where": "web"})
    return await _controls(db, user, application_id)


@router.post(
    "/applications/{application_id}/autofill", response_model=ApplicationControlsOut, status_code=201,
    dependencies=AUTH_FIRST,
)
async def set_autofill(
    application_id: int,
    body: AutofillRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> dict[str, Any]:
    """"Autofill" / "Don't autofill" - may the assistant type into this form at
    all. Latest wins; unset means the assistant follows its own app permission."""
    await _web_decision(db, user, application_id, "autofill_set", {"mode": body.mode})
    return await _controls(db, user, application_id)


@router.post(
    "/applications/{application_id}/duplicate/clear", response_model=ApplicationControlsOut, status_code=201,
    dependencies=AUTH_FIRST,
)
async def clear_duplicate(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> dict[str, Any]:
    """"Not a duplicate, go ahead" - clears the duplicate-job ask and the unattended
    duplicate stop for THIS application. Web only."""
    await _web_decision(db, user, application_id, "duplicate_cleared", {"where": "web"})
    return await _controls(db, user, application_id)


@router.post(
    "/applications/{application_id}/submit/resolve", response_model=ApplicationControlsOut, status_code=201,
    dependencies=AUTH_FIRST,
)
async def resolve_submit(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> dict[str, Any]:
    """"It was not sent" - the user says a started submit (``submit_attempted``) never went out, which
    ends the ``submit_unconfirmed`` stop. Appends ``submit_resolved`` {outcome: not_sent}. Web only;
    409 when no started submit is waiting. A submit that WAS sent is recorded as ``applied``."""
    await _web_decision(db, user, application_id, "submit_resolved", {"outcome": "not_sent"})
    return await _controls(db, user, application_id)


@router.post(
    "/applications/{application_id}/blocked/resolve", response_model=ApplicationControlsOut, status_code=201,
    dependencies=AUTH_FIRST,
)
async def resolve_blocked(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> dict[str, Any]:
    """"Mark resolved" - the user sorted out what stopped their assistant: records
    ``unblocked`` {resolution: user_did_it} and closes the blocked ask. 409 when
    nothing is blocked. Session only; assistants use ``record_event``."""
    if await spine.get_owned_application(db, user.id, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    try:
        stored = blocked_service.check_payload(user, "unblocked", {"resolution": "user_did_it"}, "")
        await blocked_service.record(
            db, user, application_id, "unblocked", stored, occurred_at=datetime.now(timezone.utc).isoformat(),
        )
    except SpineError as exc:
        _raise(exc)
    return await _controls(db, user, application_id)


@router.get(
    "/applications/{application_id}/submit-check", response_model=SubmitCheckResponse, dependencies=AUTH_FIRST
)
async def submit_check(
    application_id: int,
    form_url: str = Query("", max_length=2048),
    session: str = Query(""),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """THE one gate before a final submit (owner decision 2026-10-08, S2).

    Reads the user's settings, this application's own facts (status, receipt,
    the per-job override) and the day's counts, then answers through
    ``settings_rules.may_submit`` - the same function for every caller (this route
    and the MCP ``check_submit`` tool). ``form_url`` is the address of the page
    the form is on (a bare host works too); without it the answer is ``ask``
    (``unknown_site``). The address is read strictly (``screen_form_url``): a
    user-info ``@``, ``%`` or backslash in the authority, a non-ASCII / IDNA host
    or a non-http(s) scheme answers ``ask`` / ``unknown_site``, and a host other
    than the newest ``form_filled``'s answers ``ask`` / ``form_host_mismatch``, and a
    host + path other than the page stored on the newest ``form_filled`` / ``form_swept``
    (``kit.latest_form_url``) answers ``ask`` / ``form_page_mismatch`` - a stored yes
    clears none of them. An unconfirmed email answers ``ask`` /
    ``email_not_verified``. ``session`` is the caller's own label (the one it gave
    ``get_application_kit``; 422 when malformed): another session's live kit hold
    answers ``ask`` / ``held_by_other_session``, which a yes does not clear (only
    ``hold_released`` or the end of ``KIT_HOLD_MINUTES`` does). Read-only. 404 for an
    application that is not the caller's (rule #12)."""
    try:
        label = kit_service.clean_session(session)
        app_row = await spine.get_owned_application(db, user.id, application_id)
        if app_row is None:
            raise SpineError(404, "application not found")
        now = datetime.now(timezone.utc)
        cfg = settings_rules.effective((load_profile(user.id) or UserProfile()).assistant_settings)
        counts = await settings_rules.load_submit_counts(
            db, user.id, now, application_id, window_days=cfg.company_cap_days
        )
        hold = await kit_service.live_hold(db, user, application_id, now, label)
        facts = await kit_service.gate_facts(
            db, user.id, app_row, email_verified=email_verification_satisfied(user),
            other_session_hold=kit_service.hold_fact(hold),
        )
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover
    verdict = settings_rules.may_submit(cfg, facts, form_url, counts, now=now)
    type_first = await ready_service.changed_answers(db, user.id, app_row)
    verdict = ready_service.type_first_lock(verdict, type_first)
    detail = verdict.detail
    get_audit_logger().info(
        "submit_check",
        extra={
            "event": "submit_check", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "application_id": application_id,
            "decision": verdict.decision, "reason": verdict.reason,
            "host_kind": settings_rules.host_kind(settings_rules.screen_form_url(form_url)), "result": "ok",
        },
    )
    return {
        "application_id": application_id, "decision": verdict.decision,
        "reason": verdict.reason, "detail": detail, "type_first": type_first,
        # Bulk-apply Q2: what is left of the user's own limits (null = no cap).
        "daily_left": settings_rules.daily_left(cfg, counts),
        "company_left": settings_rules.company_left(cfg, counts.company_applied_in_window),
    }


class ReleaseHoldOut(BaseModel):
    released: bool


@router.post(
    "/applications/{application_id}/hold/release", response_model=ReleaseHoldOut, dependencies=AUTH_FIRST
)
async def release_hold(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - a human web action (session only)
) -> dict[str, Any]:
    """"Release" - the user frees an application another assistant session holds (it crashed
    or was closed): appends ``hold_released`` {reason: stopped, by: web}; nothing is rewritten.
    ``{released: false}`` and no event when no hold is live. 404 for an application that is not
    the caller's. Session only: a bearer token is refused, so an assistant can never release
    another session's hold; assistants record ``hold_released`` through ``record_event``."""
    try:
        if await spine.get_owned_application(db, user.id, application_id) is None:
            raise SpineError(404, "application not found")
        now = datetime.now(timezone.utc)
        if application_id not in await kit_service.holds_for_apps(db, user.id, [application_id], now):
            return {"released": False}
        stored = await kit_service.check_kit_event(db, user, application_id, "hold_released", {"reason": "stopped"})
        result = await spine.append_event(
            db, user_id=user.id, application_id=application_id, event_type="hold_released",
            payload=stored, occurred_at=now.isoformat(), recorded_by=actor_for(user),
        )
        kit_service.log_kit_event(user, application_id, "hold_released", stored, int(result["event_id"]))
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover
    return {"released": True}


@router.post(
    "/applications/{application_id}/events", status_code=201, response_model=RecordEventResponse,
    dependencies=AUTH_FIRST,
)
async def record_event(
    application_id: int,
    body: RecordEventRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        if body.contact_id is None and body.event_type in _OUTREACH_EVENT_TYPES:
            raise _outreach_needs_contact("record_event", user.id, application_id=application_id)
        source = spine.validate_source(body.source.model_dump() if body.source else None)
        if body.contact_id is not None:
            # Owner decision, 2026-09-25 — record this as OUTREACH for a
            # person too, in the same transaction as the job-timeline event.
            # Only the two outreach event types are allowed here; a cold
            # contact (no application_id of its own) can never match THIS
            # route's application_id, so it reads as 422 — use
            # POST /api/contacts/{contact_id}/outreach for a cold contact.
            # Bug fix (coordinator review, 2026-09-26) — RecordOutreachRequest
            # (what this delegates to) has no slot for corrects_event_id/
            # payload/scheduled_at; silently accepting and dropping them here
            # would lose data the caller thinks was recorded. Refuse instead.
            if body.corrects_event_id is not None or body.payload or body.scheduled_at:
                raise SpineError(
                    422, "corrects_event_id/payload/scheduled_at are not supported for outreach (contact_id)"
                )
            if body.event_type not in ("outreach_sent", "outreach_replied"):
                raise SpineError(
                    422, "event_type must be 'outreach_sent' or 'outreach_replied' when contact_id is given"
                )
            if not body.channel:
                raise SpineError(422, "channel is required when contact_id is given")
            entry = "sent" if body.event_type == "outreach_sent" else "reply"
            result = await contacts_service.record_outreach(
                db, user.id, body.contact_id, actor_for(user),
                entry=entry, channel=body.channel, text=body.detail, occurred_at=body.occurred_at,
                source_message_id=source["message_id"] if source else "",
                follow_up_on=body.follow_up_on, application_id_hint=application_id,
            )
            app_row = await spine.get_owned_application(db, user.id, application_id)
            if app_row is None:  # pragma: no cover — record_outreach already 422s a mismatch
                raise SpineError(404, "application not found")
            return {
                "event_id": result["event_id"], "event_type": body.event_type,
                "occurred_at": result["outreach"]["occurred_at"], "recorded_at": result["outreach"]["recorded_at"],
                "recorded_by": result["outreach"]["recorded_by"], "status": app_row["status"],
                "already_existed": result["already_existed"], "scheduled_at": None,
                "follow_up_on": result["follow_up_on"],
            }

        spine.validate_event_type(body.event_type)
        if body.event_type in proof_service.SERVER_ONLY_EVENTS:
            raise SpineError(422, f"{body.event_type} is written by Job360 itself (the upload), not by a caller")
        detail = body.clamp_detail()
        # proof_text is capped in CHARACTERS (owner limit), so it skips the generic byte cap.
        if body.event_type == "proof_text":
            if source is not None:
                raise SpineError(422, "proof_text takes no source")
            payload = proof_service.check_proof_text(user, body.payload)
            if body.corrects_event_id is not None:
                cur = await db._db.execute(
                    "SELECT 1 FROM application_events WHERE id = ? AND user_id = ? AND application_id = ? "
                    "AND event_type = 'proof_text'", (body.corrects_event_id, user.id, application_id),
                )
                if await cur.fetchone() is None:
                    raise SpineError(422, "corrects_event_id must be a proof_text event on this application")
        else:
            payload = spine.validate_payload(
                body.payload,
                max_bytes=settings.KIT_FORM_PAYLOAD_MAX_BYTES if body.event_type == "form_filled" else None,
            )
        payload = spine.validate_outcome_payload(body.event_type, payload)
        payload = spine.validate_auto_email_payload(body.event_type, payload)
        payload = interview_payloads.validate(body.event_type, payload)
        occurred_at = spine.parse_occurred_at(body.occurred_at)
        scheduled_at = spine.parse_scheduled_at(body.scheduled_at, body.event_type)
        follow_up_on_arg: Any = spine.FOLLOW_UP_UNSET
        if body.follow_up_on is not None:
            today = await spine.user_today(db, user.id)
            follow_up_on_arg = spine.parse_follow_up_on(body.follow_up_on, today)
        if await spine.get_owned_application(db, user.id, application_id) is None:
            raise SpineError(404, "application not found")
        await spine.check_outcome_event_id(db, user.id, application_id, body.event_type, payload)
        await interview_payloads.check_refs(
            db, user.id, application_id, body.event_type, payload, body.corrects_event_id
        )
        if body.event_type == "submit_mode_set":
            _check_submit_mode_event(user, application_id, payload)
        if body.event_type == "answer_corrected":
            _check_answer_corrected(user, application_id, payload)
        if body.event_type in _QUEUE_EVENT_TYPES:
            await _check_queue_event(db, user, application_id, body.event_type, payload, body.corrects_event_id)
        if body.event_type in kit_service.KIT_EVENT_TYPES:
            # S3: closed payloads + who may write which (the MCP tool inherits this).
            payload = await kit_service.check_kit_event(db, user, application_id, body.event_type, payload)
        if body.event_type in blocked_service.BLOCKED_EVENT_TYPES:
            # S6: closed payloads; `blocked` opens one Needs-you ask, `unblocked`
            # closes it - ask + event in ONE transaction (the MCP tool inherits this).
            if body.corrects_event_id is not None or source is not None:
                raise SpineError(422, f"{body.event_type} takes no corrects_event_id or source")
            stored = blocked_service.check_payload(user, body.event_type, payload, detail)
            return await blocked_service.record(
                db, user, application_id, body.event_type, stored,
                occurred_at=occurred_at, follow_up_on=follow_up_on_arg,
            )
        # append_event always returns the REAL final follow_up_on — set,
        # cleared, auto-cleared (an overdue date + a status event), replay-
        # derived (a correction), or unchanged — so there is nothing left to
        # patch here (coordinator review, 2026-09-25).
        # G338: `submit_attempted` is a claim - the row lock, the 409 checks and the append are
        # one transaction, so of two sessions that both passed check_submit only one may click.
        claim: contextlib.AbstractAsyncContextManager[Any] = (
            kit_service.submit_claim(db, user, application_id)
            if body.event_type == "submit_attempted" else contextlib.nullcontext()
        )
        async with claim:
            result = await spine.append_event(
                db, user_id=user.id, application_id=application_id, event_type=body.event_type,
                detail=detail, payload=payload, occurred_at=occurred_at, recorded_by=actor_for(user),
                corrects_event_id=body.corrects_event_id, source=source, scheduled_at=scheduled_at,
                follow_up_on=follow_up_on_arg,
                # Weak spot #4: a repeat `applied` (e.g. the confirmation email) is
                # stored as the submission-confirmation note, never a second applied.
                repeat_applied_as_confirmation=True,
            )
        if body.event_type == "replied" and source and source.get("sender"):
            # The sender is a person we sent outreach to on this job: mark them
            # replied too (a new outreach_replied entry; old events untouched).
            await contacts_service.mark_reply_from_sender(
                db, user.id, application_id, actor_for(user),
                sender=source["sender"], message_id=source["message_id"], occurred_at=body.occurred_at,
            )
        if body.event_type in kit_service.KIT_EVENT_TYPES:
            kit_service.log_kit_event(user, application_id, body.event_type, payload, int(result["event_id"]))
        if body.event_type == "proof_text":
            proof_service.log_proof_text(user, application_id, int(result["event_id"]), payload)
        if body.event_type in interview_payloads.INTERVIEW_EVENT_TYPES:
            interview_payloads.log_recorded(user.id, application_id, int(result["event_id"]), body.event_type, payload)
        # Keep append_event's own warnings (a duplicate message_id recorded as another type)
        # and add the reply-before-applied one; never replace one list with the other.
        # Judge the STORED type: a duplicate message_id returns the existing event.
        stored_type = str(result.get("event_type") or body.event_type)
        missing = await spine.missing_applied_warnings(db, application_id, stored_type)
        return {**result, "warnings": [*result.get("warnings", []), *missing]}
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


_QUEUE_EVENT_TYPES = ("queued", "unqueued")


def _queue_refused(user: CurrentUser, application_id: int, event_type: str, status: int, reason: str) -> SpineError:
    """Audit a refused batch-queue event (who, which application, a CLOSED reason - never free
    text or the company) and return the error to raise."""
    get_audit_logger().warning(
        "queue_event_refused",
        extra={
            "event": "queue_event_refused", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "application_id": application_id,
            "event_type": safe_log_value(event_type), "status": status, "reason": safe_log_value(reason),
            "result": "refused",
        },
    )
    detail = {
        "payload_not_empty": f"{event_type} takes no payload",
        "bad_reason": "unqueued takes only an optional reason: " + ", ".join(queue_preflight.UNQUEUE_REASONS),
        "not_open": "not open",
        "already_queued": "already queued",
        "not_queued": "not queued",
        "corrects_non_queue_event": f"{event_type} may only correct a queued or unqueued event",
    }[reason]
    return SpineError(status, detail)


async def _check_queue_event(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, payload: dict[str, Any],
    corrects_event_id: Optional[int],
) -> None:
    """The batch queue (owner decision 2026-10-18): ``queued`` / ``unqueued`` are note events
    with NO payload. 422 for a payload. ``queued`` needs an open application (status
    ``considering`` and no receipt): 409 ``not open`` otherwise. 409 ``already queued`` when it
    is on the queue now, 409 ``not queued`` for an ``unqueued`` when it is not (a correction
    via ``corrects_event_id`` re-derives the queue, so it skips these two). Lives in the ROUTE
    function, so the MCP ``record_event`` tool inherits it."""
    if event_type == "unqueued":
        # Bulk-apply M1: an optional closed reason (what queue_summary counts).
        if queue_preflight.check_unqueue_payload(payload):
            raise _queue_refused(user, application_id, event_type, 422, "bad_reason")
    elif payload:
        raise _queue_refused(user, application_id, event_type, 422, "payload_not_empty")
    if event_type == "queued":
        app_row = await spine.get_owned_application(db, user.id, application_id)
        cur = await db._db.execute(
            "SELECT COUNT(*) FROM application_receipts WHERE application_id = ? AND user_id = ?",
            (application_id, user.id),
        )
        has_receipt = int((await cur.fetchone())[0]) > 0
        if app_row is None or app_row["status"] != "considering" or has_receipt:
            raise _queue_refused(user, application_id, event_type, 409, "not_open")
    if corrects_event_id is not None:
        # A queue event may only retract a queue event (security review 2026-10-18), never
        # hide a kit, decline or status event of the same application.
        cur = await db._db.execute(
            "SELECT 1 FROM application_events WHERE id = ? AND user_id = ? AND application_id = ? "
            "AND event_type IN ('queued', 'unqueued')",
            (corrects_event_id, user.id, application_id),
        )
        if await cur.fetchone() is None:
            raise _queue_refused(user, application_id, event_type, 422, "corrects_non_queue_event")
    if corrects_event_id is None:
        is_queued = application_id in await spine.queued_at_by_app(db, user.id, [application_id])
        if event_type == "queued" and is_queued:
            raise _queue_refused(user, application_id, event_type, 409, "already_queued")
        if event_type == "unqueued" and not is_queued:
            raise _queue_refused(user, application_id, event_type, 409, "not_queued")
    get_audit_logger().info(
        "queue_event",
        extra={
            "event": "queue_event", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
            "application_id": application_id, "event_type": safe_log_value(event_type), "result": "ok",
            "reason": (
                safe_log_value(payload["reason"]) if event_type == "unqueued" and payload.get("reason") else None
            ),
        },
    )


def _check_answer_corrected(user: CurrentUser, application_id: int, payload: dict[str, Any]) -> None:
    """Bulk-apply F4: the user changed an answer the assistant wrote or guessed. Payload
    ``{key | question, was_source}`` (``was_source`` = ``written`` or ``guessed``), nothing else. A note: it
    saves nothing to memory (that stays a separate ``update_profile`` after the user's yes)."""
    key, question = payload.get("key"), payload.get("question")
    ok = (
        set(payload) <= {"key", "question", "was_source"}
        and payload.get("was_source") in ("written", "guessed")
        and any(isinstance(v, str) and v.strip() for v in (key, question))
        and all(v is None or (isinstance(v, str) and len(v) <= 300) for v in (key, question))
    )
    if not ok:
        raise SpineError(
            422, 'answer_corrected needs payload {key or question (text, max 300), was_source: "written"|"guessed"}'
        )
    get_audit_logger().info(
        "answer_corrected",
        extra={
            "event": "answer_corrected", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
            "application_id": application_id, "was_source": safe_log_value(str(payload["was_source"])), "result": "ok",
        },
    )


def _check_submit_mode_event(user: CurrentUser, application_id: int, payload: dict[str, Any]) -> None:
    """The per-job submit switch (owner decision 2026-10-08, S2): payload
    ``{"submit_mode": "confirm" | "auto_when_sure" | "inherit"}`` and nothing else.

    An assistant (any non-web actor) may only SEND ``confirm`` - the safe
    direction. ``auto_when_sure`` and ``inherit`` (which hands the job back to the
    account setting, possibly auto) need the user's own click on the website.
    Lives in the ROUTE function, so the MCP ``record_event`` tool inherits it."""
    actor = actor_for(user)
    mode = payload.get("submit_mode")
    if set(payload) != {"submit_mode"} or mode not in settings_rules.VALID_OVERRIDE_VALUES:
        raise SpineError(
            422,
            'submit_mode_set needs payload {"submit_mode": "confirm" | "auto_when_sure" | "inherit"} and nothing else',
        )
    if actor != "web" and mode != "confirm":
        get_audit_logger().warning(
            "submit_mode_set_refused",
            extra={
                "event": "submit_mode_set_refused", "user_id": safe_log_value(user.id),
                "actor": safe_log_value(actor), "application_id": application_id,
                "submit_mode": safe_log_value(mode, max_len=20), "status": 403, "result": "refused",
            },
        )
        raise SpineError(403, "this needs your click on the Job360 website")
    get_audit_logger().info(
        "submit_mode_set",
        extra={
            "event": "submit_mode_set", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor),
            "application_id": application_id, "submit_mode": safe_log_value(mode, max_len=20), "result": "ok",
        },
    )


@router.post(
    "/applications/{application_id}/receipt",
    status_code=201,
    response_model=RecordApplicationReceiptResponse,
    dependencies=AUTH_FIRST,
)
async def record_application_receipt(
    application_id: int,
    body: RecordApplicationReceiptRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        fields_filled = body.clamp_fields_filled()
        applied_at = spine.parse_occurred_at(body.applied_at, field="applied_at") if body.applied_at else None
        if body.request_id is not None:
            idempotency.check_key(body.request_id)
        return await spine.record_receipt(
            db, user_id=user.id, application_id=application_id, recorded_by=actor_for(user),
            channel=body.channel, note=body.note, confirmation=body.confirmation,
            answers=[a.model_dump(exclude_none=True) for a in body.answers], fields_filled=fields_filled,
            cv_artifact_id=body.cv_artifact_id, cover_letter_artifact_id=body.cover_letter_artifact_id,
            applied_at=applied_at, request_id=body.request_id,
        )
    except idempotency.IdempotencyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except SpineError as exc:
        _raise(exc)
        raise AssertionError("unreachable")  # pragma: no cover


@router.get("/whats-new", response_model=WhatsNewResponse, dependencies=AUTH_FIRST)
async def whats_new(
    since: Optional[str] = Query(None),
    after_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    return await spine.whats_new(db, user.id, since=since, after_id=after_id, limit=limit)
