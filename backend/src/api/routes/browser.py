"""The Job360 browser agent's one route (docs/product/BROWSER_AGENT.md).

``POST /api/browser`` carries out ONE action in the caller's own cloud
browser and returns the page after it. The MCP ``browser`` tool calls this
same function (hard rule M5: one per-user route per tool). The thinking stays
with the caller's assistant; the safety stays here and in
``services/browser_agent/safety.py``:

* OFF until ``BROWSER_AGENT_ENABLED`` (the answer is ``status: off``);
* the browser is the caller's own (keyed by ``user.id``, never a parameter —
  rule #12), one per job: each ``application_id`` gets its own browser;
* the final submit runs the caller's ``submit_check`` and records
  ``submit_attempted`` first, both scoped to the caller;
* uploads are only the caller's own saved CV / cover letter for that
  application, rendered by Job360, and each one records ``document_uploaded``
  (the sha256 of the exact bytes and the form's host - slice 29), which the
  receipt names as its ``files``;
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
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.core import settings
from src.services.applications import spine as applications_spine
from src.services.browser_agent import agent as browser_agent
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["browser"])


class FillField(BaseModel):
    """One field for action fill: a ref from the latest look and its value."""

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(max_length=32)
    text: str = Field("", max_length=browser_agent.MAX_TYPE_CHARS)


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
    fields: list[FillField] = Field(default_factory=list, max_length=browser_agent.MAX_FILL_FIELDS)


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
        try:
            async with _db() as db:
                await applications_route.record_event(
                    application_id, applications_route.RecordEventRequest(event_type="submit_attempted"), db, user
                )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            # G338: another session holds the submit claim - refuse, so nothing is clicked
            # (the route's BrowserError handler writes the audit line).
            raise browser_agent.BrowserError(
                "refused", "submit_already_started",
                "Another session already started submitting this application; nothing was clicked.",
            ) from None

    async def _files(kind: str) -> tuple[str, bytes]:
        from src.services.applications import kit as applications_kit  # noqa: PLC0415
        from src.services.applications import uploads  # noqa: PLC0415
        from src.services.applications.authorship import actor_for  # noqa: PLC0415
        from src.services.tailoring.cv_render import RENDERER_VERSION, render_document_pdf  # noqa: PLC0415 - heavy

        async with _db() as db:
            row = await applications_spine.latest_artifact(db, user.id, application_id, kind)
        if row is None:
            raise browser_agent.BrowserError(
                "refused", "no_document", f"No {kind} is saved for this application: save_artifact first."
            )
        pdf = await render_document_pdf(user.id, kind, row.get("text") or "")
        # Slice 29: the receipt names exactly these bytes (sha256 only). Never blocks the upload.
        host = await _page_host(user, application_id)
        try:
            async with _db() as db:
                await uploads.record(
                    db, user_id=user.id, application_id=application_id, kind=kind, artifact=row, data=pdf,
                    fmt="pdf", renderer_version=RENDERER_VERSION, host=host, recorded_by=actor_for(user),
                )
        except Exception:  # noqa: BLE001 - opening the connection failed: the upload still goes ahead
            get_audit_logger().warning(
                "document_uploaded_failed",
                extra={
                    "event": "document_uploaded_failed", "user_id": safe_log_value(user.id),
                    "application_id": int(application_id), "kind": safe_log_value(kind, max_len=20),
                    "artifact_id": int(row["id"]), "result": "error",
                },
            )
        # The file name an employer sees: the kit's own name for this PDF, else the plain default.
        stem = await applications_kit.named_stem(user.id, kind)
        return (f"{stem}.pdf" if stem else "CV.pdf" if kind == "cv" else "Cover_letter.pdf"), pdf

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


async def _page_host(user: CurrentUser, application_id: int) -> Optional[str]:
    """The host of the page that job's browser is on (the form an upload goes into); ``None``
    when there is no browser or the address has no host. Never raises."""
    from src.services.browser_agent.sign_ins import site_host  # noqa: PLC0415

    try:
        s = await browser_agent.get_pool().get(browser_agent.browser_key(user.id, application_id), create=False)
        if s is None:
            return None
        return site_host(str(s.page.url)) or None
    except Exception:  # noqa: BLE001 - a missing host never stops an upload
        return None


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
    """One action in the caller's cloud browser (open, look, click, type, choose, tick, fill, upload, back,
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
            owner=body.session, fields=[f.model_dump() for f in body.fields], application_id=body.application_id,
        )
    except browser_agent.BrowserError as exc:
        _audit(user, body.action, exc.status, exc.reason)
        refused = exc.as_dict()
        # A CAPTCHA that came up after the last look stops this action: the job still goes to Needs you.
        if exc.status == "needs_you" and body.application_id is not None:
            refused["blocked_recorded"] = await _hand_off(
                user, body.application_id, {**refused, "url": exc.url},
            )
        return refused
    if result.get("status") == "needs_you" and body.application_id is not None:
        result["blocked_recorded"] = await _hand_off(user, body.application_id, result)
    elif body.action in _FILL_ACTIONS and body.application_id is not None and result.get("status") == "ok":
        result["held_for_you"] = await _hold_if_waiting(user, body.application_id)
    _audit(user, body.action, str(result.get("status", "")), str(result.get("reason", "")))
    return result


# Slice 3: the browser's stop reasons, in the blocked record's closed set (services/applications/blocked.py).
_BLOCKED_REASON = {"captcha": "captcha", "login_required": "login_needed"}
HAND_OFF_DETAIL = (
    "Job360's cloud browser stopped here. Finish it with your own assistant's browser when your computer is on "
    "(Claude in Chrome, Gemini in Chrome, ChatGPT's own browser), or sign in once on Settings, Job sites."
)
# A human check: the filled form is kept open for the user to finish here (agent.hold_for_user).
CAPTCHA_DETAIL = (
    "The site asked for a human check. Your filled form is kept open for 30 minutes: press Finish it here, "
    "solve the check yourself, then press Done. After that, finish it in your own browser."
)


# A refill of a form whose CAPTCHA waits on the user: keep it open for them, like the first time (owner, 2026-10-24).
_FILL_ACTIONS = frozenset({"fill", "type", "choose", "tick", "upload"})


async def _hold_if_waiting(user: CurrentUser, application_id: int) -> bool:
    """The application's open block is a CAPTCHA in Job360's browser: hold the browser 30 minutes so the
    user finishes it on Needs you instead of the 10-minute idle close. True when held."""
    from src.services.applications import blocked as blocked_service  # noqa: PLC0415

    async with _db() as db:
        block = await blocked_service.open_block(db, user.id, application_id)
    if not block or block["reason"] != "captcha" or block["step"] != "Job360 browser":
        return False
    return browser_agent.hold_for_user(browser_agent.get_pool(), user.id, application_id)


async def _hand_off(user: CurrentUser, application_id: int, result: dict[str, Any]) -> bool:
    """Record ``blocked`` for the application the browser stopped on, so the job waits on Needs you
    whatever the assistant does next. Once per open block of the same reason (no event per look).
    True when a new ``blocked`` was recorded."""
    from src.api.routes import applications as applications_route  # noqa: PLC0415 - avoid an import cycle
    from src.services.applications import blocked as blocked_service  # noqa: PLC0415
    from src.services.browser_agent.sign_ins import site_host  # noqa: PLC0415

    reason = _BLOCKED_REASON.get(str(result.get("reason")), "other")
    held = reason == "captcha" and browser_agent.hold_for_user(browser_agent.get_pool(), user.id, application_id)
    async with _db() as db:
        current = await blocked_service.open_block(db, user.id, application_id)
        if current is not None and current["reason"] == reason:
            return False
        # "Job360 browser" is read by the web (ApplicationDecisions.tsx) to offer "Sign in once on Job sites".
        payload: dict[str, Any] = {
            "reason": reason, "step": "Job360 browser", "detail": CAPTCHA_DETAIL if held else HAND_OFF_DETAIL,
        }
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
    # "finish": the hand-off view, in the user's own assistant browser (a CAPTCHA the user solves).
    mode: Literal["sign_in", "finish"] = "sign_in"
    # finish: whose job's browser (required); sign_in ignores it (its own live view).
    application_id: Optional[int] = None


_LIVE_AUDIT = {"sign_in": "live", "finish": "finish"}


@router.post("/browser/live")
async def browser_live(
    body: LiveActionRequest,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the user's own hands only, never a token
) -> dict[str, Any]:
    """One step of the user signing in to a job site themselves: start (url), shot, click (x, y),
    type (text), key, scroll (dy), keep, cancel. Returns a picture of the page. Typed text is never
    logged or returned; the audit line names the action only. ``mode: finish``: the user finishes a
    human check in their assistant's browser instead (shot, click, type, key, scroll, done), in the
    browser of ``application_id``."""
    if body.mode == "finish" and body.application_id is None:
        raise HTTPException(status_code=422, detail="application_id is required for mode finish")
    if not settings.BROWSER_AGENT_ENABLED:
        _audit(user, f"{_LIVE_AUDIT[body.mode]}_{body.action}", "off")
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
            key=body.key, dy=body.dy, load_state=_loader(user), keep=_keep, finish=body.mode == "finish",
            application_id=body.application_id,
        )
    except browser_agent.BrowserError as exc:
        _audit(user, f"{_LIVE_AUDIT[body.mode]}_{body.action}", exc.status, exc.reason)
        return exc.as_dict()
    _audit(user, f"{_LIVE_AUDIT[body.mode]}_{body.action}", str(result.get("status", "")))
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
    """Forget one saved sign-in. The user's browsers (if any) are closed so they forget too."""
    from src.services.browser_agent import sign_ins  # noqa: PLC0415

    clean = sign_ins.site_host(host)
    if not clean:
        raise HTTPException(status_code=422, detail="not a site")
    async with _db() as db:
        forgotten = await sign_ins.forget(db, user.id, clean)
    if not forgotten:
        raise HTTPException(status_code=404, detail="no saved sign-in for that site")
    await browser_agent.get_pool().close_user(user.id, "sign_in_forgotten")
    await browser_agent.get_pool().close(browser_agent.live_key(user.id), "sign_in_forgotten")
    _audit(user, "forget_sign_in", "ok")
    return {"forgotten": clean}
