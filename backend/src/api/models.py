"""Pydantic models for Job360 FastAPI backend."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    version: str


class LivezResponse(BaseModel):
    status: str


class ReadyzChecks(BaseModel):
    db: str
    redis: str


class ReadyzResponse(BaseModel):
    status: str
    checks: ReadyzChecks


class StatusResponse(BaseModel):
    jobs_total: int
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
    visa_flag: bool
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
    # AI-SUGGESTED adjacent skills (neighbours of what the user has). SUGGESTIONS
    # only — the user opts in; never counted in tiering/scoring/matching.
    ai_suggestions: list[str] = []
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
    # LLM-read skills, bio, profile README. Measured 2026-08-09: 92 pieces of
    # this were stored and rendered NOWHERE while only languages+topics showed.
    # Defaults to {} so every existing caller and older profile stays valid.
    github_detail: dict[str, Any] = {}
    # Step-1.5 S3-E — newest snapshot id from ``user_profile_versions``;
    # surfaces "current version" alongside the history list. None when
    # the version table is empty / unavailable.
    current_version_id: Optional[int] = None


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


class NotificationLedgerEntry(BaseModel):
    """One row of ``notification_ledger`` exposed via the API. ``body`` is
    intentionally absent — Step-1.5 plan §non-scope defers the schema
    column for notification message bodies to a follow-up batch."""

    id: int
    job_id: int
    channel: str
    status: str
    sent_at: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: str


class NotificationLedgerListResponse(BaseModel):
    """Paginated ``GET /notifications`` body."""

    notifications: list[NotificationLedgerEntry]
    total: int
    limit: int
    offset: int


class LinkedInResponse(BaseModel):
    ok: bool
    merged: bool


class GitHubResponse(BaseModel):
    ok: bool
    merged: bool


class PipelineApplication(BaseModel):
    job_id: int
    stage: str
    created_at: str
    updated_at: str
    notes: str = ""
    title: str = ""
    company: str = ""


class PipelineListResponse(BaseModel):
    applications: list[PipelineApplication]


class PipelineAdvanceRequest(BaseModel):
    stage: str


class PipelineRemindersResponse(BaseModel):
    reminders: list[PipelineApplication]


# ── Step-3 B-07 — Application timeline models ──────────────────────────────


class TimelineEntry(BaseModel):
    id: int
    job_id: int
    user_id: str
    from_stage: Optional[str]
    to_stage: str
    transitioned_at: str
    notes: Optional[str]


class ApplicationTimelineResponse(BaseModel):
    job_id: int
    timeline: list[TimelineEntry]


# ── Single-rulebook notification model (migration 0020) ──────────────────────
# One rule per user, applies to ALL connected channels.

# HH:MM (24-hour) regex for time fields.
_HHMM_PATTERN = r"^(?:[01]\d|2[0-3]):[0-5]\d$"


class NotificationRule(BaseModel):
    user_id: str
    score_threshold: int = 60
    notify_mode: str = "instant"  # instant | daily | every_n_hours
    interval_hours: int = 6
    daily_send_time: str = "08:00"
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    last_sent_at: Optional[str] = None
    enabled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class NotificationRuleUpdate(BaseModel):
    score_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    notify_mode: Optional[str] = Field(default=None, pattern="^(instant|daily|every_n_hours)$")
    interval_hours: Optional[int] = Field(default=None, ge=1, le=24)
    daily_send_time: Optional[str] = Field(default=None, pattern=_HHMM_PATTERN)
    quiet_hours_start: Optional[str] = Field(default=None, pattern=_HHMM_PATTERN)
    quiet_hours_end: Optional[str] = Field(default=None, pattern=_HHMM_PATTERN)
    enabled: Optional[bool] = None
