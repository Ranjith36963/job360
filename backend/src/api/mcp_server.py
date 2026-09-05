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

from src.api.auth_deps import CurrentUser, require_verified_user, resolve_current_user
from src.core import settings
from src.services.auth.oauth_flow import SUPPORTED_SCOPE, resource_matches_canonical
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


async def _verified_user() -> CurrentUser:
    """``_user()`` plus the email gate the HTTP route puts in ``Depends``.

    Tools call route *functions* directly, so a route's ``Depends(...)`` chain
    never runs here — every gate the route declares has to be re-applied by
    hand. Use this for any tool whose route is ``Depends(require_verified_user)``
    (``tests/test_mcp_gate_parity.py`` pins the mapping).
    """
    return await require_verified_user(_user())


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


def _application_url(application_id: int) -> str:
    """Slice 5 (#483) deleted the public `/jobs/{id}` page; the only web view
    of a brought ad is now the user's own application page."""
    from src.core.settings import SITE_BASE_URL

    return f"{SITE_BASE_URL}/applications/{application_id}"


def _receipt_url(receipt_id: int) -> str:
    from src.core.settings import SITE_BASE_URL

    return f"{SITE_BASE_URL}/receipts/{receipt_id}"


def _job_summary(job: Any, application_id: int) -> dict[str, Any]:
    """Slice 5 (#483): no score, no dims, no fit words. Job360 stores the ad;
    the calling agent judges it and records its verdict with `save_fit`."""
    return {
        "job_id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "application_id": application_id,
        "url": _application_url(application_id),
    }


def _job_detail(job: Any, application_id: int) -> dict[str, Any]:
    out = _job_summary(job, application_id)
    out.update(
        {
            "description": job.description or "",
            "apply_url": job.apply_url,
            "salary": job.salary,
            "source": job.source,
            "experience_level": job.experience_level,
            "posted_at": job.posted_at,
            "deadline": job.deadline,
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

    from src.api.routes import applications as applications_route
    from src.api.routes import bring as bring_route
    from src.api.routes import profile as profile_route
    from src.api.routes import receipts as receipts_route
    from src.api.routes import tailor as tailor_route
    from src.services.applications import spine as applications_spine

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
        Job360 stores it and starts an application for it, then returns the job id and
        the application id to work against. It does NOT judge the fit — that is your
        job; record your own verdict with `save_fit`. Bringing the same title+company
        again returns the existing job (existing=true). Never use this to search."""
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
        out = _job_summary(resp.job, resp.application_id)
        out.update({"existing": resp.existing, "status": resp.status})
        return out

    @mcp.tool()
    async def get_job(job_id: int) -> dict[str, Any]:
        """An ad the user brought, by job id: the full text, the apply link and the
        dates we hold. Only jobs THIS user brought are readable."""
        try:
            async with _request_db() as db:
                resp = await applications_route.get_job(job_id, db, _user())
                # The route already proved the caller owns an application for
                # this job (404 otherwise), so this read cannot come back None.
                app_row = await applications_spine.get_application_by_job(db, _user().id, job_id)
        except HTTPException as exc:
            _audit("get_job", "error", job_id=job_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("get_job", "ok", job_id=job_id)
        return _job_detail(resp, int(app_row["id"]) if app_row else 0)

    @mcp.tool()
    async def tailor_documents(job_id: int) -> dict[str, Any]:
        """Generate a tailored CV and cover letter for this job from the user's stored
        CV (an LLM call; counts against the monthly free quota — a 402 error means the
        quota is used up). Returns the documents and any flagged terms the user should
        check before applying."""
        try:
            async with _request_db() as db:
                resp = await tailor_route.generate(job_id, db, await _verified_user())
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
                resp = await tailor_route.get_tailored(job_id, db, await _verified_user())
        except HTTPException as exc:
            _audit("get_tailored_documents", "error", job_id=job_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("get_tailored_documents", "ok", job_id=job_id)
        return _bundle(resp)

    @mcp.tool()
    async def record_application(
        job_id: int,
        channel: str = "",
        note: str = "",
        confirmation: str = "",
        answers: Optional[list[dict[str, str]]] = None,
        fields_filled: Optional[dict[str, Any]] = None,
        cv_artifact_id: Optional[int] = None,
        cover_letter_artifact_id: Optional[int] = None,
        applied_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record that the user has applied to this job — ONLY after they say so. Freezes
        the named CV / cover-letter version (or the newest saved one, if none named) and
        any answers/fields into an immutable receipt, and appends an `applied` event to
        the application's history. Sends nothing anywhere. `channel` is where they
        applied ("company site", "LinkedIn", "email"); `note` is free text.

        C1 (application-spine review) — this is the SAME tool as before (`job_id`,
        `channel`, `note` still work unchanged), rewired onto the rich
        `POST /applications/{id}/receipt` route instead of the legacy
        `POST /receipts/{job_id}` — the new optional fields (`confirmation`, `answers`,
        `fields_filled`, `cv_artifact_id`, `cover_letter_artifact_id`, `applied_at`)
        are exactly spec R8/S4's tool contract.
        """
        try:
            body = applications_route.RecordApplicationReceiptRequest(
                channel=channel, note=note, confirmation=confirmation,
                answers=[applications_route.ReceiptAnswer(**a) for a in (answers or [])],
                fields_filled=fields_filled or {}, cv_artifact_id=cv_artifact_id,
                cover_letter_artifact_id=cover_letter_artifact_id, applied_at=applied_at,
            )
        except ValidationError as exc:
            raise _validation_error(exc) from None
        try:
            async with _request_db() as db:
                job = await db.get_job_by_id(job_id)
                if job is None:
                    raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
                # Upsert-by-read, same as the legacy `/receipts/{job_id}` route
                # (receipts.py:126-128): a job the caller never explicitly
                # `bring_job`-ed still gets an application row here, so
                # "record I applied" never 404s on a job that plainly exists.
                await db.create_application(job_id, _user().id)
                application = await applications_spine.get_application_by_job(db, _user().id, job_id)
                if application is None:  # pragma: no cover — create_application always upserts one
                    raise HTTPException(status_code=404, detail="application not found")
                resp = await applications_route.record_application_receipt(
                    application["id"], body, db, _user()
                )
        except HTTPException as exc:
            _audit("record_application", "error", job_id=job_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("record_application", "ok", job_id=job_id, receipt_id=resp["receipt_id"])
        return {"job_id": job_id, **resp}

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

    # ── Application spine (spec 2026-09-04-application-spine, S11) ──────────
    # Seven tools, each calling its route FUNCTION directly. None of these
    # routes is `require_verified_user` (spec: "nothing here spends an LLM
    # call"), so every one uses `_user()`, never `_verified_user()` — the
    # parity test (test_mcp_gate_parity.py) checks exactly this.

    @mcp.tool()
    async def get_application(application_id: int, with_artifact_text: bool = False) -> dict[str, Any]:
        """One application in full: status, the job snapshot, the fit verdict,
        every artifact version (text omitted unless with_artifact_text=true),
        the whole event timeline, and receipts."""
        try:
            async with _request_db() as db:
                resp = await applications_route.get_application(application_id, with_artifact_text, db, _user())
        except HTTPException as exc:
            _audit("get_application", "error", application_id=application_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("get_application", "ok", application_id=application_id)
        return resp

    @mcp.tool()
    async def list_applications(
        status: Optional[str] = None, updated_since: Optional[str] = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """The user's applications (newest activity first). Filter by status
        (e.g. "considering", "applied", "interview_scheduled")."""
        try:
            async with _request_db() as db:
                resp = await applications_route.list_applications(status, updated_since, limit, offset, db, _user())
        except HTTPException as exc:
            _audit("list_applications", "error", http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("list_applications", "ok", count=len(resp.get("applications", [])))
        return resp

    @mcp.tool()
    async def save_artifact(
        application_id: int, kind: str, text: str, label: str = "", model: Optional[str] = None
    ) -> dict[str, Any]:
        """Save a new version of a CV / cover letter / answers / outreach note
        for this application. Every save is a NEW version — nothing is ever
        overwritten; both old and new stay readable forever."""
        try:
            body = applications_route.SaveArtifactRequest(kind=kind, text=text, label=label, model=model)
        except ValidationError as exc:
            raise _validation_error(exc) from None
        try:
            async with _request_db() as db:
                resp = await applications_route.save_artifact(application_id, body, db, _user())
        except HTTPException as exc:
            _audit("save_artifact", "error", application_id=application_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("save_artifact", "ok", application_id=application_id, kind=kind)
        return resp

    @mcp.tool()
    async def save_fit(
        application_id: int,
        score: Optional[int] = None,
        verdict: Optional[str] = None,
        gaps: Optional[list[str]] = None,
        reasoning: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record YOUR OWN fit judgement for this application — never computed
        by Job360 (VISION rule 4). Overwrites the current verdict; the log
        keeps every past judgement too."""
        try:
            body = applications_route.SaveFitRequest(score=score, verdict=verdict, gaps=gaps, reasoning=reasoning)
        except ValidationError as exc:
            raise _validation_error(exc) from None
        try:
            async with _request_db() as db:
                resp = await applications_route.save_fit(application_id, body, db, _user())
        except HTTPException as exc:
            _audit("save_fit", "error", application_id=application_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("save_fit", "ok", application_id=application_id)
        return resp

    @mcp.tool()
    async def record_event(
        application_id: int,
        event_type: str,
        detail: str = "",
        payload: Optional[dict[str, Any]] = None,
        occurred_at: Optional[str] = None,
        corrects_event_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Append one event to this application's history — replied, an
        interview stage, a note, a lesson learned. `occurred_at` may be in the
        past (backdating a reply you just found is normal); it may not be
        implausibly in the future. A status event (applied/replied/interview_*/
        offer/rejected/withdrawn/ghosted) moves the application's status; a
        note-family event never does."""
        try:
            body = applications_route.RecordEventRequest(
                event_type=event_type, detail=detail, payload=payload or {},
                occurred_at=occurred_at, corrects_event_id=corrects_event_id,
            )
        except ValidationError as exc:
            raise _validation_error(exc) from None
        try:
            async with _request_db() as db:
                resp = await applications_route.record_event(application_id, body, db, _user())
        except HTTPException as exc:
            _audit("record_event", "error", application_id=application_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("record_event", "ok", application_id=application_id, event_type=event_type)
        return resp

    @mcp.tool()
    async def whats_new(since: Optional[str] = None, after_id: Optional[int] = None, limit: int = 50) -> dict[str, Any]:
        """What happened across ALL of the user's applications since a given
        time — for an agent waking up and asking "what did I miss?". Paged by
        when Job360 recorded each event, never by when it happened in the
        world, so a backdated event can never be silently skipped."""
        try:
            async with _request_db() as db:
                resp = await applications_route.whats_new(since, after_id, limit, db, _user())
        except HTTPException as exc:
            _audit("whats_new", "error", http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("whats_new", "ok", count=len(resp.get("events", [])))
        return resp

    @mcp.tool()
    async def export_history(since: Optional[str] = None, include_text: bool = False) -> dict[str, Any]:
        """Export the user's whole application history: every application,
        its events, and artifact metadata (full text only when
        include_text=true). Bounded and rate-limited — a truncated response
        names next_since to page from."""
        try:
            async with _request_db() as db:
                resp = await applications_route.export_history(since, include_text, db, _user())
        except HTTPException as exc:
            _audit("export_history", "error", http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit(
            "export_history", "ok",
            applications=len(resp.get("applications", [])), truncated=resp.get("truncated", False),
        )
        return resp

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


def _mcp_challenge_headers() -> dict[str, str]:
    """R7 — the 401 challenge every ``/api/mcp`` failure carries.

    Points a discovering OAuth client at the protected-resource metadata
    document (RFC 9728); the ``scope`` hint is SHOULD, not MUST, but costs
    nothing to include. Deliberately stamped ONLY here, not in
    ``auth_deps._BEARER_CHALLENGE`` — every other ``/api/*`` route shares
    that constant and has no OAuth discovery story.
    """
    resource_metadata = f"{settings.SITE_BASE_URL}/.well-known/oauth-protected-resource/api/mcp"
    return {
        "WWW-Authenticate": (
            f'Bearer realm="job360", resource_metadata="{resource_metadata}", scope="{SUPPORTED_SCOPE}"'
        )
    }


async def _mcp_asgi(scope: Scope, receive: Receive, send: Send) -> None:
    """The ``/api/mcp`` endpoint: bearer check → contextvar → SDK handler.

    Bearer ONLY. A session cookie is deliberately not accepted here: MCP is a
    cross-origin JSON endpoint and a cookie would make it CSRF-able. Every
    failure is a JSON body with the right status; the runtime missing is 503.

    Every 401 (no bearer, bad/expired/revoked bearer, wrong audience) carries
    the R7 challenge. The bearer-throttle 429 keeps its plain ``Bearer``
    challenge (spec R7: "The 429 keeps Bearer") — it isn't part of the
    discovery contract, just a retry hint.
    """
    if scope["type"] != "http":
        return
    request = Request(scope)
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.lower().startswith("bearer "):
        await _send_json(scope, receive, send, 401, {"detail": "bearer token required"}, _mcp_challenge_headers())
        return
    try:
        user = await resolve_current_user(request, None, authorization)
    except HTTPException as exc:
        headers = _mcp_challenge_headers() if exc.status_code == 401 else exc.headers
        await _send_json(scope, receive, send, exc.status_code, {"detail": exc.detail}, headers)
        return
    if user is None:  # pragma: no cover — bearer path raises rather than returning None
        await _send_json(scope, receive, send, 401, {"detail": "invalid or revoked token"}, _mcp_challenge_headers())
        return
    # S13 — an OAuth token must carry the canonical MCP audience here; a
    # personal token (auth_via != "oauth") is unaffected, matching "same
    # routes, same rules" everywhere else (spec S13's stated deviation).
    if user.auth_via == "oauth" and not resource_matches_canonical(user.audience or ""):
        await _send_json(
            scope, receive, send, 401,
            {"detail": "token audience does not match this resource"}, _mcp_challenge_headers(),
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
