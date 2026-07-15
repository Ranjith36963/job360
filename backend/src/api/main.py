"""FastAPI application for Job360 backend."""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

# psycopg async requires the selector event loop on Windows (the default
# ProactorEventLoop never signals socket readiness for libpq -> the DB lifespan
# would hang on boot). Set it here so every entry point that imports the app
# (``uvicorn main:app``, ``python main.py``, ``python -m src.cli api``) is safe.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.dependencies import close_db, init_db
from src.api.errors import register_exception_logging
from src.api.middleware import AccessLogMiddleware, RequestIdMiddleware, SecurityHeadersMiddleware
from src.api.routes import (
    actions,
    auth,
    channels,
    client_log,
    health,
    jobs,
    notification_rules,
    notifications,
    pipeline,
    profile,
    runs,
    search,
    tailor,
)
from src.core.settings import LOG_LEVEL, validate_required_env
from src.utils.logger import setup_audit_logger, setup_logging


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
async def lifespan(app: FastAPI):
    # Phase 3 — Sentry must be up before anything else so boot errors are captured.
    _init_sentry()
    # Tier-A Step-0 #9 — honour LOG_LEVEL env var at process boot.
    # setup_logging() configures the "job360" subtree; we also set the root
    # logger so libraries (uvicorn, fastapi, httpx) inherit the same level
    # when they haven't been individually configured.
    setup_logging(LOG_LEVEL)
    setup_audit_logger()
    logging.getLogger().setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    # Fail fast in production when required env vars are absent (no-op in dev).
    validate_required_env()
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Job360 API", version="1.0.0", lifespan=lifespan)

# Gap E — log every unhandled exception (traceback + request_id) into data/logs/.
register_exception_logging(app)

# CORS — env-driven so dev / staging / prod can differ without a rebuild.
# Default keeps Batch 1 behaviour (localhost:3000) so existing dev flows work.
_origins = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)
# SecurityHeadersMiddleware is added FIRST (innermost in LIFO) so it stamps
# security headers on the final response — after routing and all other
# middleware have finished. This guarantees headers appear on every response
# including CORS pre-flight and error responses.
app.add_middleware(SecurityHeadersMiddleware)
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
# Batch 2 — auth + channel config
app.include_router(auth.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
# Step-1.5 S3-D — notification ledger reader
app.include_router(notifications.router, prefix="/api")
# Step-3 B-02 — per-user per-channel notification rules
app.include_router(notification_rules.router, prefix="/api")
# Step-3 B-15 — run history
app.include_router(runs.router, prefix="/api")
# Per-User AI CV & Cover Letter (docs/peruser_cv_coverletter.md)
app.include_router(tailor.router, prefix="/api")
