"""The user hides one CV version or one timeline note from the web (owner 2026-10-29, S11a).

Read-side only (M3): nothing is updated or deleted. A hide is an appended event that corrects its
target, the S9 pattern:

* a CV or letter version: a web ``note`` ("Hidden by you") correcting that version's
  ``artifact_saved`` event. A hidden version is never the current one (``spine.latest_artifact``, so
  the kit, tailored documents and the CV to approve skip it). A receipt that names it still shows it.
* a timeline note: a web ``process_log`` correcting it (S9 moves it into the work log).

Undo is a web ``note`` ("Shown again by you") correcting the hide; a corrected hide is no hide.
Only the user's web session hides or shows (session-only routes); ``record_event`` refuses to
correct a version's ``artifact_saved`` event or a hide, so no assistant can do either.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications import process_log, spine
from src.services.applications.spine import SpineError

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

HIDE_NOTE = "Hidden by you"
SHOW_NOTE = "Shown again by you"
_LIVE = "NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = h.id)"


async def hidden_artifact_ids(db: JobDatabase, user_id: str, application_id: Optional[int] = None) -> set[int]:
    """The artifact ids this user has hidden (on one application, or on all of them)."""
    sql = (
        "SELECT e.payload FROM application_events e JOIN application_events h ON h.corrects_event_id = e.id "  # noqa: S608
        "WHERE e.user_id = ? AND h.user_id = e.user_id AND e.event_type = 'artifact_saved' "
        f"AND h.event_type = 'note' AND h.recorded_by = 'web' AND {_LIVE}"
    )
    params: list[Any] = [user_id]
    if application_id is not None:
        sql += " AND e.application_id = ?"
        params.append(application_id)
    cur = await db._db.execute(sql, params)
    out: set[int] = set()
    for (raw,) in await cur.fetchall():
        aid = json.loads(raw or "{}").get("artifact_id")
        if isinstance(aid, int):
            out.add(aid)
    return out


async def _target(db: JobDatabase, user_id: str, application_id: int, event_id: int) -> Optional[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT e.id, e.event_type, e.recorded_by, t.event_type AS target_type FROM application_events e "
        "LEFT JOIN application_events t ON t.id = e.corrects_event_id "
        "WHERE e.id = ? AND e.user_id = ? AND e.application_id = ?",
        (event_id, user_id, application_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def refuse_correction(db: JobDatabase, user_id: str, application_id: int, event_id: Optional[int]) -> None:
    """``record_event`` may not correct a version's ``artifact_saved`` event or a hide: both are the
    user's own Hide / Show again buttons (403)."""
    if event_id is None:
        return
    row = await _target(db, user_id, application_id, event_id)
    if row is None:
        return  # append_event answers 422 for an id that is not on this application
    if row["event_type"] == "artifact_saved":
        raise SpineError(403, "only the user hides a document version, with its Hide button on the web")
    is_hide = row["recorded_by"] == "web" and (
        row["event_type"] == process_log.PROCESS_LOG
        or (row["event_type"] == "note" and row["target_type"] == "artifact_saved")
    )
    if is_hide:
        raise SpineError(403, "only the user shows a hidden item again, on the web")


async def _append(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, detail: str, corrects: int
) -> int:
    result = await spine.append_event(
        db, user_id=user.id, application_id=application_id, event_type=event_type, detail=detail,
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by="web", corrects_event_id=corrects,
    )
    return int(result["event_id"])


async def _saved_event(db: JobDatabase, user_id: str, application_id: int, artifact_id: int) -> int:
    cur = await db._db.execute(
        "SELECT id, payload FROM application_events WHERE user_id = ? AND application_id = ? "
        "AND event_type = 'artifact_saved' ORDER BY id",
        (user_id, application_id),
    )
    for row in await cur.fetchall():
        if json.loads(row["payload"] or "{}").get("artifact_id") == artifact_id:
            return int(row["id"])
    raise SpineError(404, "document version not found")


async def _live_hide(db: JobDatabase, user_id: str, target_id: int, event_type: str) -> Optional[int]:
    cur = await db._db.execute(
        f"SELECT h.id FROM application_events h WHERE h.user_id = ? AND h.corrects_event_id = ? "  # noqa: S608
        f"AND h.event_type = ? AND {_LIVE} ORDER BY h.id DESC LIMIT 1",
        (user_id, target_id, event_type),
    )
    row = await cur.fetchone()
    return int(row["id"]) if row else None


async def hide_artifact(db: JobDatabase, user: CurrentUser, application_id: int, artifact_id: int) -> int:
    saved = await _saved_event(db, user.id, application_id, artifact_id)
    if await _live_hide(db, user.id, saved, "note") is not None:
        raise SpineError(409, "this version is already hidden")
    return await _append(db, user, application_id, "note", HIDE_NOTE, saved)


async def show_artifact(db: JobDatabase, user: CurrentUser, application_id: int, artifact_id: int) -> int:
    saved = await _saved_event(db, user.id, application_id, artifact_id)
    hide = await _live_hide(db, user.id, saved, "note")
    if hide is None:
        raise SpineError(409, "this version is not hidden")
    return await _append(db, user, application_id, "note", SHOW_NOTE, hide)


async def hide_event(db: JobDatabase, user: CurrentUser, application_id: int, event_id: int) -> int:
    await process_log.check(
        db, user, application_id, payload={}, source=None, follow_up_on=None, corrects_event_id=event_id,
    )
    return await _append(db, user, application_id, process_log.PROCESS_LOG, "", event_id)


async def show_event(db: JobDatabase, user: CurrentUser, application_id: int, event_id: int) -> int:
    if await _target(db, user.id, application_id, event_id) is None:
        raise SpineError(404, "event not found")
    hide = await _live_hide(db, user.id, event_id, process_log.PROCESS_LOG)
    if hide is None:
        raise SpineError(409, "this note is not hidden")
    return await _append(db, user, application_id, "note", SHOW_NOTE, hide)
