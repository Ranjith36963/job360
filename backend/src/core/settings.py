import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Root logging level — read once at import so every entrypoint (FastAPI,
# CLI) sees the same value. Tier-A Step-0 pre-flight #9.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
# DB_PATH is retained as the single "connection selector" the codebase passes
# around. Post-Postgres migration it no longer points at a real file — it is a
# schema selector (production always resolves to the ``public`` schema). Kept as
# a Path so every ``from src.core.settings import DB_PATH`` importer and every
# conftest ``monkeypatch.setattr(mod, "DB_PATH", ...)`` keeps working unchanged.
DB_PATH = DATA_DIR / "jobs.db"

# PostgreSQL connection string (psycopg3). Single source of truth for the DB
# connection. Defaults to the local dev Postgres (docker-compose.dev.yml, host
# port 5433). Override via the DATABASE_URL env var in prod.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://job360:job360dev@localhost:5433/job360"
)

# Point the psycopg helper at the configured DSN (import kept local to avoid a
# heavy import at settings load; pg only depends on psycopg).
from src.repositories import pg as _pg  # noqa: E402

_pg.configure(DATABASE_URL)
EXPORTS_DIR = DATA_DIR / "exports"
REPORTS_DIR = DATA_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"
METRICS_DIR = DATA_DIR / "metrics"


def _secret(name: str) -> str:
    """Read a credential, stripping surrounding whitespace.

    Secrets are pasted into a dashboard by hand, so a trailing newline or space
    rides along more often than anyone expects. It is invisible in every UI and
    fatal wherever the value is concatenated rather than form-encoded — a key
    in a URL path or a header becomes a DIFFERENT key, and the upstream answers
    401 or 403, which reads exactly like "the key is wrong" rather than "the
    key has a space on the end". Measured on production 2026-08-24 on two live
    keys at once.
    """
    return os.getenv(name, "").strip()


# GitHub (optional — for higher rate limits on profile enrichment)
# Accepts either name. Measured 2026-08-24 across all four environments: the
# developer's .env defines GITHUB_TOKEN, while BOTH Railway services define
# GITHUB_PERSONAL_ACCESS_TOKEN — so `os.getenv("GITHUB_TOKEN")` found nothing in
# production and GitHub profile enrichment ran unauthenticated at 60 requests/hour
# instead of 5,000, silently, because an absent token is a supported state.
#
# Reading both is the fix that cannot drift: renaming one side would leave the
# other environment broken until someone noticed, and nobody noticed this one.
GITHUB_TOKEN = _secret("GITHUB_TOKEN") or _secret("GITHUB_PERSONAL_ACCESS_TOKEN")

# LLM providers for CV analysis.
# OpenAI (paid) is the PRIMARY — reliable quota, deterministic (temp 0), structured
# output. The free tiers (Gemini/Groq/Cerebras) remain as fallbacks. Key is read
# case-insensitively so a lowercase `openai_api_key=` in .env still works.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "") or os.getenv("openai_api_key", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Env-overridable for the same reason OPENAI_MODEL is. It was HARDCODED as
# "gemini-2.0-flash" in llm_provider.py, and Google retired that model: prod
# Sentry PYTHON-FASTAPI-J, "404 This model models/gemini-2.0-flash is no longer
# available", last seen 2026-08-11. A hardcoded model name is a dependency on
# someone else's release schedule with no way to respond except a deploy —
# which is why the fallback chain was dead and nobody noticed.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")

# Email
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")

# NOTE (2026-08-24) — the per-user Slack / Discord / Telegram delivery channels
# were removed. Nine settings lived here for them: SLACK_WEBHOOK_URL,
# DISCORD_WEBHOOK_URL (which had ZERO consumers anywhere in the codebase — dead
# on arrival), SLACK_CLIENT_ID/SECRET, DISCORD_CLIENT_ID/SECRET,
# TELEGRAM_BOT_TOKEN/USERNAME, and OAUTH_REDIRECT_BASE, which existed solely to
# build the Slack and Discord OAuth redirect URIs.
#
# Delivery is email + webhook only. See
# docs/plans/2026-08-24-email-webhook-only-delivery.md
#
# Do NOT confuse these with the CI/harness alerting secrets of the same family
# (SLACK_BOT_TOKEN, and the repo-level SLACK_WEBHOOK_URL used by
# .github/actions/slack). Those page the owner when a build breaks, are
# configured as GitHub repo secrets rather than app settings, and are untouched.

# Where a link in a delivered notification points. Every job link in an email
# is built from this, never from the employer's raw ``apply_url`` — the click
# has to land on our page so we can (a) attribute it, (b) say "this closed"
# instead of dumping the reader on a 404, and (c) not look like the scam mail
# job seekers are drowning in.
#
# A PARAMETER, not a hardcode: staging, preview deploys and local dev all need
# a different origin, and a link that silently points at production from a
# staging send is a real way to mislead a real person.
# NORMALISE FIRST, fall back LAST. This got written wrong three times, each a
# narrower version of the same mistake — every one of which would have shipped
# job links with no origin ("/jobs/147" in someone's inbox, resolving to
# nothing):
#   1. `os.getenv("SITE_BASE_URL", default)` — a getenv default never fires for
#      a var that is SET BUT EMPTY, which is what a blank Railway variable or a
#      bare `SITE_BASE_URL=` line in .env produces.
#   2. `os.getenv(...) or default` — closes (1), but `" "` is truthy, so the
#      fallback is skipped and the later .strip() empties it again.
#   3. `(...strip() or default).rstrip("/")` — closes (2), but `"/"` and `"///"`
#      are truthy AND survive the fallback, then rstrip turns them into "".
# The fix is to do every transformation the value needs, and only then decide
# whether what is left is usable. Guard: tests/test_site_base_url.py.
_SITE_BASE_URL_RAW = os.getenv("SITE_BASE_URL", "").strip().rstrip("/")
SITE_BASE_URL = _SITE_BASE_URL_RAW or "https://job360.uk"
# True when the operator set SITE_BASE_URL. The OAuth discovery documents
# advertise SITE_BASE_URL as the issuer, so a non-prod instance running on
# the default is misconfigured — api.main.lifespan warns once at boot.
SITE_BASE_URL_IS_EXPLICIT = bool(_SITE_BASE_URL_RAW)

# The `jobs.source` value for an ad the user (or their agent) brought —
# `POST /jobs/bring`. Since slice 5 (#483) it is the ONLY value any new row
# ever gets: nothing else writes to `jobs`.
USER_BROUGHT_SOURCE = "user_brought"
# Brute-force login lockout (LAUNCH_PLAN Phase 2 #10). After this many FAILED
# logins for one email within the window, /api/auth/login returns HTTP 429
# until the burst ages out. In-memory (services/auth/rate_limit.py); Redis swap
# is Phase 3.
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_WINDOW_SECONDS = int(os.getenv("LOGIN_LOCKOUT_WINDOW_SECONDS", "900"))

# docs/fable/01 — cross-instance rate limiting. The default limiter (services/
# auth/rate_limit.py) keeps its sliding windows in-process, so on a multi-replica
# deploy each replica counts independently and an attacker gets N× the intended
# budget. Setting RATE_LIMIT_REDIS=true routes the SAME limiter through Redis
# (shared across replicas). Default OFF keeps behaviour byte-identical to the
# in-memory path — matching the "flags default off" discipline (rules #18/#24) —
# and the Redis path always FALLS BACK to in-memory on any Redis error, so a
# Redis outage degrades to today's behaviour rather than failing open/closed.
# Requires REDIS_URL to be set; ignored otherwise.
RATE_LIMIT_REDIS = os.getenv("RATE_LIMIT_REDIS", "false").lower() in ("1", "true", "yes")

# Per-User AI CV & Cover Letter (docs/product/peruser_cv_coverletter.md) — guardrail #1:
# each generation is a paid LLM call, so cap free usage. One generation = one CV +
# cover letter for one job. When a real premium plan lands, premium users bypass this
# cap (no plan column exists yet — everyone is on the free cap for now).
TAILOR_FREE_PER_MONTH = int(os.getenv("TAILOR_FREE_PER_MONTH", "10"))

# ESCO skill normalisation (services/profile/skill_normalizer.py) — OFF by
# default. Replaces `SEMANTIC_ENABLED`, which gated two unrelated things: the
# job side's embeddings/ChromaDB retrieval (deleted with the sourcing era,
# slice 5 #483) and this profile-side skill canonicaliser. Only the second
# still exists, so the flag is named after it.
#
# Renaming it changes no behaviour: hard rule #28 records that the ESCO index
# has never been built or shipped, so `skill_normalizer.is_available()` is the
# real gate and this flag alone turns nothing on. The old name is deliberately
# NOT read as a fallback — a dead env var that still does something is exactly
# what this slice deletes.
ESCO_SKILL_NORMALISATION_ENABLED = os.getenv(
    "ESCO_SKILL_NORMALISATION_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}

# Cost cap on profile re-extraction (docs/fable/08 "Cost economics — NOT audited").
# Every profile change re-runs the FULL two-pass extraction — 4+ paid LLM calls
# over CV / LinkedIn / GitHub / about_me — from stored data. Nothing bounded how
# often a user could trigger that, and five routes reach the same code path.
#
# 12/hour is deliberately generous: a person genuinely setting up a profile edits
# it a handful of times in a sitting, so this should never be felt by a real user.
# It exists to stop a loop (or a bored user) from running up a bill.
#
# Set 0 to disable the cap entirely.
PROFILE_EXTRACT_MAX_PER_HOUR = int(os.getenv("PROFILE_EXTRACT_MAX_PER_HOUR", "12"))

# Personal API tokens + MCP (docs/plans/2026-09-03-mcp-server). Read through
# `settings.X` at call time, never bound at import — tests monkeypatch them.
# Active (unrevoked) tokens one user may hold. 10 = one per client/machine.
API_TOKENS_PER_USER = int(os.getenv("API_TOKENS_PER_USER", "10"))
# Failed bearer attempts per client IP per minute before 429 (brute-force brake;
# the token itself is 256-bit random, this just makes guessing loud and slow).
API_TOKEN_FAIL_MAX_PER_MIN = int(os.getenv("API_TOKEN_FAIL_MAX_PER_MIN", "30"))
# Comma-separated Host values the MCP transport accepts (DNS-rebinding guard).
# Empty = off: the backend sits behind the Next rewrite, so the Host header is
# Railway's internal name, not job360.uk. The bearer token is the real guard.
MCP_ALLOWED_HOSTS = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]

# OAuth 2.1 authorization server for MCP clients (docs/plans/2026-09-03-oauth-mcp).
# Read through `settings.X` at call time, never bound at import — tests monkeypatch them.
#
# S3 — host-anchored redirect allow-list. Comma-separated full URLs; a
# candidate is accepted when scheme+host+port match an entry AND the path is
# byte-equal (or the entry path ends in "/" and the candidate starts with it).
# Adding a client = editing this var, no deploy.
OAUTH_REDIRECT_ALLOWLIST = os.getenv(
    "OAUTH_REDIRECT_ALLOWLIST",
    "https://claude.ai/api/mcp/auth_callback,"
    "https://chatgpt.com/connector_platform_oauth_redirect,"
    "https://chatgpt.com/connector/oauth/",
)
# RFC 8252 loopback redirects (127.0.0.1 / ::1 / localhost, scheme http, any
# port). Defaults OFF — nothing in production needs it (Claude Code keeps a
# personal token; intent.md).
OAUTH_ALLOW_LOOPBACK_REDIRECTS = os.getenv("OAUTH_ALLOW_LOOPBACK_REDIRECTS", "0").lower() in (
    "1", "true", "yes", "on",
)
# Token/code/request lifetimes, all in seconds.
OAUTH_ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("OAUTH_ACCESS_TOKEN_TTL_SECONDS", "3600"))
OAUTH_REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("OAUTH_REFRESH_TOKEN_TTL_SECONDS", "2592000"))
OAUTH_CODE_TTL_SECONDS = int(os.getenv("OAUTH_CODE_TTL_SECONDS", "60"))
# Long enough for the magic-link email round-trip between /authorize and consent.
OAUTH_AUTHORIZE_TTL_SECONDS = int(os.getenv("OAUTH_AUTHORIZE_TTL_SECONDS", "1800"))
# A code/refresh token reused within this window after being consumed is a
# client timeout-and-retry (Claude.ai retries ~10s later), not an attack —
# `invalid_grant` without revoking. Reuse AFTER the window revokes the grant.
OAUTH_REUSE_GRACE_SECONDS = int(os.getenv("OAUTH_REUSE_GRACE_SECONDS", "10"))
# Registration is unauthenticated, so it is rate-limited per IP and globally
# (Claude.ai re-registers a fresh client per connection).
OAUTH_REGISTER_MAX_PER_HOUR = int(os.getenv("OAUTH_REGISTER_MAX_PER_HOUR", "60"))
OAUTH_REGISTER_MAX_PER_HOUR_GLOBAL = int(os.getenv("OAUTH_REGISTER_MAX_PER_HOUR_GLOBAL", "600"))
OAUTH_AUTHORIZE_MAX_PER_MIN = int(os.getenv("OAUTH_AUTHORIZE_MAX_PER_MIN", "60"))
# /token failures, keyed unconditionally on IP (a missing/unknown client_id
# cannot dodge the counter by varying client_id).
OAUTH_TOKEN_FAIL_MAX_PER_MIN = int(os.getenv("OAUTH_TOKEN_FAIL_MAX_PER_MIN", "30"))
# Bearer failures on a `j360a_` credential — its own bucket, never api_tokens'.
OAUTH_BEARER_FAIL_MAX_PER_MIN = int(os.getenv("OAUTH_BEARER_FAIL_MAX_PER_MIN", "30"))
# Housekeeping (R10): a client with no oauth_grants row at all (revoked
# included) older than this many days is prune-eligible.
OAUTH_CLIENT_PRUNE_DAYS = int(os.getenv("OAUTH_CLIENT_PRUNE_DAYS", "7"))
# S7 — bounded client table. At/above this count, /register first prunes the
# oldest grant-less clients to make room before ever refusing a real client.
OAUTH_MAX_CLIENTS = int(os.getenv("OAUTH_MAX_CLIENTS", "10000"))
# Housekeeping runs sampled 1-in-N token-endpoint calls, after the response is
# computed, so a slow prune can never delay a token exchange (Claude.ai's 10s
# budget). `1` = every call; `0` disables housekeeping entirely.
OAUTH_PRUNE_SAMPLE = int(os.getenv("OAUTH_PRUNE_SAMPLE", "20"))

def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean env var. Unset -> ``default``. Accepts 1/true/yes/on."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Comma-separated env var -> tuple of non-empty, stripped strings."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(v.strip() for v in raw.split(",") if v.strip())


# ---------------------------------------------------------------------------
# The application spine (docs/plans/2026-09-04-application-spine/spec.md).
#
# R7 — the event vocabulary. Closed set in CODE, not a DB CHECK constraint, so
# a new event type is a settings.py edit, never a migration (constraint 5).
# `brought` maps to status `considering`; every other status event's NAME is
# the status itself (src/services/applications/status.py's mapping dict).
# A frozen test (test_event_types_match_vision_doc) pins these two tuples to
# docs/product/VISION.md:66-71 so doc and code cannot drift apart silently.
APPLICATION_STATUS_EVENT_TYPES = (
    "brought", "applied", "replied", "interview_requested",
    "interview_scheduled", "interview_done", "offer", "rejected",
    "withdrawn", "ghosted",
)
APPLICATION_NOTE_EVENT_TYPES = (
    "fit_judged", "artifact_saved", "contact_added", "outreach_sent", "note", "lesson",
)
# Env-added types are non-status only — a status type also needs an R4
# mapping entry, which an env var cannot supply.
APPLICATION_EXTRA_EVENT_TYPES = _env_list("APPLICATION_EXTRA_EVENT_TYPES", ())

# S5 — input caps for the application spine. Every one a parameter; every
# breach a 422/429 naming the field and the limit, never a silent drop/clip.
APPLICATION_EVENT_DETAIL_MAX_CHARS = int(os.getenv("APPLICATION_EVENT_DETAIL_MAX_CHARS", "2000"))
APPLICATION_EVENT_PAYLOAD_MAX_BYTES = int(os.getenv("APPLICATION_EVENT_PAYLOAD_MAX_BYTES", "8192"))
# S6 — how far into the future `occurred_at` may claim to be before it is
# refused as implausible. No lower bound: backdating is the normal case.
APPLICATION_EVENT_MAX_FUTURE_SECONDS = int(os.getenv("APPLICATION_EVENT_MAX_FUTURE_SECONDS", "300"))

# R5 — artifact versions. Storage decision: TEXT in Postgres, not a file store
# (spec §Data model) — a tailored CV measured on this codebase is 2-8 KB.
APPLICATION_ARTIFACT_KINDS = ("cv", "cover_letter", "answers", "outreach")
APPLICATION_ARTIFACT_MAX_CHARS = int(os.getenv("APPLICATION_ARTIFACT_MAX_CHARS", "60000"))
APPLICATION_ARTIFACT_MAX_VERSIONS = int(os.getenv("APPLICATION_ARTIFACT_MAX_VERSIONS", "200"))

# R8 — record_application (the rich receipt).
APPLICATION_RECEIPT_ANSWERS_MAX = int(os.getenv("APPLICATION_RECEIPT_ANSWERS_MAX", "50"))
APPLICATION_RECEIPT_ANSWER_MAX_CHARS = int(os.getenv("APPLICATION_RECEIPT_ANSWER_MAX_CHARS", "2000"))
APPLICATION_RECEIPT_FIELDS_MAX_BYTES = int(os.getenv("APPLICATION_RECEIPT_FIELDS_MAX_BYTES", "8192"))

# R6 — the fit verdict is stored, never computed.
APPLICATION_FIT_REASONING_MAX_CHARS = int(os.getenv("APPLICATION_FIT_REASONING_MAX_CHARS", "4000"))

# S3 — how much of an OAuth client's attacker-supplied name `actor_for` keeps
# as `agent:<name>` authorship. Env-backed like every other spine cap (C8);
# was hardcoded in authorship.py before.
APPLICATION_ACTOR_NAME_MAX_CHARS = int(os.getenv("APPLICATION_ACTOR_NAME_MAX_CHARS", "60"))

# R9 — whats_new.
WHATS_NEW_DEFAULT_WINDOW_DAYS = int(os.getenv("WHATS_NEW_DEFAULT_WINDOW_DAYS", "7"))
WHATS_NEW_MAX_EVENTS = int(os.getenv("WHATS_NEW_MAX_EVENTS", "200"))

# R10/S8 — export_history: bounds explicit, never silent; rate-limited per USER
# (never per IP — every agent shares the proxy IP behind the Next rewrite
# unless JOB360_TRUST_PROXY=1, the trap the OAuth slice documented).
EXPORT_HISTORY_MAX_APPLICATIONS = int(os.getenv("EXPORT_HISTORY_MAX_APPLICATIONS", "500"))
EXPORT_HISTORY_MAX_BYTES = int(os.getenv("EXPORT_HISTORY_MAX_BYTES", str(8 * 1024 * 1024)))
EXPORT_HISTORY_MAX_PER_HOUR = int(os.getenv("EXPORT_HISTORY_MAX_PER_HOUR", "12"))
# Slice 4 review (S3) — the export's top-level `profile_edits` blob is bounded
# like everything else it carries: the NEWEST N rows, rendered oldest-first,
# with `profile_edits_truncated` saying when the tail was cut.
EXPORT_HISTORY_MAX_PROFILE_EDITS = int(os.getenv("EXPORT_HISTORY_MAX_PROFILE_EDITS", "500"))

# ── Slice 4 (docs/plans/2026-09-05-contacts-stats/spec.md) ─────────────────
# S4 — every cap a parameter; a breach is a 422 naming the field and the
# limit, never a silent clip.

# add_contact (R1/R2). Contacts are add-only rows on an application.
CONTACT_NAME_MAX_CHARS = int(os.getenv("CONTACT_NAME_MAX_CHARS", "200"))
CONTACT_ROLE_MAX_CHARS = int(os.getenv("CONTACT_ROLE_MAX_CHARS", "200"))
CONTACT_EMAIL_MAX_CHARS = int(os.getenv("CONTACT_EMAIL_MAX_CHARS", "254"))
CONTACT_LINKEDIN_URL_MAX_CHARS = int(os.getenv("CONTACT_LINKEDIN_URL_MAX_CHARS", "300"))
CONTACT_NOTES_MAX_CHARS = int(os.getenv("CONTACT_NOTES_MAX_CHARS", "2000"))
CONTACTS_PER_APPLICATION_MAX = int(os.getenv("CONTACTS_PER_APPLICATION_MAX", "50"))
CONTACTS_MAX_PER_HOUR = int(os.getenv("CONTACTS_MAX_PER_HOUR", "120"))  # S7 — per USER

# stats (R5/R6/R7). Counts over the event log, grouped by CV version and role.
STATS_MAX_GROUPS = int(os.getenv("STATS_MAX_GROUPS", "50"))
STATS_MAX_PER_HOUR = int(os.getenv("STATS_MAX_PER_HOUR", "60"))  # S7 — per USER
# S6 — the DRIVING query is bounded too, not just the group list: stats reads
# the newest N applications for the user and says so (`applications_truncated`).
# A user with more than this many applications gets an honest partial answer
# rather than an unbounded fetch.
STATS_MAX_APPLICATIONS = int(os.getenv("STATS_MAX_APPLICATIONS", "5000"))


# ── The event-type vocabulary has ONE home (review finding S4) ───────────────
# `APPLICATION_STATUS_EVENT_TYPES` above is it. Everything stats counts is
# DERIVED from that tuple here and bound as a query parameter downstream —
# `services/applications/stats.py` never re-types an event name as a SQL
# literal, so a rename in the vocabulary cannot leave a stale string behind in
# the counting code. Drift refuses to boot rather than counting silently wrong.
def _status_event(name: str) -> str:
    """One member of ``APPLICATION_STATUS_EVENT_TYPES``, or a startup error."""
    if name not in APPLICATION_STATUS_EVENT_TYPES:
        raise RuntimeError(
            f"stats names the event type {name!r}, which is not in "
            f"APPLICATION_STATUS_EVENT_TYPES {APPLICATION_STATUS_EVENT_TYPES}"
        )
    return name


STATS_APPLIED_EVENT_TYPE = _status_event("applied")
STATS_REPLIED_EVENT_TYPE = _status_event("replied")
STATS_OFFER_EVENT_TYPE = _status_event("offer")
STATS_REJECTED_EVENT_TYPE = _status_event("rejected")
# The event types that make an application count as "interview" — derived from
# the status vocabulary by prefix, never a second hand-typed list.
STATS_INTERVIEW_EVENT_TYPES = tuple(
    t for t in APPLICATION_STATUS_EVENT_TYPES if t.startswith("interview_")
)
if not STATS_INTERVIEW_EVENT_TYPES:  # pragma: no cover — a vocabulary edit would trip this
    raise RuntimeError(
        "APPLICATION_STATUS_EVENT_TYPES declares no interview_* event type; "
        "stats cannot compute an interview count"
    )

# update_profile (R8-R11). The overlay of agent edits over extraction.
# R9 — a CLOSED set of dotted paths into UserProfile. Each MUST be a declared
# dataclass field (S8); src/services/profile/edits.py asserts that at import.
PROFILE_EDITABLE_PATHS = (
    "cv_data.name", "cv_data.headline", "cv_data.location", "cv_data.summary",
    "cv_data.skills", "cv_data.job_titles", "cv_data.education", "cv_data.certifications",
    "cv_data.achievements", "cv_data.cv_right_to_work", "cv_data.cv_languages", "cv_data.links",
    "preferences.target_job_titles", "preferences.preferred_locations", "preferences.industries",
    "preferences.additional_skills", "preferences.excluded_skills", "preferences.negative_keywords",
    "preferences.salary_min", "preferences.salary_max", "preferences.work_arrangement",
    "preferences.experience_level", "preferences.about_me", "preferences.needs_visa",
)
# Env-added paths must ALSO be declared dataclass fields — an unknown one is a
# startup error, not an accepted path.
PROFILE_EXTRA_EDITABLE_PATHS = _env_list("PROFILE_EXTRA_EDITABLE_PATHS", ())
PROFILE_EDIT_MAX_PATHS_PER_CALL = int(os.getenv("PROFILE_EDIT_MAX_PATHS_PER_CALL", "30"))
PROFILE_EDIT_MAX_CHARS = int(os.getenv("PROFILE_EDIT_MAX_CHARS", "2000"))
PROFILE_EDIT_MAX_LIST_ITEMS = int(os.getenv("PROFILE_EDIT_MAX_LIST_ITEMS", "100"))
PROFILE_EDIT_MAX_ITEM_CHARS = int(os.getenv("PROFILE_EDIT_MAX_ITEM_CHARS", "200"))
PROFILE_EDIT_MAX_PER_HOUR = int(os.getenv("PROFILE_EDIT_MAX_PER_HOUR", "120"))  # S7 — per USER


# Outbound HTTP defaults. Kept through slice 5 (#483) on purpose: the URL
# fetcher (#496) and the channel dispatcher are the outbound callers now, and
# both need a timeout and an honest identifying agent string.
REQUEST_TIMEOUT = 30
USER_AGENT = "Job360/1.0 (+https://job360.uk)"

# Sentry error tracking (Phase 3). Empty string → Sentry is disabled (no-op
# at init time). Populate in production via the SENTRY_DSN env var.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# --- Production env validation --------------------------------------------
# These vars must be non-empty when running in production.
_REQUIRED_PROD_VARS = ["SESSION_SECRET", "CHANNEL_ENCRYPTION_KEY", "DATABASE_URL"]


# ---------------------------------------------------------------------------
# Bring-a-job field caps (docs/plans/2026-09-04-url-fetch/spec.md C4).
# Originally hand-typed module constants in api/routes/bring.py; moved here so
# src/services/fetch/extract.py (a SERVICE, no FastAPI) can read them without
# importing a route module — the only such import that existed in the
# backend. Bounds are guardrails, not product limits: a real ad is
# 500-8,000 chars; the cap stops a pasted PDF dump (or a hostile 50MB body)
# from becoming a catalog row every later read has to carry. Shared by the
# URL-fetch extraction ladder so the two caps never drift apart.
# ---------------------------------------------------------------------------
BRING_MAX_TEXT = int(os.getenv("BRING_MAX_TEXT", "40000"))
BRING_MAX_FIELD = int(os.getenv("BRING_MAX_FIELD", "300"))

# ---------------------------------------------------------------------------
# URL fetch on the web (docs/plans/2026-09-04-url-fetch/spec.md) — every cap
# a parameter (intent constraint 6), never a literal in guard.py/fetcher.py.
# ---------------------------------------------------------------------------

# The kill switch (R11). Default ON; flipping it OFF 404s the route on the
# next restart with no code deploy — the control that actually matters if
# the fetcher is ever implicated in an incident (the frontend's
# NEXT_PUBLIC_URL_FETCH_ENABLED only hides the button and needs a rebuild).
URL_FETCH_ENABLED = _env_flag("URL_FETCH_ENABLED", True)

# Decoded-body size cap (2 MiB) — checked BOTH on a declared Content-Length
# (refuse before reading) and while streaming (catches a lying/absent header
# and a decompression bomb, since the cap is on decoded bytes).
URL_FETCH_MAX_BYTES = int(os.getenv("URL_FETCH_MAX_BYTES", str(2 * 1024 * 1024)))
# Three independent time budgets (R7) — bytes, per-request time and
# whole-journey time are different attacks and need different ceilings.
URL_FETCH_TIMEOUT_S = int(os.getenv("URL_FETCH_TIMEOUT_S", "10"))
URL_FETCH_TOTAL_BUDGET_S = int(os.getenv("URL_FETCH_TOTAL_BUDGET_S", "20"))
URL_FETCH_EXTRACT_BUDGET_S = float(os.getenv("URL_FETCH_EXTRACT_BUDGET_S", "3"))
URL_FETCH_MAX_REDIRECTS = int(os.getenv("URL_FETCH_MAX_REDIRECTS", "5"))
# A7 — the heuristic extractor's container-frame stack never grows past this,
# so a pathologically nested document can't make our OWN algorithm blow up
# even though html.parser itself would happily keep streaming.
URL_FETCH_MAX_HTML_DEPTH = int(os.getenv("URL_FETCH_MAX_HTML_DEPTH", "200"))

# R9 — rate limits: two per-user buckets plus one GLOBAL bucket. Per USER,
# never per IP (every browser/agent shares the proxy address behind the Next
# rewrite unless JOB360_TRUST_PROXY=1 — the OAuth slice's own trap). The
# global bucket is what stops a handful of accounts turning Job360 into an
# open relay/scanner for the whole internet (A9) — it is not redundant with
# the per-address deny list.
URL_FETCH_MAX_PER_MINUTE = int(os.getenv("URL_FETCH_MAX_PER_MINUTE", "6"))
URL_FETCH_MAX_PER_HOUR = int(os.getenv("URL_FETCH_MAX_PER_HOUR", "60"))
URL_FETCH_MAX_PER_HOUR_GLOBAL = int(os.getenv("URL_FETCH_MAX_PER_HOUR_GLOBAL", "2000"))

# R7 — we never sniff content; a server's stated Content-Type decides
# unsupported_content vs. a bounded, tag-stripping, script-free parse.
URL_FETCH_ALLOWED_CONTENT_TYPES = _env_list(
    "URL_FETCH_ALLOWED_CONTENT_TYPES", ("text/html", "application/xhtml+xml")
)
# Constraint 8 — never pretend to be a browser. Names Job360 and says the
# fetch is user-initiated; beating a bot wall by lying about who we are is
# not a decision this header gets to make quietly.
URL_FETCH_USER_AGENT = os.getenv(
    "URL_FETCH_USER_AGENT", "Job360/1.0 (+https://job360.uk/bot; user-initiated)"
)
# S9 — a self-hosted deployment may legitimately need an internal careers
# page; EXTRA_DENY_NETS adds a net without a code change, ALLOW_NETS is the
# loud escape hatch (empty by default, changes nothing, warns every time it
# lets an address through — a documented hole with an alarm on it, not a
# convenience).
URL_FETCH_EXTRA_DENY_NETS = _env_list("URL_FETCH_EXTRA_DENY_NETS", ())
URL_FETCH_ALLOW_NETS = _env_list("URL_FETCH_ALLOW_NETS", ())

# B3 — a single ``<script type="application/ld+json">`` block is skipped
# before ``json.loads`` ever sees it once it exceeds this many bytes. A real
# JobPosting block is a few KB; this bounds the CPU a hostile
# ``"["*60000``-shaped bomb can spend even after RecursionError is caught,
# and stops one pathological block from starving the other rungs' budget.
URL_FETCH_MAX_JSONLD_BYTES = int(os.getenv("URL_FETCH_MAX_JSONLD_BYTES", str(256 * 1024)))


def validate_required_env() -> None:
    """Raise ``RuntimeError`` if required env vars are missing in production.

    Checks only when ``APP_ENV=production`` OR ``RAILWAY_ENVIRONMENT`` is set.
    In dev/test it is a no-op, so the 1600+ test suite is unaffected.
    """
    is_prod = os.getenv("APP_ENV", "").lower() == "production" or bool(
        os.getenv("RAILWAY_ENVIRONMENT", "")
    )
    if not is_prod:
        return
    missing = [name for name in _REQUIRED_PROD_VARS if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(
            "Missing required environment variables for production: "
            + ", ".join(missing)
        )
