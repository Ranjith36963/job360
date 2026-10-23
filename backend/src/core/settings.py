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
# Every connection the request-path pool opens gets this Postgres
# ``statement_timeout`` (repositories/pool.py), so one runaway query cannot pin
# a pooled connection forever. Pool only: migrations and boot DDL open their own
# connections through ``pg.connect`` and never see it. ``0`` = no timeout.
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
# How long /api/readyz waits to borrow a pooled connection (and then for its
# ``SELECT 1``) before answering 503. Railway's deploy healthcheck calls it.
READYZ_POOL_TIMEOUT_S = float(os.getenv("READYZ_POOL_TIMEOUT_S", "2"))
LOGS_DIR = DATA_DIR / "logs"


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

# NOTE (2026-09-21, decision 28) — Job360 has no LLM of its own. Six settings
# lived here for the provider pool that read CVs, LinkedIn exports and GitHub
# profiles: OPENAI_API_KEY, OPENAI_MODEL, GEMINI_API_KEY, GEMINI_MODEL,
# GROQ_API_KEY and CEREBRAS_API_KEY. The user's own agent reads the raw text we
# store and writes the structured fields back through `update_profile`, so there
# is no provider chain left to key. Nothing reads these names any more — if one
# is still set on Railway it is simply ignored, and deleting it is safe.

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
# Per-EMAIL cap across ALL source addresses, beside the email+IP lockout above
# (same window). OFF BY DEFAULT (0): when on, it refuses even the right password
# once that many failures are counted, so ~10 attacker addresses (or IPv6 /64s)
# can keep a chosen account locked out (security review P2, gap audit #6). Turn
# it on only as a deliberate choice, e.g. LOGIN_EMAIL_MAX_ATTEMPTS=50. When on,
# each address adds at most LOGIN_MAX_ATTEMPTS failures before its own email+IP
# bucket locks, so tripping it takes LOGIN_EMAIL_MAX_ATTEMPTS / LOGIN_MAX_ATTEMPTS
# distinct addresses inside one window. ``0`` disables it.
LOGIN_EMAIL_MAX_ATTEMPTS = int(os.getenv("LOGIN_EMAIL_MAX_ATTEMPTS", "0"))

# Client address behind proxies (src/api/client_ip.py is the only reader of
# X-Forwarded-For). JOB360_TRUSTED_PROXY_HOPS = how many trusted proxies in
# front of the backend each APPEND one X-Forwarded-For entry (or set it). The
# client address is the entry that many positions from the RIGHT; everything
# left of it was written by the caller and is ignored. Never the leftmost.
# Interaction with the older flag:
#   * HOPS set (any integer) -> it wins; 0 means "never trust X-Forwarded-For"
#     even when JOB360_TRUST_PROXY=1. A negative value counts as 0.
#   * HOPS unset, JOB360_TRUST_PROXY=1 -> 1 hop: the rightmost entry, the one
#     the nearest proxy wrote, which a caller cannot forge.
#   * both unset -> 0: X-Forwarded-For ignored, the socket peer is the address.
# Set it ONLY when every path to the backend crosses that many proxies; a
# backend the public can reach without them lets the caller write the entry.
def resolve_trusted_proxy_hops(trust_proxy_raw: str, hops_raw: str) -> int:
    """The effective hop count from the two env values (rules above)."""
    hops = hops_raw.strip()
    if hops:
        return max(0, int(hops))
    return 1 if trust_proxy_raw.strip() == "1" else 0


TRUSTED_PROXY_HOPS = resolve_trusted_proxy_hops(
    os.getenv("JOB360_TRUST_PROXY", ""), os.getenv("JOB360_TRUSTED_PROXY_HOPS", "")
)

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

# LinkedIn PDF line-wrap detection (services/profile/linkedin_parser.py). A
# "Technologies:" line that ends WITHOUT a bullet was cut by the PDF when its
# length is at least this fraction of the document's own line width (the 95th
# percentile of its line lengths). The owner's real export had cut lines at
# 69/75 and 71/75 (0.92, 0.95); 0.85 leaves margin for proportional fonts.
# Read live via `settings.X` so tests can monkeypatch it.
LINKEDIN_WRAP_WIDTH_RATIO = float(os.getenv("LINKEDIN_WRAP_WIDTH_RATIO", "0.85"))

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
# Tell a connected MCP client its cached tool list is stale, once per user per
# process (2026-09-20: `save_fit` grew an `axes` parameter and Claude.ai kept
# answering from the list it cached at connect time until the user reconnected
# the connector by hand). ON declares `tools.listChanged` and answers each POST
# with its own SSE stream — the only back-channel a stateless mount has — so the
# notification has somewhere to ride. OFF restores the silent JSON-only
# transport exactly: the kill switch if a client cannot read SSE.
MCP_ANNOUNCE_TOOLS_CHANGED = os.getenv("MCP_ANNOUNCE_TOOLS_CHANGED", "1").lower() in {"1", "true", "yes", "on"}

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
# A session's `last_seen` is written at most once per this many seconds.
# Writing it on EVERY cookie resolve turned each signed-in read into a commit,
# and prod's commits wait on a slow disk (0.2-1 s, spikes past 10 s), so
# `/api/auth/me` took 17-61 s under a burst. `last_seen` is informational only:
# expiry is the absolute `expires_at`, nothing reads `last_seen` to decide
# access. `0` restores the old write-every-request behaviour.
SESSION_TOUCH_INTERVAL_SECONDS = int(os.getenv("SESSION_TOUCH_INTERVAL_SECONDS", "300"))
# The touch runs in a background task on its OWN Postgres connection (outside
# the pool). A stalled disk or dead TCP link must not hold that connection
# forever: the whole write (connect + UPDATE + commit + close) is abandoned
# after this many seconds and retried on a later request.
SESSION_TOUCH_TIMEOUT_SECONDS = float(os.getenv("SESSION_TOUCH_TIMEOUT_SECONDS", "10"))
# At most this many touches run at once per process; each holds a real
# connection, and Postgres allows ~100. Extra touches are skipped (informational
# only) with a rate-limited warning.
SESSION_TOUCH_MAX_INFLIGHT = int(os.getenv("SESSION_TOUCH_MAX_INFLIGHT", "20"))
# Account deletion for a magic-link-only account (no password to ask for, GDPR
# Art. 17): allowed only from a session created at most this many seconds ago,
# i.e. one the user just made by clicking a fresh email link.
ACCOUNT_DELETE_FRESH_SESSION_SECONDS = int(os.getenv("ACCOUNT_DELETE_FRESH_SESSION_SECONDS", "600"))

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
    # Feedback loop slice 1: `hired` is a rung above `offer`; `offer_declined` is
    # terminal (the user turned the offer down).
    "hired", "offer_declined",
)
APPLICATION_NOTE_EVENT_TYPES = (
    "fit_judged", "artifact_saved", "contact_added", "outreach_sent", "outreach_replied", "note", "lesson",
    # Feedback loop slice 1: the written look-back after an outcome (payload keys
    # checked by spine.validate_outcome_payload).
    "post_mortem",
    "asked", "answered", "ask_withdrawn",
    # Owner decision 2026-10-08 (S2) — the per-job submit switch
    # (payload {"submit_mode": "confirm" | "auto_when_sure" | "inherit"}). A NOTE
    # event: it never moves the status. Latest one wins (spine.submit_override).
    "submit_mode_set",
    # Owner decision 2026-10-08 (S3, application kit) - the human-in-the-loop
    # trail. All note-family, none moves the status. Payload shapes and who may
    # write each are checked in the record_event ROUTE (applications._check_kit_event).
    "kit_read", "cv_seen", "submit_approved", "submit_declined", "autofill_set", "duplicate_cleared",
    "form_filled", "form_swept", "hold_released", "site_account", "account_needed", "form_checked",
    # Owner decision 2026-10-10 (S6, blocked record) - the assistant got stuck
    # (`blocked`, which opens one Needs-you ask) and the way out (`unblocked`).
    # Note-family; closed payloads checked in services/applications/blocked.py.
    "blocked", "unblocked",
    # Owner decision 2026-10-10 (S7, proof of application): the pasted
    # confirmation text, and the server-written trail of an uploaded screenshot.
    "proof_text", "proof_screenshot",
    # Owner decision 2026-10-15: a test/simulated application is marked with a
    # `simulation` note (no payload; detail is free text) recorded FIRST. Stats,
    # proof_missing and the practice run never count it; a corrects_event_id
    # event retracts the mark.
    "simulation",
    # Feedback loop slice 2: automatic employer emails. The assistant sends them from the
    # owner's own Gmail; Job360 only records (`*_sent`, exact text) and gates (check_auto_email).
    # `auto_email_skipped` records why a due email was NOT sent. Payloads: spine.validate_auto_email_payload.
    "checkin_sent", "feedback_asked", "feedback_followup_sent", "auto_email_skipped",
    # Feedback loop slice 4 (interview side): payloads checked by
    # services/applications/interview_payloads.py. All note-family; `assessment`
    # must never enter FEEDBACK_FUNNEL_RANK.
    "interview_session", "process_friction", "assessment",
    # Owner decision 2026-10-18: the batch queue. `queued` puts a considering application on
    # the user's queue, `unqueued` takes it off. Note-family, never moves the status; no
    # payload, detail optional. Checked in the record_event ROUTE (applications._check_queue_event);
    # read by spine.queued_at_by_app.
    "queued", "unqueued",
    # Owner decision 2026-10-18 (bulk-apply D1): `submit_attempted` (no payload) is written by the
    # assistant right before the final click; `submit_resolved` {outcome: "not_sent"} is WEB ONLY
    # (the user says the submit never went out). Checked in services/applications/kit.py.
    "submit_attempted", "submit_resolved",
    # Owner decision 2026-10-18 (bulk-apply F4): the user changed an answer the assistant wrote or
    # guessed; payload {key | question, was_source}. A note: saving it to memory is a separate,
    # user-approved update_profile. Checked in the record_event ROUTE (_check_answer_corrected).
    "answer_corrected",
    # Outreach pack #5/#6 (2026-10-20): a contact ledger entry (meeting / call / intro, a
    # referral asked or given) mirrored onto a LINKED contact's job timeline, payload
    # {contact_id, outreach_id, channel}. Written only by contacts.record_outreach; record_event
    # refuses them without contact_id. Note-family: never move the status. A wrong one is
    # retracted by a `note` with corrects_event_id (stats and the kit skip it).
    "contact_meeting", "contact_call", "contact_intro", "referral_asked", "referral_given",
    # Gap roadmap slice 29: the exact file a form was sent. SERVER-WRITTEN only (the browser's
    # upload and the kit's /api/files/{token} link): {kind, artifact_id, version, sha256 of the
    # bytes served, fmt, renderer_version, host}. A hash, never bytes or text. Refused from
    # record_event (services/applications/uploads.py); record_application freezes it as `files`.
    "document_uploaded",
    # Gap roadmap slice 31 (G078): an EMAIL application's exact draft, recorded by the assistant before
    # check_submit {to, subject, attachments: [{artifact_id, sha256 of the text}], gmail_draft_id?}; never
    # the body. Checked in services/applications/email_channel.py; read by the gate and the receipt.
    "email_drafted",
)
SIMULATION_EVENT_TYPE = "simulation"
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

# ── Slice 6 (docs/plans/2026-09-07-email-evidence/spec.md) — an event may
# carry the email it came from, and an interview may carry a real datetime
# instead of prose in `detail`. Every cap a parameter; a breach is 422 naming
# the setting (S2).
APPLICATION_EVENT_SOURCE_KINDS = _env_list("APPLICATION_EVENT_SOURCE_KINDS", ("email", "pasted"))
APPLICATION_EVENT_SOURCE_MESSAGE_ID_MAX_CHARS = int(
    os.getenv("APPLICATION_EVENT_SOURCE_MESSAGE_ID_MAX_CHARS", "256")
)
APPLICATION_EVENT_SOURCE_SENDER_MAX_CHARS = int(os.getenv("APPLICATION_EVENT_SOURCE_SENDER_MAX_CHARS", "320"))
APPLICATION_EVENT_SOURCE_SUBJECT_MAX_CHARS = int(os.getenv("APPLICATION_EVENT_SOURCE_SUBJECT_MAX_CHARS", "500"))

# R3 — which status event types accept a `scheduled_at`. Derived from
# APPLICATION_STATUS_EVENT_TYPES the way STATS_INTERVIEW_EVENT_TYPES (below)
# is, so a vocabulary rename cannot leave a stale name behind here.
_SCHEDULABLE_NAMES = {"interview_requested", "interview_scheduled"}
APPLICATION_SCHEDULABLE_EVENT_TYPES = tuple(
    t for t in APPLICATION_STATUS_EVENT_TYPES if t in _SCHEDULABLE_NAMES
)
_schedulable_mismatch = set(APPLICATION_SCHEDULABLE_EVENT_TYPES) != _SCHEDULABLE_NAMES
if _schedulable_mismatch:  # pragma: no cover — a vocabulary edit would trip this
    raise RuntimeError(
        f"APPLICATION_SCHEDULABLE_EVENT_TYPES expected {sorted(_SCHEDULABLE_NAMES)} inside "
        f"APPLICATION_STATUS_EVENT_TYPES {APPLICATION_STATUS_EVENT_TYPES}, got {APPLICATION_SCHEDULABLE_EVENT_TYPES}"
    )
# S8 — the mirror of `occurred_at`'s future bound: an interview can be
# scheduled far ahead (an email cannot arrive in the future — `received_at`
# reuses APPLICATION_EVENT_MAX_FUTURE_SECONDS instead).
APPLICATION_SCHEDULED_AT_MAX_FUTURE_SECONDS = int(
    os.getenv("APPLICATION_SCHEDULED_AT_MAX_FUTURE_SECONDS", str(366 * 86400))
)

# R5 — artifact versions. Storage decision: TEXT in Postgres, not a file store
# (spec §Data model) — a tailored CV measured on this codebase is 2-8 KB.
APPLICATION_ARTIFACT_KINDS = (
    "cv", "cover_letter", "answers", "outreach",
    # Feedback loop slice 4 (interview side).
    "story_bank", "interview_transcript", "redflag_analysis",
    # Feedback loop slice 5 (fit patterns): the learning plan.
    "skill_gap_report",
)
APPLICATION_ARTIFACT_MAX_CHARS = int(os.getenv("APPLICATION_ARTIFACT_MAX_CHARS", "60000"))
APPLICATION_ARTIFACT_MAX_VERSIONS = int(os.getenv("APPLICATION_ARTIFACT_MAX_VERSIONS", "200"))
# Slice 8 (#515) — the read-only artifact diff caps each side in LINES before
# difflib runs (quadratic worst case); the response says `truncated`.
APPLICATION_DIFF_MAX_LINES = int(os.getenv("APPLICATION_DIFF_MAX_LINES", "4000"))
# Owner decision 2026-10-04 — the ASSISTANT's own ATS opinion on a saved
# document (score 0-100 + notes). Job360 never computes or markets it; it is
# stored on the artifact version. Only these kinds may carry one — any other
# kind given an ats_score/ats_notes is a 422 (the stricter choice).
APPLICATION_ARTIFACT_ATS_KINDS = ("cv", "cover_letter")
APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS = int(os.getenv("APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS", "2000"))
# Slice 9 (#516) — "flag for next time". `get_profile` hands the agent the
# last PROFILE_LESSONS_MAX lessons; the web's full list pages up to
# LESSONS_PAGE_MAX per call.
PROFILE_LESSONS_MAX = int(os.getenv("PROFILE_LESSONS_MAX", "20"))
LESSONS_PAGE_MAX = int(os.getenv("LESSONS_PAGE_MAX", "100"))
# Slice 7 (#514) — the visa / sponsorship signal. A CLOSED set the agent (or
# a human at the browser) picks from after reading the ad; `unknown` = the
# ad said nothing and the web shows nothing (rule #29). Job360 holds no
# country rule anywhere — the only comparison is "is the job's country in
# the candidate's own list" (docs/plans/2026-09-11-visa-signal/spec.md).
APPLICATION_VISA_SIGNALS = ("sponsors", "no_sponsorship", "unknown")
# 2026-09-20 — the fit picture on the application page: how many of the
# candidate's own skills are checked against the stored ad text (a regex
# per skill; the profile page caps skills well below this anyway).
ALIGNMENT_MAX_SKILLS = int(os.getenv("ALIGNMENT_MAX_SKILLS", "200"))
# The one skill list (`skill_tiering.profile_skills`): a GitHub language is a
# skill only when it is the user's top language by bytes or holds at least
# this share of all language bytes. Drops config-file "languages" (Procfile,
# Batchfile, Just…) by PROPORTION, never by name (rule #28). Read at call
# time, so a changed value applies without a restart of the reader.
GITHUB_LANGUAGE_MIN_SHARE = float(os.getenv("GITHUB_LANGUAGE_MIN_SHARE", "0.005"))
APPLICATION_VISA_DETAIL_MAX_CHARS = int(os.getenv("APPLICATION_VISA_DETAIL_MAX_CHARS", "500"))

# R8 — record_application (the rich receipt).
APPLICATION_RECEIPT_ANSWERS_MAX = int(os.getenv("APPLICATION_RECEIPT_ANSWERS_MAX", "50"))
APPLICATION_RECEIPT_ANSWER_MAX_CHARS = int(os.getenv("APPLICATION_RECEIPT_ANSWER_MAX_CHARS", "2000"))
APPLICATION_RECEIPT_FIELDS_MAX_BYTES = int(os.getenv("APPLICATION_RECEIPT_FIELDS_MAX_BYTES", "8192"))
# Owner decision 2026-10-04 — where the user applied. A CLOSED set for every
# NEW receipt ('' = not said, rule #29); spine.normalize_receipt_channel is
# the one validator every door calls. Receipts written before this were free
# text and are history (append-only, never rewritten): stats maps a legacy
# value that spells a set member (case/space/hyphen-insensitive, e.g.
# "Company site") to it and everything else to "other".
APPLICATION_RECEIPT_CHANNELS = (
    "company_site", "linkedin_easy_apply", "job_board", "email", "referral", "recruiter", "other",
)

# Owner decision 2026-10-04 — facts about the job the ASSISTANT reads off
# the ad and passes with bring_job (or fixes later): the ISO alpha-2 country
# (validated by visa.normalize_country — no country table), remote yes/no,
# and where the ad was found. Stored per USER on `applications`, never on the
# shared `jobs` row (hard rule #10). Unset = NULL (rule #29).
JOB_FOUND_ON = (
    "indeed", "linkedin", "company_careers", "job_board", "referral", "visa_sponsor_list",
    "pasted_by_user", "other",
)

# Job checks (deterministic facts about ONE brought job, never used to sort).
# Closed TECHNICAL lists of ATS vendors / big job boards - NOT job sources
# (rule 4: Job360 never searches). A host is "official" only on an exact match
# or a real subdomain (`x.greenhouse.io`); see job_checks.classify_host.
OFFICIAL_ATS_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "smartrecruiters.com",
    "workable.com", "bamboohr.com", "recruitee.com", "teamtailor.com", "personio.de", "personio.com",
    "applytojob.com", "jazzhr.com", "gem.com", "dayforcehcm.com", "freshteam.com",
    "zohorecruit.com", "zohorecruit.in", "ripplehire.com", "rippling.com", "icims.com", "taleo.net",
    "ultipro.com", "careers-page.com",
)
# Hosts under an official ATS domain that are NOT one employer's site: RippleHire's
# shared sandbox answers any company's token (security review 2026-10-20).
SHARED_ATS_HOSTS = ("app.ripplehire.com", "www.ripplehire.com")
# Gmail check slice (owner decision 2026-10-15): an employer's ATS sends its
# emails from its OWN domain, and many never name the company in the text
# (an Ashby confirmation signed "TALENT TEAM"), so a company-name search misses
# them. Each application exposes `email_senders` = the ATS host's domain plus the
# extra sender domains below, for the assistant's Gmail `from:` search. Data, not
# a keyword list: a vendor missing here still gets its own domain.
ATS_EXTRA_EMAIL_SENDERS: dict[str, tuple[str, ...]] = {
    "greenhouse.io": ("greenhouse-mail.io",),
    "myworkdayjobs.com": ("myworkday.com",),
    "workable.com": ("workablemail.com",),
}
# A batch mark covers at most this many applications (one list_applications page).
INBOX_CHECK_MAX_APPLICATIONS = int(os.getenv("INBOX_CHECK_MAX_APPLICATIONS", "200"))
JOB_BOARD_HOSTS = ("linkedin.com",)  # plus indeed.<tld>, handled in job_checks
JOB_CHECK_PARTIAL_MIN_WORDS = 40   # below this the "ad" is a title or a teaser
JOB_CHECK_FULL_MIN_WORDS = 150     # at or above this the ad is treated as complete
JOB_CHECK_REPOST_GAP_DAYS = 3      # listing date vs employer date further apart than this = reposted

# R6 — the fit verdict is stored, never computed.
APPLICATION_FIT_REASONING_MAX_CHARS = int(os.getenv("APPLICATION_FIT_REASONING_MAX_CHARS", "4000"))
# The fit picture's axes (2026-09-20): the AGENT names 3..MAX dimensions and
# puts two 0-100 numbers on each (what the role asks / what the seeker brings).
# Job360 stores and draws them; the bounds keep the chart readable and the
# row small. Live settings, never a hardcoded list of axis names (rule #28).
APPLICATION_FIT_AXES_MIN = int(os.getenv("APPLICATION_FIT_AXES_MIN", "3"))
APPLICATION_FIT_AXES_MAX = int(os.getenv("APPLICATION_FIT_AXES_MAX", "8"))
APPLICATION_FIT_AXIS_NAME_MAX_CHARS = int(os.getenv("APPLICATION_FIT_AXIS_NAME_MAX_CHARS", "40"))

# S3 — how much of an OAuth client's attacker-supplied name `actor_for` keeps
# as `agent:<name>` authorship. Env-backed like every other spine cap (C8);
# was hardcoded in authorship.py before.
APPLICATION_ACTOR_NAME_MAX_CHARS = int(os.getenv("APPLICATION_ACTOR_NAME_MAX_CHARS", "60"))

# ── Follow-up dates (owner decision, 2026-09-25) ────────────────────────────
# Job360 stores a follow-up date and serves "what's due"; the user's OWN
# agent (a scheduled ChatGPT/Claude task with its Gmail connector) does the
# daily email check and writes back through record_event / list_applications.
# Job360 reads no email, runs no worker, sends no push (VISION rule 4/5).
#
# Bounds are relative to the CALLER's own "today" (spine.user_today) — a
# little past-dating is normal (catching up on a missed reply), a full year
# ahead covers almost every real recruiter promise.
APPLICATION_FOLLOW_UP_MAX_PAST_DAYS = int(os.getenv("APPLICATION_FOLLOW_UP_MAX_PAST_DAYS", "7"))
APPLICATION_FOLLOW_UP_MAX_FUTURE_DAYS = int(os.getenv("APPLICATION_FOLLOW_UP_MAX_FUTURE_DAYS", "366"))
# The `due` / `quiet_days` list filters exclude these — a closed application
# needs no chasing. Matches next_step.py's own `_CLOSED` tuple exactly (the
# real closed statuses in APPLICATION_STATUS_EVENT_TYPES; there is no
# "offer accepted" status — `offer` stays open until the caller records what
# happened next).
APPLICATION_FOLLOW_UP_CLOSED_STATUSES = ("rejected", "withdrawn", "ghosted", "hired", "offer_declined")
# Feedback loop slice 1 (owner decisions #1, #4): an application still at `applied` this many
# days after its newest status event is marked `ghosted` by Job360 itself
# (services/applications/ghosting.py). An operator constant, not a user setting.
GHOSTED_AFTER_DAYS = int(os.getenv("GHOSTED_AFTER_DAYS", "45"))
GHOSTED_SWEEP_MAX = int(os.getenv("GHOSTED_SWEEP_MAX", "200"))
# Feedback loop slice 4 (interview side). Operator constants, not user settings.
INTERVIEW_COURTESY_DAYS = int(os.getenv("INTERVIEW_COURTESY_DAYS", "30"))  # rejection-latency.mjs:71
INTERVIEW_SILENT_MAX_ITEMS = int(os.getenv("INTERVIEW_SILENT_MAX_ITEMS", "20"))  # NOT IN REFERENCE - a bound
INTERVIEW_VIEW_MAX_EVENTS = int(os.getenv("INTERVIEW_VIEW_MAX_EVENTS", "5000"))  # NOT IN REFERENCE - a bound
FRICTION_MIN_ROUNDS = int(os.getenv("FRICTION_MIN_ROUNDS", "1"))  # process-quality.mjs:70-73, :173
DIGEST_RECURRING_MIN = int(os.getenv("DIGEST_RECURRING_MIN", "2"))  # weekly-digest.mjs:250
# quiet_days (R "gone quiet" list filter) — a live cap, not a hardcoded 365,
# so a breach is a 422 an agent can read rather than a silently clipped value.
APPLICATION_QUIET_DAYS_MAX = int(os.getenv("APPLICATION_QUIET_DAYS_MAX", "365"))

# ── Per-user timezone (users.timezone, migration 0012 — column existed but
# nothing read or wrote it until this feature). Bounds the PUT body; the
# actual "is this a real zone" check is ZoneInfo itself (services/applications
# /spine.py::user_today, api/routes/auth.py's timezone route) — this cap only
# keeps an absurdly long string away from that lookup.
USER_TIMEZONE_MAX_CHARS = int(os.getenv("USER_TIMEZONE_MAX_CHARS", "64"))

# R9 — whats_new.
WHATS_NEW_DEFAULT_WINDOW_DAYS = int(os.getenv("WHATS_NEW_DEFAULT_WINDOW_DAYS", "7"))
WHATS_NEW_MAX_EVENTS = int(os.getenv("WHATS_NEW_MAX_EVENTS", "200"))

# R10/S8 — export_history: bounds explicit, never silent; rate-limited per USER
# (never per IP — every agent shares the proxy IP behind the Next rewrite
# unless JOB360_TRUST_PROXY=1, the trap the OAuth slice documented).
EXPORT_HISTORY_MAX_APPLICATIONS = int(os.getenv("EXPORT_HISTORY_MAX_APPLICATIONS", "500"))
EXPORT_HISTORY_MAX_BYTES = int(os.getenv("EXPORT_HISTORY_MAX_BYTES", str(8 * 1024 * 1024)))
EXPORT_HISTORY_MAX_PER_HOUR = int(os.getenv("EXPORT_HISTORY_MAX_PER_HOUR", "12"))
# Feature 4 — "Download everything" (GET /api/auth/users/me/export): per-user cap.
USER_EXPORT_MAX_PER_HOUR = int(os.getenv("USER_EXPORT_MAX_PER_HOUR", "6"))
# Slice 4 review (S3) — the export's top-level `profile_edits` blob is bounded
# like everything else it carries: the NEWEST N rows, rendered oldest-first,
# with `profile_edits_truncated` saying when the tail was cut.
EXPORT_HISTORY_MAX_PROFILE_EDITS = int(os.getenv("EXPORT_HISTORY_MAX_PROFILE_EDITS", "500"))
# Bug fix (coordinator review, 2026-09-26) — unlinked (cold) contacts are
# fetched from Postgres a PAGE at a time (never the whole table into memory
# first), keyed by `id >= unlinked_after_id`; `unlinked_next_after_id` names
# where the next page starts.
EXPORT_HISTORY_UNLINKED_PAGE_SIZE = int(os.getenv("EXPORT_HISTORY_UNLINKED_PAGE_SIZE", "50"))

# ── Slice 4 (docs/plans/2026-09-05-contacts-stats/spec.md) ─────────────────
# S4 — every cap a parameter; a breach is a 422 naming the field and the
# limit, never a silent clip.

# add_contact (R1/R2). Contacts are add-only rows on an application.
CONTACT_NAME_MAX_CHARS = int(os.getenv("CONTACT_NAME_MAX_CHARS", "200"))
CONTACT_ROLE_MAX_CHARS = int(os.getenv("CONTACT_ROLE_MAX_CHARS", "200"))
CONTACT_EMAIL_MAX_CHARS = int(os.getenv("CONTACT_EMAIL_MAX_CHARS", "254"))
CONTACT_LINKEDIN_URL_MAX_CHARS = int(os.getenv("CONTACT_LINKEDIN_URL_MAX_CHARS", "300"))
CONTACT_SOURCE_URL_MAX_CHARS = int(os.getenv("CONTACT_SOURCE_URL_MAX_CHARS", "1000"))
CONTACT_COMPANY_MAX_CHARS = int(os.getenv("CONTACT_COMPANY_MAX_CHARS", "200"))
CONTACT_SHARED_MAX_CHARS = int(os.getenv("CONTACT_SHARED_MAX_CHARS", "300"))
CONTACT_NOTES_MAX_CHARS = int(os.getenv("CONTACT_NOTES_MAX_CHARS", "2000"))
CONTACTS_PER_APPLICATION_MAX = int(os.getenv("CONTACTS_PER_APPLICATION_MAX", "50"))
CONTACTS_MAX_PER_HOUR = int(os.getenv("CONTACTS_MAX_PER_HOUR", "120"))  # S7 — per USER

# ── Outreach tracking (owner decisions, 2026-09-25) ─────────────────────────
# People: a contact can be linked to a job or to none (cold networking). The
# user's own assistant WRITES messages; the USER sends them (or, with assistant_settings.outreach_send = auto,
# the assistant sends from the user's own Gmail); Job360 never sends.
OUTREACH_CHANNELS = ("linkedin", "email", "other")
# contact_outreach.entry — free TEXT in the DB, closed here. `bounced` (pack #7): a
# delivery failure for an outreach email; its `text` is the address that bounced.
OUTREACH_ENTRIES = ("message", "sent", "reply", "bounced")
OUTREACH_MESSAGE_MAX_CHARS = int(os.getenv("OUTREACH_MESSAGE_MAX_CHARS", "5000"))
# LinkedIn connection-request note limits: career-ops (MIT) modes/contacto.md:168
# "200 characters on a free account, 300 on Premium/Sales Navigator ... Default to the
# safer 200-char budget unless the user has confirmed". LinkedIn's own facts, not
# operator knobs (no env). Warn-only save check: cv_checks.linkedin_note_too_long.
LINKEDIN_NOTE_MAX_CHARS = 200
LINKEDIN_NOTE_MAX_CHARS_PREMIUM = 300
OUTREACH_VERSIONS_PER_CONTACT_MAX = int(os.getenv("OUTREACH_VERSIONS_PER_CONTACT_MAX", "50"))
# Outreach pack #13 — the caller's own variant label on a message version / a template
# (stats `by_outreach_variant` groups on lower(trim(label))). Free text, one line.
VARIANT_LABEL_MAX_CHARS = 100
# User-level templates (not tied to a job or a person): reusable wording the assistant
# fills in itself. Versioned per label, append-only. Job360 never fills, renders or sends one.
USER_TEMPLATE_KINDS = ("outreach_template",)
USER_TEMPLATE_MAX_CHARS = int(os.getenv("USER_TEMPLATE_MAX_CHARS", "3000"))
USER_TEMPLATE_LABELS_MAX = int(os.getenv("USER_TEMPLATE_LABELS_MAX", "10"))
USER_TEMPLATE_VERSIONS_PER_LABEL_MAX = int(os.getenv("USER_TEMPLATE_VERSIONS_PER_LABEL_MAX", "50"))
OUTREACH_ENTRIES_PER_CONTACT_MAX = int(os.getenv("OUTREACH_ENTRIES_PER_CONTACT_MAX", "200"))
CONTACTS_UNLINKED_MAX = int(os.getenv("CONTACTS_UNLINKED_MAX", "500"))
CONTACT_EDITS_PER_CONTACT_MAX = int(os.getenv("CONTACT_EDITS_PER_CONTACT_MAX", "200"))
OUTREACH_MAX_PER_HOUR = int(os.getenv("OUTREACH_MAX_PER_HOUR", "120"))  # S7 — per USER
# The outreach SEND gate (check_outreach, feature 10). Operator ceilings, not user
# settings: OUTREACH_MAX_PER_HOUR above throttles WRITES; these cap SENDS. Only
# recorded `sent` marks count. Day = the user's own today (as for check_submit).
OUTREACH_AUTO_SENDS_MAX_PER_DAY = int(os.getenv("OUTREACH_AUTO_SENDS_MAX_PER_DAY", "10"))
# At most this many sends to people at the same (lower-cased) job_company within
# the window, so one company is never carpet-bombed.
OUTREACH_COMPANY_MAX = int(os.getenv("OUTREACH_COMPANY_MAX", "2"))
OUTREACH_COMPANY_WINDOW_DAYS = int(os.getenv("OUTREACH_COMPANY_WINDOW_DAYS", "14"))
# Outreach pack #5/#6 (2026-10-20): contact ledger entries beyond message/sent/reply. Stored
# only — Job360 sends nothing, and these never count as sends (check_outreach reads `sent`).
CONTACT_INTERACTION_ENTRIES = ("meeting", "call", "intro")
CONTACT_REFERRAL_ENTRIES = ("referral_asked", "referral_given")
CONTACT_INTERACTION_CHANNELS = ("call", "video", "in_person")
# The ONE channel set every meeting/call/intro/referral entry accepts (a store tool refuses no
# true fact). OUTREACH_CHANNELS itself stays narrow: it gates sends.
CONTACT_LEDGER_CHANNELS = OUTREACH_CHANNELS + CONTACT_INTERACTION_CHANNELS
# The job-timeline event a ledger mark appends when its contact is linked to an application.
CONTACT_LEDGER_EVENT_TYPES = {
    "sent": "outreach_sent", "reply": "outreach_replied", "meeting": "contact_meeting",
    "call": "contact_call", "intro": "contact_intro", "referral_asked": "referral_asked",
    "referral_given": "referral_given",
}
# Their own budgets, apart from the outreach ones: an interaction never spends a send's budget.
CONTACT_INTERACTIONS_PER_CONTACT_MAX = int(os.getenv("CONTACT_INTERACTIONS_PER_CONTACT_MAX", "100"))
CONTACT_INTERACTIONS_MAX_PER_HOUR = int(os.getenv("CONTACT_INTERACTIONS_MAX_PER_HOUR", "60"))  # per USER
# Automatic employer emails (feedback loop slice 2; check_auto_email). Operator constants, not user
# settings: how long to wait before each email, and the most that go out per user per day.
AUTO_EMAIL_CHECKIN_DAYS = int(os.getenv("AUTO_EMAIL_CHECKIN_DAYS", "7"))
AUTO_EMAIL_FOLLOWUP_DAYS = int(os.getenv("AUTO_EMAIL_FOLLOWUP_DAYS", "7"))
AUTO_EMAILS_MAX_PER_DAY = int(os.getenv("AUTO_EMAILS_MAX_PER_DAY", "10"))
LIST_PEOPLE_MAX = int(os.getenv("LIST_PEOPLE_MAX", "500"))
# known_people: at most this many people in a bring/kit/detail payload; count is the full number.
KNOWN_PEOPLE_MAX = int(os.getenv("KNOWN_PEOPLE_MAX", "20"))
# Owner decision 2026-10-04 — where a person was found. Closed set; unset =
# NULL (rule #29). Editable like the other fields (a contact_edits row).
CONTACT_FOUND_VIA = (
    "company_site", "linkedin", "apollo", "referral", "job_ad", "email", "event",
    "company_team_page", "github", "speaker_page", "email_finder", "other",
)
# Outreach pack #7 — where the contact's EMAIL came from (the person's origin is
# found_via). Closed set; unset = NULL (rule #29). `pattern_guess` makes
# check_outreach answer ask (email_guessed). Editable like found_via (a contact_edits row).
CONTACT_EMAIL_SOURCES = ("they_emailed", "on_company_page", "provider_verified", "pattern_guess", "user_gave")
# Outreach pack #12 — how the user knows this person. Closed set; unset = NULL
# (rule #29); "none" is an explicit answer. The agent judges, Job360 stores.
CONTACT_RELATIONSHIP = (
    "former_colleague", "classmate", "alumni", "friend", "recruiter", "hiring_manager", "interviewer", "none",
)
# The profile record lists a `shared` pointer may name (get_profile `fields` paths).
CONTACT_SHARED_SOURCES = ("cv_data.cv_positions", "cv_data.cv_projects", "cv_data.education")
CONTACT_EDIT_FIELDS = (
    "name", "role", "email", "linkedin_url", "notes", "found_via", "source_url", "company", "email_source",
    "relationship", "shared",
)

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

# ── Feedback numbers (slice 3): operator constants, NOT user settings ────────
# Each mirrors a career-ops constant (file:line in services/applications/
# feedback_numbers.py). Env-backed like GHOSTED_AFTER_DAYS.
STATS_SMALL_SAMPLE_N = int(os.getenv("STATS_SMALL_SAMPLE_N", "10"))  # stats.mjs:182
STATS_CLAIM_MIN_N = int(os.getenv("STATS_CLAIM_MIN_N", "20"))  # funnel-velocity.mjs:26,105
VELOCITY_HOP_MIN_N = int(os.getenv("VELOCITY_HOP_MIN_N", "3"))  # funnel-velocity.mjs:106
CALIBRATION_MIN_BAND_N = int(os.getenv("CALIBRATION_MIN_BAND_N", "5"))  # calibrate.mjs:136
CALIBRATION_GAP_POINTS = int(os.getenv("CALIBRATION_GAP_POINTS", "10"))  # calibrate.mjs:200
CALIBRATION_FEEDBACK_MAX = int(os.getenv("CALIBRATION_FEEDBACK_MAX", "10"))  # calibrate.mjs:215
# Owner #16: calibrate.mjs:84-89 (3.5/4.0/4.5 of 5) re-mapped to 0-100. Bands:
# "<70", "70-79", "80-89", ">=90" (score >= min and < max).
CALIBRATION_BAND_EDGES = (70, 80, 90)
COMPANY_SILENCE_WINDOW_DAYS = int(os.getenv("COMPANY_SILENCE_WINDOW_DAYS", "28"))  # company-history.mjs:79
COMPANY_STALE_AFTER_DAYS = int(os.getenv("COMPANY_STALE_AFTER_DAYS", "365"))  # company-history.mjs:78
COMPANY_FACTS_MAX = int(os.getenv("COMPANY_FACTS_MAX", "50"))  # NOT IN REFERENCE - a bound
STATS_COMPANY_MAX_CHARS = int(os.getenv("STATS_COMPANY_MAX_CHARS", "200"))
# The `view` values of stats. "all" is computed as every member except "all", so
# a view a later slice appends (before "all") joins it automatically.
STATS_VIEWS = (
    "funnel", "velocity", "calibration", "company", "lesson_effect", "followups",
    "digest", "friction", "assessments", "patterns", "skill_gaps", "sites", "referrals", "all",
)
# Furthest-stage ladder (funnel-stages.mjs:3-6) in Job360 names. Rank 3
# (assessment) stays EMPTY for good: slice 4 records `assessment` as a NOTE
# event, so it never enters this table. brought/ghosted/withdrawn have no rank.
FEEDBACK_FUNNEL_RANK: dict[str, int] = {
    _status_event("applied"): 1,
    _status_event("replied"): 2,
    _status_event("rejected"): 2,  # a rejection is an answer
    _status_event("interview_requested"): 4,
    _status_event("interview_scheduled"): 4,
    _status_event("interview_done"): 4,
    _status_event("offer"): 5,
    _status_event("offer_declined"): 5,  # an offer was reached (calibrate.mjs:66)
    _status_event("hired"): 6,
}
# Plain strings on purpose, not validated: slice 2 adds these note types in parallel.
FEEDBACK_FOLLOWUP_EVENT_TYPES = ("checkin_sent", "feedback_asked", "feedback_followup_sent")
FEEDBACK_SKIP_EVENT_TYPE = "auto_email_skipped"

# Slice 5 — fit and patterns
# career-ops file:line cites are in services/applications/fit_record.py,
# fit_patterns.py and reposts.py. Env-backed operator constants.
FIT_SCORE_REASON_MAX_CHARS = int(os.getenv("FIT_SCORE_REASON_MAX_CHARS", "140"))  # rank-pipeline.mjs:53
# Who/what produced the score (modes/pipeline.md:108-113). Values NOT IN REFERENCE.
FIT_SCORE_SOURCES = ("full_ad", "listing", "owner")
FIT_GAP_KINDS = ("hard", "soft")  # analyze-patterns.mjs:999-1002
FIT_GAP_TEXT_MAX_CHARS = int(os.getenv("FIT_GAP_TEXT_MAX_CHARS", "200"))  # NOT IN REFERENCE - a bound
FIT_JD_REQUIREMENTS_MAX = int(os.getenv("FIT_JD_REQUIREMENTS_MAX", "50"))  # NOT IN REFERENCE - a bound
FIT_ARCHETYPE_MAX_CHARS = int(os.getenv("FIT_ARCHETYPE_MAX_CHARS", "80"))  # analyze-patterns.mjs:1418
FIT_REMOTE_POLICIES = ("remote", "hybrid", "onsite")  # analyze-patterns.mjs:1095-1107
FIT_VIA = ("agency", "direct")  # analyze-patterns.mjs:321-334
FIT_TEAM_SIZE_MAX = 10_000_000  # a bound
FIT_TEAM_SIZE_BUCKETS = (50, 500)  # analyze-patterns.mjs:1117-1127: <=50, <=500, else
PATTERNS_MIN_SUBMITTED = int(os.getenv("PATTERNS_MIN_SUBMITTED", "5"))  # analyze-patterns.mjs:96
PATTERNS_MIN_DECIDED = int(os.getenv("PATTERNS_MIN_DECIDED", "2"))  # analyze-patterns.mjs:161-164
PATTERNS_LEAD_POINTS = int(os.getenv("PATTERNS_LEAD_POINTS", "10"))  # analyze-patterns.mjs:1569,1585
PATTERNS_REASONS_MAX = int(os.getenv("PATTERNS_REASONS_MAX", "20"))  # NOT IN REFERENCE - a bound
SKILL_GAPS_MIN_SCORED = int(os.getenv("SKILL_GAPS_MIN_SCORED", "5"))  # upskill.mjs:1076,483-490
SKILL_GAPS_LOW_FIT_SCORE = int(os.getenv("SKILL_GAPS_LOW_FIT_SCORE", "80"))  # upskill.mjs:98 (4.0 of 5 x20)
SKILL_GAPS_MAX = int(os.getenv("SKILL_GAPS_MAX", "50"))  # NOT IN REFERENCE - a bound
SKILL_GAP_REPORT_SCHEMA = 1  # upskill.mjs:85-94
REPOST_WINDOW_DAYS = int(os.getenv("REPOST_WINDOW_DAYS", "90"))  # detect-reposts.mjs:79
REPOST_MIN_SPAN_DAYS = int(os.getenv("REPOST_MIN_SPAN_DAYS", "1"))  # detect-reposts.mjs:87
# Similar-ad check (posting_history.py): career-ops fingerprint-core.mjs. Same ad text,
# another link / title / company, among THIS user's own brings in the repost window.
SIMILAR_AD_MIN_TEXT = 200  # fingerprint-core.mjs:26 FINGERPRINT_MIN_TEXT
SIMILAR_AD_THRESHOLD = 0.92  # fingerprint-core.mjs:30 CROSSLIST_THRESHOLD (at most 5 of 64 bits differ)
SIMILAR_AD_MAX_ROWS = int(os.getenv("SIMILAR_AD_MAX_ROWS", "500"))  # NOT IN REFERENCE - a bound
SIMILAR_AD_MAX_LISTED = 3  # NOT IN REFERENCE - a bound
# Posting signs (posting_history.py): freehire internal/job/jobreality/classify.go:21
# oldAgeDays = 90, and ghost/classify.go:90 convergence = 2 (one sign alone says nothing).
POSTING_SIGN_OLD_DAYS = 90
POSTING_SIGNS_CONVERGE = 2

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
    # Slice 7 — ISO alpha-2 codes where the candidate needs no sponsorship.
    "preferences.work_authorization_countries",
    # Owner decision 2026-09-25 — "the daily check offer, remembered by
    # Job360, not by any one assistant" (mcp_server.py INSTRUCTIONS).
    "preferences.daily_check",
    # Owner decision 2026-10-03 — how often that check runs (3h/6h/12h/24h);
    # daily_check also holds the inbox mode auto/ask/paused. Both stored by Job360.
    "preferences.check_every",
    # S16 - the user's LinkedIn tier (bool): true = Premium / Sales Navigator, so a
    # connection note may run to 300 characters instead of 200. Overlay-only.
    "preferences.linkedin_premium",
    # Slice B2 (decision 28) — dated work history and projects, as lists of
    # records with a closed key set (services/profile/edits.py RECORD_SCHEMAS).
    "cv_data.cv_positions", "cv_data.cv_projects",
    # Standing instructions for the assistant — one short line each
    # (PROFILE_NOTE_MAX_CHARS), at most PROFILE_EDIT_MAX_LIST_ITEMS lines.
    "preferences.assistant_notes",
    # Owner decision 2026-10-10 - what the user is looking for (setup interview,
    # targets round): years of experience (0-60, min <= max), what they look for,
    # dealbreakers, companies to avoid. Empty = don't care (rule #29).
    "preferences.experience_years_min", "preferences.experience_years_max",
    "preferences.looking_for", "preferences.dealbreakers", "preferences.avoid_companies",
    "preferences.target_companies",
    # Owner decision 2026-10-08 — THREE STORES. (1) The salary the user wants,
    # one record per hiring country, is a normal PREFERENCE. (2) USER INFO
    # MEMORY — the facts job forms ask (contact, right to work, logistics,
    # languages, equality answers, approved free-text answers) — lives in its
    # own `user_profiles.user_info` column under the `user_info.*` head, written
    # only by storage.save_user_info. (3) The assistant's edits ride the
    # profile_edits overlay on top of both. Shapes: services/profile/user_info.py.
    "preferences.salary_by_country",
    "user_info.contact", "user_info.right_to_work", "user_info.logistics",
    "user_info.languages", "user_info.equality", "user_info.answers",
    # Owner decision 2026-10-08 (S2) — ASSISTANT SETTINGS, a fourth head with
    # its own `user_profiles.assistant_settings` column (default '{}'). The
    # values ride the same profile_edits overlay (who/when history for free).
    # A RISKIER change from an assistant is never written here: it becomes a
    # waiting request the user confirms on the website
    # (services/profile/setting_requests.py). Rules: services/profile/assistant_settings.py.
    # apply_mode is RETIRED (2026-10-10): the user's own score line plus two
    # separate switches (above / below it) replace it.
    "assistant_settings.apply_min_score",
    "assistant_settings.above_line", "assistant_settings.below_line",
    "assistant_settings.submit_mode", "assistant_settings.daily_cap",
    # Owner decision 2026-10-18 - the per-company limit and its window in days.
    "assistant_settings.company_cap", "assistant_settings.company_cap_days",
    # Bulk-apply C1 / C2 / R1 - the weekly cap, the pace (gap + apply hours) and the counted auto grant.
    "assistant_settings.weekly_cap", "assistant_settings.min_gap_minutes", "assistant_settings.apply_hours",
    "assistant_settings.auto_grant_count", "assistant_settings.auto_grant_until",
    "assistant_settings.paused_until", "assistant_settings.pause_reason",
    # Feedback loop slice 2 - the owner's switch for automatic employer emails (bool).
    "assistant_settings.auto_employer_emails",
    # Owner 2026-10-20 (G087) - may the assistant send outreach itself (draft_only | auto); its own consent.
    "assistant_settings.outreach_send",
    # Owner 2026-10-23 - hunt for jobs every N hours (1-168; null / 0 = off). Turning it on is riskier.
    "assistant_settings.hunt_every_hours",
    # S4 - setup progress (six rounds, each with a done_at). Not a gated setting.
    "assistant_settings.setup_progress",
)
# Env-added paths must ALSO be declared dataclass fields — an unknown one is a
# startup error, not an accepted path.
PROFILE_EXTRA_EDITABLE_PATHS = _env_list("PROFILE_EXTRA_EDITABLE_PATHS", ())
PROFILE_EDIT_MAX_PATHS_PER_CALL = int(os.getenv("PROFILE_EDIT_MAX_PATHS_PER_CALL", "30"))
PROFILE_EDIT_MAX_CHARS = int(os.getenv("PROFILE_EDIT_MAX_CHARS", "2000"))
PROFILE_EDIT_MAX_LIST_ITEMS = int(os.getenv("PROFILE_EDIT_MAX_LIST_ITEMS", "100"))
PROFILE_EDIT_MAX_ITEM_CHARS = int(os.getenv("PROFILE_EDIT_MAX_ITEM_CHARS", "200"))
PROFILE_EDIT_MAX_PER_HOUR = int(os.getenv("PROFILE_EDIT_MAX_PER_HOUR", "120"))  # S7 — per USER
# Slice B2 — the record-list paths (cv_data.cv_positions / cv_data.cv_projects).
# Short fields (title, company, location, dates, name, one technology) reuse
# PROFILE_EDIT_MAX_ITEM_CHARS; a project description reuses PROFILE_EDIT_MAX_CHARS.
PROFILE_EDIT_MAX_RECORDS = int(os.getenv("PROFILE_EDIT_MAX_RECORDS", "50"))  # records per list
# bullets / technologies per record
PROFILE_EDIT_MAX_RECORD_ITEMS = int(os.getenv("PROFILE_EDIT_MAX_RECORD_ITEMS", "30"))
PROFILE_EDIT_MAX_BULLET_CHARS = int(os.getenv("PROFILE_EDIT_MAX_BULLET_CHARS", "500"))
# The ENCODED bound for one record-list write (S9). PROFILE_EDIT_MAX_CHARS
# (2000) bounds every other path, but one honest work history is longer than
# that, so the record lists get their own ceiling.
PROFILE_EDIT_MAX_RECORDS_CHARS = int(os.getenv("PROFILE_EDIT_MAX_RECORDS_CHARS", "60000"))
# preferences.assistant_notes — the length of ONE note (a line, not an essay).
PROFILE_NOTE_MAX_CHARS = int(os.getenv("PROFILE_NOTE_MAX_CHARS", "200"))
# Assistant settings (S2, owner decision 2026-10-08). The most applications a
# user may cap themselves to per day; no cap is the default (owner: "no cap by
# default"), this is only the ceiling a typed cap may take.
ASSISTANT_DAILY_CAP_MAX = int(os.getenv("ASSISTANT_DAILY_CAP_MAX", "500"))
# Owner decision 2026-10-18: the per-company limit (applications to ONE company
# inside a window). Like the daily cap there is no limit by default; these are only
# the ceiling a typed limit may take and the window used when the user set a limit
# without choosing days.
ASSISTANT_COMPANY_CAP_MAX = int(os.getenv("ASSISTANT_COMPANY_CAP_MAX", "50"))
ASSISTANT_COMPANY_CAP_DEFAULT_DAYS = int(os.getenv("ASSISTANT_COMPANY_CAP_DEFAULT_DAYS", "180"))
# Bulk-apply C2 / R1: the ceiling a typed weekly cap may take, the most a counted auto grant may allow,
# and how far ahead its end may be (a grant that never ends would be the old standing auto mode).
ASSISTANT_WEEKLY_CAP_MAX = int(os.getenv("ASSISTANT_WEEKLY_CAP_MAX", "2000"))
ASSISTANT_AUTO_GRANT_MAX = int(os.getenv("ASSISTANT_AUTO_GRANT_MAX", "100"))
ASSISTANT_AUTO_GRANT_MAX_DAYS = int(os.getenv("ASSISTANT_AUTO_GRANT_MAX_DAYS", "7"))
# A waiting (riskier) change request the user never answered stops counting
# after this many days; the assistant has to ask again.
ASSISTANT_SETTING_REQUEST_TTL_DAYS = int(os.getenv("ASSISTANT_SETTING_REQUEST_TTL_DAYS", "7"))
# How many waiting requests ONE user's assistants may create per hour — a loop
# cannot bury the Needs-you page. Counted off the table, never per IP.
ASSISTANT_SETTING_REQUESTS_MAX_PER_HOUR = int(os.getenv("ASSISTANT_SETTING_REQUESTS_MAX_PER_HOUR", "20"))
# Owner decision 2026-10-23 - "what is due now?" for the user's OWN scheduled task (migration 0081).
# The clock lives at the AI vendor; Job360 only answers. SCHEDULE_TICK_HOURS is how often that task
# is expected to wake (returned as `expected_every_hours`). Duties are a closed set; `tick` is the
# stamp of the last scheduled run. Reads and marks are per-user rate limited (a loop cannot hammer).
SCHEDULE_TICK_HOURS = int(os.getenv("SCHEDULE_TICK_HOURS", "1"))
SCHEDULE_DUTIES: tuple[str, ...] = ("hunt", "inbox", "batch")
SCHEDULE_DUTY_RECIPES: dict[str, str] = {"hunt": "hunt", "inbox": "daily", "batch": "batch"}
SCHEDULE_READS_MAX_PER_HOUR = int(os.getenv("SCHEDULE_READS_MAX_PER_HOUR", "60"))
SCHEDULE_MARKS_MAX_PER_HOUR = int(os.getenv("SCHEDULE_MARKS_MAX_PER_HOUR", "60"))
# Sites where auto-submit never runs: the assistant fills the form, stops and
# asks yes. Matched against whole host labels (indeed.co.uk, uk.indeed.com,
# linkedin.com, lnkd.in all hit; notindeed.com does not). Over-blocking only
# asks, so a loose match is the safe direction. Owner: Indeed bans automated apply.
SUBMIT_ASK_ALWAYS_BRANDS = _env_list("SUBMIT_ASK_ALWAYS_BRANDS", ("indeed", "linkedin", "lnkd"))
# ── S3 application kit (owner decisions 2026-10-08). Every number a parameter. ──
# A file link the kit hands out: how long it lives and how often it downloads.
KIT_LINK_TTL_MINUTES = int(os.getenv("KIT_LINK_TTL_MINUTES", "30"))
KIT_LINK_MAX_DOWNLOADS = int(os.getenv("KIT_LINK_MAX_DOWNLOADS", "3"))
# Kit reads one user may make per hour (each mints links + writes an event).
KIT_DOC_TEXT_MAX_CHARS = int(os.getenv("KIT_DOC_TEXT_MAX_CHARS", "8000"))
KIT_APPROVED_TEXT_MAX = int(os.getenv("KIT_APPROVED_TEXT_MAX", "25"))
KIT_READS_MAX_PER_HOUR = int(os.getenv("KIT_READS_MAX_PER_HOUR", "60"))
# Public /api/files/{token}: downloads per link OWNER per minute (not per IP:
# behind the Next rewrite every caller may share one address), and bad tokens
# per IP per minute before that IP's bad tokens are refused (a valid link is
# never locked out).
FILE_DOWNLOADS_MAX_PER_MIN = int(os.getenv("FILE_DOWNLOADS_MAX_PER_MIN", "30"))
FILE_BAD_TOKEN_MAX_PER_MIN = int(os.getenv("FILE_BAD_TOKEN_MAX_PER_MIN", "20"))
# Private calendar feed (GET /api/calendar/{token}.ics, migration 0058): fetches per feed
# OWNER per hour (calendar apps poll slowly; this only stops a leaked URL being hammered).
CALENDAR_FEED_MAX_PER_HOUR = int(os.getenv("CALENDAR_FEED_MAX_PER_HOUR", "30"))
# How far back the feed reaches for interviews (days).
CALENDAR_FEED_LOOKBACK_DAYS = int(os.getenv("CALENDAR_FEED_LOOKBACK_DAYS", "30"))
# Reminders (VALARM) in the feed: an interview alerts this many minutes before it starts
# (two alarms; a value <= 0 drops that alarm), a follow-up all-day event alerts at this
# hour of the day (0-23).
CALENDAR_ALARM_INTERVIEW_FIRST_MINUTES = int(os.getenv("CALENDAR_ALARM_INTERVIEW_FIRST_MINUTES", "1440"))
CALENDAR_ALARM_INTERVIEW_SECOND_MINUTES = int(os.getenv("CALENDAR_ALARM_INTERVIEW_SECOND_MINUTES", "60"))
CALENDAR_ALARM_FOLLOW_UP_HOUR = int(os.getenv("CALENDAR_ALARM_FOLLOW_UP_HOUR", "9"))
# "Another assistant is on this application": a kit read by someone else this
# recently warns (never blocks).
KIT_HOLD_MINUTES = int(os.getenv("KIT_HOLD_MINUTES", "30"))
# Gap roadmap slice 8: the user's Send yes (``submit_approved``) counts for this many minutes
# after it was recorded; an older yes is ``approval_stale`` (expired) and check_submit asks again.
SUBMIT_APPROVAL_TTL_MINUTES = int(os.getenv("SUBMIT_APPROVAL_TTL_MINUTES", "120"))
# A second application at the same company inside this window is flagged.
DUPLICATE_COMPANY_WINDOW_DAYS = int(os.getenv("DUPLICATE_COMPANY_WINDOW_DAYS", "30"))
# Bulk-apply D3: the same role (same normalized company AND title) applied to inside this window
# asks `duplicate_role` (a repost on another board has a new URL). Exact equality only.
DUPLICATE_ROLE_WINDOW_DAYS = int(os.getenv("DUPLICATE_ROLE_WINDOW_DAYS", "180"))
# Hosts that need an account before you can apply (seed list; the user's own
# `account_needed` events add more). Registrable-domain match, subdomains included.
ACCOUNT_REQUIRED_HOST_SUFFIXES = _env_list(
    "ACCOUNT_REQUIRED_HOST_SUFFIXES",
    (
        "myworkdayjobs.com", "myworkdaysite.com", "taleo.net", "icims.com",
        "successfactors.com", "successfactors.eu",
    ),
)
# Most fields a `form_filled` event may claim.
KIT_FORM_FIELDS_MAX = int(os.getenv("KIT_FORM_FIELDS_MAX", "500"))
# S5d - the biggest `form_filled` payload (it may carry the answers the assistant typed).
KIT_FORM_PAYLOAD_MAX_BYTES = int(os.getenv("KIT_FORM_PAYLOAD_MAX_BYTES", "65536"))
# S5b - the most "ready to send" applications one read returns (newest first).
READY_TO_SEND_MAX = int(os.getenv("READY_TO_SEND_MAX", "50"))

# ── S7 proof of application (owner decision 2026-10-10). Every number a parameter. ──
PROOF_TEXT_MAX_CHARS = int(os.getenv("PROOF_TEXT_MAX_CHARS", "4000"))
PROOF_SCREENSHOT_MAX_BYTES = int(os.getenv("PROOF_SCREENSHOT_MAX_BYTES", str(3 * 1024 * 1024)))
# Live (not deleted) screenshots one application may hold.
PROOF_SCREENSHOTS_MAX_LIVE = int(os.getenv("PROOF_SCREENSHOTS_MAX_LIVE", "3"))
# Deadline for reading an upload body; it runs before a DB connection is borrowed so a trickling client
# cannot pin one of the pool's 10 connections.
PROOF_UPLOAD_READ_SECONDS = float(os.getenv("PROOF_UPLOAD_READ_SECONDS", "30"))
# An upload link lives this long and works once.
PROOF_LINK_TTL_MINUTES = int(os.getenv("PROOF_LINK_TTL_MINUTES", "5"))
# An applied job with no proof after this many days becomes a "Needs you" ask.
PROOF_NO_PROOF_AFTER_DAYS = int(os.getenv("PROOF_NO_PROOF_AFTER_DAYS", "7"))
# Upload links one user may mint per hour.
PROOF_LINKS_MAX_PER_HOUR = int(os.getenv("PROOF_LINKS_MAX_PER_HOUR", "30"))
# report_missing_tool (feature 7): reports ONE user's assistants may store per
# rolling day — counted off the table, so a looping agent cannot fill it.
MISSING_TOOL_REPORTS_MAX_PER_DAY = int(os.getenv("MISSING_TOOL_REPORTS_MAX_PER_DAY", "20"))
MISSING_TOOL_WHY_MAX_CHARS = 280

# GET /profile/edits/history — how many rows of one field's history come back
# (newest first). The export carries the full log.
PROFILE_EDIT_HISTORY_MAX = int(os.getenv("PROFILE_EDIT_HISTORY_MAX", "50"))
# user_info.answers — one saved question (a line), one answer (keeps
# newlines), and the ENCODED ceiling for the whole list write.
USER_INFO_QUESTION_MAX_CHARS = int(os.getenv("USER_INFO_QUESTION_MAX_CHARS", "300"))
USER_INFO_ANSWER_MAX_CHARS = int(os.getenv("USER_INFO_ANSWER_MAX_CHARS", "2000"))
USER_INFO_ANSWERS_MAX_CHARS = int(os.getenv("USER_INFO_ANSWERS_MAX_CHARS", "120000"))
# Gap 4 (slice 26) - saved answers grow by one or two per application, so they
# get their own record cap; every other list keeps PROFILE_EDIT_MAX_RECORDS.
USER_INFO_ANSWERS_MAX_RECORDS = int(os.getenv("USER_INFO_ANSWERS_MAX_RECORDS", "500"))


# Outbound HTTP defaults. Kept through slice 5 (#483) on purpose: the URL
# fetcher (#496) is the outbound caller now, and it needs a timeout and an
# honest identifying agent string.

# Sentry error tracking (Phase 3). Empty string → Sentry is disabled (no-op
# at init time). Populate in production via the SENTRY_DSN env var.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# --- Production env validation --------------------------------------------
# These vars must be non-empty when running in production.
_REQUIRED_PROD_VARS = ["SESSION_SECRET", "DATABASE_URL"]


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
# 2026-09-19 real-world run: a JS-rendered board (job-boards.greenhouse.io)
# ships only page chrome in its HTML, so the heuristic rung "found" a
# description of "Skip to Content Jump to the top…". A heuristic description
# shorter than this is dropped, which lands the user in the paste box with a
# clear sentence instead of a junk pre-fill. JSON-LD / meta descriptions are
# not subject to it — they are the site's own structured answer.
URL_FETCH_MIN_DESCRIPTION_CHARS = int(os.getenv("URL_FETCH_MIN_DESCRIPTION_CHARS", "400"))

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

# Owner decision, 2026-09-28 — two funnel events only the backend can see
# (`first_tool_call`, `first_bring`) fire through `src/services/analytics.py`.
# This is the owner's global switch, off by default; on top of it each user
# must have turned on "Help improve Job360" (`users.analytics_consent_at`,
# migration 0067) or nothing is sent for them. Off, every `capture_event`
# call is a no-op: nothing is sent, nothing is queued.
ANALYTICS_BACKEND_ENABLED = _env_flag("ANALYTICS_BACKEND_ENABLED", False)
# The SAME PostHog project the frontend posts to (its keys are
# NEXT_PUBLIC_POSTHOG_KEY / NEXT_PUBLIC_POSTHOG_HOST, PostHogProviderWrapper.tsx)
# — env var NAMES only, never hardcoded. Set these to the same values on the
# backend service so both sides land in one project; an unset key keeps
# capture_event() a no-op even with ANALYTICS_BACKEND_ENABLED on.
POSTHOG_PROJECT_API_KEY = _secret("POSTHOG_PROJECT_API_KEY")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://eu.i.posthog.com").strip().rstrip("/")


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


# -- "Needs you" (owner plan 2026-10-01) - asks the assistant raises when it
# would have to guess; the user answers once. Every cap a parameter; a breach
# is a 422 (429 for the open-ask cap) naming the setting.
ASKS_QUESTION_MAX_CHARS = int(os.getenv("ASKS_QUESTION_MAX_CHARS", "1000"))
ASKS_CONTEXT_MAX_CHARS = int(os.getenv("ASKS_CONTEXT_MAX_CHARS", "2000"))
ASKS_ANSWER_MAX_CHARS = int(os.getenv("ASKS_ANSWER_MAX_CHARS", "4000"))
ASKS_MAX_OPEN_PER_USER = int(os.getenv("ASKS_MAX_OPEN_PER_USER", "50"))
ASKS_WHATS_NEW_MAX = int(os.getenv("ASKS_WHATS_NEW_MAX", "20"))  # open asks whats_new carries


# -- The Job360 browser agent (docs/product/BROWSER_AGENT.md, VISION decision 29).
# OFF by default: the `browser` MCP tool answers "off" until the owner sets
# BROWSER_AGENT_ENABLED=1 on Railway (the same variable makes the Docker build
# install Chromium). One live browser costs ~300 MB of memory, hence the cap.
BROWSER_AGENT_ENABLED = os.getenv("BROWSER_AGENT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
BROWSER_AGENT_MAX_SESSIONS = int(os.getenv("BROWSER_AGENT_MAX_SESSIONS", "2"))
# Live sign-in views (Settings -> Job sites) have their own cap, apart from the assistants' browsers.
BROWSER_AGENT_MAX_LIVE = int(os.getenv("BROWSER_AGENT_MAX_LIVE", "2"))
BROWSER_AGENT_IDLE_SECONDS = int(os.getenv("BROWSER_AGENT_IDLE_SECONDS", "600"))


# -- Migration safety (gap roadmap slice 20). Migrations auto-apply on boot and
# every merge to main deploys, so one bad migration must fail fast, not stall
# production. migrations/runner.py issues SET LOCAL lock_timeout / statement_timeout
# inside each migration's own transaction (0 = Postgres "no limit"). Before applying
# pending migrations it copies every existing table their up.sql alters, drops,
# updates, deletes from or truncates into the `migration_snapshots` schema as
# `<table>__<migration_id>`, skipping tables over the row cap. Snapshots never run in
# test mode (pg.TEST_MODE), whatever this flag says.
MIGRATION_LOCK_TIMEOUT_S = int(os.getenv("MIGRATION_LOCK_TIMEOUT_S", "5"))
MIGRATION_STATEMENT_TIMEOUT_S = int(os.getenv("MIGRATION_STATEMENT_TIMEOUT_S", "120"))
MIGRATION_SNAPSHOTS_ENABLED = _env_flag("MIGRATION_SNAPSHOTS_ENABLED", True)
MIGRATION_SNAPSHOT_MAX_ROWS = int(os.getenv("MIGRATION_SNAPSHOT_MAX_ROWS", "200000"))
# Snapshots hold whole tables (users, applications, receipts), outside the erasure
# path, so they must not outlive the backups: every up() drops those older than this.
MIGRATION_SNAPSHOT_RETENTION_DAYS = int(os.getenv("MIGRATION_SNAPSHOT_RETENTION_DAYS", "14"))
