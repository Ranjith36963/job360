"""The user marks one person as a duplicate of another, from the web, with Undo (owner 2026-10-29, S11b).

The S11a pattern (``hiding.py``) on the contacts ledger: nothing is updated or deleted (S12). A mark is
an appended ``contact_edits`` row, field ``duplicate_of``, value the kept contact's id; Undo appends
the same field with ``''``. The newest row wins, so a taken-back mark is no mark.

A live duplicate is left out of ``list_people`` (so ``known_people`` and the kit), the Needs-you
count and the frontend's people to reach, and ``check_outreach`` answers ``stop``. It stays on its
job's card (marked, with Undo) and in the data export. Only the user's web session marks or
unmarks (a session-only route); ``duplicate_of`` is never one of ``CONTACT_EDIT_FIELDS``, so
``update_contact`` cannot write it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications import contacts
from src.services.applications.spine import SpineError
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase

FIELD = "duplicate_of"
parse = contacts._duplicate_of  # the kept id from a stored value; '' (an Undo) reads as None

async def current(db: JobDatabase, user_id: str, contact_id: int) -> Optional[int]:
    cur = await db._db.execute(
        "SELECT value FROM contact_edits WHERE user_id = ? AND contact_id = ? AND field = ? "
        "ORDER BY id DESC LIMIT 1",
        (user_id, contact_id, FIELD),
    )
    row = await cur.fetchone()
    return parse(row["value"]) if row else None


async def _marked(db: JobDatabase, user_id: str) -> dict[int, int]:
    """{duplicate contact id: kept contact id} for every live mark this user has."""
    edits = await contacts._latest_edits_for_user(db, user_id)
    out: dict[int, int] = {}
    for cid, fields in edits.items():
        kept = parse(fields.get(FIELD))
        if kept is not None:
            out[int(cid)] = kept
    return out


async def mark(
    db: JobDatabase, user_id: str, contact_id: int, actor: str, duplicate_of: Optional[int]
) -> dict[str, Any]:
    """``duplicate_of`` = the person to keep; None = Undo. Web only (403 otherwise)."""
    contact = await contacts.get_owned_contact(db, user_id, contact_id)
    if contact is None:
        raise SpineError(404, "contact not found")
    if actor != contacts._WEB_ACTOR:
        raise SpineError(403, "session_required")
    async with db._db.transaction():
        await db._db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"{user_id}:duplicate",))
        marked = await _marked(db, user_id)
        now_kept = marked.get(contact_id)
        if duplicate_of is None:
            if now_kept is None:
                raise SpineError(409, "this person is not marked as a duplicate")
        else:
            if duplicate_of == contact_id:
                raise SpineError(422, "a person cannot be a duplicate of themselves")
            if await contacts.get_owned_contact(db, user_id, duplicate_of) is None:
                raise SpineError(404, "the person to keep was not found")
            if duplicate_of in marked:
                raise SpineError(422, "the person to keep is marked as a duplicate too: pick the one you keep")
            if contact_id in marked.values():
                raise SpineError(422, "another person is marked as a duplicate of this one: undo that first")
            if now_kept == duplicate_of:
                return await contacts._full_contact_view(db, user_id, contact)
        await db._db.execute(
            "INSERT INTO contact_edits (user_id, contact_id, field, value, recorded_at, recorded_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, contact_id, FIELD, "" if duplicate_of is None else str(duplicate_of),
             datetime.now(timezone.utc).isoformat(), actor),
        )
    get_audit_logger().info(
        "contact_duplicate", extra={
            "event": "contact_duplicate", "contact_id": safe_log_value(contact_id), "undo": duplicate_of is None,
        },
    )
    return await contacts._full_contact_view(db, user_id, contact)


async def list_marked(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """The People page's "Marked as duplicates" list (web only): who, and who was kept. Newest id first."""
    edits = await contacts._latest_edits_for_user(db, user_id)
    out = []
    for cid, kept in sorted((await _marked(db, user_id)).items(), reverse=True):
        dup_row = await contacts.get_owned_contact(db, user_id, cid)
        kept_row = await contacts.get_owned_contact(db, user_id, kept)
        if dup_row is None or kept_row is None:
            continue
        out.append({
            "contact_id": cid, "name": edits.get(cid, {}).get("name") or dup_row["name"],
            "duplicate_of": kept, "duplicate_of_name": edits.get(kept, {}).get("name") or kept_row["name"],
        })
    return out
