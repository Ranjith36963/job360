"""Pydantic models for Job360 FastAPI backend."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    version: str


class SourceInfo(BaseModel):
    name: str
    type: str
    health: dict


class SourcesResponse(BaseModel):
    sources: list[SourceInfo]


class StatusResponse(BaseModel):
    jobs_total: int
    last_run: Optional[dict]
    sources_active: int
    sources_total: int
    profile_exists: bool


class JobResponse(BaseModel):
    id: int
    title: str
    company: str
    location: str
    salary: Optional[str]
    match_score: int
    source: str
    date_found: str
    apply_url: str
    visa_flag: bool
    job_type: str = ""
    experience_level: str = ""
    # Score-dim breakdown (Pillar 2 Batch 2.9). Step-1.5 S1.1 wired the
    # 9 columns end-to-end (migration 0011 → Job dataclass → main.py
    # capture → insert_job → _row_to_job_response). `role`/`skill`/
    # `location_score`/`recency`/`seniority_score` carry their respective
    # ScoreBreakdown component each run; the remaining four
    # (experience/credentials/semantic/penalty) persist as 0 until the
    # engine starts producing those signals — see CLAUDE.md rule #21.
    role: int = 0
    skill: int = 0
    seniority_score: int = 0
    experience: int = 0
    credentials: int = 0
    location_score: int = 0
    recency: int = 0
    semantic: int = 0
    penalty: int = 0
    matched_skills: list[str] = []
    missing_required: list[str] = []
    transferable_skills: list[str] = []
    action: Optional[str] = None
    bucket: str = ""
    # Step-1 B6 — date-model fields (Pillar 3 Batch 1). Persisted on the
    # `jobs` table; `posted_at` is None when no trustworthy source field
    # was found, `staleness_state` flips to 'stale' / 'expired' as the
    # ghost detector runs. Frontend lib/types.ts must mirror these.
    posted_at: Optional[str] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    date_confidence: Optional[str] = None
    staleness_state: Optional[str] = None
    # Step-1 B6 — enrichment fields (Pillar 2 Batch 2.5 subset). Sourced
    # from the `job_enrichment` row via a LEFT JOIN — None when no row
    # exists. Mirrors a 13-of-18 user-facing slice of `JobEnrichment`.
    title_canonical: Optional[str] = None
    seniority: Optional[str] = None
    employment_type: Optional[str] = None
    workplace_type: Optional[str] = None
    visa_sponsorship: Optional[bool] = None
    salary_min_gbp: Optional[int] = None
    salary_max_gbp: Optional[int] = None
    salary_period: Optional[str] = None
    salary_currency_original: Optional[str] = None
    required_skills: Optional[list[str]] = None
    nice_to_have_skills: Optional[list[str]] = None
    industry: Optional[str] = None
    years_experience_min: Optional[int] = None
    # Step-1.5 S3-F — surface the "also posted on Indeed + Reed" badge
    # ID list. Optional because the dedup-group writer is deferred to a
    # follow-up batch (see plan §non-scope). Defaults to None today; the
    # frontend renders a fallback "no group info" badge until populated.
    dedup_group_ids: Optional[list[int]] = None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    filters_applied: dict


class ActionRequest(BaseModel):
    action: str
    notes: str = ""


class ActionResponse(BaseModel):
    ok: bool
    job_id: int
    action: str


class ActionsListResponse(BaseModel):
    actions: list[ActionResponse]


class ProfileSummary(BaseModel):
    is_complete: bool
    job_titles: list[str]
    skills_count: int
    cv_length: int
    has_linkedin: bool
    has_github: bool
    education: list[str]
    experience_level: str


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
    # Aggregated highlights for the CV viewer — merges skills + titles +
    # companies + achievements + name/headline/location for in-text highlighting
    highlights: list[str] = []


class ProfileResponse(BaseModel):
    summary: ProfileSummary
    preferences: dict
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
    # ``user_declared``). Computed from the SkillEntry merge — empty
    # when the profile has no skills.
    skill_provenance: dict[str, list[str]] = {}
    # Step-1.5 S3-E — LinkedIn sub-sections for the profile detail UI.
    # Each value is the raw list of dicts as parsed by
    # ``services.profile.linkedin_parser`` — see CVData fields with the
    # same names. UI flattens for display; backend keeps the raw shape
    # so callers can format independently.
    linkedin_subsections: dict[str, list[dict]] = {}
    # Step-1.5 S3-E — GitHub temporal data: per-language byte counts
    # (top-K by volume) + topic frequencies. Pure metric surface — UI
    # renders trend graphs without backend re-shaping.
    github_temporal: dict[str, dict] = {}
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
    cv_data: dict
    preferences: dict


class ProfileVersionsListResponse(BaseModel):
    """``GET /profile/versions`` body wrapper."""

    versions: list[ProfileVersionSummary]
    total: int


class JsonResumeResponse(BaseModel):
    """``GET /profile/json-resume`` body. Wraps the canonical JSON Resume
    dict (https://jsonresume.org/schema/) under a ``resume`` key so the
    response is a JSON object, not a bare list."""

    resume: dict


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


class DedupGroupSummary(BaseModel):
    """``GET /jobs/{id}/dedup-group`` shape — placeholder for the upcoming
    dedup-group writer batch. Not exposed by any route in Step 1.5; the
    model is shipped now so the frontend agent can wire the type-safe
    consumer in Step 2 without a Pydantic round-trip change later."""

    group_id: int
    job_ids: list[int]
    canonical_job_id: int


class LinkedInResponse(BaseModel):
    ok: bool
    merged: bool


class GitHubResponse(BaseModel):
    ok: bool
    merged: bool


class SearchStartResponse(BaseModel):
    run_id: str
    status: str


class SearchStatusResponse(BaseModel):
    run_id: str
    status: str
    progress: str
    result: Optional[dict] = None


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


# ── Step-3 B-02 — Notification rules (per-user per-channel preferences) ──


class NotificationRule(BaseModel):
    id: int
    user_id: str
    channel: str
    score_threshold: int
    notify_mode: str
    quiet_hours_start: Optional[str]
    quiet_hours_end: Optional[str]
    digest_send_time: Optional[str]
    enabled: bool
    created_at: str
    updated_at: str


class NotificationRuleCreate(BaseModel):
    channel: str
    score_threshold: int = Field(default=60, ge=0, le=100)
    notify_mode: str = Field(default="instant", pattern="^(instant|digest)$")
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    digest_send_time: Optional[str] = "08:00"
    enabled: bool = True


class NotificationRuleUpdate(BaseModel):
    score_threshold: Optional[int] = Field(None, ge=0, le=100)
    notify_mode: Optional[str] = Field(None, pattern="^(instant|digest)$")
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    digest_send_time: Optional[str] = None
    enabled: Optional[bool] = None


class NotificationRuleListResponse(BaseModel):
    rules: list[NotificationRule]
