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


class SourceInfo(BaseModel):
    name: str
    type: str
    health: dict[Any, Any]


class SourcesResponse(BaseModel):
    sources: list[SourceInfo]


class StatusResponse(BaseModel):
    jobs_total: int
    last_run: Optional[dict[Any, Any]]
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
    # THREE-state visa fact: "sponsors" | "no_sponsorship" | "unknown".
    # `visa_flag` above is a bool and therefore cannot distinguish "this ad
    # says it will NOT sponsor" from "this ad never mentions visas" — opposite
    # facts for a candidate who needs sponsorship (a dead end vs a question
    # worth asking). Product rule #31: visa is a SPOTLIGHT, not a wall — the
    # UI badges all three states and never hides the 58% that are unknown.
    visa_status: str = "unknown"
    job_type: str = ""
    experience_level: str = ""
    # ── Score-dim breakdown ─────────────────────────────────────────────
    # These are the EIGHT dimensions the engine actually computes, with the
    # weights it actually uses (skill_matcher.TITLE/SKILL/LOCATION/RECENCY_WEIGHT
    # = 40/40/10/10 and settings.{SENIORITY,SALARY,VISA,WORKPLACE}_WEIGHT =
    # 8/10/6/6). `role`/`skill`/`location_score`/`recency`/`seniority_score`
    # are ALSO persisted on `jobs` (migration 0011); salary/visa/workplace are
    # computed per-request only — they depend on the CALLER's preferences, so
    # persisting them on the shared catalog would leak one user's salary target
    # into another user's row (rules #10/#17).
    #
    # NEVER sum these to derive the total: the raw max is 130 clamped to 100
    # (rule #27), and the title/location penalties (−30/−15) land on no
    # dimension at all. `match_score` is the only truth for the total.
    role: int = 0
    skill: int = 0
    location_score: int = 0
    recency: int = 0
    seniority_score: int = 0
    salary_score: int = 0
    visa_score: int = 0
    workplace_score: int = 0
    # Did the enrichment-driven dims (seniority/salary/visa/workplace) actually
    # run for this job+user? MUST be explicit — a 0 is ambiguous: it can mean
    # "not measured" OR a real, earned 0 (e.g. visa_score=0 because the caller
    # needs sponsorship and this job offers none). The UI greys the four dims
    # out when this is False instead of drawing a truthful-looking 0%.
    dims_active: bool = False
    # Legacy dead columns (migration 0011) — the engine never produced these.
    # Kept only so existing consumers don't break; scheduled for removal.
    experience: int = 0
    credentials: int = 0
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
    # Funnel->judge (LLM matcher) — per-user verdict from user_feed. None for
    # unauthenticated reads, unjudged jobs, or MATCHER_ENABLED=false.
    llm_fit_score: Optional[int] = None
    llm_verdict: Optional[str] = None
    llm_reason: Optional[str] = None
    # Application deadline — None means "no deadline listed".
    deadline: Optional[str] = None           # ISO date YYYY-MM-DD
    deadline_source: Optional[str] = None    # "listing" | "description" | None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    # Rows before min_score was applied. `total - total_unfiltered` is what the
    # filter removed, which the UI shows as "N hidden by filters". Without it a
    # filter tuned above the score distribution looks identical to an empty feed —
    # exactly how 2,830 of one user's 3,236 jobs vanished with nothing on screen
    # to say so. Defaults to 0 for back-compat with older clients.
    total_unfiltered: int = 0
    filters_applied: dict[Any, Any]


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
    # ``user_declared``). Computed from the SkillEntry merge — empty
    # when the profile has no skills.
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
    result: Optional[dict[Any, Any]] = None


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
