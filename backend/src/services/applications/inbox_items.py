"""Inbox items - mail the user's assistant read but could not tie to ONE application (feature 10).

Job360 reads no mail and judges nothing: the assistant sends the message facts in ``record_events``
``unmatched``; we store them once per (user, message id) so a later run skips them. The user picks
the application (or dismisses) on Needs you, or the assistant records the event for the message,
which closes the item.

Rows are insert-only. ``resolution`` / ``application_id`` / ``resolved_by`` / ``resolved_at`` are a
WRITE-ONCE slot: the only UPDATE is ``_resolve_open`` (``WHERE resolution = ''``). Every read and
write filters ``user_id`` by hand (the shim strips FK clauses).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import spine
from src.services.applications.spine import SpineError
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

STATUS_FILTERS = ("open", "all")

_SELECT = (
    "SELECT i.id, i.kind, i.message_id, i.thread_id, i.sender, i.subject, i.received_at, i.recorded_by, "
    "i.created_at, i.resolution, i.application_id, i.resolved_by, i.resolved_at, a.job_title, a.job_company, "
    "(SELECT e.id FROM application_events e WHERE e.user_id = i.user_id AND e.application_id = i.application_id "
    "AND e.source_message_id = i.message_id ORDER BY e.id LIMIT 1) AS event_id "
    "FROM inbox_items i LEFT JOIN applications a ON a.id = i.application_id AND a.user_id = i.user_id "
    "WHERE i.user_id = ? "
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["thread_id"] = out.get("thread_id") or None
    out["received_at"] = out.get("received_at") or None
    return out


async def _get(db: JobDatabase, user_id: str, key: str, value: Any) -> Optional[dict[str, Any]]:
    """One item by ``id`` or ``message_id`` (``key`` is one of those two literals, never caller text)."""
    cur = await db._db.execute(_SELECT + f"AND i.{key} = ?", (user_id, value))
    row = await cur.fetchone()
    return _serialize(dict(row)) if row else None


async def _count(db: JobDatabase, user_id: str, *, open_only: bool) -> int:
    sql = "SELECT COUNT(*) AS n FROM inbox_items WHERE user_id = ? AND (resolution = '' OR NOT ?)"
    cur = await db._db.execute(sql, (user_id, open_only))
    row = await cur.fetchone()
    return int(dict(row)["n"]) if row else 0


async def count_open(db: JobDatabase, user_id: str) -> int:
    return await _count(db, user_id, open_only=True)


async def _resolve_open(
    db: JobDatabase, user_id: str, item_id: int, resolution: str, application_id: Optional[int], actor: str
) -> bool:
    """The ONLY UPDATE on inbox_items: fill the write-once slot of a still-open item."""
    cur = await db._db.execute(
        "UPDATE inbox_items SET resolution = ?, application_id = ?, resolved_by = ?, resolved_at = ? "
        "WHERE id = ? AND user_id = ? AND resolution = '' RETURNING id",
        (resolution, application_id, actor, _now(), item_id, user_id),
    )
    return await cur.fetchone() is not None


def _log_resolved(
    user_id: str, item_id: int, resolution: str, application_id: Optional[int], via: str,
    event_id: Optional[int] = None,
) -> None:
    extra: dict[str, Any] = {
        "event": "inbox_item_resolved", "user_id": safe_log_value(user_id), "inbox_item_id": item_id,
        "resolution": resolution, "application_id": application_id, "via": via,
    }
    if event_id is not None:
        extra["event_id"] = event_id
    get_audit_logger().info("inbox_item_resolved", extra=extra)


def _result(item: Optional[dict[str, Any]], *, existed: bool, recorded: bool, app_id: Optional[int]) -> dict[str, Any]:
    return {
        "inbox_item_id": item["id"] if item else None,
        "already_existed": existed,
        "already_recorded": recorded,
        "resolution": item["resolution"] if item else "",
        "application_id": app_id,
    }


async def store_unmatched(
    db: JobDatabase, user_id: str, raw: dict[str, Any], actor: str, *, dismissed: bool
) -> dict[str, Any]:
    """Store one unmatched message (idempotent by message id). SpineError on a bad source / cap."""
    src = spine.validate_source(raw)
    assert src is not None
    message_id = src["message_id"]

    cur = await db._db.execute(
        "SELECT application_id FROM application_events WHERE user_id = ? AND source_message_id = ? "
        "ORDER BY id LIMIT 1", (user_id, message_id),
    )
    ev = await cur.fetchone()
    if ev is not None:
        item = await _get(db, user_id, "message_id", message_id)
        return _result(item, existed=True, recorded=True, app_id=int(dict(ev)["application_id"]))

    item = await _get(db, user_id, "message_id", message_id)
    if item is not None:
        if dismissed and item["resolution"] == "":
            if await _resolve_open(db, user_id, item["id"], "dismissed", None, actor):
                _log_resolved(user_id, item["id"], "dismissed", None, "record_events")
            item = await _get(db, user_id, "message_id", message_id)
        assert item is not None
        return _result(item, existed=True, recorded=False, app_id=item["application_id"])

    cap = settings.INBOX_ITEMS_MAX_PER_USER  # every stored row, dismissed too: the table is never pruned
    if await _count(db, user_id, open_only=False) >= cap:
        raise SpineError(429, f"too many stored inbox items (INBOX_ITEMS_MAX_PER_USER = {cap})")
    if not dismissed:
        cap = settings.INBOX_ITEMS_MAX_OPEN_PER_USER
        if await _count(db, user_id, open_only=True) >= cap:
            raise SpineError(
                429,
                f"too many open inbox items (INBOX_ITEMS_MAX_OPEN_PER_USER = {cap}): "
                "the user resolves some on Needs you first",
            )
    now = _now()
    cur = await db._db.execute(
        "INSERT INTO inbox_items (user_id, kind, message_id, thread_id, sender, subject, received_at, "
        "recorded_by, created_at, resolution, resolved_by, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (user_id, message_id) DO NOTHING RETURNING id",
        (
            user_id, src["kind"], message_id, src["thread_id"], src["sender"], src["subject"],
            src["received_at"], actor, now, "dismissed" if dismissed else "",
            actor if dismissed else None, now if dismissed else None,
        ),
    )
    row = await cur.fetchone()
    item = await _get(db, user_id, "message_id", message_id)
    assert item is not None
    if row is None:  # a parallel call stored it first
        return _result(item, existed=True, recorded=False, app_id=item["application_id"])
    get_audit_logger().info(
        "inbox_item_stored",
        extra={
            "event": "inbox_item_stored", "user_id": safe_log_value(user_id),
            "inbox_item_id": item["id"], "dismissed": dismissed,
        },
    )
    return _result(item, existed=False, recorded=False, app_id=None)


async def resolve(
    db: JobDatabase, user_id: str, item_id: int, *, application_id: Optional[int], dismiss: bool, actor: str
) -> dict[str, Any]:
    """The user's web pick: match to one of their applications, or dismiss. Writes NO event."""
    if application_id is not None and dismiss:
        raise SpineError(422, "give application_id or dismiss=true, not both")
    if application_id is None and not dismiss:
        raise SpineError(422, "give application_id or dismiss=true")
    if await _get(db, user_id, "id", item_id) is None:
        raise SpineError(404, "inbox item not found")
    if application_id is not None and await spine.get_owned_application(db, user_id, application_id) is None:
        raise SpineError(404, "application not found")
    resolution = "matched" if application_id is not None else "dismissed"
    if not await _resolve_open(db, user_id, item_id, resolution, application_id, actor):
        raise SpineError(409, "this email was already resolved")
    _log_resolved(user_id, item_id, resolution, application_id, "web")
    item = await _get(db, user_id, "id", item_id)
    assert item is not None
    return item


async def resolve_by_event(
    db: JobDatabase, user_id: str, message_id: str, application_id: int, event_id: int, actor: str
) -> None:
    """An event was recorded with this message id: close the user's open item for it, if any."""
    cur = await db._db.execute(
        "SELECT id FROM inbox_items WHERE user_id = ? AND message_id = ? AND resolution = ''",
        (user_id, message_id),
    )
    row = await cur.fetchone()
    if row is None:
        return
    item_id = int(dict(row)["id"])
    if await _resolve_open(db, user_id, item_id, "matched", application_id, actor):
        _log_resolved(user_id, item_id, "matched", application_id, "event", event_id)


async def list_items(db: JobDatabase, user_id: str, status: str, limit: int) -> list[dict[str, Any]]:
    if status not in STATUS_FILTERS:
        raise SpineError(422, "status must be open or all")
    where = "AND i.resolution = '' " if status == "open" else ""
    cur = await db._db.execute(_SELECT + where + "ORDER BY i.id DESC LIMIT ?", (user_id, max(1, int(limit))))
    return [_serialize(dict(r)) for r in await cur.fetchall()]


async def for_whats_new(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """Every open item plus the ones resolved in the last INBOX_ITEMS_RECENT_DAYS (open first)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.INBOX_ITEMS_RECENT_DAYS)).isoformat()
    cur = await db._db.execute(
        _SELECT + "AND (i.resolution = '' OR i.resolved_at >= ?) "
        "ORDER BY CASE WHEN i.resolution = '' THEN 0 ELSE 1 END, i.id DESC LIMIT ?",
        (user_id, cutoff, settings.INBOX_ITEMS_WHATS_NEW_MAX),
    )
    return [_serialize(dict(r)) for r in await cur.fetchall()]
