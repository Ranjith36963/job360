"""A scheduled run's own credential never approves for the user (owner decision 2026-11-02, option b).

The owner's hourly scheduled tasks and his live chats used one connector, and MCP is stateless, so the
server could not tell a run from him. A run now gets its own personal token, minted with
``for_scheduled_runs`` (``api_tokens``, migration 0093); ``CurrentUser.scheduled_run`` is True only for
it. Every approval such a caller sends for the user is refused here with one code and one message, on
REST and MCP alike (the MCP tools call the same route functions): a chat ``cv_seen`` /
``submit_approved`` / ``submit_declined``, a remembered or newly approved answer, and an answered ask.
Run work (``form_filled``, a guess, ``submit_attempted``, a setting REQUEST) stays allowed. Setting
confirms were already web-session only, so a token of any kind can only request.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:
    from src.api.auth_deps import CurrentUser

CODE = "chat_approval_from_run"
MESSAGE = "A scheduled run cannot approve for the user; put it on Needs you"


def refuse_if_run(user: CurrentUser, what: str) -> None:
    """403 ``chat_approval_from_run`` when ``user`` is a scheduled-run token; otherwise nothing.
    ``what`` is a closed label (the event type or the path kind) for the audit line, never a value."""
    if not user.scheduled_run:
        return
    get_audit_logger().warning(
        "chat_approval_from_run",
        extra={
            "event": "chat_approval_from_run", "user_id": safe_log_value(user.id),
            "what": safe_log_value(what, max_len=40), "status": 403, "result": "refused",
        },
    )
    raise HTTPException(status_code=403, detail={"code": CODE, "message": MESSAGE})
