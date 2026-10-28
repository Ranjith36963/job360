"""What changed - one cheap read so an open web page can stay live (Needs-you redesign, PR 5).

``GET /api/changes`` returns a ``cursor`` built ONLY from rows that already exist: no change
table, no migration, no hook in the DB doors. The page polls it; when the cursor moves it
refetches, and the per-area ``areas`` markers say WHICH reads to repeat. ``If-None-Match``
carrying the current cursor answers 304 with no body (the ETag IS the cursor).

The parts, all scoped by ``user.id`` (rules #12/#25):

* newest ``audit_log`` row with ``event = 'mcp_tool_call'``: every assistant tool call, also the
  ones that change a row in place with no timestamp. Its time is ``assistant_last_active_at``.
  ONLY ``mcp_tool_call`` counts: web reads write audit rows too (``ready_to_send_read``), so
  counting them would make the page's own refetch move the cursor, forever.
* ``MAX(id)`` of ``application_events`` (every decision, web or assistant).
* ``application_asks``: ``MAX(id)``, ``MAX(answered_at)``, ``MAX(withdrawn_at)``, ``MAX(confirmed_at)``.
* ``user_profiles.updated_at`` and ``MAX(id)`` of ``profile_edits``: pause and the other
  settings are ``profile_edits`` rows (``edits.record_edits``), which do not touch ``updated_at``.
* ``assistant_setting_requests``: waiting count + ``MAX(id)`` ("Waiting for your OK").
* ``inbox_items``: ``MAX(id)``, ``MAX(resolved_at)`` (unmatched mail).

Known miss: a row saved on another device that only updates a row in place with no timestamp
(``answers/save``) is not seen here; the page refetches everything on focus for that.

``GET /api/needs-you-count`` (below) is the menu badge's exact total, so the badge matches the
Needs-you page on every route (``services/applications/needs_you_count.py``).

A WEB read only (``require_session_user``): no MCP twin (agents have ``whats_new``), so the gate
parity table has no row for it. It writes nothing: no audit line, so polling never moves it.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user
from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase
from src.services.applications.needs_you_count import needs_you_count

router = APIRouter(tags=["changes"])

MCP_TOOL_CALL = "mcp_tool_call"

# Uses idx_audit_log_user_time (user_id, occurred_at DESC), migration 0025. ``event || ''``
# keeps the planner off idx_audit_log_event_time, which walks EVERY user's tool calls when
# this user has none; the user index bounds the scan to this user's own rows.
_ASSISTANT_SQL = (
    "SELECT id, occurred_at FROM audit_log WHERE user_id = ? AND event || '' = ? "
    "ORDER BY occurred_at DESC, id DESC LIMIT 1"
)

# One round trip; each derived table reads one user's rows through its user_id index.
_STATE_SQL = (
    "SELECT e.events_max, k.asks_max, k.asks_answered, k.asks_withdrawn, k.asks_confirmed, "
    "p.profile_updated, pe.edits_max, s.requests_waiting, s.requests_max, i.inbox_max, i.inbox_resolved "
    "FROM (SELECT MAX(id) AS events_max FROM application_events WHERE user_id = ?) e "
    "CROSS JOIN (SELECT MAX(id) AS asks_max, MAX(answered_at) AS asks_answered, "
    "MAX(withdrawn_at) AS asks_withdrawn, MAX(confirmed_at) AS asks_confirmed "
    "FROM application_asks WHERE user_id = ?) k "
    "CROSS JOIN (SELECT MAX(updated_at) AS profile_updated FROM user_profiles WHERE user_id = ?) p "
    "CROSS JOIN (SELECT MAX(id) AS edits_max FROM profile_edits WHERE user_id = ?) pe "
    "CROSS JOIN (SELECT SUM(CASE WHEN decision IS NULL THEN 1 ELSE 0 END) AS requests_waiting, "
    "MAX(id) AS requests_max "
    "FROM assistant_setting_requests WHERE user_id = ?) s "
    "CROSS JOIN (SELECT MAX(id) AS inbox_max, MAX(resolved_at) AS inbox_resolved "
    "FROM inbox_items WHERE user_id = ?) i"
)

# Which parts feed which area of the page. ``assistant`` alone moving means the assistant
# touched something with no timestamp of its own: the page refetches every area.
AREAS: dict[str, tuple[str, ...]] = {
    "assistant": ("assistant_id",),
    "asks": ("asks_max", "asks_answered", "asks_withdrawn", "asks_confirmed"),
    "ready": ("events_max", "profile_updated", "edits_max"),
    "applications": ("events_max",),
    "settings": ("profile_updated", "edits_max", "requests_waiting", "requests_max"),
    "other": ("events_max", "inbox_max", "inbox_resolved"),
}


class ChangeAreas(BaseModel):
    """One short marker per area of the page; a marker that moved = refetch that area."""

    assistant: str
    asks: str
    ready: str
    applications: str
    settings: str
    other: str


class ChangesOut(BaseModel):
    cursor: str
    assistant_last_active_at: Optional[str] = None
    areas: ChangeAreas


def _digest(user_id: str, label: str, values: list[Any], size: int) -> str:
    # The user id salts every marker: two accounts in one browser never share a cursor.
    raw = "\x1f".join([user_id, label, *("" if v is None else str(v) for v in values)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def _etag_matches(header: Optional[str], cursor: str) -> bool:
    """RFC 9110 weak comparison of ``If-None-Match`` against our one tag (``*`` matches)."""
    if not header:
        return False
    for tag in header.split(","):
        tag = tag.strip()
        if tag == "*":
            return True
        if tag.startswith("W/"):
            tag = tag[2:]
        if tag.strip('"') == cursor:
            return True
    return False


async def read_parts(db: JobDatabase, user_id: str) -> dict[str, Any]:
    """Every part of the cursor for ``user_id``: two indexed reads, nothing written."""
    cur = await db._db.execute(_ASSISTANT_SQL, (user_id, MCP_TOOL_CALL))
    row = await cur.fetchone()
    parts: dict[str, Any] = {
        "assistant_id": row["id"] if row else None,
        "assistant_at": str(row["occurred_at"]) if row and row["occurred_at"] is not None else None,
    }
    cur = await db._db.execute(_STATE_SQL, (user_id,) * 6)
    state = await cur.fetchone()
    for name in ("events_max", "asks_max", "asks_answered", "asks_withdrawn", "asks_confirmed",
                 "profile_updated", "edits_max", "requests_waiting", "requests_max", "inbox_max",
                 "inbox_resolved"):
        parts[name] = state[name] if state else None
    parts["requests_waiting"] = int(parts["requests_waiting"] or 0)
    return parts


def build(user_id: str, parts: dict[str, Any]) -> ChangesOut:
    names = sorted({n for fed in AREAS.values() for n in fed})
    cursor = _digest(user_id, "cursor", [f"{n}={parts.get(n)}" for n in names], 24)
    areas = {area: _digest(user_id, area, [parts.get(n) for n in fed], 12) for area, fed in AREAS.items()}
    return ChangesOut(cursor=cursor, assistant_last_active_at=parts.get("assistant_at"), areas=ChangeAreas(**areas))


@router.get(
    "/changes",
    response_model=ChangesOut,
    dependencies=AUTH_FIRST,
    responses={304: {"description": "Nothing changed since the cursor sent in If-None-Match"}},
)
async def changes(
    response: Response,
    if_none_match: Optional[str] = Header(default=None),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 - FastAPI DI idiom
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the web page's own poll (session only)
) -> Any:
    """What changed for the signed-in user: a cursor (also the ETag) and one marker per page area.
    Send the last cursor as ``If-None-Match``; an unchanged cursor answers 304 with no body."""
    out = build(user.id, await read_parts(db, user.id))
    headers = {"ETag": f'"{out.cursor}"', "Cache-Control": "no-store"}
    if _etag_matches(if_none_match, out.cursor):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return out


class NeedsYouCountOut(BaseModel):
    n: int  # the Needs-you page's total: jobs + other
    jobs: int  # distinct jobs with anything open (an ask, a ready card, a CV to approve, a person to reach)
    other: int  # asks with no job, waiting setting requests, outcome suggestions, unmatched mail, new connections


@router.get("/needs-you-count", response_model=NeedsYouCountOut, dependencies=AUTH_FIRST)
async def needs_you_count_route(
    response: Response,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 - FastAPI DI idiom
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the web badge's own read (session only)
) -> Any:
    """The number "N things need you" shows, for the menu badge on every page. Read-only: no audit
    line is written, so a navigation never moves ``/api/changes``."""
    response.headers["Cache-Control"] = "no-store"
    return NeedsYouCountOut(**await needs_you_count(db, user.id))
