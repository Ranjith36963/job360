"""Frontend → server log bridge (full-lifecycle logging piece D).

A tiny endpoint the browser POSTs to so UI errors/events land in data/logs/
instead of vanishing when the user closes the tab. Rate-limited per client IP so
a runaway browser loop can't flood the logs; optional-user so errors that happen
before login are still captured. Logging must never break the client, so even a
rate-limited or malformed-ish call returns 204.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, optional_user
from src.services.auth import rate_limit as auth_rate_limit
from src.utils.logger import get_logger

router = APIRouter(tags=["client-log"])

_client_log = get_logger("client")  # "job360.client" → main job360 handlers (data/logs/)
_LEVELS = {"error", "warning", "info"}


class ClientLogRequest(BaseModel):
    level: str = Field(default="error")
    message: str = Field(min_length=1, max_length=2000)
    url: Optional[str] = Field(default=None, max_length=500)
    stack: Optional[str] = Field(default=None, max_length=4000)
    context: Optional[str] = Field(default=None, max_length=1000)


@router.post("/client-log", status_code=204)
async def client_log(
    body: ClientLogRequest,
    request: Request,
    user: Optional[CurrentUser] = Depends(optional_user),  # noqa: B008
) -> None:
    """Record a browser-side log event into the server's data/logs/ stream."""
    ip = request.client.host if request.client else "unknown"
    # Per-IP cap (60/min): a runaway client can't flood the logs. Over the cap we
    # silently drop but still return 204 — logging must not cause a client error.
    key = "client-log:" + hashlib.sha256(ip.encode()).hexdigest()[:16]
    if not auth_rate_limit.check_and_record(key, max_in_window=60, window_seconds=60):
        return None

    level = body.level if body.level in _LEVELS else "error"
    log_fn = getattr(_client_log, level, _client_log.error)
    log_fn(
        "client_event",
        extra={
            "event": "client_event",
            "client_level": level,
            "client_message": body.message,
            "client_url": body.url,
            "client_stack": body.stack,
            "client_context": body.context,
            "user_id": user.id if user else None,
            "client_ip": ip,
        },
    )
    return None
