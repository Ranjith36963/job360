"""HTTP middleware for Job360 FastAPI app."""
from __future__ import annotations

import asyncio
import os
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.utils.logger import _request_id_var, get_logger, get_request_id, set_request_id

_access_log = get_logger("access")  # "job360.access" → main job360 handlers (jsonl + log file)

# Content-Security-Policy for a JSON API that also serves FastAPI's Swagger UI.
# cdn.jsdelivr.net hosts swagger-ui-bundle.js + swagger-ui.css.
# 'unsafe-inline' is required for Swagger UI's inline event handlers / styles;
# the API endpoints themselves return JSON so the script/style directives are
# only reachable via the /docs page.
_CSP = (
    "default-src 'none'; "
    "script-src 'self' cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)

# HSTS: 1 year, include subdomains. Only sent in production (needs HTTPS).
_HSTS = "max-age=31536000; includeSubDomains"


def _is_production() -> bool:
    return (
        os.environ.get("APP_ENV", "").lower() == "production"
        or bool(os.environ.get("RAILWAY_ENVIRONMENT", ""))
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Stamp every request with a correlation id.

    Honours an incoming ``X-Request-Id`` header (e.g. from a load-balancer or
    upstream service) so request chains stay traceable end-to-end. When no
    header is present a fresh 16-hex-char id is generated.

    The id is stored in ``_request_id_var`` so every ``JSONFormatter`` log line
    emitted during the request lifetime automatically carries it. It is also
    echoed back in the ``X-Request-Id`` response header.
    """

    # Max id length — prevents oversized upstream values from bloating log lines.
    _MAX_RID_LEN = 64

    async def dispatch(self, request: Request, call_next) -> Response:
        raw = request.headers.get("X-Request-Id", "").strip()
        rid = raw[: self._MAX_RID_LEN] if raw else uuid.uuid4().hex[:16]
        # Stash on request.state too: the contextvar is reset in `finally`
        # before the outermost exception handler runs, so errors.py reads it here.
        request.state.request_id = rid
        token = set_request_id(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers["X-Request-Id"] = rid
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit one structured access-log line per HTTP request.

    The backbone of full-lifecycle logging: *every* request gets a line —
    method / path / status / duration_ms / request_id — even routes that log
    nothing themselves. Logged on `job360.access` so it lands in the same
    `data/logs/` JSON + text streams as everything else.

    Must run INSIDE ``RequestIdMiddleware`` (add it BEFORE RequestId so RequestId
    is outermost) so ``request_id`` is already set when this logs. The ``finally``
    guarantees a line even when the route raises (recorded as status 500).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            _access_log.info(
                "http_request",
                extra={
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    "request_id": get_request_id(),
                    # WHO made the request — set on request.state by require_user /
                    # optional_user (None for anonymous routes).
                    "user_id": getattr(request.state, "user_id", None),
                    "client": request.client.host if request.client else None,
                },
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every HTTP response (Phase 3 observability).

    Headers set on every response:
      * ``X-Content-Type-Options: nosniff`` — prevent MIME sniffing.
      * ``X-Frame-Options: DENY`` — block framing (legacy browsers).
      * ``Referrer-Policy: strict-origin-when-cross-origin`` — limit referer leakage.
      * ``Content-Security-Policy`` — lock down resource loading; allow what
        Swagger UI needs (cdn.jsdelivr.net + unsafe-inline) while keeping the
        default to 'none' for non-UI endpoints.

    HSTS is only emitted in production (``APP_ENV=production`` or
    ``RAILWAY_ENVIRONMENT`` set), because HSTS pins HTTPS in the browser and
    would break plain-HTTP local development.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = _CSP
        if _is_production():
            response.headers["Strict-Transport-Security"] = _HSTS
        return response


class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """Cap inbound request duration so a hung handler can't tie up a worker.

    A backstop, not a scheduler: outbound source-fetch ceilings
    (``SOURCE_FETCH_TIMEOUT*``) are a different knob. LLM-heavy routes (CV
    tailoring, profile extraction) legitimately run longer than a normal API
    round-trip, so they are exempted by path prefix. Everything else that
    exceeds ``timeout_seconds`` gets a ``504`` instead of hanging.

    Note: cancelling ``call_next`` abandons the handler mid-flight. DB writes in
    this app are autocommit single statements (``repositories/pg.py``), so a
    cancelled request cannot leave an open transaction.
    """

    def __init__(self, app, timeout_seconds: float = 60.0, exempt_prefixes: tuple = ()):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds
        self.exempt_prefixes = tuple(exempt_prefixes)

    async def dispatch(self, request: Request, call_next) -> Response:
        if any(request.url.path.startswith(p) for p in self.exempt_prefixes):
            return await call_next(request)
        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=504, content={"detail": "request timed out"})
