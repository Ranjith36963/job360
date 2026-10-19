"""The Job360 browser agent's one route (docs/product/BROWSER_AGENT.md).

``POST /api/browser`` carries out ONE action in the caller's own cloud
browser and returns the page after it. The MCP ``browser`` tool calls this
same function (hard rule M5: one per-user route per tool). The thinking stays
with the caller's assistant; the safety stays here and in
``services/browser_agent/safety.py``:

* OFF until ``BROWSER_AGENT_ENABLED`` (the answer is ``status: off``);
* the browser is the caller's own (keyed by ``user.id``, never a parameter —
  rule #12);
* the final submit runs the caller's ``submit_check`` and records
  ``submit_attempted`` first, both scoped to the caller;
* uploads are only the caller's own saved CV / cover letter for that
  application, rendered by Job360;
* step 6: when a gated submit lands on the site's confirmation, a screenshot
  of it is stored as that application's proof (``proof_screenshot``);
* slice 3: when the browser stops on a CAPTCHA or a sign-in wall for a named
  application, Job360 records ``blocked`` itself (once per open block), so the
  job waits on Needs you for the user's own assistant's browser;
* slice 2: the user signs in to a job site THEMSELVES in a live view
  (``POST /api/browser/live``, a browser session only — never a token, so no
  assistant can drive it or type into it). "Keep me signed in" saves what the
  site remembered, encrypted (``services/browser_agent/sign_ins.py``); every
  later browser of that user starts with it. What the user types is never
  logged, stored or returned.
"""
from __future__ import annotations

import contextlib
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.core import settings
from src.services.applications import spine as applications_spine
from src.services.browser_agent import agent as browser_agent
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["browser"])


class BrowserActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(max_length=16)
    url: str = Field("", max_length=2048)
    ref: str = Field("", max_length=32)
    text: str = Field("", max_length=browser_agent.MAX_TYPE_CHARS)
    on: bool = True
    doc_kind: str = Field("", max_length=16)
    application_id: Optional[int] = None
    session: str = Field("", max_length=64)


OFF = {
    "status": "off",
    "reason": "not_enabled",
    "detail": "Job360's browser is not turned on. Use your own browser, or record_event blocked so the job "
    "waits on Needs you.",
}


def _db() -> contextlib.AbstractAsyncContextManager[Any]:
    """A DB connection opened only for the moment it is needed — never held across a page load."""
    return contextlib.asynccontextmanager(get_request_db)()


def _gate_and_files(
    user: CurrentUser, application_id: int, session: str
) -> tuple[browser_agent.Gate, browser_agent.FileFn]:
    from src.api.routes import applications as applications_route  # noqa: PLC0415 - avoid an import cycle

    async def _check(form_url: str) -> dict[str, Any]:
        async with _db() as db:
            return await applications_route.submit_check(
                application_id, form_url=form_url, session=session, db=db, user=user
            )

    async def _mark() -> None:
        async with _db() as db:
            await applications_route.record_event(
                application_id, applications_route.RecordEventRequest(event_type="submit_attempted"), db, user
            )

    async def _files(kind: str) -> tuple[str, bytes]:
        from src.services.tailoring.cv_render import render_document_pdf  # noqa: PLC0415 - fpdf2 is heavy

        async with _db() as db:
            row = await applications_spine.latest_artifact(db, user.id, application_id, kind)
        if row is None:
            raise browser_agent.BrowserError(
                "refused", "no_document", f"No {kind} is saved for this application: save_artifact first."
            )
        pdf = await render_document_pdf(user.id, kind, row.get("text") or "")
        return ("CV.pdf" if kind == "cv" else "Cover_letter.pdf"), pdf

    async def _proof(image: bytes) -> None:
        # The site's confirmation page, kept as this application's proof screenshot (S7).
        from datetime import datetime, timezone  # noqa: PLC0415

        from src.services.applications import proof  # noqa: PLC0415
        from src.services.applications.authorship import actor_for  # noqa: PLC0415

        async with _db() as db:
            await proof.store_screenshot(
                db, user.id, application_id, image, actor_for(user), "browser", datetime.now(timezone.utc)
            )

    return browser_agent.Gate(
        check=_check, mark_attempt=_mark, save_proof=_proof, application_id=application_id
    ), _files


def _loader(user: CurrentUser) -> browser_agent.StateFn:
    """The user's saved sign-ins, read only when a NEW browser is started for them."""

    async def _load() -> dict[str, Any]:
        from src.services.browser_agent import sign_ins  # noqa: PLC0415

        try:
            async with _db() as db:
                return await sign_ins.load_all(db, user.id)
        except sign_ins.SignInKeyMissingError:
            return {}

    return _load


def _audit(user: CurrentUser, action: str, result: str, reason: str = "") -> None:
    get_audit_logger().info(
        "browser_action",
        extra={
            "event": "browser_action", "user_id": safe_log_value(user.id), "action": safe_log_value(action),
            "result": safe_log_value(result), "reason": safe_log_value(reason),
        },
    )


@router.post("/browser", dependencies=AUTH_FIRST)
async def browser_action(
    body: BrowserActionRequest,
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """One action in the caller's cloud browser (open, look, click, type, choose, tick, upload, back,
    close). Refusals come back as ``status`` + ``reason`` + ``detail`` with HTTP 200, so an assistant
    reads them like any page; a 404 from the gate (not the caller's application) stays a 404."""
    if not settings.BROWSER_AGENT_ENABLED:
        _audit(user, body.action, "off")
        return dict(OFF)
    gate: Optional[browser_agent.Gate] = None
    files: Optional[browser_agent.FileFn] = None
    if body.application_id is not None:
        gate, files = _gate_and_files(user, body.application_id, body.session)
    try:
        result = await browser_agent.run(
            browser_agent.get_pool(), user.id, body.action, url=body.url, ref=body.ref, text=body.text,
            on=body.on, doc_kind=body.doc_kind, gate=gate, files=files, load_state=_loader(user),
        )
    except browser_agent.BrowserError as exc:
        _audit(user, body.action, exc.status, exc.reason)
        return exc.as_dict()
    if result.get("status") == "needs_you" and body.application_id is not None:
        result["blocked_recorded"] = await _hand_off(user, body.application_id, result)
    _audit(user, body.action, str(result.get("status", "")), str(result.get("reason", "")))
    return result


# Slice 3: the browser's stop reasons, in the blocked record's closed set (services/applications/blocked.py).
_BLOCKED_REASON = {"captcha": "captcha", "login_required": "login_needed"}
HAND_OFF_DETAIL = (
    "Job360's cloud browser stopped here. Finish it with your own assistant's browser when your computer is on "
    "(Claude in Chrome, Gemini in Chrome, ChatGPT agent), or sign in once on Settings, Job sites."
)


async def _hand_off(user: CurrentUser, application_id: int, result: dict[str, Any]) -> bool:
    """Record ``blocked`` for the application the browser stopped on, so the job waits on Needs you
    whatever the assistant does next. Once per open block of the same reason (no event per look).
    True when a new ``blocked`` was recorded."""
    from src.api.routes import applications as applications_route  # noqa: PLC0415 - avoid an import cycle
    from src.services.applications import blocked as blocked_service  # noqa: PLC0415
    from src.services.browser_agent.sign_ins import site_host  # noqa: PLC0415

    reason = _BLOCKED_REASON.get(str(result.get("reason")), "other")
    async with _db() as db:
        current = await blocked_service.open_block(db, user.id, application_id)
        if current is not None and current["reason"] == reason:
            return False
        # "Job360 browser" is read by the web (ApplicationDecisions.tsx) to offer "Sign in once on Job sites".
        payload: dict[str, Any] = {"reason": reason, "step": "Job360 browser", "detail": HAND_OFF_DETAIL}
        host = site_host(str(result.get("url", "")))
        if host and "." in host:
            payload["page_host"] = host
        await applications_route.record_event(
            application_id, applications_route.RecordEventRequest(event_type="blocked", payload=payload), db, user
        )
    return True


# ── Slice 2: the user's own sign-in, on the website only ──────────────────────


class LiveActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(max_length=16)
    url: str = Field("", max_length=2048)
    x: float = 0
    y: float = 0
    text: str = Field("", max_length=browser_agent.MAX_TYPE_CHARS)
    key: str = Field("", max_length=16)
    dy: float = 0


@router.post("/browser/live")
async def browser_live(
    body: LiveActionRequest,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the user's own hands only, never a token
) -> dict[str, Any]:
    """One step of the user signing in to a job site themselves: start (url), shot, click (x, y),
    type (text), key, scroll (dy), keep, cancel. Returns a picture of the page. Typed text is never
    logged or returned; the audit line names the action only."""
    if not settings.BROWSER_AGENT_ENABLED:
        _audit(user, f"live_{body.action}", "off")
        return dict(OFF)
    from src.services.browser_agent import sign_ins  # noqa: PLC0415

    async def _keep(host: str, state: dict[str, Any]) -> int:
        try:
            async with _db() as db:
                return await sign_ins.save(db, user.id, host, state)
        except sign_ins.SignInKeyMissingError:
            raise browser_agent.BrowserError(
                "refused", "no_key", "This server cannot keep sign-ins yet (no SESSION_SECRET)."
            ) from None

    try:
        result = await browser_agent.live(
            browser_agent.get_pool(), user.id, body.action, url=body.url, x=body.x, y=body.y, text=body.text,
            key=body.key, dy=body.dy, load_state=_loader(user), keep=_keep,
        )
    except browser_agent.BrowserError as exc:
        _audit(user, f"live_{body.action}", exc.status, exc.reason)
        return exc.as_dict()
    _audit(user, f"live_{body.action}", str(result.get("status", "")))
    return result


@router.get("/browser/sign-ins")
async def list_sign_ins(
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> dict[str, Any]:
    """The job sites the user's cloud browser is kept signed in to (never a cookie value)."""
    from src.services.browser_agent import sign_ins  # noqa: PLC0415

    async with _db() as db:
        sites = await sign_ins.list_sites(db, user.id)
    return {"enabled": settings.BROWSER_AGENT_ENABLED, "sites": sites}


@router.delete("/browser/sign-ins/{host}")
async def forget_sign_in(
    host: str,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> dict[str, Any]:
    """Forget one saved sign-in. The user's live browser (if any) is closed so it forgets too."""
    from src.services.browser_agent import sign_ins  # noqa: PLC0415

    clean = sign_ins.site_host(host)
    if not clean:
        raise HTTPException(status_code=422, detail="not a site")
    async with _db() as db:
        forgotten = await sign_ins.forget(db, user.id, clean)
    if not forgotten:
        raise HTTPException(status_code=404, detail="no saved sign-in for that site")
    await browser_agent.get_pool().close(user.id)
    _audit(user, "forget_sign_in", "ok")
    return {"forgotten": clean}
