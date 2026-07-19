import os
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv

load_dotenv()

# Root logging level — read once at import so every entrypoint (FastAPI,
# CLI, ARQ worker) sees the same value. Tier-A Step-0 pre-flight #9.
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

# API Keys (Group A)
REED_API_KEY = os.getenv("REED_API_KEY", "")
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
JSEARCH_API_KEY = os.getenv("JSEARCH_API_KEY", "")
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
CAREERJET_AFFID = os.getenv("CAREERJET_AFFID", "")
FINDWORK_API_KEY = os.getenv("FINDWORK_API_KEY", "")
# DfE "Find an apprenticeship" Display Advert API v2 — register for a free
# subscription key at https://developer.apprenticeships.education.gov.uk.
# Empty (default) → the gov_apprenticeships source skips gracefully.
DFE_APPRENTICESHIPS_API_KEY = os.getenv("DFE_APPRENTICESHIPS_API_KEY", "")

# GitHub (optional — for higher rate limits on profile enrichment)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# LLM providers for CV analysis.
# OpenAI (paid) is the PRIMARY — reliable quota, deterministic (temp 0), structured
# output. The free tiers (Gemini/Groq/Cerebras) remain as fallbacks. Key is read
# case-insensitively so a lowercase `openai_api_key=` in .env still works.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "") or os.getenv("openai_api_key", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")

# Email
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")

# Slack / Discord webhooks
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Slack OAuth App credentials (one-click connect flow).
# Register at https://api.slack.com/apps — set OAuth redirect URL to
# {OAUTH_REDIRECT_BASE}/api/settings/channels/callback/slack
# Leave blank (default) to disable the /connect/slack endpoint.
SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
# Public-facing base URL of this deployment (no trailing slash).
# Used to build the OAuth redirect_uri sent to Slack and Discord.
OAUTH_REDIRECT_BASE = os.getenv("OAUTH_REDIRECT_BASE", "")

# Discord OAuth App credentials (one-click connect flow).
# Register at https://discord.com/developers/applications — add the
# incoming-webhook scope and set the redirect URL to
# {OAUTH_REDIRECT_BASE}/api/settings/channels/callback/discord
# Leave blank (default) to disable the /connect/discord endpoint.
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")

# Telegram Bot credentials (deep-link + poll connect flow).
# Create a bot via @BotFather on Telegram to obtain both values.
# Leave blank (default) to disable the /connect/telegram endpoint.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")

# Search
MIN_MATCH_SCORE = 30
MAX_RESULTS_PER_SOURCE = 100
MAX_DAYS_OLD = 7

# Step-1 B7 — gate the LLM enrichment pipeline by score. Only jobs whose
# match_score >= ENRICHMENT_THRESHOLD get sent to the LLM. Default 60 cuts
# noise-floor traffic and keeps free-tier provider quotas usable.
ENRICHMENT_THRESHOLD = int(os.getenv("ENRICHMENT_THRESHOLD", "60"))

# Step-1 B12 — per-user concurrent search cap. POST /search refuses with
# HTTP 429 once a user already has this many runs in `pending`/`running`
# status. Prevents a single authed user from exhausting LLM credits via
# burst dispatch.
MAX_CONCURRENT_SEARCHES_PER_USER = int(os.getenv("MAX_CONCURRENT_SEARCHES_PER_USER", "3"))

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

# Pillar 2 Batch 2.2 — gate-pass scoring
# A job must clear BOTH the title gate AND the skill gate to receive a linear
# score; otherwise the score is suppressed to max(10, (title+skill)*0.25) so
# location/recency alone can no longer inflate a non-matching job. The gates
# are expressed as fractions of the component max (TITLE_WEIGHT / SKILL_WEIGHT
# in skill_matcher.py — both 40). Default 0.15 → absolute threshold of 6.
MIN_TITLE_GATE = float(os.getenv("MIN_TITLE_GATE", "0.15"))
MIN_SKILL_GATE = float(os.getenv("MIN_SKILL_GATE", "0.15"))

# Pillar 2 Batch 2.9 — multi-dimensional scoring weights.
# JobScorer.score() adds these on top of the legacy 4-component formula
# (title + skill + location + recency). The sum (legacy 100 + 30 new)
# is clamped to 100 at the call site, so individual weights can be tuned
# via env vars without changing the ceiling.
SALARY_WEIGHT = int(os.getenv("SALARY_WEIGHT", "10"))
SENIORITY_WEIGHT = int(os.getenv("SENIORITY_WEIGHT", "8"))
VISA_WEIGHT = int(os.getenv("VISA_WEIGHT", "6"))
WORKPLACE_WEIGHT = int(os.getenv("WORKPLACE_WEIGHT", "6"))

# Per-User AI CV & Cover Letter (docs/peruser_cv_coverletter.md) — guardrail #1:
# each generation is a paid LLM call, so cap free usage. One generation = one CV +
# cover letter for one job. When a real premium plan lands, premium users bypass this
# cap (no plan column exists yet — everyone is on the free cap for now).
TAILOR_FREE_PER_MONTH = int(os.getenv("TAILOR_FREE_PER_MONTH", "10"))

# Pillar 2 Batch 2.6 — semantic stack feature flag.
# When false (default), embeddings + ChromaDB + ESCO normalisation all skip.
# When true, callers that check this flag activate the semantic retrieval path.
SEMANTIC_ENABLED = os.getenv("SEMANTIC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean env var. Unset -> ``default``. Accepts 1/true/yes/on."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


# --- Independent per-engine switches -------------------------------------
# Four scoring engines, each with its own on/off switch so ANY combination
# can be selected (e.g. E1+E3 on, E2+E4 off). Each switch DEFAULTS to its
# legacy gate so existing .env files + the whole test suite behave identically:
#   * Engine 1 — keyword       -> ON (was always-on; first flag it ever had)
#   * Engine 2 — dimensions    -> defaults to ENRICHMENT_ENABLED
#   * Engine 3 — hybrid search -> defaults to SEMANTIC_ENABLED
#   * Engine 4 — LLM judge     -> defaults to MATCHER_ENABLED
# At each gate the effective state is "ENGINEx_ENABLED OR <legacy flag>", so
# flipping the legacy flag still works (back-compat) while the new switch lets
# you drive every engine from one uniform set of names.
ENGINE1_ENABLED = _env_flag("ENGINE1_ENABLED", True)
ENGINE2_ENABLED = _env_flag("ENGINE2_ENABLED", _env_flag("ENRICHMENT_ENABLED", False))
ENGINE3_ENABLED = _env_flag("ENGINE3_ENABLED", SEMANTIC_ENABLED)
ENGINE4_ENABLED = _env_flag("ENGINE4_ENABLED", _env_flag("MATCHER_ENABLED", False))

# Target salary range (GBP, annual) — used for tiebreaker sorting, not scoring
TARGET_SALARY_MIN = int(os.getenv("TARGET_SALARY_MIN", "40000"))
TARGET_SALARY_MAX = int(os.getenv("TARGET_SALARY_MAX", "120000"))

# Rate limits (requests per second)
class RateLimitConfig(TypedDict):
    """Per-source rate-limit knobs: max in-flight requests + sleep between them."""

    concurrent: int
    delay: float


RATE_LIMITS: dict[str, RateLimitConfig] = {
    "reed": {"concurrent": 1, "delay": 2.0},
    "adzuna": {"concurrent": 1, "delay": 2.0},
    "jsearch": {"concurrent": 1, "delay": 3.0},
    "arbeitnow": {"concurrent": 2, "delay": 1.0},
    "remoteok": {"concurrent": 1, "delay": 2.0},
    "jobicy": {"concurrent": 2, "delay": 1.0},
    "himalayas": {"concurrent": 2, "delay": 1.0},
    "greenhouse": {"concurrent": 2, "delay": 1.5},
    "lever": {"concurrent": 2, "delay": 1.5},
    "workable": {"concurrent": 2, "delay": 1.5},
    "ashby": {"concurrent": 2, "delay": 1.5},
    "remotive": {"concurrent": 2, "delay": 1.0},
    "jooble": {"concurrent": 1, "delay": 2.0},
    "linkedin": {"concurrent": 1, "delay": 3.0},
    "smartrecruiters": {"concurrent": 2, "delay": 1.5},
    "pinpoint": {"concurrent": 2, "delay": 1.5},
    "recruitee": {"concurrent": 2, "delay": 1.5},
    "indeed": {"concurrent": 1, "delay": 3.0},
    # S8 — NEVER READ AT RUNTIME, and kept deliberately. `indeed` and `glassdoor`
    # are two SOURCE_REGISTRY keys pointing at ONE class (JobSpySource), which
    # hardcodes `name = "indeed"` (sources/other/indeed.py:16). base.py:82 looks
    # up RATE_LIMITS by `self.name`, so this row is unreachable — a single
    # instance fetches both sites through the "indeed" limiter, which is the
    # correct behaviour for one instance making one stream of requests.
    # It stays because tests/test_cli.py:49 names the RATE_LIMITS entry as one of
    # the FIVE surfaces that must move together when a source is added/removed
    # (CLAUDE.md rule #8) — deleting it would break that contract for a row that
    # costs nothing. Job rows are still labelled "glassdoor" correctly: that comes
    # per-row from JobSpy's own `site` column, not from `self.name`.
    "glassdoor": {"concurrent": 1, "delay": 3.0},
    "workday": {"concurrent": 2, "delay": 1.5},
    "google_jobs": {"concurrent": 1, "delay": 2.0},
    "devitjobs": {"concurrent": 2, "delay": 1.0},
    "landingjobs": {"concurrent": 2, "delay": 1.0},
    "aijobs": {"concurrent": 2, "delay": 1.0},
    "themuse": {"concurrent": 1, "delay": 2.0},
    "hackernews": {"concurrent": 2, "delay": 1.0},
    "careerjet": {"concurrent": 1, "delay": 2.0},
    "findwork": {"concurrent": 1, "delay": 2.0},
    "gov_apprenticeships": {"concurrent": 1, "delay": 2.0},  # 150 req / 5 min = 1 per 2s
    "nofluffjobs": {"concurrent": 2, "delay": 1.5},
    # New sources (Phase 4)
    "hn_jobs": {"concurrent": 3, "delay": 0.5},
    "jobs_ac_uk": {"concurrent": 1, "delay": 2.0},
    "nhs_jobs": {"concurrent": 1, "delay": 2.0},
    "personio": {"concurrent": 1, "delay": 3.0},
    "workanywhere": {"concurrent": 1, "delay": 5.0},
    "weworkremotely": {"concurrent": 1, "delay": 2.0},
    "realworkfromanywhere": {"concurrent": 1, "delay": 2.0},
    "biospace": {"concurrent": 1, "delay": 2.0},
    "climatebase": {"concurrent": 1, "delay": 3.0},
    "eightykhours": {"concurrent": 1, "delay": 2.0},
    "bcs_jobs": {"concurrent": 1, "delay": 3.0},
    "uni_jobs": {"concurrent": 1, "delay": 2.0},
    "successfactors": {"concurrent": 1, "delay": 2.0},
    "aijobs_ai": {"concurrent": 1, "delay": 2.0},
    # Batch 3 additions — published rate-limits cited in each source's tests
    "teaching_vacancies": {"concurrent": 1, "delay": 2.0},  # no stated cap, polite
    "nhs_jobs_xml": {"concurrent": 1, "delay": 2.0},  # feed XML, 15-min tier
    "rippling": {"concurrent": 2, "delay": 1.5},  # ATS, 60s tier
}

# Retry
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

# HTTP
REQUEST_TIMEOUT = 30
USER_AGENT = "Job360/1.0 (UK Job Search Aggregator)"

# Hard ceiling (seconds) on a single source's whole fetch_jobs() call. The
# scheduler gathers all ~49 sources at once, so without this one slow/hanging
# source (a blocked host, a JobSpy scrape, a stuck ATS slug loop) would freeze
# the entire search — the "Refresh hangs" bug. A source that exceeds this is
# cancelled and counted as a failure; every other source's results still land.
# Generous enough for legit multi-request sources (REQUEST_TIMEOUT is per
# request); since sources run concurrently, total search ~= this value.
SOURCE_FETCH_TIMEOUT = int(os.getenv("SOURCE_FETCH_TIMEOUT", "60"))

# ATS boards sweep hundreds of company slugs with per-request rate-limit
# delays; measured unbounded: greenhouse 138.2s for 1,331 jobs (2026-06-11).
# The generic 60s cap was cancelling those sweeps mid-flight and silently
# discarding their results, so the "ats" category gets its own ceiling.
SOURCE_FETCH_TIMEOUT_ATS = int(os.getenv("SOURCE_FETCH_TIMEOUT_ATS", "240"))

# Auth / encryption secrets — deliberately NOT bound as module constants here.
#
# M10: this file used to define
#     SESSION_SECRET = os.getenv("SESSION_SECRET", "")
#     CHANNEL_ENCRYPTION_KEY = os.getenv("CHANNEL_ENCRYPTION_KEY", "")
# Both were DEAD (nothing imported them — verified across src/ and tests/) and
# actively misleading: the `""` default reads as "an empty secret is a valid
# state". It is not. The real consumers read the env var at CALL time and
# FAIL CLOSED:
#   * api/auth_deps.py::_secret()          -> raises if SESSION_SECRET unset
#   * services/channels/crypto.py::_key()  -> raises if CHANNEL_ENCRYPTION_KEY unset
# Keeping a defaulted-to-empty binding around invites a future caller to read it
# and silently sign/encrypt with "". If you need a secret, call the accessor —
# never re-add a module constant with an empty default.
# (Their NAMES still appear in _REQUIRED_PROD_VARS below for the prod check.)

# Sentry error tracking (Phase 3). Empty string → Sentry is disabled (no-op
# at init time). Populate in production via the SENTRY_DSN env var.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# --- Production env validation --------------------------------------------
# These vars must be non-empty when running in production.
_REQUIRED_PROD_VARS = ["SESSION_SECRET", "CHANNEL_ENCRYPTION_KEY", "DATABASE_URL"]


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
