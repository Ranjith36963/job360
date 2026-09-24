"""Job360 as an MCP server — the same routes, reached by an agent.

Mounted at ``/api/mcp`` (streamable HTTP, stateless) so any MCP client —
Claude Code first — can bring a job, record its own fit verdict, save the CV
and cover letter it wrote, record "I applied" and read receipts, as the user,
with a personal token. Decision 28 (slice A): no tool here writes text for
the agent — Job360 stores, versions and renders what the agent saves.

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
* **A deploy must not need a manual reconnect.**
  :class:`_AnnounceToolListChanged` tells each user's client, once per
  process, that the tool list changed — see that class for the whole story
  and for what it still cannot guarantee.
* Heavy imports (the ``mcp`` SDK, the route modules) stay inside functions
  (rule #16): CLI runs and test collection never pay for them.
"""
from __future__ import annotations

import contextlib
import hashlib
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
    from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
    from mcp_types import Tool as McpTool
    from starlette.types import Receive, Scope, Send

    from src.repositories.database import JobDatabase

logger = get_logger(__name__)

SERVER_NAME = "job360"
INSTRUCTIONS = (
    "Job360 is the memory of a job hunt AFTER the click: the user brings a job "
    "(they found it themselves — never search for jobs on their behalf), YOU judge "
    "whether it fits and Job360 STORES your verdict, your tailored CV and cover "
    "letter, and an immutable receipt when the user says they applied. Job360 has no "
    "LLM of its own: it never ranks, scores, recommends or writes anything itself — "
    "you write the CV and cover letter, it versions, renders and remembers them. "
    "Nothing here submits an application anywhere; record_application only records "
    "a fact the user states. Two flows without our website: (1) build the "
    "profile — get_profile returns `raw` (the CV/LinkedIn/GitHub text Job360 "
    "extracted) and `editable_paths`; read `raw`, then write the structured "
    "fields with update_profile. Dated positions and projects are not writable "
    "yet, so put a role's substance in cv_data.job_titles and cv_data.summary. "
    "(2) apply to a job — bring_job, then get_job + get_profile, judge fit "
    "yourself and save_fit, write the CV/cover letter yourself and save_artifact, "
    "then record_application once the user says they applied."
)

# The user behind the request being served. Set by the ASGI shim per request,
# read by every tool. Context-local, so concurrent requests never cross.
_current_user: ContextVar[Optional[CurrentUser]] = ContextVar("mcp_current_user", default=None)

# The live SDK ASGI handlers. None until mcp_runtime() is entered. `_handler`
# is the leg a client's Accept header selects by default (SSE responses while
# announcing is on, JSON otherwise); `_json_handler` is the JSON-only fallback
# that exists only while announcing is on, for a client that did not accept
# text/event-stream. See `mcp_runtime` for why there are two.
_handler: Optional[Callable[[Scope, Receive, Send], Awaitable[None]]] = None
_json_handler: Optional[Callable[[Scope, Receive, Send], Awaitable[None]]] = None


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
        "application_id": bundle.application_id,
        "documents": [
            {
                "doc_kind": d.doc_kind,
                "text": d.text,
                "artifact_id": d.artifact_id,
                "version_no": d.version_no,
                "made_by": d.made_by,
                "updated_at": d.updated_at,
            }
            for d in bundle.documents
        ],
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


def build_server(version: str = "") -> MCPServer:
    """Create the MCPServer with the seventeen tools. Imports the SDK here (rule #16).

    ``version`` becomes ``serverInfo.version`` in the ``initialize`` result;
    :func:`mcp_runtime` passes :func:`tools_fingerprint` so the wire says which
    tool surface this process serves. Left empty the server reports no version,
    exactly as before.
    """
    from mcp.server import MCPServer
    from pydantic import ValidationError
    from starlette.responses import Response

    from src.api.routes import applications as applications_route
    from src.api.routes import bring as bring_route
    from src.api.routes import profile as profile_route
    from src.api.routes import receipts as receipts_route
    from src.api.routes import tailor as tailor_route
    from src.services.applications import spine as applications_spine

    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=version)

    def _validation_error(exc: ValidationError) -> Exception:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors()
        )
        return _tool_error(HTTPException(status_code=422, detail=problems))

    # How much stored text one `get_profile` call may hand back per document,
    # and how many repo briefs. Deliberately larger than any real CV (a dense
    # two-page CV is ~6 KB of text) so the cap is invisible in practice and
    # only bites a pathological input.
    _RAW_DOC_CHARS = 120_000
    _RAW_REPOS = 60

    def _raw_block(cv: Any) -> dict[str, Any]:
        """The stored documents the agent reads, bounded, with a truncation flag."""
        docs = {
            "cv": cv.raw_text or "",
            "linkedin": cv.linkedin_raw_text or "",
            "github_bio": cv.github_bio or "",
            "github_profile_readme": cv.github_profile_readme or "",
        }
        repos = list(cv.github_repos_brief or [])
        truncated = len(repos) > _RAW_REPOS or any(
            len(v) > _RAW_DOC_CHARS for v in docs.values()
        )
        out: dict[str, Any] = {k: v[:_RAW_DOC_CHARS] for k, v in docs.items()}
        out["github_repos"] = repos[:_RAW_REPOS]
        out["truncated"] = truncated
        return out

    @mcp.tool()
    async def get_profile() -> dict[str, Any]:
        """The user's Job360 profile, and the raw text you are meant to read.

        Job360 extracts TEXT from the CV, the LinkedIn export and GitHub, and
        stores the structure it can prove (the skills listed under a Skills
        heading, the summary, the contact block). It does not read the document
        for meaning — that is YOUR job.

        So: read `raw.cv`, `raw.linkedin`, `raw.github_bio`,
        `raw.github_profile_readme` and `raw.github_repos` here, then write what
        you found back with `update_profile`. **`editable_paths` is the exact,
        closed list of what you may write** — skills, job titles, education,
        certifications, achievements, name, headline, location, summary,
        languages, links, right-to-work, and the preferences. Their current
        values are in `fields`. Dated work history and projects are NOT
        writable yet (`cv_data.cv_positions`, `cv_data.cv_projects`); put a
        role's substance into `cv_data.job_titles` and `cv_data.summary` until
        they are. What you write survives every later re-upload — Job360 never
        overwrites or clears it.

        `skills` is THE user's skill list — the same one, with the same count
        (`skills_count`), that the web profile page and the application page
        show: each entry is {"name", "sources"}, sources being where it was
        found (`cv_explicit`, `linkedin`, `github_lang`, `user_declared`,
        `about_me_llm`). To REMOVE a wrong skill (a line-wrap fragment, a
        non-skill) from every surface, add its name to
        `preferences.excluded_skills` with `update_profile` — send the current
        `fields["preferences.excluded_skills"]` plus the new names. Do not
        rewrite `cv_data.skills` to prune: exclusion reaches every source and
        stays under the per-edit list cap.

        Also returned: whether the profile is complete, job titles,
        experience level, which inputs the user has given, your own past edits
        (`agent_edits`), and the newest `lessons` the user flagged for next
        time. `raw` keys are empty strings when that input was never given; if
        `raw.truncated` is true, a document was longer than the cap and you are
        seeing its opening — the full text is on the web profile page."""
        # ONE profile read for the whole tool call. `load_profile_response` is
        # the same function `GET /profile` itself is (same 404, same rendering),
        # and it hands back BOTH the UserProfile object and the rendered
        # response from a single connection — so the `fields` map below costs
        # nothing extra. Calling the route and then re-loading the profile
        # meant four connections to answer one tool call.
        from src.services.profile import edits as profile_edits  # noqa: PLC0415
        from src.services.profile.skill_tiering import profile_skills  # noqa: PLC0415

        user_id = _user().id
        try:
            profile, resp = profile_route.load_profile_response(user_id)
        except HTTPException as exc:
            _audit("get_profile", "error", http_status=exc.status_code)
            raise _tool_error(exc) from None
        s = resp.summary
        _audit("get_profile", "ok")

        # R11 (docs/plans/2026-09-05-contacts-stats/spec.md) — provenance: the
        # closed set of paths an agent may edit, its own live overlay, and a
        # `{path: current value}` map over every editable path.
        editable_paths = list(profile_edits.editable_paths())
        return {
            "is_complete": s.is_complete,
            "job_titles": s.job_titles,
            # THE one skill list (skill_tiering.profile_skills) — the same
            # rows and count the web shows; built from the profile already
            # loaded above, so no extra query.
            "skills_count": s.skills_count,
            "skills": profile_skills(profile),
            "experience_level": s.experience_level,
            "education": s.education,
            "has_cv": s.cv_length > 0,
            "has_linkedin": s.has_linkedin,
            "has_github": s.has_github,
            "top_skills": resp.skill_tiers.get("primary", [])[:15],
            "editable_paths": editable_paths,
            # Already read on the profile's own connection — the response
            # carries it, so this is not a third query.
            "agent_edits": [row.model_dump() for row in resp.agent_edits],
            "fields": profile_edits.field_values(profile, editable_paths),
            # Slice 9 (#516) — "flag for next time": the last PROFILE_LESSONS_MAX
            # lessons across every application, newest first. Read them before
            # tailoring the next CV; write a new one with
            # record_event(event_type="lesson").
            "lessons": [row.model_dump() for row in resp.lessons],
            # DECISION 28 (2026-09-21) — the point of the product. Job360 has no
            # model of its own, so the agent must be able to READ the documents
            # it is asked to fill the profile from. Without this block
            # `update_profile` is a door onto an empty room: the summary above
            # says "has_cv: true" and nothing here says what the CV says.
            # Stored text, never a re-fetch — the uploaded file is long gone.
            #
            # CAPPED, like every other list in this payload (`lessons` by
            # PROFILE_LESSONS_MAX, `top_skills[:15]`). A CV is a few kilobytes,
            # but the upload route accepts 10 MB and `github_repos_brief`
            # carries a README excerpt per repo — an uncapped block could hand
            # a client a multi-megabyte tool result in one call. The cap is
            # generous enough that a real CV or LinkedIn export is never cut,
            # and `truncated` says so out loud when one is.
            "raw": _raw_block(profile.cv_data),
        }

    @mcp.tool()
    async def bring_job(
        title: str,
        company: str,
        description: str,
        location: str = "",
        apply_url: str = "",
        visa_signal: Optional[str] = None,
        visa_detail: str = "",
        visa_country: str = "",
    ) -> dict[str, Any]:
        """Bring a job ad the user found (paste the full ad text as `description`).
        Job360 stores it and starts an application for it, then returns the job id and
        the application id to work against. It does NOT judge the fit — that is your
        job; record your own verdict with `save_fit`. Bringing the same title+company
        again returns the existing job (existing=true). Never use this to search.

        Visa (optional): if the ad SAYS whether the employer sponsors visas, pass
        `visa_signal` = "sponsors" or "no_sponsorship", quote the sentence in
        `visa_detail`, and give the job's country as ISO alpha-2 in `visa_country`
        (e.g. "GB", "DE", "IN"). If the ad says nothing, leave it out — Job360 never
        guesses. The web compares the country with the user's own list of countries
        where they need no sponsorship (get_profile → fields →
        preferences.work_authorization_countries; set it with update_profile)."""
        try:
            body = bring_route.BringJobRequest(
                title=title, company=company, description=description, location=location, apply_url=apply_url,
                visa_signal=visa_signal, visa_detail=visa_detail, visa_country=visa_country,
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
    async def get_tailored_documents(job_id: int) -> dict[str, Any]:
        """The newest saved CV and cover letter for this job. Job360 writes neither —
        YOU write them (from get_profile + get_job) and save them with save_artifact;
        this reads back what is saved. Empty list when nothing is saved yet."""
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
        the whole event timeline, receipts, and `next_step` — what to do next,
        read off the stored state. Branch on `next_step.code`: judge_fit → call
        save_fit; write_cv → save_artifact(kind="cv"); apply → the human applies,
        then record_application; record_receipt → record_application; schedule →
        record_event(interview_scheduled, scheduled_at=…); lesson → record_event(
        event_type="lesson"). wait / interview / await_outcome / decide / closed
        need nothing from you."""
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
        """Save a CV / cover letter / answers / outreach note for this application.
        Write the tailored text YOURSELF from get_profile + get_job — Job360 has no
        LLM — then save it here (kind = "cv" | "cover_letter" | "answers" |
        "outreach"). Every save is a NEW version: nothing is overwritten, Job360
        versions it and renders DOCX / PDF from it."""
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
        visa_signal: Optional[str] = None,
        visa_detail: str = "",
        visa_country: str = "",
        axes: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Record YOUR OWN fit judgement for this application — never computed
        by Job360 (VISION rule 4). Overwrites the current verdict; the log
        keeps every past judgement too. Visa (optional, same as bring_job): if
        the ad says whether the employer sponsors, pass `visa_signal`
        ("sponsors" / "no_sponsorship"), the ad sentence in `visa_detail`, and
        the job's ISO alpha-2 country in `visa_country`; leave it out when the ad
        is silent.

        `axes` (optional) is the fit PICTURE the web draws as a radar chart:
        3 to 8 dimensions YOU name from this ad and the profile, each
        `{"name": "...", "role": 0-100, "you": 0-100}` — `role` is how much
        the role asks on that dimension, `you` how much the seeker brings.
        Pick the dimensions that matter for THIS job (for one ad that may be
        depth in a stack, domain knowledge, leadership, location, pay; for
        another something else). Job360 never names an axis or scores one;
        it draws exactly what you send. Omit `axes` and no chart is shown.

        Next: write the tailored CV / cover letter yourself and save it with
        save_artifact."""
        try:
            body = applications_route.SaveFitRequest(
                score=score, verdict=verdict, gaps=gaps, reasoning=reasoning,
                visa_signal=visa_signal, visa_detail=visa_detail, visa_country=visa_country,
                axes=axes,  # type: ignore[arg-type]
            )
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
        source: Optional[dict[str, Any]] = None,
        scheduled_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Append one event to this application's history — replied, an
        interview stage, a note, a lesson learned. `occurred_at` may be in the
        past (backdating a reply you just found is normal); it may not be
        implausibly in the future. A status event (applied/replied/interview_*/
        offer/rejected/withdrawn/ghosted) moves the application's status; a
        note-family event never does.

        `source` names the email an event came from (kind/message_id/sender/
        subject/received_at) — the same message_id on the same application is
        the same event, so you get the first one back with
        already_existed=true and nothing is written; re-reading an inbox is
        safe. `scheduled_at` is the real interview datetime (ISO-8601 with a
        timezone) and is only accepted on interview_requested/
        interview_scheduled."""
        try:
            body = applications_route.RecordEventRequest(
                event_type=event_type, detail=detail, payload=payload or {},
                occurred_at=occurred_at, corrects_event_id=corrects_event_id,
                source=applications_route.EventSource(**source) if source else None,
                scheduled_at=scheduled_at,
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

    # ── Slice 4 (docs/plans/2026-09-05-contacts-stats/spec.md, R12/S11) ──────
    # Three more tools, each calling its route FUNCTION directly, same as the
    # spine tools above. None require_verified_user (no LLM call).

    @mcp.tool()
    async def add_contact(
        application_id: int,
        name: str,
        role: str = "",
        email: str = "",
        linkedin_url: str = "",
        notes: str = "",
        occurred_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record a person met during this application's outreach — a
        recruiter, referral, hiring manager. Give an email whenever you have
        one: adding the SAME email again on the SAME application returns the
        existing contact (already_existed=true) instead of a duplicate, so
        re-running this safely never doubles up. Without an email every call
        makes a new row."""
        try:
            body = applications_route.AddContactRequest(
                name=name, role=role, email=email, linkedin_url=linkedin_url,
                notes=notes, occurred_at=occurred_at,
            )
        except ValidationError as exc:
            raise _validation_error(exc) from None
        try:
            async with _request_db() as db:
                resp = await applications_route.add_contact(application_id, body, Response(), db, _user())
        except HTTPException as exc:
            _audit("add_contact", "error", application_id=application_id, http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit(
            "add_contact", "ok", application_id=application_id,
            already_existed=resp.get("already_existed", False),
        )
        return resp

    @mcp.tool()
    async def stats(since: Optional[str] = None) -> dict[str, Any]:
        """Counts over YOUR applications from the event log — brought,
        applied, replied, interview, offer, rejected — plus rates and two
        groupings: by CV version (label the CV with save_artifact's `label`
        to get a per-variant count here) and by role. `since` (an ISO
        date/datetime) scopes to applications brought on/after that date.
        Nothing is inferred; every number is a count of events you recorded."""
        try:
            async with _request_db() as db:
                resp = await applications_route.stats(since, db, _user())
        except HTTPException as exc:
            _audit("stats", "error", http_status=exc.status_code)
            raise _tool_error(exc) from None
        _audit("stats", "ok")
        return resp

    @mcp.tool()
    async def update_profile(edits: list[dict[str, Any]]) -> dict[str, Any]:
        """Write the profile — this is how the structured fields get filled.

        Job360 only reads a document's STRUCTURE (decision 28). Everything it
        cannot prove is yours to supply: read `get_profile`'s `raw` texts, then
        send what you found here. Also use it to correct something the
        structural read got wrong, or a preference the user told you.

        Each edit is {"path": <one of get_profile's editable_paths>, "value":
        <new value, or null to clear back to what the structural read says>}.
        An unknown path or a wrongly-typed value is refused with the allowed
        set/values named. Send several edits in one call.
        To drop a wrong skill from every surface, add it to
        `preferences.excluded_skills` (value = the current list from
        get_profile's `fields` plus the new names); rewriting `cv_data.skills`
        only reaches the CV's share and is capped per edit.
        A re-extraction (a fresh CV/LinkedIn/GitHub) never undoes your edit —
        only clearing it does."""
        try:
            # `model_validate` (not the constructor) so the raw `list[dict]`
            # coming in over MCP is validated/coerced into `ProfileEditIn`
            # rows by Pydantic itself, rather than mypy expecting the caller
            # to have already typed them.
            body = profile_route.UpdateProfileRequest.model_validate({"edits": edits})
        except ValidationError as exc:
            raise _validation_error(exc) from None
        paths = [e.get("path") for e in edits]
        try:
            resp = await profile_route.update_profile(body, _user())
        except HTTPException as exc:
            # S3 — paths only, never values, in the audit trail.
            _audit("update_profile", "error", http_status=exc.status_code, paths=paths)
            raise _tool_error(exc) from None
        _audit("update_profile", "ok", paths=paths)
        # The route now returns a typed `UpdateProfileResponse` (so OpenAPI
        # tells the truth); MCP tools answer with plain JSON.
        return resp.model_dump()

    return mcp


# ── Telling a connected client the tool list moved ─────────────────────────────


def tools_fingerprint(tools: list[McpTool]) -> str:
    """A short, stable id for the tool surface: every client-visible tool field.

    Reported as ``serverInfo.version``, so an ``initialize`` result says which
    tool surface this process is serving. Production has no other honest
    version signal — ``/api/health`` returns a hardcoded ``"1.0.0"``
    (CLAUDE.md) — so this is the only way to tell from outside whether a
    deploy changed the tools an agent can see.

    Covers name, description, input schema and output schema — everything
    ``tools/list`` actually hands a client (CodeRabbit, PR #604). A
    description-only or output-schema-only deploy must move this fingerprint
    too, or ``serverInfo.version`` would silently lie about what changed.

    It is an **instrument, not a mechanism**: no MCP revision obliges a client
    to re-fetch when ``serverInfo.version`` changes. It makes the change
    observable; :class:`_AnnounceToolListChanged` is what tries to act on it.
    """
    payload = json.dumps(
        [
            [t.name, t.description, t.input_schema, t.output_schema]
            for t in sorted(tools, key=lambda t: t.name)
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _declare_tools_list_changed(result: HandlerResult) -> HandlerResult:
    """Stamp ``capabilities.tools.listChanged = true`` on an ``initialize`` result.

    The spec (2025-06-18 / 2025-11-25 ``server/tools``) only lets a server send
    ``notifications/tools/list_changed`` if it declared the ``listChanged``
    capability, and a client that is told ``false`` is entitled to cache the
    tool list forever — which is exactly what we observed.

    The SDK derives the flag from a ``NotificationOptions`` the streamable-HTTP
    path never lets us supply: ``mcp/server/runner.py`` calls
    ``create_initialization_options()`` with no arguments, so the flag is
    always ``False`` on a handshake-era wire (measured: ``list_changed=False``
    at 2024-11-05 → 2025-11-25, ``True`` at 2026-07-28, where it is derived
    from ``subscriptions/listen`` being served instead).

    So it is stamped here, on the wire shape — which is the protocol and does
    not move between SDK versions — and deliberately in the same class that
    sends the notification, so the promise and the delivery cannot drift.
    """
    if not isinstance(result, dict):  # pragma: no cover — initialize always dumps to a dict
        return result
    capabilities = result.get("capabilities")
    if isinstance(capabilities, dict):
        tools = capabilities.get("tools")
        if isinstance(tools, dict):
            tools["listChanged"] = True
    return result


class _AnnounceToolListChanged:
    """Tell a client that cached an older tool list to fetch it again.

    **Why.** 2026-09-20: a deploy gave ``save_fit`` an optional ``axes``
    parameter. Claude.ai, already connected, kept answering from the tool list
    it had cached and told the user "save_fit has no axes field" until they
    disconnected and reconnected the connector by hand. A deploy restarts the
    process, so every MCP session is dropped and remade — and the client still
    did not re-fetch. The owner cannot ask every user to reconnect per deploy.

    **Where the notification rides.** A stateless mount has no standalone
    back-channel (``mcp/server/streamable_http_manager.py``: the stateless path
    builds the connection with the no-channel sentinel, so
    ``session.send_tool_list_changed()`` is silently dropped). The one channel
    it does have is *this POST's own response stream*, which exists only when
    the transport answers in SSE rather than a single JSON body (the SDK:
    "``is_json_response_enabled`` … removes the request-scoped back-channel …
    its notifications are dropped"). Hence ``json_response`` is off whenever
    this middleware is installed — the two are one decision, made in
    :func:`mcp_runtime` off ``settings.MCP_ANNOUNCE_TOOLS_CHANGED``.

    **When.** On the first request of *any* method each user makes after this
    process started — once per user per deploy, because the deploy restarts
    the process and empties the set. Deliberately not narrowed to
    ``tools/call``: most clients open with ``tools/list``, and being told then
    means they heal *before* the first tool call rather than after one wrong
    answer. Re-fetching cannot loop — the second ``tools/list`` finds the user
    already told.

    Not on ``initialize``: the SDK handles it inline with the read loop
    parked, so anything written there reaches the wire *before* the initialize
    response, and a client that has not finished its handshake is entitled to
    ignore it. Every other request's stream is live and post-handshake.

    **What it cannot guarantee.** No MCP revision makes the client's re-fetch
    mandatory — the 2025-06-18 and 2025-11-25 specs put the only normative
    sentence on the *server* ("servers that declared the ``listChanged``
    capability SHOULD send a notification"); the client-side re-fetch appears
    only in a non-normative sequence diagram. So this gives a conforming
    client everything it needs to notice, and cannot make it look.
    """

    # Bound on the "already told" set, so a long-lived process cannot grow it
    # without limit. Past it the middleware simply stops announcing — which is
    # the pre-2026-09-20 behaviour, not a new failure — and by then the process
    # has been up long enough that everyone reconnecting after the deploy has.
    MAX_TRACKED_USERS = 50_000

    def __init__(self) -> None:
        # Already told, for the life of this process. One entry per user who
        # sent any non-initialize request; a deploy clears it by restarting.
        self._announced: set[str] = set()

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.method == "initialize":
            return _declare_tools_list_changed(await call_next(ctx))
        # Announce BEFORE the handler runs: the notification then leads the
        # request's stream whatever the handler does, including raising.
        if ctx.request_id is not None:  # a request has a stream; a notification does not
            await self._announce_once(ctx)
        return await call_next(ctx)

    async def _announce_once(self, ctx: ServerRequestContext[Any, Any]) -> None:
        """Write the notification onto this request's own stream, at most once per user.

        Marked as told *before* the send, not after. The SDK's notification
        path never raises on a dead stream — it swallows
        ``BrokenResourceError``/``ClosedResourceError`` and debug-logs the drop
        — so "no exception" is not evidence of delivery, and treating it as
        evidence would silently re-announce forever to a client that hung up.
        At-most-once per process is the honest contract.
        """
        from mcp_types import ToolListChangedNotification

        user = _current_user.get()
        if user is None or user.id in self._announced:
            return
        if len(self._announced) >= self.MAX_TRACKED_USERS:  # pragma: no cover — 50k users on one process
            return
        self._announced.add(user.id)
        # `related_request_id` is the selector: present = this request's own
        # stream, absent = the standalone channel a stateless mount lacks.
        await ctx.session.send_notification(ToolListChangedNotification(), related_request_id=ctx.request_id)
        logger.info(
            "mcp_tools_list_changed_sent",
            extra={"event": "mcp_tools_list_changed_sent", "user_id": user.id, "method": ctx.method},
        )


# ── Runtime + ASGI mount ───────────────────────────────────────────────────────


def _build_handler(
    *, version: str, announce: bool, security: Any
) -> tuple[Any, Callable[[Scope, Receive, Send], Awaitable[None]]]:
    """One stateless streamable-HTTP leg: its session manager and ASGI handler.

    ``announce`` picks the whole leg, not a detail of it: an SSE response
    stream is the only back-channel a stateless mount has, so the middleware
    that announces a tool-list change and the SSE response mode are the same
    decision (see :class:`_AnnounceToolListChanged`).
    """
    from mcp.server.streamable_http_manager import StreamableHTTPASGIApp

    mcp = build_server(version=version)
    if announce:
        mcp.middleware.append(_AnnounceToolListChanged())
    # Builds the session manager as a side effect; the Starlette app it returns
    # (routes + its own lifespan) is not used — the shim below IS the route.
    mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=not announce,
        stateless_http=True,
        transport_security=security,
    )
    manager = mcp.session_manager
    return manager, StreamableHTTPASGIApp(manager)


@contextlib.asynccontextmanager
async def mcp_runtime() -> AsyncIterator[None]:
    """Build the server(s) and run the SDK session manager(s) for the duration.

    Entered by the app lifespan in production and by tests directly. Re-entrant
    across separate ``async with`` blocks (a fresh server each time) — the SDK's
    manager itself cannot be restarted, so we never try.

    **Two legs, chosen by the client's own ``Accept`` header.** Announcing a
    tool-list change needs each POST answered with its own SSE stream, and the
    SDK 406s an SSE-mode request unless the client accepts both
    ``application/json`` and ``text/event-stream`` (wildcards count). The MCP
    spec says a client MUST accept both, so in practice every conforming client
    lands on the SSE leg and is told. Anything narrower gets exactly the
    transport it asked for — silent, and unchanged from before — because a
    client that cannot read SSE must not be broken by a change whose whole
    point is convenience. Content negotiation is what ``Accept`` is for; see
    :func:`_pick_handler`. ``MCP_ANNOUNCE_TOOLS_CHANGED=0`` collapses this back
    to the single JSON leg.
    """
    global _handler, _json_handler
    from mcp.server.transport_security import TransportSecuritySettings

    from src.core import settings

    if settings.MCP_ALLOWED_HOSTS:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=list(settings.MCP_ALLOWED_HOSTS)
        )
    else:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    # serverInfo.version is a fingerprint of the tool surface, and the tools
    # only exist once a server is built — so build a throwaway one to read the
    # list, then the real one(s) stamped with it. Registering seventeen
    # decorated functions is microseconds; the lazy imports are already warm.
    fingerprint = tools_fingerprint(await build_server().list_tools())
    announce = settings.MCP_ANNOUNCE_TOOLS_CHANGED

    async with contextlib.AsyncExitStack() as stack:
        manager, handler = _build_handler(version=fingerprint, announce=announce, security=security)
        await stack.enter_async_context(manager.run())
        json_handler: Optional[Callable[[Scope, Receive, Send], Awaitable[None]]] = None
        if announce:
            json_manager, json_handler = _build_handler(
                version=fingerprint, announce=False, security=security
            )
            await stack.enter_async_context(json_manager.run())
        # Published only once every leg is running: a half-built runtime must
        # leave the mount answering 503, never route to a dead manager.
        _handler, _json_handler = handler, json_handler
        logger.info(
            "mcp_runtime_started",
            extra={
                "event": "mcp_runtime_started",
                # The one honest "which tools is prod serving" signal in the
                # logs; compare it across deploys to see the surface move.
                "tools_fingerprint": fingerprint,
                "announce_tools_changed": announce,
            },
        )
        try:
            yield
        finally:
            _handler = _json_handler = None
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


def _pick_handler(request: Request) -> Optional[Callable[[Scope, Receive, Send], Awaitable[None]]]:
    """The transport leg this client asked for, by its ``Accept`` header.

    The SSE leg (the one that can announce a tool-list change) 406s a request
    unless the client accepts BOTH ``application/json`` and
    ``text/event-stream``, so anything narrower is handed the JSON leg instead
    of an error. Both legs serve the same tools through the same routes; they
    differ only in the response media type and therefore in whether the server
    has a back-channel to speak on.

    The SDK's own ``check_accept_headers`` is the predicate, deliberately —
    a substring test for ``text/event-stream`` would disagree with it in both
    directions: it would send ``Accept: */*`` (what a plain httpx or requests
    client sends, and what the SDK happily serves SSE to) down the silent JSON
    leg, and it would send ``Accept: text/event-stream`` alone to the SSE leg
    to collect a 406. One predicate, no drift.
    """
    if _json_handler is None:  # announcing off (or no runtime): one leg, or none
        return _handler
    from mcp.server.streamable_http import check_accept_headers

    has_json, has_sse = check_accept_headers(request)
    return _handler if (has_json and has_sse) else _json_handler


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
    handler = _pick_handler(request)
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
