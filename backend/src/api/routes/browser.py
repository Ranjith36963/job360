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
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.core import settings
from src.services.applications import spine as applications_spine
from src.services.browser_agent import agent as browser_agent
from src.services.browser_agent import outcome as browser_outcome
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
    user: CurrentUser, application_id: int, session: str, actor: Optional[str] = None,
) -> tuple[browser_agent.Gate, browser_agent.FileFn]:
    """``actor``: who the upload records name (default: the caller; a boot restore passes ``"system"``)."""
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
                    fmt="pdf", renderer_version=RENDERER_VERSION, host=host, recorded_by=actor or actor_for(user),
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
    steps = result.pop(browser_agent.STEPS_KEY, None)
    stop_source = str(result.pop(browser_agent.STOP_KEY, "") or "")
    if steps and body.application_id is not None:
        await _keep_steps(user, body.application_id, steps)
    await _book_attempt(user, body, result, stop_source)
    if result.get("status") == "needs_you" and body.application_id is not None:
        result["blocked_recorded"] = await _hand_off(user, body.application_id, result)
    elif body.action == "open" and body.application_id is not None and _bot_blocked(result, stop_source):
        # A bot wall answered the open (transport facts only, C3): the job waits on Needs you as a bot check.
        result["blocked_recorded"] = await _hand_off(user, body.application_id, result, reason="bot_check")
    elif body.action in _FILL_ACTIONS and body.application_id is not None and result.get("status") == "ok":
        result["held_for_you"] = await _hold_if_waiting(user, body.application_id)
    _audit(user, body.action, str(result.get("status", "")), str(result.get("reason", "")))
    return result


# Slice 3: the browser's stop reasons, in the blocked record's closed set (services/applications/blocked.py).
_BLOCKED_REASON = {"captcha": "captcha", "login_required": "login_needed", "bot_check": "bot_check"}


def _open_outcome(result: dict[str, Any], stop_source: str) -> str:
    """The open's outcome, read from its ``transport`` (status, headers, final host) only, never the page
    text (a body-text stop is ``outcome.TEXT_STOP``, never a bot block)."""
    needs_you = str(result.get("reason") or "") if result.get("status") == "needs_you" else ""
    transport = result.get("transport")
    return browser_outcome.classify(
        transport if isinstance(transport, dict) else {}, stop_reason=stop_source, needs_you=needs_you,
    )


def _bot_blocked(result: dict[str, Any], stop_source: str) -> bool:
    return _open_outcome(result, stop_source) == "bot_block"


async def _book_attempt(
    user: CurrentUser, body: BrowserActionRequest, result: dict[str, Any], stop_source: str,
) -> None:
    """Routes PR C1, record only: book what this action met in the route book. Every ``open`` that came back
    books its outcome (``ok`` once per session, site and task); a confirmation page seen after any action books
    ``confirmed`` once. A refused or failed action never reaches here (the SSRF guard's refusals included), and
    this never breaks the action: any failure is logged without the host and dropped."""
    from src.services.browser_agent import route_book, routing  # noqa: PLC0415

    task = routing.learn_task(body.action, body.application_id, bool(result.get("application_form")))
    booked: list[tuple[str, str]] = []
    if body.action == "open":
        booked.append((body.url, _open_outcome(result, stop_source)))
    if result.get("confirmation_seen") is True:
        booked.append((body.url or str(result.get("url") or ""), "confirmed"))
    for url, outcome in booked:
        try:
            if not route_book.first_in_session(user.id, body.session, body.application_id, url, task, outcome):
                continue
            async with _db() as db:
                await route_book.record(db, user.id, url, task, "job360", outcome)
        except Exception as exc:  # noqa: BLE001 - booking never breaks a browser action
            route_book._log.warning(
                "route attempt not booked",
                extra={"event": "route_attempt_failed", "outcome": outcome, "error": type(exc).__name__},
            )
HAND_OFF_DETAIL = (
    "Job360's cloud browser stopped here. It is waiting for your own browser: finish it with your assistant on "
    "your computer when it is on, or sign in once on Settings, Job sites."
)
# A human check: the filled form is kept open for the user to finish here (agent.hold_for_user).
CAPTCHA_DETAIL = (
    "The site asked for a human check. Your filled form is kept open for 30 minutes: press Finish it here, "
    "solve the check yourself, then press Done. After that, finish it in your own browser."
)


# A refill of a form whose CAPTCHA waits on the user: keep it open for them, like the first time (owner, 2026-10-24).
_FILL_ACTIONS = frozenset({"fill", "type", "choose", "tick", "upload"})


async def _hold_if_waiting(user: CurrentUser, application_id: int) -> bool:
    """The application's open block is a CAPTCHA that Job360's browser itself recorded (``by: browser``,
    never a caller's look-alike): keep the browser held for the user instead of the 10-minute idle close.
    True when held. A running hold is never extended here (only ``_hand_off``, a fresh CAPTCHA, renews),
    and a new one follows the form_swept rule: at most one per form page (Done or a new page frees it),
    under the held cap. Otherwise an assistant's fills could keep a browser held forever (review of #1176)."""
    from src.services.applications import blocked as blocked_service  # noqa: PLC0415

    async with _db() as db:
        if not await blocked_service.browser_captcha_open(db, user.id, application_id):
            return False
    return browser_agent.hold_for_user(browser_agent.get_pool(), user.id, application_id, swept=True)


async def _hand_off(
    user: CurrentUser, application_id: int, result: dict[str, Any], reason: Optional[str] = None,
) -> bool:
    """Record ``blocked`` for the application the browser stopped on, so the job waits on Needs you
    whatever the assistant does next. Once per open block of the same reason (no event per look).
    ``reason``: the browser's stop reason to record (default: the result's own). True when a new
    ``blocked`` was recorded."""
    from src.services.applications import blocked as blocked_service  # noqa: PLC0415
    from src.services.browser_agent.sign_ins import site_host  # noqa: PLC0415

    reason = _BLOCKED_REASON.get(str(reason or result.get("reason")), "other")
    # A fresh CAPTCHA gets its full 30 minutes, even inside a form_swept hold that is almost over.
    held = reason == "captcha" and browser_agent.hold_for_user(
        browser_agent.get_pool(), user.id, application_id, renew=True
    )
    async with _db() as db:
        current = await blocked_service.open_block(db, user.id, application_id)
        # A caller's look-alike CAPTCHA block (no ``by: browser``) is replaced by the browser's own.
        if current is not None and current["reason"] == reason and (
            reason != "captcha" or await blocked_service.browser_captcha_open(db, user.id, application_id)
        ):
            return False
        # "Job360 browser" is read by the web (ApplicationDecisions.tsx) to offer "Sign in once on Job sites".
        payload: dict[str, Any] = {
            "reason": reason, "step": "Job360 browser", "detail": CAPTCHA_DETAIL if held else HAND_OFF_DETAIL,
        }
        host = site_host(str(result.get("url", "")))
        if host and "." in host:
            payload["page_host"] = host
        # Not record_event: this path stores the server-only ``by: browser`` a caller can never write.
        try:
            await blocked_service.record_browser_block(db, user, application_id, payload)
        except applications_spine.SpineError as exc:  # the same HTTP error record_event gave
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return True


async def _keep_steps(user: CurrentUser, application_id: int, steps: list[dict[str, Any]]) -> None:
    """Store the action's form steps as the job's replayable fill script. The gate already proved the job is
    the caller's on a gated action; an ungated one (type, fill) is checked here. Never stops the action."""
    from src.services.applications import fill_script  # noqa: PLC0415

    try:
        async with _db() as db:
            if await applications_spine.get_owned_application(db, user.id, application_id) is not None:
                await fill_script.append(db, user.id, application_id, steps)
    except Exception:  # noqa: BLE001 - the script is a convenience: the user's action already happened
        get_audit_logger().warning(
            "fill_steps_failed",
            extra={"event": "fill_steps_failed", "user_id": safe_log_value(user.id),
                   "application_id": int(application_id), "result": "error"},
        )


# ── Refill the form (owner decision 2026-10-26), on the website only ──────────

REFILL_DETAIL = {
    "browser_off": "Job360's browser is not turned on.",
    "no_script": "Your assistant has not filled this form in Job360's browser yet, so there is nothing to refill.",
    "host_mismatch": "The filled answers were typed on another site than this job's form. Ask your assistant.",
    "busy": "Job360's browser is busy with this job right now. Try again in a minute.",
}


def _refill_refused(reason: str, detail: str = "") -> dict[str, Any]:
    return {"status": "refused", "reason": reason, "detail": detail or REFILL_DETAIL.get(reason, ""),
            "steps_ok": 0, "steps_failed": [], "held_seconds": None}


@router.post("/applications/{application_id}/browser/refill")
async def refill_form(
    application_id: int,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the user's own click only, never a token
) -> dict[str, Any]:
    """"Refill the form": Job360's own browser opens this job's form again and replays exactly what the user's
    assistant did there last time (the same answers, the same CV upload, the same option clicks), never
    anything new, never a submit, never a CAPTCHA; then keeps it open 30 minutes for Finish it here. Records
    ``form_filled`` {by: refill}: identical answers keep the user's yes. 404 when the job is not the
    caller's. Refusals: ``browser_off``, ``no_script``, ``host_mismatch``, ``busy``."""
    from src.services.applications.authorship import actor_for  # noqa: PLC0415

    out = await replay_and_hold(user, application_id, actor=actor_for(user), by="refill")
    get_audit_logger().info(
        "browser_refill",
        extra={"event": "browser_refill", "user_id": safe_log_value(user.id), "application_id": int(application_id),
               "result": safe_log_value(out["status"]), "reason": safe_log_value(out.get("reason", "")),
               "steps_ok": int(out["steps_ok"]), "steps_failed": len(out["steps_failed"])},
    )
    return out


async def replay_and_hold(
    user: CurrentUser, application_id: int, *, actor: str, by: str, ends_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Replay the job's stored fill script in Job360's browser (never a submit, never a CAPTCHA), hold the page
    for Finish it here, and record ``form_filled`` {by}. The hold runs to ``ends_at`` (a boot restore: the
    old hold's own end, never a fresh 30 minutes), else the full ``HANDOFF_HOLD_SECONDS``. 404 (HTTPException)
    when the job is not ``user``'s. Refusals: ``browser_off``, ``no_script``, ``host_mismatch``, ``busy``."""
    from src.services.applications import fill_script  # noqa: PLC0415
    from src.services.applications import kit as kit_service  # noqa: PLC0415
    from src.services.browser_agent import refill  # noqa: PLC0415

    async with _db() as db:
        if await applications_spine.get_owned_application(db, user.id, application_id) is None:
            raise HTTPException(status_code=404, detail="application not found")
        steps, hosts = await fill_script.load(db, user.id, application_id)
        form_url = await kit_service.latest_form_url(db, user.id, application_id)
        last_fill = await fill_script.newest_fill(db, user.id, application_id)
    if not settings.BROWSER_AGENT_ENABLED:
        return _refill_refused("browser_off")
    if not steps or not form_url:
        return _refill_refused("no_script")
    host = refill.host_of(form_url)
    if host not in hosts:
        return _refill_refused("host_mismatch")
    _gate, files = _gate_and_files(user, application_id, "", actor=actor)
    pool = browser_agent.get_pool()
    try:
        done = await refill.replay(
            pool, user.id, application_id, form_url, [s for s in steps if s["host"] == host],
            files=files, load_state=_loader(user),
        )
    except browser_agent.BrowserError as exc:
        reason = {"busy": "busy", "off": "browser_off"}.get(exc.status, exc.reason)
        return _refill_refused(reason, REFILL_DETAIL.get(reason, exc.detail))
    if done["status"] == "ok":
        # Measured after the replay (it can take minutes): the hold still ends when the old one would have.
        seconds = (
            browser_agent.HANDOFF_HOLD_SECONDS if ends_at is None
            else min((ends_at - datetime.now(timezone.utc)).total_seconds(), browser_agent.HANDOFF_HOLD_SECONDS)
        )
        if seconds > 0:
            browser_agent.hold_for_user(pool, user.id, application_id, seconds, renew=True)
        done["held_seconds"] = browser_agent.held_seconds(pool, user.id, application_id)
        await _record_refill(user, application_id, last_fill, form_url, done, actor, by)
    return done


async def _record_refill(
    user: CurrentUser, application_id: int, last_fill: Optional[dict[str, Any]], form_url: str,
    done: dict[str, Any], actor: str, by: str = "refill",
) -> None:
    """``form_filled`` {by: refill}: the newest fill's own record when every step held (the same answers, so the
    user's yes stays good), else that record without its answers (a form the user has not seen: the yes is stale)."""
    payload = {**(last_fill or {}), "form_url": form_url, "by": by}
    payload.setdefault("fields_count", int(done["steps_ok"]))
    if done["steps_failed"]:
        payload.pop("answers", None)
        payload["refill_failed"] = len(done["steps_failed"])
    async with _db() as db:
        await applications_spine.append_event(
            db, user_id=user.id, application_id=application_id, event_type="form_filled",
            occurred_at=applications_spine.parse_occurred_at(None), recorded_by=actor, payload=payload,
        )


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
    human check in their assistant's browser instead (shot, click, type, key, scroll, back, done), in the
    browser of ``application_id``; when the user's click or key lands on the site's confirmation page,
    the application is recorded as submitted by the user and the answer carries ``submitted: true``."""
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
    proof_image = result.pop(browser_agent.PROOF_KEY, None)
    if body.mode == "finish" and body.application_id is not None and result.pop("confirmation_seen", False):
        result["submitted"] = await _user_submitted(user, body.application_id, str(result.get("url", "")), proof_image)
    _audit(user, f"{_LIVE_AUDIT[body.mode]}_{body.action}", str(result.get("status", "")))
    return result


async def _user_submitted(user: CurrentUser, application_id: int, url: str, image: Optional[bytes]) -> bool:
    """The user's own click in the hand-off view landed on the site's confirmation (the detector the
    assistant's submit uses, ``agent._confirmation``): record it as "I submitted it" does (``unblocked``
    user_did_it, the receipt by web, the CAPTCHA asks closed), keep the page as proof, and let the browser
    go as Done does. True also when it was already recorded (a second confirmation writes nothing)."""
    from src.api.routes import applications as applications_route  # noqa: PLC0415 - avoid an import cycle
    from src.services.browser_agent.sign_ins import site_host  # noqa: PLC0415

    host = site_host(url) or "the site"
    try:
        async with _db() as db:
            await applications_route.record_user_submit(db, user, application_id, seen_on=host)
    except HTTPException as exc:
        if exc.status_code != 409:  # 409: already applied, so nothing new (no second receipt, no second proof)
            raise
        browser_agent.release_for_user(browser_agent.get_pool(), user.id, application_id)
        return True
    save_proof = _gate_and_files(user, application_id, "")[0].save_proof
    if image and save_proof is not None:
        try:
            await save_proof(image)
        except Exception:  # noqa: BLE001 - proof is extra: the receipt is already kept
            get_audit_logger().warning(
                "live_proof_failed",
                extra={"event": "live_proof_failed", "user_id": safe_log_value(user.id),
                       "application_id": int(application_id), "result": "error"},
            )
    browser_agent.release_for_user(browser_agent.get_pool(), user.id, application_id)
    return True


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
