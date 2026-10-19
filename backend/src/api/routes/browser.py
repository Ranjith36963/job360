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
  application, rendered by Job360.
"""
from __future__ import annotations

import contextlib
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
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

    return browser_agent.Gate(check=_check, mark_attempt=_mark), _files


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
            on=body.on, doc_kind=body.doc_kind, gate=gate, files=files,
        )
    except browser_agent.BrowserError as exc:
        _audit(user, body.action, exc.status, exc.reason)
        return exc.as_dict()
    _audit(user, body.action, str(result.get("status", "")), str(result.get("reason", "")))
    return result
