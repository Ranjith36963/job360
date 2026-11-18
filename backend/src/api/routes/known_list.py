"""``GET /api/known-list/{token}`` - the user's known jobs as plain text for a hunt run (2026-11-17).

PUBLIC on purpose (tests/test_route_auth_coverage.py PUBLIC_ROUTES): an assistant's ``curl`` carries no Job360
credential. The signed token IS the credential: minted by ``whats_due`` for the signed-in assistant, it names
one user, is read-only and dies after ``KNOWN_LIST_TTL_MINUTES``. Bad tokens 404 behind the per-IP lockout
files.py uses; an expired one is 410. The path is redacted from access logs (utils/logger.py). Rules:
``services/applications/known_list.py``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.dependencies import get_request_db
from src.api.routes.files import _NO_STORE, _ip_hash
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import known_list
from src.services.auth import rate_limit as auth_rate_limit
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["hunt-kit"])
_GONE = 'This known-list link expired: call whats_due() again for a fresh one.'


def _refuse(request: Request, status: int, reason: str) -> HTTPException:
    get_audit_logger().warning(
        "known_list_refused",
        extra={"event": "known_list_refused", "status": status, "reason": reason, "result": "refused",
               "client": _ip_hash(request)},
    )
    details = {404: "not found", 410: _GONE, 429: "too many requests", 503: "known list unavailable"}
    return HTTPException(status_code=status, detail=details.get(status, reason), headers=_NO_STORE)


@router.get("/known-list/{token}")
async def serve_known_list(
    token: str, request: Request, form: Optional[str] = Query(None, max_length=8),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> Response:
    """``form=ct``: ``company ||| title ||| id ||| location`` per application; else one job link per line."""
    bad_key = f"known_list_bad:{_ip_hash(request, throttle=True)}"
    try:
        user_id = known_list.resolve(token)
    except known_list.KnownListError as exc:
        if exc.reason == "expired":
            raise _refuse(request, 410, "expired") from None
        if exc.reason == "no_secret":
            raise _refuse(request, 503, "no_secret") from None
        if auth_rate_limit.is_locked(bad_key, max_failures=settings.FILE_BAD_TOKEN_MAX_PER_MIN, window_seconds=60):
            raise _refuse(request, 429, "locked_out") from None
        auth_rate_limit.record_failure(bad_key)
        raise _refuse(request, 404, exc.reason) from None
    shape = known_list.parse_form(form)
    lines = await known_list.lines_for(db, user_id, shape)
    get_audit_logger().info(
        "known_list_served",
        extra={"event": "known_list_served", "user_id": safe_log_value(user_id), "form": shape, "lines": len(lines),
               "client": _ip_hash(request), "result": "ok"},
    )
    body = "".join(f"{line}\n" for line in lines)
    return Response(content=body, media_type="text/plain; charset=utf-8", headers=dict(_NO_STORE))
