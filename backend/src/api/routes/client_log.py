"""Frontend → server log bridge (full-lifecycle logging piece D).

A tiny endpoint the browser POSTs to so UI errors/events land in data/logs/
instead of vanishing when the user closes the tab. Rate-limited per client IP so
a runaway browser loop can't flood the logs; optional-user so errors that happen
before login are still captured. Logging must never break the client, so even a
rate-limited or malformed-ish call returns 204.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, optional_user
from src.api.client_ip import client_ip, throttle_ip
from src.services.auth import rate_limit as auth_rate_limit
from src.utils.logger import get_logger

router = APIRouter(tags=["client-log"])

_client_log = get_logger("client")  # "job360.client" → main job360 handlers (data/logs/)
_LEVELS = {"error", "warning", "info"}


_URL_IN_TEXT = re.compile(r"(https?://[^\s?#]+)[?#]\S*")


def _strip_url(value: Optional[str]) -> Optional[str]:
    """Drop the query string and fragment from a URL, and from any URL inside free text.

    A page URL can carry a live credential (``/auth/magic?token=...``); it must not
    reach the log file or audit_log. Text without a URL passes through unchanged."""
    if not value:
        return value
    return _URL_IN_TEXT.sub(r"\1", value)


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
    ip = client_ip(request)  # the full address, as the log lines record it
    # Per-IP cap (60/min): a runaway client can't flood the logs. Over the cap we
    # silently drop but still return 204 — logging must not cause a client error.
    # The key counts an IPv6 caller per /64 (throttle_ip).
    key = "client-log:" + hashlib.sha256(throttle_ip(ip).encode()).hexdigest()[:16]
    if not auth_rate_limit.check_and_record(key, max_in_window=60, window_seconds=60):
        return None

    # Credentials ride in page URLs (?token=...): strip query and fragment from every
    # URL-bearing field before anything is written.
    page_url = re.split(r"[?#]", body.url, maxsplit=1)[0] if body.url else body.url  # relative URLs too
    message = _strip_url(body.message) or ""
    stack = _strip_url(body.stack)
    context = _strip_url(body.context)

    level = body.level if body.level in _LEVELS else "error"

    # GIVE BROWSER ERRORS A DURABLE, QUERYABLE HOME.
    #
    # Until 2026-08-03 this endpoint was the ONLY working browser-error channel
    # in production, and it dead-ended: records went to rotating files under
    # data/logs/ on Railway's EPHEMERAL filesystem, wiped by every deploy, read
    # by no loop and reachable by no human after the fact. Sentry deliberately
    # ignores this logger (observability.py) because client ERRORs once flooded
    # the backend error stream — including the synthetic monitor's own failure
    # posts — so forwarding there would re-create a solved problem.
    #
    # audit_log is the right home: durable, SQL-queryable, already retention-
    # managed, and product_assertions now reads it. Only ERRORs are recorded —
    # info/warn stay in the file stream — so the flood that motivated the Sentry
    # exclusion cannot happen here either, and the per-IP 60/min cap above
    # bounds it regardless.
    # Anonymous posts stay in the file stream only: audit_log is durable and any
    # unauthenticated caller could otherwise write free text into it.
    if level == "error" and user is not None:
        try:
            from src.utils.logger import get_audit_logger, mask_ip, safe_log_value

            get_audit_logger().error(
                "client_error",
                # CodeQL py/log-injection — `client_message`/`client_url` are
                # free text the browser sends; length-capping alone doesn't
                # strip control chars, so both go through the shared
                # sanitizer (which also caps length).
                extra={
                    "event": "client_error",
                    "status": "error",
                    "user_id": user.id,
                    "client_ip": mask_ip(ip),  # audit_log is never pruned: no raw IPs
                    "client_message": safe_log_value(message, max_len=500),
                    "client_url": safe_log_value(page_url or "", max_len=300),
                },
            )
        except Exception:  # noqa: BLE001 — logging must never break the client
            pass

    log_fn = getattr(_client_log, level, _client_log.error)
    log_fn(
        "client_event",
        extra={
            "event": "client_event",
            "client_level": level,
            "client_message": message,
            "client_url": page_url,
            "client_stack": stack,
            "client_context": context,
            "user_id": user.id if user else None,
            "client_ip": ip,
        },
    )
    return None
