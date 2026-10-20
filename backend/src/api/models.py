"""Pydantic models for Job360 FastAPI backend."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    version: str


class LivezResponse(BaseModel):
    status: str


class StatusResponse(BaseModel):
    profile_exists: bool


class JobResponse(BaseModel):
    """The ad the user brought, exactly as it is stored.

    Slice 5 (#483) took the score off this model. Job360 never judges fit
    (product rule 4) — the user's own agent does, and records its verdict with
    `save_fit`. Every field below is read straight off the `jobs` row; nothing
    here is computed about the user.
    """

    id: int
    title: str
    company: str
    location: str
    salary: Optional[str]
    source: str
    date_found: str
    apply_url: str
    job_type: str = ""
    experience_level: str = ""
    # The stored ad text. A user-brought job has no source website to read the
    # ad on, so the caller must be able to get back what they pasted.
    description: Optional[str] = None
    # Date model (Pillar 3 Batch 1). `posted_at` is when the ad was brought
    # (confidence 'low' — see routes/bring.py); the two _seen_at fields bound
    # how long we have held it.
    posted_at: Optional[str] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    date_confidence: Optional[str] = None
    # Application deadline — None means "no deadline listed".
    deadline: Optional[str] = None           # ISO date YYYY-MM-DD
    deadline_source: Optional[str] = None    # "listing" | "description" | None


class ProfileSummary(BaseModel):
    is_complete: bool
    job_titles: list[str]
    # THE one skill count — len(skill_tiering.profile_skills(profile)), the
    # same number the grouped list, the alignment panel and get_profile use.
    skills_count: int
    cv_length: int
    has_linkedin: bool
    has_github: bool
    education: list[str]
    experience_level: str
    # Upload receipts — what the user gave us and when, per input. Defaults
    # keep every existing caller and old stored profile valid (a profile saved
    # before 2026-08-08 has no receipt, and the UI falls back to a plain
    # "uploaded" state). ``github_repo_count`` is the GitHub equivalent of a
    # filename: proof of what we actually read.
    cv_filename: str = ""
    cv_uploaded_at: str = ""
    # "cv_file_garbled" = the uploaded PDF has unreadable glyphs; "" = fine.
    cv_file_warning: str = ""
    linkedin_filename: str = ""
    linkedin_uploaded_at: str = ""
    github_username: str = ""
    github_connected_at: str = ""
    github_repo_count: int = 0


class CVDetail(BaseModel):
    """Full extracted CV data for transparent display."""

    raw_text: str = ""
    skills: list[str] = []
    job_titles: list[str] = []
    companies: list[str] = []
    education: list[str] = []
    certifications: list[str] = []
    summary_text: str = ""
    experience_text: str = ""
    # Display-only fields (NOT used in scoring)
    name: str = ""
    headline: str = ""
    location: str = ""
    achievements: list[str] = []
    # Portfolio / personal-site / other links — new CVData field (slice 4,
    # spec R9), agent-editable via `PATCH /profile` path `cv_data.links`.
    links: list[str] = []
    # Dated work history {company, title, dates, location, bullets}. Stored
    # since 2026-08-06 but never exposed to the frontend, so the user could
    # never SEE their parsed experience — only a "Roles: N" count. Part of
    # the "stored but not shown" gap closed 2026-08-08.
    cv_positions: list[dict[str, Any]] = []
    # Projects stated on the CV. A Projects heading was already used as a
    # section boundary and then discarded — for a junior or career-changing
    # candidate it is often the strongest evidence on the document.
    cv_projects: list[dict[str, Any]] = []
    # Both are MATCHING shelves (the judge reads them) and both were
    # invisible to the person they describe until 2026-08-12 - the same
    # "scored on but never shown" gap that hid linkedin_summary, repeated
    # one commit later on the shelves that replaced it.
    cv_experience_level: str = ""
    cv_right_to_work: str = ""
    # Audit finding 11 (2026-08-16): extracted from the CV, but shown
    # nowhere — the same "stored but not shown" gap as cv_positions.
    cv_industries: list[str] = []
    # Aggregated highlights for the CV viewer — merges skills + titles +
    # companies + achievements + name/headline/location for in-text highlighting
    highlights: list[str] = []
    # The universal extraction gate's verdict, so the USER can see how well we
    # understood their CV. A score computed and logged but never shown is a dead
    # artifact — the person whose profile it is has no way to know we only
    # understood a fraction of their document, and no way to correct it.
    extraction_score: dict[str, Any] = {}


class NormalizedOut(BaseModel):
    """One forgiving-input normalisation ``update_profile`` applied (owner decision 2026-10-10):
    where it happened and a CLOSED rule code (``services/profile/forgiving.py``) - never a value."""

    path: str
    rule: str


class ProfileEditOut(BaseModel):
    """One row of the agent-edit overlay (slice 4, spec R8/R11).

    The SAME shape everywhere an edit appears: ``GET /profile``'s
    ``agent_edits``, ``PATCH /profile``'s ``applied``, and
    ``export_history``'s ``profile_edits``. Declared once, here, so the
    generated ``api-types.ts`` gives the frontend real field names instead of
    the ``{[key: string]: unknown}[]`` an untyped ``dict`` renders as — the
    profile page reads ``set_by`` and ``set_at`` off every one of these
    (``components/profile/EditedMark.tsx``).

    ``value`` is ``Any``: the overlay carries whatever the path's dataclass
    field holds — a string, a list of strings, a number, a bool.
    """

    path: str
    value: Any = None
    set_by: str
    set_at: str


class AgentEditOut(ProfileEditOut):
    """One live ASSISTANT edit on ``GET /profile``'s ``agent_edits``.

    ``previous_value`` is the field's base value — what the CV / the user's
    own saves hold under this edit, and so exactly what "Take back" restores
    (the label never promises a value Take back would not produce).
    ``None`` / ``""`` / ``[]`` all mean "it was empty" — the page words them
    that way.
    """

    previous_value: Any = None
    # Feature 9: "user_said" | "agent_inferred" | "" (legacy row). `confirmed` is
    # true only when the user stands behind the value (web write or user_said).
    source: str = ""
    confirmed: bool = False


class ProfileEditHistoryRow(BaseModel):
    """One row of one field's history, newest first. ``value: null`` is a
    clear (the field fell back to what the CV / the web form holds)."""

    value: Any = None
    set_by: str
    set_at: str


class ProfileEditHistoryResponse(BaseModel):
    path: str
    rows: list[ProfileEditHistoryRow]


class TakeBackRequest(BaseModel):
    """``POST /profile/edits/take-back`` and ``/keep`` — the path whose
    assistant edit the human takes back or keeps. ``extra="forbid"``: no way
    to name another user."""

    model_config = ConfigDict(extra="forbid")

    path: str


class LessonOut(BaseModel):
    """One `lesson` event, read back across applications (slice 9, #516)."""

    event_id: int
    application_id: int
    job_title: str
    job_company: str
    detail: str
    occurred_at: str
    recorded_by: str


class SettingRequestOut(BaseModel):
    """One RISKIER setting change an assistant asked for and the user has not
    yet confirmed (S2, owner decision 2026-10-08). Nothing is applied until the
    signed-in user confirms it on the Job360 website."""

    id: int
    path: str
    value: Any = None
    requested_by: str
    requested_at: str
    expires_at: str
    status: str = "waiting"


class SettingFieldOut(BaseModel):
    """One assistant setting: ``value`` as chosen ("" / null = not chosen),
    ``effective`` with the safe default filled in."""

    value: Any = None
    effective: Any = None


class PracticeRunOut(BaseModel):
    needed: bool
    auto_on_since: Optional[str] = None


class SetupProgressOut(BaseModel):
    """How far the six-round setup got: the rounds done (with when), how many,
    and the first unfinished one (``""`` when all are done)."""

    rounds: dict[str, dict[str, str]] = {}
    done: int = 0
    total: int = 6
    next: str = "targets"


class AssistantSettingsView(BaseModel):
    """The assistant settings, as the web and MCP ``get_profile`` read them.

    ``inbox_mode`` / ``check_every`` / ``notes`` are an ALIAS of
    ``preferences.daily_check`` / ``check_every`` / ``assistant_notes`` — shown
    here, still edited at those paths."""

    apply_min_score: SettingFieldOut
    above_line: SettingFieldOut
    below_line: SettingFieldOut
    submit_mode: SettingFieldOut
    daily_cap: SettingFieldOut
    company_cap: SettingFieldOut
    company_cap_days: SettingFieldOut
    weekly_cap: SettingFieldOut
    min_gap_minutes: SettingFieldOut
    apply_hours: SettingFieldOut
    auto_grant_count: SettingFieldOut
    auto_grant_until: SettingFieldOut
    paused_until: SettingFieldOut
    pause_reason: SettingFieldOut
    auto_employer_emails: SettingFieldOut
    paused: bool
    inbox_mode: str = ""
    check_every: str = ""
    notes: list[str] = []
    practice_run: PracticeRunOut
    waiting: list[SettingRequestOut] = []
    setup_progress: SetupProgressOut = SetupProgressOut()


class ProfileResponse(BaseModel):
    summary: ProfileSummary
    preferences: dict[Any, Any]
    cv_detail: CVDetail | None = None
    # Step-1.5 S1.5-F — evidence-based skill tiering surfaced via
    # ``services.profile.skill_tiering.tier_skills_by_evidence``. Maps
    # tier name → ordered list of skill names. Empty dict when no profile
    # is loaded (or the profile has no skills yet).
    skill_tiers: dict[str, list[str]] = {}
    # Step-1.5 S1.5-D/E — ESCO concept URIs per skill (canonical_label →
    # esco_uri). Mirrors `CVData.cv_skills_esco`. Empty when SEMANTIC is
    # off or the index is missing — gracefully degrades.
    skill_esco: dict[str, str] = {}
    # Step-1.5 S3-E — provenance map: skill name → list of source labels
    # (``cv_explicit`` / ``linkedin`` / ``github_dep`` / ``github_lang`` /
    # ``user_declared``). Computed from ``skill_tiering.SkillEvidence.sources``
    # — empty when the profile has no skills. (It never came from "the
    # SkillEntry merge", as this comment claimed for months: that module had no
    # caller and has been deleted.)
    skill_provenance: dict[str, list[str]] = {}
    # Skills grouped by WHERE they came from — for the source-based profile view
    # (From CV / From LinkedIn / From GitHub / From Preferences). Derived from
    # provenance; a skill seen in >1 source appears under each. Empty when no
    # skills.
    skills_by_source: dict[str, list[str]] = {}
    # Step-1.5 S3-E — LinkedIn sub-sections for the profile detail UI.
    # Each value is the raw list of dicts as parsed by
    # ``services.profile.linkedin_parser`` — see CVData fields with the
    # same names. UI flattens for display; backend keeps the raw shape
    # so callers can format independently.
    linkedin_subsections: dict[str, list[dict[Any, Any]]] = {}
    # Step-1.5 S3-E — GitHub temporal data: per-language byte counts
    # (top-K by volume) + topic frequencies. Pure metric surface — UI
    # renders trend graphs without backend re-shaping.
    github_temporal: dict[str, dict[Any, Any]] = {}
    # Everything else GitHub gave us: repos (name/language/description/topics/
    # README excerpt/stars/pushed_at), dependency-file frameworks, inferred and
    # Legacy model-read skills, bio, profile README. Measured 2026-08-09: 92 pieces of
    # this were stored and rendered NOWHERE while only languages+topics showed.
    # Defaults to {} so every existing caller and older profile stays valid.
    github_detail: dict[str, Any] = {}
    # Step-1.5 S3-E — newest snapshot id from ``user_profile_versions``;
    # surfaces "current version" alongside the history list. None when
    # the version table is empty / unavailable.
    current_version_id: Optional[int] = None
    # Gap 4 (slice 26) — MAX(profile_edits.id) for the user, read BEFORE the profile
    # (0 = none yet). It moves on every accepted edit, unlike `current_version_id`;
    # send it back as `expected_profile_version` to refuse a write over a newer one.
    profile_edit_id: int = 0
    # Slice 4 (spec R11) — the current agent-edit overlay: every path an
    # agent has SET and not since cleared, as {path, value, set_by, set_at}.
    # Provenance made visible: the web profile page renders each overlaid
    # field in place with an "Edited by <set_by> on <date>" mark instead of
    # keeping a second list to reconcile. Empty when no edit is active.
    #
    # ASSISTANT rows only (`agent:…` / `token:…`): a field whose newest row is
    # the human's own web save is not an assistant edit, and never carries a
    # mark. Each row also says what the field held before (`previous_value`),
    # so the page can read "Changed by Claude · was £45k".
    agent_edits: list[AgentEditOut] = []
    # Slice 9 (#516) — the last PROFILE_LESSONS_MAX "flag for next time"
    # lessons across every application, newest first, so the agent reading
    # the profile sees them BEFORE it tailors the next CV. The full list is
    # `GET /applications/lessons`.
    lessons: list[LessonOut] = []
    # Owner decision 2026-10-08 — USER INFO MEMORY (contact, right_to_work,
    # logistics, languages, equality, answers): the facts job forms ask, merged
    # with the assistant's overlay. Its own store, not part of `preferences`.
    # Empty = nothing answered (rule #29).
    user_info: dict[str, Any] = {}
    # Owner decision 2026-10-08 (S2) — ASSISTANT SETTINGS (apply mode, score
    # line, submit mode, daily cap, pause switch) with the safe defaults filled
    # in, the practice-run state and the riskier changes WAITING for the user's
    # click. None only on a response built without the settings read.
    assistant_settings: Optional[AssistantSettingsView] = None


class LessonsResponse(BaseModel):
    lessons: list[LessonOut]
    total: int


# ── Step-1.5 S3-G — six new Pydantic models for Cohort Z endpoints. ──


class ProfileVersionSummary(BaseModel):
    """One row in ``GET /profile/versions``. Mirrors the dict shape that
    ``services.profile.storage.list_profile_versions`` returns; CVData +
    preferences blobs are passed through unmodified so the frontend can
    diff snapshot-to-snapshot without an extra round-trip."""

    id: int
    created_at: str
    source_action: str
    cv_data: dict[Any, Any]
    preferences: dict[Any, Any]
    # Migration 0030 — human-readable intake id, "SNAP-YYYYMMDD-<user4>-
    # <content8>" (see services/profile/snapshot.py). None for rows saved
    # before 0030 shipped — no snapshot id was ever computed for them.
    snapshot_id: Optional[str] = None


class ProfileVersionsListResponse(BaseModel):
    """``GET /profile/versions`` body wrapper."""

    versions: list[ProfileVersionSummary]
    total: int


class JsonResumeResponse(BaseModel):
    """``GET /profile/json-resume`` body. Wraps the canonical JSON Resume
    dict (https://jsonresume.org/schema/) under a ``resume`` key so the
    response is a JSON object, not a bare list."""

    resume: dict[Any, Any]


class LinkedInResponse(BaseModel):
    ok: bool
    merged: bool


class GitHubResponse(BaseModel):
    ok: bool
    merged: bool


# ── Agent doctor + report_missing_tool (feature 7) ───────────────────────────


class DoctorAuth(BaseModel):
    """How this caller is signed in: ``session`` (browser), ``token`` (personal
    ``j360_…`` token) or ``oauth`` (an OAuth-connected assistant), and the
    actor string every event it records will carry."""

    kind: str
    actor: str


class DoctorSetup(BaseModel):
    """The six-round setup: rounds done, of ``total``; ``next`` = first unfinished ("" when all done)."""

    done: int
    total: int
    next: str


class DoctorMissingItem(BaseModel):
    """One thing the profile lacks: a path-like ``key`` and a plain ``why``."""

    key: str
    why: str


class DoctorOutreach(BaseModel):
    """Today's outreach (the user's own day): ``sent_today`` recorded sends,
    ``daily_max`` the cap check_outreach applies, ``blocked_today`` how many
    check_outreach answers today were ``stop``."""

    sent_today: int
    daily_max: int
    blocked_today: int


class AutoEmailCheckResponse(BaseModel):
    """``GET /api/applications/{id}/auto-email-check`` - the gate before an automatic employer email.
    ``send`` only means the gate is open; ``due_since`` is "" when the kind has no starting event."""

    decision: str
    reason: str
    detail: str
    sent_today: int
    daily_max: int
    due_since: str = ""


class DoctorAutoEmailLast(BaseModel):
    event_id: int
    application_id: int
    company: str = ""
    kind: str
    at: str


class DoctorAutoEmailDue(BaseModel):
    application_id: int
    kind: str
    due_since: str = ""


class DoctorAutoEmailViolation(BaseModel):
    event_id: int
    application_id: int
    rule: str


class DoctorAutoEmails(BaseModel):
    """Automatic employer emails, watched: the switch, today's count, the last sends (company, never the
    address), what is due, and any send that broke a guardrail."""

    on: bool = False
    sent_today: int = 0
    daily_max: int = 0
    last: list[DoctorAutoEmailLast] = []
    due: list[DoctorAutoEmailDue] = []
    violations: list[DoctorAutoEmailViolation] = []
    stop_hint: str = ""


SILENT_DISCLAIMER = (
    "Elapsed time only \u2014 not legal advice or a claim that the employer did anything wrong."
)


class DoctorSilentItem(BaseModel):
    """One interview-stage application with no word since its last interview_done."""

    application_id: int
    company: str
    job_title: str
    last_interview_event_id: int
    last_interview_at: str
    days_silent: int
    confirmed: bool


class InterviewAgendaItem(BaseModel):
    """One live interview in the agenda read (services/applications/agenda.py)."""

    application_id: int
    company: str
    title: str
    scheduled_at: str
    round: str = ""
    link: str = ""
    duration_minutes: int = 60


class InterviewAgenda(BaseModel):
    """``upcoming`` = live interviews in the next 7 days; ``just_passed`` = those of the last 3 days whose
    application has no outcome event yet (ask the user how it went)."""

    upcoming: list[InterviewAgendaItem] = []
    just_passed: list[InterviewAgendaItem] = []


class DoctorSilentBlock(BaseModel):
    """Feedback loop slice 4: interviews with silence past ``courtesy_days`` (read-only)."""

    courtesy_days: int = 30
    items: list[DoctorSilentItem] = []
    disclaimer: str = SILENT_DISCLAIMER
    # True when the event cap was hit (the OLDEST events were left out).
    truncated: bool = False


class DoctorResponse(BaseModel):
    """``GET /api/doctor`` - one read of "what state is this account in, and
    what should the assistant do next?". Every field is built defensively: a
    user with no profile gets ``profile_exists: false``, never a 404."""

    auth: DoctorAuth
    email_verified: bool
    profile_exists: bool
    setup: DoctorSetup
    missing: list[DoctorMissingItem]
    # TODO(feature 9 - confirmed vs inferred facts): add `unconfirmed` (count) once that branch lands.
    paused: bool
    paused_until: str
    open_asks: int
    waiting_settings: int
    latest_event_recorded_at: Optional[str] = None
    whats_new_hint: str
    outreach: DoctorOutreach
    next_step: str
    # Application ids this very call marked `ghosted` (no answer after GHOSTED_AFTER_DAYS).
    ghosted_now: list[int] = []
    auto_emails: DoctorAutoEmails = DoctorAutoEmails()
    silent_after_interview: DoctorSilentBlock = DoctorSilentBlock()
    interviews: InterviewAgenda = InterviewAgenda()
    # The MCP tool list as served now: {tool: [parameter names]} and its 12-hex
    # hash. `tools_stale` is null unless the caller passed `client_tools_version`.
    tools_version: str = ""
    tools_rev: int = 0
    tools: dict[str, list[str]] = {}
    tools_stale: Optional[bool] = None
    stale_hint: str = ""


class MissingToolRequest(BaseModel):
    """``POST /api/missing-tools`` body. ``name`` must match
    ``^[a-z][a-z0-9_]{1,59}$``; ``why`` is at most 280 characters."""

    model_config = ConfigDict(extra="forbid")

    name: str
    why: str = ""


class FormQuestion(BaseModel):
    """One question read off a LIVE application form. ``key`` is the kit key the
    assistant plans to answer it with (``contact.email``, ``logistics.notice_period``,
    or the saved ``approved_text`` question)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=500)
    required: bool = True
    key: Optional[str] = Field(None, max_length=300)
    # The control's own facts, read off the live form by the assistant (never guessed here).
    field_type: Literal["text", "textarea", "select", "radio", "checkbox", "number", "file", "unknown"] = "unknown"
    options: list[str] = Field(default_factory=list, max_length=50)
    limit: Optional[int] = Field(None, ge=1, le=100000)
    limit_unit: Literal["chars", "words"] = "chars"

    @field_validator("options")
    @classmethod
    def _options_short(cls, value: list[str]) -> list[str]:
        if any(len(o) > 200 for o in value):
            raise ValueError("each option must be at most 200 characters")
        return value


class FormCheckRequest(BaseModel):
    """``POST /applications/{id}/kit/form-check`` body: every question on the live form."""

    model_config = ConfigDict(extra="forbid")

    form_url: str = Field("", max_length=2000)
    questions: list[FormQuestion] = Field(min_length=1, max_length=80)


class FormAnsweredOut(BaseModel):
    question: str
    required: bool
    key: str
    value: Any
    source: str
    confirmed: Optional[bool] = None
    matched_by: Literal["key", "wording", "topic"]
    field_type: Optional[str] = None
    over_limit: Optional[bool] = None
    needs_confirmation: Optional[bool] = None


class FormMissingOut(BaseModel):
    question: str
    required: bool
    why: str
    field_type: Optional[str] = None
    needs_confirmation: Optional[bool] = None


class FormKnockoutOut(BaseModel):
    question: str
    key: Optional[str] = None
    value: Any = None


class FormCheckCountsOut(BaseModel):
    answered: int
    missing: int
    missing_required: int


class FormCheckOut(BaseModel):
    """For every question on the live form: a saved answer from this application's
    kit, or a gap to ask the user about. Job360 only looks up; the assistant reads
    the form and decides."""

    application_id: int
    questions_count: int
    answered: list[FormAnsweredOut]
    missing: list[FormMissingOut]
    counts: FormCheckCountsOut
    needs_confirmation: list[str] = Field(default_factory=list)
    knockouts: list[FormKnockoutOut] = Field(default_factory=list)
    stop_for_user: Optional[bool] = None
    next: str


class MissingToolResponse(BaseModel):
    id: int


