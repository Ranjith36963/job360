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

def _secret(name: str) -> str:
    """Read a credential, stripping surrounding whitespace.

    Secrets are pasted into a dashboard by hand, so a trailing newline or space
    rides along more often than anyone expects. It is invisible in every UI and
    fatal wherever the value is concatenated rather than form-encoded — a key in
    a URL path or a header becomes a DIFFERENT key, and the upstream answers 401
    or 403, which reads exactly like "the key is wrong" rather than "the key has
    a space on the end".

    Measured on production 2026-08-24: SERPAPI_KEY and JSEARCH_API_KEY both carry
    surrounding whitespace on the worker service, and google_jobs was returning
    HTTP 401 while the same account's key looked correct.

    Stripping is safe for every credential here: none of these vendors issue keys
    whose leading or trailing whitespace is significant.
    """
    return os.getenv(name, "").strip()


# API Keys (Group A)
REED_API_KEY = _secret("REED_API_KEY")
ADZUNA_APP_ID = _secret("ADZUNA_APP_ID")
ADZUNA_APP_KEY = _secret("ADZUNA_APP_KEY")
JSEARCH_API_KEY = _secret("JSEARCH_API_KEY")
JOOBLE_API_KEY = _secret("JOOBLE_API_KEY")
SERPAPI_KEY = _secret("SERPAPI_KEY")
CAREERJET_AFFID = _secret("CAREERJET_AFFID")
FINDWORK_API_KEY = _secret("FINDWORK_API_KEY")
# DfE "Find an apprenticeship" Display Advert API v2 — register for a free
# subscription key at https://developer.apprenticeships.education.gov.uk.
# Empty (default) → the gov_apprenticeships source skips gracefully.
DFE_APPRENTICESHIPS_API_KEY = _secret("DFE_APPRENTICESHIPS_API_KEY")

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

# Search
# MIN_MATCH_SCORE is the *display* floor — the default "good enough to show"
# threshold used by the CLI viewer and sent by the dashboard as ``min_score``.
# It is NOT a storage floor: filtering at read time means a job can be recovered
# by lowering the filter, and a score that drifts (recency decays up to 10 pts
# as a posting ages) no longer silently destroys the row.
MIN_MATCH_SCORE = 30
# MIN_STORE_SCORE is the *storage* floor — the spam cut. A job must beat this to
# be persisted/fed at all. Deliberately far below MIN_MATCH_SCORE: the pipeline
# used to hard-DELETE everything under 30 before it was ever stored, so a thin
# profile (or a job that simply aged past the recency bands) lost jobs forever
# with no way to get them back. Store broadly, filter at read time.
MIN_STORE_SCORE = int(os.getenv("MIN_STORE_SCORE", "1"))
# USER_BROUGHT_SOURCE — the `jobs.source` value for an ad the user pasted
# (POST /jobs/bring). Not a scraper: it is outside SOURCE_REGISTRY and the
# five-surface contract on purpose. Selection treats such rows as protected —
# the user chose them, so no cap or score floor may evict them.
USER_BROUGHT_SOURCE = "user_brought"
# FEED_CANDIDATE_CAP — the User-Level candidate bound (funnel Stage-1,
# 2026-08-05). user_feed is a bounded per-user CANDIDATE SET, not a mirror of
# the shared catalog: only the user's top-N jobs by score enter/stay in it
# (measured before the cap: one user's feed held 85% of the whole catalog).
# Rows that fall out of the selection are marked stale — reversible, and rows
# carrying an LLM verdict are never evicted. `0` disables the cap entirely
# (legacy flood behaviour). Industry anchor: Twitter materializes ~800-1000
# candidates per user.
FEED_CANDIDATE_CAP = int(os.getenv("FEED_CANDIDATE_CAP", "800"))
MAX_RESULTS_PER_SOURCE = 100
MAX_DAYS_OLD = 7

# Step-1 B7 — gate the LLM enrichment pipeline.
#
# WAS a hard threshold of 60. Measured in prod 2026-07-28: the maximum
# match_score across the entire 3,342-row feed was 58 and `job_enrichment` held
# 0 rows. The gate sat ABOVE the highest score the scorer can produce, so this
# stage had never run once — and produced no error, no log and no empty state
# while doing so. A filter tuned past your maximum is indistinguishable from a
# feature that was never built.
#
# Cause: the distribution moved. `merge_cv_and_preferences` used to copy the
# whole CV into preferences, inflating every score; removing that dropped scores
# to normal, and this constant was never re-tuned.
#
# Now a BUDGET: enrich the best N per run, above a low sanity floor. A budget
# makes no claim about the distribution, so it cannot go stale — and it doubles
# as a hard cost ceiling, which a threshold never was. Same pattern as
# MATCHER_MAX_JOBS (engine 4), which is precisely why the LLM judge was running
# in prod while enrichment was dark.
ENRICHMENT_MAX_JOBS = int(os.getenv("ENRICHMENT_MAX_JOBS", "20"))
ENRICHMENT_MIN_SCORE = int(os.getenv("ENRICHMENT_MIN_SCORE", "10"))
# Back-compat: some call sites / .env files still reference the old name. Kept as
# an alias of the floor so nothing breaks, but it is no longer the gate.
ENRICHMENT_THRESHOLD = int(os.getenv("ENRICHMENT_THRESHOLD", str(ENRICHMENT_MIN_SCORE)))

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

# Per-User AI CV & Cover Letter (docs/product/peruser_cv_coverletter.md) — guardrail #1:
# each generation is a paid LLM call, so cap free usage. One generation = one CV +
# cover letter for one job. When a real premium plan lands, premium users bypass this
# cap (no plan column exists yet — everyone is on the free cap for now).
TAILOR_FREE_PER_MONTH = int(os.getenv("TAILOR_FREE_PER_MONTH", "10"))

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

# Pillar 2 Batch 2.6 — semantic stack feature flag.
# When false (default), embeddings + ChromaDB + ESCO normalisation all skip.
# When true, callers that check this flag activate the semantic retrieval path.
SEMANTIC_ENABLED = os.getenv("SEMANTIC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
# Convergence backfill (2026-08-05): each pipeline run ALSO embeds up to this
# many EXISTING catalog jobs that never got a vector, so embedding coverage
# converges to 100% through ordinary searches — no manual sweep on the prod
# box needed. ~50ms/job on CPU => 300 ≈ 15s inside a background search task.
# 0 disables. Only active when SEMANTIC_ENABLED is on (rule #18).
EMBED_BACKFILL_PER_RUN = int(os.getenv("EMBED_BACKFILL_PER_RUN", "300"))

# Description-backfill phase of the enrichment_sweep cron (2026-08-07) —
# workers/tasks.py::_backfill_thin_descriptions. Sources fetch job-detail
# pages under strict PER-RUN budgets (_MAX_DETAIL_FETCHES in workday.py /
# smartrecruiters.py) so a single run never blows the 240s ATS timeout; a job
# stored past that budget keeps description="" forever unless something goes
# back for it. Measured in prod: 1,311 active jobs (30% of the catalog) carry
# under 200 chars of description, and coverage of every enriched field
# (workplace/seniority/visa) tracks description length almost perfectly.
# Same knobs as ENRICHMENT_SWEEP_PER_TICK: default 50/tick keeps HTTP fetch
# volume polite across the 30-min cron cadence; `0` disables the phase
# entirely (no SELECT, no fetch, no-op).
DESCRIPTION_BACKFILL_PER_TICK = int(os.getenv("DESCRIPTION_BACKFILL_PER_TICK", "50"))

# ---------------------------------------------------------------------------
# JOB SOURCE ENRICHMENT sweep — the two-pass catalog pass
# (docs/pillars/UNIVERSAL_SHELF.md §6 step 3, §7 cost).
#
# JOB SOURCE ENRICHMENT = an LLM READING a job ad to extract facts about the
# JOB (salary, seniority, workplace, visa, employment type, category). Facts
# about the JOB, identical for every user, so the cost is CATALOG cost: it is
# paid once per job and shared by everyone. It does NOT scale with user count.
#
# Two budgets, both hard, both loud when they bite (services/shelf_enrichment.py):
#   * a JOB cap  — how many ads one sweep may read;
#   * a SPEND cap — estimated USD one sweep may spend, checked BEFORE each
#     call, so the sweep stops on the cheaper of the two.
# The owner is pre-revenue: a sweep must never be able to surprise him.
SHELF_ENRICHMENT_MAX_JOBS = int(os.getenv("SHELF_ENRICHMENT_MAX_JOBS", "500"))
SHELF_ENRICHMENT_MAX_SPEND_USD = float(os.getenv("SHELF_ENRICHMENT_MAX_SPEND_USD", "1.00"))

# WALL-CLOCK ceiling for pass 2, in seconds. The third cap, and the only one
# grounded in how long things actually take rather than how many of them there
# are.
#
# Sized from measurement, not taste: `refresh_catalog` runs under ARQ's
# job_timeout=600s (`job_timeout` in workers/settings.py) and the source fan-out alone took
# ~430s on a real 40-source run (2026-08-17). That leaves ~170s, so 150 is the
# honest budget with a little headroom for pass 1 and the final ledger write.
#
# Why a TIME cap when max_jobs already exists: a healthy LLM call measured 2-4s,
# but with a dead provider key the retry cascade took ~120s PER JOB — so 500
# jobs is anywhere from 25 minutes to 16 hours. A job count cannot bound that;
# a clock can. And overrunning is not a soft failure here: ARQ retries on
# `max_tries = 5` (workers/settings.py), so being killed re-runs the WHOLE task
# — re-fetching every source and re-spending — up to five times. Named by
# SYMBOL, not by line: the `:175`/`:196` that stood here pointed at neither
# setting, and there is no explicit `retry_jobs` in that file at all — a
# reference that has gone stale sends the reader somewhere with confidence.
# (CodeRabbit, PR #388.)
SHELF_ENRICHMENT_MAX_SECONDS = float(os.getenv("SHELF_ENRICHMENT_MAX_SECONDS", "150"))

# PASS 1 is FREE (no LLM): it re-runs the gate's own detectors over rows
# ALREADY in the catalog, so existing jobs gain visa / deadline / normalised
# enum / annualised-salary shelves without paying for a single token. Its only
# cost is DB writes, so its budget is much larger than the LLM pass's.
SHELF_ENRICHMENT_PASS1_MAX_JOBS = int(os.getenv("SHELF_ENRICHMENT_PASS1_MAX_JOBS", "2000"))

# How many still-absent consumer shelves a job must have before it is worth
# reading its ad. Measured on the live catalog 2026-08-17 (2,826 eligible
# jobs): absence is CORRELATED — 99.6% of eligible jobs are missing 2+ shelves
# and 78% are missing 4+ — so raising this from 1 to 4 saves only ~17% of the
# spend while dropping 15% of the jobs. 1 is the honest default; the knob
# exists so the cost/coverage trade stays a setting, not a code change.
SHELF_ENRICHMENT_MIN_ABSENT_SHELVES = int(os.getenv("SHELF_ENRICHMENT_MIN_ABSENT_SHELVES", "1"))

# gpt-4o-mini list price, web-verified 2026-08-17. There is no other price
# constant in this repo — before this, NOTHING could answer "what did last
# night cost". Env-overridable for exactly the same reason OPENAI_MODEL is:
# the model can be swapped, and a stale hardcoded price is a silent lie.
# Batch API is -50% on both numbers if the sweep ever moves to it.
LLM_INPUT_USD_PER_1M = float(os.getenv("LLM_INPUT_USD_PER_1M", "0.150"))
LLM_OUTPUT_USD_PER_1M = float(os.getenv("LLM_OUTPUT_USD_PER_1M", "0.600"))
# Output cannot be measured without making the call, so it is ESTIMATED per
# job: the enrichment contract is a fixed ~16-field JSON object, measured at
# ~200 tokens. Input IS measured, from the real prompt text.
LLM_OUTPUT_TOKENS_PER_JOB = int(os.getenv("LLM_OUTPUT_TOKENS_PER_JOB", "200"))


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
    "themuse": {"concurrent": 1, "delay": 2.0},
    "hackernews": {"concurrent": 2, "delay": 1.0},
    "careerjet": {"concurrent": 1, "delay": 2.0},
    "findwork": {"concurrent": 1, "delay": 2.0},
    "gov_apprenticeships": {"concurrent": 1, "delay": 2.0},  # 150 req / 5 min = 1 per 2s
    "nofluffjobs": {"concurrent": 2, "delay": 1.5},
    # New sources (Phase 4)
    "hn_jobs": {"concurrent": 3, "delay": 0.5},
    "nhs_jobs": {"concurrent": 1, "delay": 2.0},
    "personio": {"concurrent": 1, "delay": 3.0},
    "weworkremotely": {"concurrent": 1, "delay": 2.0},
    "realworkfromanywhere": {"concurrent": 1, "delay": 2.0},
    "climatebase": {"concurrent": 1, "delay": 3.0},
    "eightykhours": {"concurrent": 1, "delay": 2.0},
    "bcs_jobs": {"concurrent": 1, "delay": 3.0},
    "uni_jobs": {"concurrent": 1, "delay": 2.0},
    "successfactors": {"concurrent": 1, "delay": 2.0},
    "aijobs_ai": {"concurrent": 1, "delay": 2.0},
    # Batch 3 additions — published rate-limits cited in each source's tests
    "teaching_vacancies": {"concurrent": 1, "delay": 2.0},  # no stated cap, polite
    # Removed 2026-08-10 (upstreams dead, verified live): aijobs, jobs_ac_uk,
    # biospace, rippling, nhs_jobs_xml, workanywhere. nhs_jobs (non-XML) stays.
}

# Retry
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

# HTTP
REQUEST_TIMEOUT = 30
USER_AGENT = "Job360/1.0 (UK Job Search Aggregator)"

# Hard ceiling (seconds) on a single source's whole fetch_jobs() call. The
# scheduler gathers every registered source at once, so without this one
# slow/hanging source (a blocked host, a JobSpy scrape, a stuck ATS slug loop)
# would freeze the entire search — the "Refresh hangs" bug. A source that
# exceeds this is cancelled and counted as a failure; every other source's
# results still land.
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
