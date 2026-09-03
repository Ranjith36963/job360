"""HTTP middleware for Job360 FastAPI app."""
from __future__ import annotations

import os
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.utils.logger import _request_id_var, get_logger, get_request_id, mask_ip, set_request_id

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

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
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

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            # The timing MUST be in the message, not only in `extra`. The console
            # handler renders `%(message)s`, so an extras-only duration is
            # invisible wherever logs are actually read — `railway logs` showed
            # nothing but a bare "http_request" for every request, which made
            # "the site feels slow" impossible to answer. `extra` is kept for the
            # structured JSON stream; the message carries it for humans.
            _access_log.info(
                "%s %s %s %sms",
                request.method,
                request.url.path,
                status,
                duration_ms,
                extra={
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status,
                    "duration_ms": duration_ms,
                    "request_id": get_request_id(),
                    # WHO made the request — set on request.state by require_user /
                    # optional_user (None for anonymous routes). Left unmasked: an
                    # internal opaque uuid needed for correlation, far lower PII
                    # than a raw client IP (M9).
                    "user_id": getattr(request.state, "user_id", None),
                    # M9 — mask the client IP before it hits the on-disk rotating
                    # access log; see mask_ip() in src/utils/logger.py.
                    "client": mask_ip(request.client.host if request.client else None),
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

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = _CSP
        if _is_production():
            response.headers["Strict-Transport-Security"] = _HSTS
        return response


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """CSRF defence-in-depth on state-changing requests (docs/fable/01).

    Cookie-auth requests already rely on ``SameSite=Lax``; this adds a second
    layer. On unsafe methods (POST/PUT/PATCH/DELETE), if the request carries an
    ``Origin`` header that is NOT in the allowlist, it is rejected with 403.

    A request with NO ``Origin`` (curl, server-to-server, TestClient) is allowed —
    a CSRF attack needs a browser, and browsers ALWAYS send ``Origin`` on a
    cross-site state-changing request. So this blocks the browser CSRF vector
    without breaking non-browser API clients. Allowlist = ``FRONTEND_ORIGIN``
    (same source as CORS).

    Exemption (docs/plans/2026-09-03-oauth-mcp/spec.md §Design "CSRF"):
    ``/api/oauth/register``, ``/api/oauth/token`` and ``/api/oauth/revoke``
    carry no cookie at all — an arbitrary OAuth client (ChatGPT, Claude.ai)
    calls them cross-origin with a credential (PKCE / an opaque code/token)
    that this check cannot see, so CSRF does not apply and a foreign
    ``Origin`` must not be rejected. ``/authorize`` (GET) is safe by method;
    ``/authorize/{rid}(/decision)`` and ``/grants`` use the session cookie and
    stay checked.
    """

    _UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    _EXEMPT_PATHS = frozenset({"/api/oauth/register", "/api/oauth/token", "/api/oauth/revoke"})

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method in self._UNSAFE and request.url.path not in self._EXEMPT_PATHS:
            origin = request.headers.get("origin")
            if origin:
                allowed = {
                    o.strip()
                    for o in os.environ.get(
                        "FRONTEND_ORIGIN", "http://localhost:3000"
                    ).split(",")
                    if o.strip()
                }
                if origin not in allowed:
                    return JSONResponse(
                        {"detail": "cross-origin request rejected"}, status_code=403
                    )
        return await call_next(request)
