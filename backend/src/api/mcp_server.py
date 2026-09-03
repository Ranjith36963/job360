"""Job360 as an MCP server — the same routes, reached by an agent.

Mounted at ``/api/mcp`` (streamable HTTP, stateless, JSON responses) so any MCP
client — Claude Code first — can bring a job, read the fit, tailor documents,
record "I applied" and read receipts, as the user, with a personal token.

Design (docs/plans/2026-09-03-mcp-server/spec.md R4):

* **Same API for every surface.** Each tool calls the existing route function
  in-process with the token's user and a per-request DB connection. Zero
  duplicated logic; when a route changes, the tool changes with it.
* **Own auth shim, not the SDK's.** :func:`mcp_asgi` checks the bearer through
  ``auth_deps.resolve_current_user`` (the same code path as every other
  route), parks the user in a contextvar, and forwards to the SDK app. The
  SDK's ``AuthSettings`` would publish OAuth discovery metadata for an
  authorisation server that does not exist — deferred (intent.md).
* **One runtime, two owners.** The SDK's session manager must run inside a
  task group; :func:`mcp_runtime` builds the server and enters it. The app
  lifespan uses it in prod; tests use it too, because the auth fixture
  swaps the lifespan for a no-op. With no runtime the mount answers 503.
* Heavy imports (the ``mcp`` SDK, the route modules) stay inside functions
  (rule #16): CLI runs and test collection never pay for them.
"""
from __future__ import annotations

import contextlib
import json
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Mapping, Optional

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.api.auth_deps import CurrentUser, resolve_current_user
from src.utils.logger import get_audit_logger, get_logger

if TYPE_CHECKING:  # pragma: no cover — type-only; the SDK is lazy-imported at runtime
    from mcp.server import MCPServer
    from starlette.types import Receive, Scope, Send

    from src.repositories.database import JobDatabase

logger = get_logger(__name__)

SERVER_NAME = "job360"
INSTRUCTIONS = (
    "Job360 keeps the record of a job hunt AFTER the click: the user brings a job "
    "(they found it themselves — never search for jobs on their behalf), Job360 "
    "scores the fit against their profile, tailors a CV + cover letter on request, "
    "and keeps an immutable receipt when the user says they applied. Nothing here "
    "submits an application anywhere; record_application only records a fact the "
    "user states."
)

# The user behind the request being served. Set by the ASGI shim per request,
# read by every tool. Context-local, so concurrent requests never cross.
_current_user: ContextVar[Optional[CurrentUser]] = ContextVar("mcp_current_user", default=None)

# The live SDK ASGI handler. None until mcp_runtime() is entered.
_handler: Optional[Callable[[Scope, Receive, Send], Awaitable[None]]] = None


# ── Tool plumbing ──────────────────────────────────────────────────────────────


def _user() -> CurrentUser:
    user = _current_user.get()
    if user is None:  # pragma: no cover — the shim refuses unauthenticated requests
        raise RuntimeError("MCP tool called with no authenticated user")
    return user


def _request_db() -> AbstractAsyncContextManager[JobDatabase]:
    """The per-request DB connection every route depends on, as a context manager."""
    from src.api.dependencies import get_request_db

    return contextlib.asynccontextmanager(get_request_db)()


def _tool_error(exc: HTTPException) -> Exception:
    """Route HTTPException → tool error the agent can read: ``"404: Job not found"``."""
    from mcp.server.mcpserver.exceptions import ToolError

    detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
    return ToolError(f"{exc.status_code}: {detail}")


def _audit(tool: str, status: str, **fields: Any) -> None:
    get_audit_logger().info(
        "mcp_tool_call",
        extra={"event": "mcp_tool_call", "tool": tool, "user_id": _user().id, "status": status, **fields},
    )


def _job_url(job_id: int) -> str:
    from src.core.settings import SITE_BASE_URL

    return f"{SITE_BASE_URL}/jobs/{job_id}"


def _receipt_url(receipt_id: int) -> str:
    from src.core.settings import SITE_BASE_URL

    return f"{SITE_BASE_URL}/receipts/{receipt_id}"


def _job_summary(job: Any) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "match_score": job.match_score,
        "bucket": job.bucket,
        "matched_skills": job.matched_skills,
        "missing_required": job.missing_required,
        "action": job.action,
        "url": _job_url(job.id),
    }


def _job_detail(job: Any) -> dict[str, Any]:
    out = _job_summary(job)
    out.update(
        {
            "description": job.description or "",
            "apply_url": job.apply_url,
            "salary": job.salary,
            "source": job.source,
            "experience_level": job.experience_level,
            "workplace_type": job.workplace_type,
            "required_skills": job.required_skills,
            "dims": {
                "role": job.role,
                "skill": job.skill,
                "location": job.location_score,
                "recency": job.recency,
                "seniority": job.seniority_score,
                "salary": job.salary_score,
                "visa": job.visa_score,
                "workplace": job.workplace_score,
                "experience": job.experience,
                "credentials": job.credentials,
                "active": job.dims_active,
            },
        }
    )
    return out


def _bundle(bundle: Any) -> dict[str, Any]:
    return {
        "job_id": bundle.job_id,
        "documents": [
            {
                "doc_kind": d.doc_kind,
                "status": d.status,
                "text": d.polished if d.polished is not None else d.ai_draft,
                "flagged_terms": d.flagged_terms,
            }
            for d in bundle.documents
        ],
        "quota": {"used": bundle.quota_used, "limit": bundle.quota_limit},
    }


def _receipt_summary(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "job_id": r.job_id,
        "sent_at": r.sent_at,
        "job_title": r.job_title,
        "job_company": r.job_company,
        "job_location": r.job_location,
        "has_cv": r.has_cv,
        "has_cover_letter": r.has_cover_letter,
        "channel": r.channel,
        "note": r.note,
        "url": _receipt_url(r.id),
    }


def _receipt_full(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "job_id": r.job_id,
        "sent_at": r.sent_at,
        "job_title": r.job_title,
        "job_company": r.job_company,
        "job_location": r.job_location,
        "job_apply_url": r.job_apply_url,
        "job_description": r.job_description,
        "has_cv": r.cv_text is not None,
        "has_cover_letter": r.cover_letter_text is not None,
        "cv_text": r.cv_text,
        "cv_origin": r.cv_origin,
        "cover_letter_text": r.cover_letter_text,
        "cover_letter_origin": r.cover_letter_origin,
        "channel": r.channel,
        "note": r.note,
        "url": _receipt_url(r.id),
    }


def build_server() -> MCPServer:
    """Create the MCPServer with the eight tools. Imports the SDK here (rule #16)."""
    from mcp.server import MCPServer
    from pydantic import ValidationError

    from src.api.routes import bring as bring_route
    from src.api.routes import jobs as jobs_route
    from src.api.routes import profile as profile_route
    from src.api.routes import receipts as receipts_route
    from src.api.routes import tailor as tailor_route

    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)

    def _validation_error(exc: ValidationError) -> Exception:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors()
        )
        return _tool_error(HTTPException(status_code=422, detail=problems))

    @mcp.tool()
    async def get_profile() -> dict[str, Any]:
        """The user's Job360 profile summary: is it complete, job titles, skill count,
        experience level, and which inputs (CV / LinkedIn / GitHub) they have given."""
        try:
            resp = await profile_route.get_profile(_user())
        except HTTPException as exc:
            _audit("get_profile", "error", http_status=exc.status_code)
            raise _tool_error(exc) from None
        s = resp.summary
        _audit("get_profile", "ok")
        return {
            "is_complete": s.is_complete,
            "job_titles": s.job_titles,
            "skills_count": s.skills_count,
            "experience_level": s.experience_level,
            "education": s.education,
            "has_cv": s.cv_length > 0,
            "has_linkedin": s.has_linkedin,
            "has_github": s.has_github,
            "top_skills": resp.skill_tiers.get("primary", [])[:15],
        }

    @mcp.tool()
    async def bring_job(
        title: str,
        company: str,
        description: str,
        location: str = "",
        apply_url: str = "",
    ) -> dict[str, Any]:
        """Bring a job ad the user found (paste the full ad text as `description`).
        Job360 stores it, scores the fit against their profile and returns the job id,
        match score and skill gaps. Bringing the same title+company again returns the
        existing job (existing=true). Never use this to search for jobs."""
        try:
            body = bring_route.BringJobRequest(
                title=title, company=company, description=description, location=location, apply_url=apply_url
            )
        except ValidationError as exc:
            raise _validation_error(exc) from None
        try:
            async with _request_db() as db:
                resp = await bring_route.bring_job(body, db, _user())
        except HTTPException as exc:
            _audit("bring_job", "error", http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("bring_job", "ok", job_id=resp.job.id, existing=resp.existing)
        out = _job_summary(resp.job)
        out.update({"existing": resp.existing, "scored": resp.scored})
        return out

    @mcp.tool()
    async def get_job(job_id: int) -> dict[str, Any]:
        """A job the user brought: the ad text, apply link, the per-dimension fit
        breakdown and what the user has done with it (action)."""
        try:
            async with _request_db() as db:
                resp = await jobs_route.get_job(job_id, db, _user())
        except HTTPException as exc:
            _audit("get_job", "error", job_id=job_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("get_job", "ok", job_id=job_id)
        return _job_detail(resp)

    @mcp.tool()
    async def tailor_documents(job_id: int) -> dict[str, Any]:
        """Generate a tailored CV and cover letter for this job from the user's stored
        CV (an LLM call; counts against the monthly free quota — a 402 error means the
        quota is used up). Returns the documents and any flagged terms the user should
        check before applying."""
        try:
            async with _request_db() as db:
                resp = await tailor_route.generate(job_id, db, _user())
        except HTTPException as exc:
            _audit("tailor_documents", "error", job_id=job_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("tailor_documents", "ok", job_id=job_id)
        return _bundle(resp)

    @mcp.tool()
    async def get_tailored_documents(job_id: int) -> dict[str, Any]:
        """The tailored CV and cover letter already generated for this job (no LLM call).
        404 if none exist yet — call tailor_documents first."""
        try:
            async with _request_db() as db:
                resp = await tailor_route.get_tailored(job_id, db, _user())
        except HTTPException as exc:
            _audit("get_tailored_documents", "error", job_id=job_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("get_tailored_documents", "ok", job_id=job_id)
        return _bundle(resp)

    @mcp.tool()
    async def record_application(job_id: int, channel: str = "", note: str = "") -> dict[str, Any]:
        """Record that the user has applied to this job — ONLY after they say so. Freezes
        the job and the tailored documents as sent into an immutable receipt. Sends
        nothing anywhere. `channel` is where they applied ("company site", "LinkedIn",
        "email"); `note` is free text."""
        try:
            body = receipts_route.CreateReceiptRequest(channel=channel, note=note)
        except ValidationError as exc:
            raise _validation_error(exc) from None
        try:
            async with _request_db() as db:
                resp = await receipts_route.create_receipt(job_id, body, db, _user())
        except HTTPException as exc:
            _audit("record_application", "error", job_id=job_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("record_application", "ok", job_id=job_id, receipt_id=resp.id)
        return _receipt_summary(
            receipts_route.ReceiptSummary(
                id=resp.id, job_id=resp.job_id, sent_at=resp.sent_at, job_title=resp.job_title,
                job_company=resp.job_company, job_location=resp.job_location,
                job_apply_url=resp.job_apply_url, has_cv=resp.cv_text is not None,
                has_cover_letter=resp.cover_letter_text is not None, channel=resp.channel, note=resp.note,
            )
        )

    @mcp.tool()
    async def list_receipts(job_id: Optional[int] = None, limit: int = 20) -> dict[str, Any]:
        """The user's application receipts (newest first) — what they applied to and when.
        Optionally filter by job_id. This lists applications the user made, not jobs."""
        limit = max(1, min(int(limit), 200))
        try:
            async with _request_db() as db:
                resp = await receipts_route.list_receipts(job_id, limit, 0, db, _user())
        except HTTPException as exc:
            _audit("list_receipts", "error", http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("list_receipts", "ok", count=len(resp.receipts))
        return {"receipts": [_receipt_summary(r) for r in resp.receipts], "total": resp.total}

    @mcp.tool()
    async def get_receipt(receipt_id: int) -> dict[str, Any]:
        """One application receipt in full: the job as it read at the time and the exact
        CV / cover letter text that was sent."""
        try:
            async with _request_db() as db:
                resp = await receipts_route.get_receipt(receipt_id, db, _user())
        except HTTPException as exc:
            _audit("get_receipt", "error", receipt_id=receipt_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("get_receipt", "ok", receipt_id=receipt_id)
        return _receipt_full(resp)

    return mcp


# ── Runtime + ASGI mount ───────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def mcp_runtime() -> AsyncIterator[None]:
    """Build the server and run the SDK session manager for the duration.

    Entered by the app lifespan in production and by tests directly. Re-entrant
    across separate ``async with`` blocks (a fresh server each time) — the SDK's
    manager itself cannot be restarted, so we never try.
    """
    global _handler
    from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
    from mcp.server.transport_security import TransportSecuritySettings

    from src.core import settings

    if settings.MCP_ALLOWED_HOSTS:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=list(settings.MCP_ALLOWED_HOSTS)
        )
    else:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    mcp = build_server()
    # Builds the session manager as a side effect; the Starlette app it returns
    # (routes + its own lifespan) is not used — the shim below IS the route.
    mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=security,
    )
    manager = mcp.session_manager
    async with manager.run():
        _handler = StreamableHTTPASGIApp(manager)
        logger.info("mcp_runtime_started", extra={"event": "mcp_runtime_started"})
        try:
            yield
        finally:
            _handler = None
            logger.info("mcp_runtime_stopped", extra={"event": "mcp_runtime_stopped"})


async def _send_json(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    payload: dict[str, Any],
    headers: Optional[Mapping[str, str]] = None,
) -> None:
    await JSONResponse(payload, status_code=status, headers=headers)(scope, receive, send)


async def _mcp_asgi(scope: Scope, receive: Receive, send: Send) -> None:
    """The ``/api/mcp`` endpoint: bearer check → contextvar → SDK handler.

    Bearer ONLY. A session cookie is deliberately not accepted here: MCP is a
    cross-origin JSON endpoint and a cookie would make it CSRF-able. Every
    failure is a JSON body with the right status; the runtime missing is 503.
    """
    if scope["type"] != "http":
        return
    request = Request(scope)
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.lower().startswith("bearer "):
        await _send_json(
            scope, receive, send, 401,
            {"detail": "bearer token required"}, {"WWW-Authenticate": 'Bearer realm="job360"'},
        )
        return
    try:
        user = await resolve_current_user(request, None, authorization)
    except HTTPException as exc:
        await _send_json(scope, receive, send, exc.status_code, {"detail": exc.detail}, exc.headers)
        return
    if user is None:  # pragma: no cover — bearer path raises rather than returning None
        await _send_json(
            scope, receive, send, 401, {"detail": "invalid or revoked token"}, {"WWW-Authenticate": "Bearer"}
        )
        return
    handler = _handler
    if handler is None:
        await _send_json(scope, receive, send, 503, {"detail": "MCP server not running"})
        return
    request.state.user_id = user.id  # for the access-log middleware, like require_user
    token = _current_user.set(user)
    try:
        await handler(scope, receive, send)
    finally:
        _current_user.reset(token)


class _McpEndpoint:
    """Raw-ASGI endpoint object for ``app.add_route``.

    Starlette wraps a plain *function* endpoint in ``request_response`` (it
    would expect a ``Request -> Response`` signature). A callable *instance* is
    passed the raw ``(scope, receive, send)`` triple untouched, which the SDK
    transport needs. A ``Route`` (not a ``Mount``) is used so ``POST /api/mcp``
    matches exactly — a Mount answers the slash-less path with a 307 redirect
    that MCP clients do not follow.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await _mcp_asgi(scope, receive, send)


mcp_asgi = _McpEndpoint()
