"""FastAPI application for Job360 backend."""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

# psycopg async requires the selector event loop on Windows (the default
# ProactorEventLoop never signals socket readiness for libpq -> the DB lifespan
# would hang on boot). Set it here so every entry point that imports the app
# (``uvicorn main:app``, ``python main.py``, ``python -m src.cli api``) is safe.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Route

from src.api.dependencies import close_db, init_db
from src.api.errors import register_exception_logging
from src.api.mcp_server import mcp_asgi, mcp_runtime
from src.api.middleware import (
    AccessLogMiddleware,
    OriginCheckMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    _is_production,
)
from src.api.routes import (
    actions,
    applications,
    auth,
    bring,
    channels,
    client_log,
    health,
    jobs,
    notification_rules,
    notifications,
    oauth,
    pipeline,
    profile,
    receipts,
    runs,
    search,
    tailor,
    tokens,
    well_known,
)
from src.core import settings
from src.core.settings import LOG_LEVEL, validate_required_env
from src.repositories import pg, pool
from src.utils.logger import setup_audit_logger, setup_logging
from src.utils.loop_guard import start_loop_watchdog


def _is_prod_env() -> bool:
    """Shared prod detection (APP_ENV=production OR RAILWAY_ENVIRONMENT set).

    Delegates to ``middleware._is_production`` so cookie Secure / HSTS / Sentry /
    CORS all gate on the exact same signal.
    """
    return _is_production()


def _resolve_cors_credentials(origins: list[str], *, is_prod: bool) -> bool:
    """Decide whether CORS may send credentials — fail closed (L3).

    * Wildcard ``*`` origin: credentials are forbidden by the CORS spec. In
      production we refuse to boot; in dev we downgrade to no-credentials.
    * Empty origin list in production: refuse to boot (misconfiguration).
    * Otherwise: allow credentials against the explicit allow-list.
    """
    has_wildcard = any(o == "*" for o in origins)
    if has_wildcard:
        if is_prod:
            raise RuntimeError(
                "CORS misconfiguration: wildcard origin '*' cannot be combined "
                "with allow_credentials=True in production. Set FRONTEND_ORIGIN "
                "to an explicit https origin."
            )
        return False
    if not origins and is_prod:
        raise RuntimeError(
            "CORS misconfiguration: no FRONTEND_ORIGIN configured in production."
        )
    return True


def _init_sentry() -> None:
    """Initialise Sentry error tracking + performance monitoring.

    Only reports from a **deployed production** environment. Guards on BOTH
    ``SENTRY_DSN`` being non-empty AND the process running in production, so
    local dev and the test suite never send errors — even when a DSN is present
    in the shared ``.env``. (Without the prod gate, local crashes like a port
    already in use or a dev DB timeout leak into the prod Sentry project and
    drown out real signal.) FastAPI / Starlette integration is automatic once
    ``sentry_sdk.init`` is called — no extra wiring required.
    """
    # Delegates to the shared init so the API and the ARQ worker report identically
    # (docs/fable/09 P0 — the worker was previously unobserved). Prod-gating + the
    # PII scrubber live in src/core/observability.py.
    from src.core.observability import init_sentry

    init_sentry(component="api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Sentry init moved to MODULE scope (below, before `app = FastAPI(...)`) —
    # see the comment there. Initialising it here was too late for the tracing
    # integration and left backend performance monitoring silently dead.
    # Tier-A Step-0 #9 — honour LOG_LEVEL env var at process boot.
    # setup_logging() configures the "job360" subtree; we also set the root
    # logger so libraries (uvicorn, fastapi, httpx) inherit the same level
    # when they haven't been individually configured.
    setup_logging(LOG_LEVEL)
    setup_audit_logger()
    logging.getLogger().setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    # Fail fast in production when required env vars are absent (no-op in dev).
    validate_required_env()
    # OAuth R1: the discovery documents advertise SITE_BASE_URL as the issuer.
    # The default is production's URL — wrong for every other instance, and a
    # wrong issuer makes every MCP client refuse the metadata. Warn once here,
    # not at import (routes/well_known.py used to, on every pytest collection).
    if not settings.SITE_BASE_URL_IS_EXPLICIT:
        logging.getLogger("job360.oauth").warning(
            "oauth: SITE_BASE_URL is not set — discovery documents will advertise %s, "
            "which is wrong for anything but production", settings.SITE_BASE_URL,
        )
    await init_db()
    # Open the request-path connection pool at boot (production only). Doing it
    # here surfaces a bad DSN / unreachable DB at startup instead of on the first
    # request, and warms min_size connections. In TEST_MODE the request path does
    # not use the pool (schema-per-test isolation needs a fresh connection each
    # time), so opening it would just tie up connections it never serves.
    if not pg.TEST_MODE:
        await pool.get_pool()
    # Loop-lag watchdog — the catch-all backstop behind @cpu_bound. The
    # decorator only guards code someone remembered to decorate; this samples
    # the loop's OWN responsiveness and reports any real stall (>0.5 s) to the
    # log and Sentry, whatever caused it. Three separate loop-freeze bugs have
    # shipped to prod (PR #123 search, the CV-upload stall, the catalog
    # backfill) and each was diagnosed only after users complained. Gated on
    # LOOP_WATCHDOG_ENABLED (default on); forced OFF under pytest, where the
    # instant-asyncio.sleep fixture would turn the sampler into a busy-spin.
    watchdog = start_loop_watchdog()
    # MCP: the SDK session manager needs a running task group for the life of
    # the process (src/api/mcp_server.py). Tests enter mcp_runtime() themselves
    # because the auth fixture replaces this lifespan with a no-op.
    try:
        async with mcp_runtime():
            yield
    finally:
        if watchdog is not None:
            watchdog.cancel()
    await close_db()
    # Idempotent — a no-op if the pool was never opened (e.g. TEST_MODE).
    await pool.close_pool()


# Sentry MUST initialise BEFORE the FastAPI/Starlette app object is created:
# Starlette builds its middleware stack at construction time, and Sentry's
# Starlette/FastAPI tracing integration patches at `sentry_sdk.init` time.
# When init ran inside the lifespan (post-construction), error capture still
# worked (global logging hooks) but performance tracing could NEVER emit a
# request transaction — the prod Sentry project had arq-worker transactions
# yet zero backend http.server transactions in its entire history. Prod-gated
# inside init_sentry, so this is a no-op in dev/tests.
_init_sentry()

app = FastAPI(title="Job360 API", version="1.0.0", lifespan=lifespan)

# Gap E — log every unhandled exception (traceback + request_id) into data/logs/.
register_exception_logging(app)

# CORS — env-driven so dev / staging / prod can differ without a rebuild.
# Default keeps Batch 1 behaviour (localhost:3000) so existing dev flows work.
_origins = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
_allow_origins = [o.strip() for o in _origins if o.strip()]
# L3 — fail closed: wildcard/empty origins can never ship credentials in prod.
_allow_credentials = _resolve_cors_credentials(_allow_origins, is_prod=_is_prod_env())
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)
# SecurityHeadersMiddleware is added FIRST (innermost in LIFO) so it stamps
# security headers on the final response — after routing and all other
# middleware have finished. This guarantees headers appear on every response
# including CORS pre-flight and error responses.
app.add_middleware(SecurityHeadersMiddleware)
# OriginCheckMiddleware — CSRF defence-in-depth: reject unsafe-method requests
# whose Origin is present but not allow-listed (docs/fable/01). No-Origin requests
# (non-browser / tests) pass; browser CSRF is blocked (browsers always send Origin).
app.add_middleware(OriginCheckMiddleware)
# AccessLogMiddleware logs one line per request. Added BEFORE RequestIdMiddleware
# so RequestId stays OUTERMOST (LIFO) — that way request_id is already set when
# the access line is emitted.
app.add_middleware(AccessLogMiddleware)
# RequestIdMiddleware is added LAST so it executes FIRST
# (Starlette processes middleware in LIFO order).
app.add_middleware(RequestIdMiddleware)

app.include_router(health.router, prefix="/api")
app.include_router(client_log.router, prefix="/api")  # frontend → server log bridge (D)
app.include_router(jobs.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
# Career-ops pivot (plan §8, slice one) — bring-a-job + application receipts
app.include_router(bring.router, prefix="/api")
app.include_router(receipts.router, prefix="/api")
app.include_router(applications.router, prefix="/api")
# Batch 2 — auth + channel config
app.include_router(auth.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
# Step-1.5 S3-D — notification ledger reader
app.include_router(notifications.router, prefix="/api")
# Step-3 B-02 — per-user per-channel notification rules
app.include_router(notification_rules.router, prefix="/api")
# Step-3 B-15 — run history
app.include_router(runs.router, prefix="/api")
# Per-User AI CV & Cover Letter (docs/product/peruser_cv_coverletter.md)
app.include_router(tailor.router, prefix="/api")
# Career-ops pivot, slice two (docs/plans/2026-09-03-mcp-server) — personal
# tokens + the MCP endpoint. A Route (not a Mount — that 307s the slash-less
# path) with a raw ASGI callable: the SDK owns the JSON-RPC transport, our shim
# owns auth (bearer only, never the cookie).
app.include_router(tokens.router, prefix="/api")
app.router.routes.append(Route("/api/mcp", endpoint=mcp_asgi, methods=["GET", "POST", "DELETE"], name="mcp"))
# OAuth 2.1 authorization server for MCP clients (docs/plans/2026-09-03-oauth-mcp).
# `well_known` is mounted at the site ROOT — no `/api` prefix — because a
# client resolves `/.well-known/...` against the bare origin it was given
# (`https://job360.uk/api/mcp`), never under the API's own path.
app.include_router(well_known.router)
app.include_router(oauth.router, prefix="/api")
