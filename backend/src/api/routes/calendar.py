"""Private calendar feed (migration 0058): interviews and follow-ups as an .ics feed.

``GET /api/calendar/{token}.ics`` has NO login: a calendar app polls it, and the URL
IS the credential. So it copies the ``files.py`` rules exactly:

* a token is 32 random bytes; only its SHA-256 is stored (``calendar_feeds``);
* hash, look up, then ``hmac.compare_digest`` the stored hash (constant time);
* an unknown token is 404 and counts against a per-IP lockout (consulted only for
  tokens that do not resolve, so junk can never lock out a valid feed);
* a per-OWNER rate limit caps fetches (``CALENDAR_FEED_MAX_PER_HOUR``);
* ``Cache-Control: no-store``, ``Referrer-Policy: no-referrer``, ``X-Robots-Tag``;
* the token, its hash and every title are NEVER logged (the access log and Sentry
  rewrite the path to ``/api/calendar/[redacted]``).

The management routes are session-only (``require_session_user``) like the token
routes, and there is deliberately NO MCP tool: an agent must not mint a credential.
One feed per user; regenerating deletes the row and inserts a new one.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.api.routes.files import _NO_STORE, _ip_hash
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import calendar_ics, kit, spine
from src.services.auth import rate_limit as auth_rate_limit
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["calendar"])

_ICS = "text/calendar; charset=utf-8"


class CalendarFeedStatus(BaseModel):
    exists: bool
    created_at: Optional[str] = None


class CalendarFeedCreated(BaseModel):
    url: str


def _refuse(request: Request, status: int, reason: str, user_id: Optional[str] = None) -> HTTPException:
    get_audit_logger().warning(
        "calendar_feed_refused",
        extra={
            "event": "calendar_feed_refused", "status": status, "reason": reason, "result": "refused",
            "user_id": safe_log_value(user_id) if user_id else None, "client": _ip_hash(request),
        },
    )
    details = {404: "not found", 429: "too many requests"}
    return HTTPException(status_code=status, detail=details.get(status, reason), headers=_NO_STORE)


@router.get("/calendar-feed", response_model=CalendarFeedStatus)
async def feed_status(
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - auth BEFORE the pooled db handle (FC-008)
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> dict[str, Any]:
    cur = await db._db.execute("SELECT created_at FROM calendar_feeds WHERE user_id = ?", (user.id,))
    row = await cur.fetchone()
    return {"exists": row is not None, "created_at": row["created_at"] if row else None}


@router.post("/calendar-feed", response_model=CalendarFeedCreated, status_code=201)
async def create_or_rotate_feed(
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - auth BEFORE the pooled db handle (FC-008)
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> dict[str, str]:
    """Create the feed, or rotate it (the old URL dies). The plain URL is in this
    response only; the database keeps the hash."""
    token = secrets.token_urlsafe(32)
    await db._db.execute("DELETE FROM calendar_feeds WHERE user_id = ?", (user.id,))
    await db._db.execute(
        "INSERT INTO calendar_feeds (user_id, token_hash, created_at) VALUES (?, ?, ?)",
        (user.id, kit.hash_token(token), datetime.now(timezone.utc).isoformat()),
    )
    await db._db.commit()
    get_audit_logger().info(
        "calendar_feed_created", extra={"event": "calendar_feed_created", "user_id": safe_log_value(user.id)}
    )
    return {"url": f"{settings.SITE_BASE_URL}/api/calendar/{token}.ics"}


@router.delete("/calendar-feed", status_code=204)
async def revoke_feed(
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - auth BEFORE the pooled db handle (FC-008)
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> Response:
    await db._db.execute("DELETE FROM calendar_feeds WHERE user_id = ?", (user.id,))
    await db._db.commit()
    get_audit_logger().info(
        "calendar_feed_revoked", extra={"event": "calendar_feed_revoked", "user_id": safe_log_value(user.id)}
    )
    return Response(status_code=204)


@router.get("/calendar/{token}.ics")
async def serve_feed(
    token: str, request: Request, db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> Response:
    """Serve the feed. See the module docstring for the rules."""
    ip = _ip_hash(request)
    bad_key = f"calendar_bad:{ip}"

    def _bad_token(reason: str) -> HTTPException:
        # Consulted only for a token that does NOT resolve (see files.py): behind the
        # Next rewrite callers can share one address, so a lock checked BEFORE the
        # lookup would let one junk client 429 every valid feed.
        if auth_rate_limit.is_locked(bad_key, max_failures=settings.FILE_BAD_TOKEN_MAX_PER_MIN, window_seconds=60):
            return _refuse(request, 429, "locked_out")
        auth_rate_limit.record_failure(bad_key)
        return _refuse(request, 404, reason)

    if not kit.TOKEN_RE.match(token):
        raise _bad_token("bad_shape")
    computed = kit.hash_token(token)
    cur = await db._db.execute("SELECT user_id, token_hash FROM calendar_feeds WHERE token_hash = ?", (computed,))
    found = await cur.fetchone()
    if found is None or not hmac.compare_digest(str(found["token_hash"]), computed):
        raise _bad_token("unknown_token")
    user_id = str(found["user_id"])
    # Keyed by the feed's OWNER, not the address: junk never spends it.
    if not auth_rate_limit.check_and_record(
        f"calendar:{user_id}", max_in_window=settings.CALENDAR_FEED_MAX_PER_HOUR, window_seconds=3600
    ):
        raise _refuse(request, 429, "rate_limited", user_id)
    events = await calendar_ics.collect_events(db, user_id, datetime.now(timezone.utc))
    get_audit_logger().info(
        "calendar_feed_served",
        extra={
            "event": "calendar_feed_served", "user_id": safe_log_value(user_id), "events": len(events),
            "client": ip, "status": 200, "result": "ok",
        },
    )
    return Response(content=calendar_ics.render_calendar(events), media_type=_ICS, headers=dict(_NO_STORE))


@router.get("/applications/{application_id}/calendar.ics", dependencies=AUTH_FIRST)
async def application_ics(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> Response:
    """One application's interview and follow-up, as a download (Add to calendar)."""
    if await spine.get_owned_application(db, user.id, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    events = await calendar_ics.collect_events(db, user.id, datetime.now(timezone.utc), application_id)
    if not events:
        raise HTTPException(status_code=404, detail="nothing scheduled on this application")
    return Response(
        content=calendar_ics.render_calendar(events),
        media_type=_ICS,
        headers={"Content-Disposition": f'attachment; filename="job360-application-{application_id}.ics"', **_NO_STORE},
    )
