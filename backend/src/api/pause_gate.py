"""Pause stops the assistant on the REST door too (owner decision 2026-10-26: "Pause stops everything").

The MCP door refuses work while ``assistant_settings.paused_until`` holds (``mcp_server.pause_blocks``).
An assistant can also call ``/api/...`` directly with its bearer (a ``j360_`` token or an OAuth access
token), so this app-level dependency applies the same table, ``mcp_server.PAUSE_REFUSED_ROUTES``, there.

The signal is the one ``auth_deps.resolve_current_user`` uses to pick a credential: an
``Authorization: Bearer`` header means the token path ONLY (auth_via "token" / "oauth"); without one the
caller is the user's web session, which is never paused (fail open). A refused call answers 423 with
``mcp_server.paused_refusal`` as its detail and the route never runs, so nothing is written.

Cost: a route outside the table, or any call without a bearer, pays one dict lookup. A gated bearer call
resolves the bearer (the route's own ``require_user`` resolves it again) and reads the profile once.
MCP tools call route functions directly, so this never runs twice for an MCP call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from src.api.auth_deps import _bearer_from_header, _current_user_from_bearer
from src.api.mcp_server import PAUSE_ALLOWED_EVENT_TYPES, PAUSE_REFUSED_ROUTES, paused_refusal

PAUSED_STATUS_CODE = 423  # Locked: the account is fine, the assistant is paused until the user resumes


def route_key(method: str, endpoint: Any) -> tuple[str, str]:
    """``("POST", "bring.bring_job")`` for an endpoint in ``src.api.routes.bring``."""
    module = str(getattr(endpoint, "__module__", "")).rsplit(".", 1)[-1]
    return ("GET" if method == "HEAD" else method, f"{module}.{getattr(endpoint, '__name__', '')}")


def _pause_state(user_id: str) -> tuple[bool, str]:
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    profile = load_profile(user_id) or UserProfile()
    return rules.pause_state(profile.assistant_settings, datetime.now(timezone.utc))


async def _event_type(request: Request) -> Optional[str]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an unreadable body is not an allowed wind-down type
        return None
    value = body.get("event_type") if isinstance(body, dict) else None
    return value if isinstance(value, str) else None


async def rest_pause_gate(request: Request) -> None:
    """Refuse a token call to a work route while the user is paused; let everything else through."""
    route = request.scope.get("route")
    key = route_key(request.method, getattr(route, "endpoint", None))
    if key not in PAUSE_REFUSED_ROUTES:
        return
    token = _bearer_from_header(request.headers.get("authorization"))
    if token is None:
        return  # the user's own web session: never paused
    user = await _current_user_from_bearer(request, token)  # a bad bearer is 401 here, as in require_user
    paused, until = await run_in_threadpool(_pause_state, user.id)
    if not paused:
        return
    if PAUSE_REFUSED_ROUTES[key] == "record_event" and await _event_type(request) in PAUSE_ALLOWED_EVENT_TYPES:
        return  # winding down (note, withdrawn, applied, ...) is still recorded
    raise HTTPException(status_code=PAUSED_STATUS_CODE, detail=paused_refusal(until))
