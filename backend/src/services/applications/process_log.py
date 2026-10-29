"""The assistant's work log (owner 2026-10-29, S9): process records stay out of the user's timeline
and Activity, hidden BY TYPE, never by words.

An event is ``process`` when its type is in ``settings.APPLICATION_PROCESS_EVENT_TYPES``, or when a
``process_log`` corrects it (an assistant's own old note moved into the log). Hiding is read-side
only (M3): no event is updated or deleted, and the export keeps every row.

A ``process_log`` takes no payload, source or follow_up_on. With ``corrects_event_id`` it may move
only a plain ``note`` (no payload, no source, not itself a correction, not corrected yet): a status,
gate or kit event, an email or a slot-setting note stays on the record. Only the user's web session
may move another actor's note; an assistant moves its own. Checked in the record_event ROUTE
function, so the MCP tool inherits it (M5).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Optional

from src.core import settings
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

PROCESS_LOG = "process_log"
PROCESS_TYPES = frozenset(settings.APPLICATION_PROCESS_EVENT_TYPES)
# Only a plain note can be moved: every other type is a status, a gate, kit state or already process.
MOVABLE_TYPES = ("note",)


def logged_in(rows: Iterable[dict[str, Any]]) -> set[int]:
    """The ids a ``process_log`` among ``rows`` corrects (moved into the work log)."""
    rows = list(rows)
    undone = {r.get("corrects_event_id") for r in rows}  # S11a: a corrected process_log moves nothing
    return {
        int(r["corrects_event_id"]) for r in rows
        if r.get("event_type") == PROCESS_LOG and r.get("corrects_event_id") is not None and r["id"] not in undone
    }


async def logged_ids(db: JobDatabase, user_id: str, event_ids: list[int]) -> set[int]:
    """Of ``event_ids``, the ones a ``process_log`` of this user corrects (any page, any time)."""
    if not event_ids:
        return set()
    marks = ",".join("?" for _ in event_ids)
    cur = await db._db.execute(
        f"SELECT corrects_event_id FROM application_events WHERE user_id = ? AND event_type = ? "  # noqa: S608
        f"AND corrects_event_id IN ({marks}) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = application_events.id)",
        [user_id, PROCESS_LOG, *event_ids],
    )
    return {int(r[0]) for r in await cur.fetchall()}


def mark(events: list[dict[str, Any]], logged: set[int]) -> None:
    """Set ``process`` on each event dict, in place."""
    for e in events:
        e["process"] = e["event_type"] in PROCESS_TYPES or e["id"] in logged


async def check(
    db: JobDatabase, user: CurrentUser, application_id: int, *, payload: dict[str, Any],
    source: Optional[dict[str, Any]], follow_up_on: Optional[str], corrects_event_id: Optional[int],
) -> None:
    """Refuse a ``process_log`` the work log cannot take (see the module docstring)."""
    if payload:
        raise SpineError(422, "process_log takes no payload")
    if source is not None or follow_up_on is not None:
        raise SpineError(422, "process_log takes no source or follow_up_on")
    if corrects_event_id is None:
        return
    cur = await db._db.execute(
        "SELECT e.event_type, e.recorded_by, e.payload, e.corrects_event_id, e.source_message_id, "
        "EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id AND NOT EXISTS "
        "(SELECT 1 FROM application_events u WHERE u.corrects_event_id = c.id)) AS corrected "
        "FROM application_events e WHERE e.id = ? AND e.user_id = ? AND e.application_id = ?",
        (corrects_event_id, user.id, application_id),
    )
    target = await cur.fetchone()
    if target is None:
        raise SpineError(422, f"corrects_event_id {corrects_event_id} does not exist on this application")
    if target["event_type"] not in MOVABLE_TYPES:
        raise SpineError(422, "a process_log may move only a note into the work log")
    if (target["payload"] or "{}") != "{}" or target["source_message_id"] or target["corrects_event_id"] is not None:
        raise SpineError(422, "a note with a payload, a source or a correction stays on the record")
    if target["corrected"]:
        raise SpineError(409, "this note is already corrected or in the work log")
    actor = actor_for(user)
    if actor != "web" and target["recorded_by"] != actor:
        raise SpineError(403, "an assistant may move only its own notes into the work log")
