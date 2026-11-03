"""The AI closes an application whose posting is gone (owner 2026-11-02, reconfirmed 2026-11-03).

The "Posting closed" item on Needs you used to ask the user "skip or keep". Now a ``blocked``
{reason: posting_closed} that carries ``evidence`` read off the job's OWN page (``page_says_closed``,
``http_404``, ``http_410``; ``blocked.CLOSED_EVIDENCE``) closes the application by itself, inside the
block's own transaction:

* ``withdrawn`` (the existing closed status), detail "posting closed", payload ``{closed_by:
  "posting_closed", blocked_event_id, evidence, page_host}``, recorded by the assistant that brought the
  proof: the typed, auditable record of the AI's close;
* ``unblocked`` {resolution: skipped} on the block, which withdraws its Needs-you ask.

Guardrails, each a reason to leave the application to the user exactly as before:
* no ``evidence`` (a block can carry no email ``source``: an email alone never closes anything);
* ``page_host`` is not the site of the application's own ad link (``applications.job_url``);
* the application is not ``considering``, has a receipt or a surviving ``applied``, or has a started
  submit with no outcome (``kit.submit_unconfirmed``: it may have gone out);
* the owner said Send on it (any ``submit_approved``), or an ask to him is open on it (other than the one
  this block just opened): it stays his to drop.

``undo`` is the user's one web action (``POST /applications/{id}/auto/undo``): a ``note`` whose
``corrects_event_id`` is that ``withdrawn``, so the status replays back (append-only, M3). Only a
``withdrawn`` this module wrote is undone (``_our_close``); a caller's look-alike payload is not.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import blocked, kit, spine
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

CLOSED_BY = "posting_closed"
UNDO_DETAIL = "Reopened: you said the posting is still open"
# Slice 2 (auto_applied.py): Job360's own `applied` from an employer's confirmation email.
CONFIRMED_BY_EMAIL = "confirmed_by_email"
UNDO_SENT_DETAIL = "Undone: you said it was not sent"
NOTHING_TO_UNDO = "nothing Job360 closed by itself waits to be undone on this application"


def _log(user_id: str, application_id: int, name: str, result: str, **extra: Any) -> None:
    get_audit_logger().info(name, extra={
        "event": name, "user_id": safe_log_value(user_id), "application_id": int(application_id),
        "result": safe_log_value(result, max_len=40), **{k: safe_log_value(v, max_len=40) for k, v in extra.items()},
    })


async def _why_not(
    db: JobDatabase, user_id: str, application_id: int, stored: dict[str, Any], asked_before: bool,
) -> str:
    """The guardrail that keeps this application open, or "" when the AI may close it."""
    if stored.get("reason") != "posting_closed" or stored.get("evidence") not in blocked.CLOSED_EVIDENCE:
        return "no_proof"
    row = await spine.get_owned_application(db, user_id, application_id)
    if row is None or row["status"] != "considering":
        return "not_considering"
    ad_site = blocked._site(row.get("job_url"))
    if not ad_site or blocked._site(stored.get("page_host")) != ad_site:
        return "not_the_ad_page"
    if await kit.already_applied(db, user_id, application_id):
        return "applied"
    if await kit.submit_unconfirmed(db, user_id, application_id):
        return "submit_started"
    # The owner said Send on this job (a `submit_approved`, web or chat): it stays his to drop, as before.
    cur = await db._db.execute(
        "SELECT 1 FROM application_events WHERE user_id = ? AND application_id = ? "
        "AND event_type = 'submit_approved' LIMIT 1",
        (user_id, application_id),
    )
    if await cur.fetchone() is not None:
        return "owner_approved"
    if asked_before:
        return "open_ask"
    return ""


async def has_open_ask(db: JobDatabase, user_id: str, application_id: int) -> bool:
    """An ask to the owner is open on this job. Read BEFORE the block opens its own ask: the one-ask-per-job
    merge folds an older ask into the block's, so it cannot be told apart afterwards."""
    cur = await db._db.execute(
        "SELECT 1 FROM application_asks WHERE user_id = ? AND application_id = ? AND answered_at IS NULL "
        "AND withdrawn_at IS NULL LIMIT 1",
        (user_id, application_id),
    )
    return await cur.fetchone() is not None


async def close_if_proven(
    db: JobDatabase, user: CurrentUser, application_id: int, block_event_id: int, stored: dict[str, Any],
    *, asked_before: bool,
) -> Optional[str]:
    """Inside ``blocked.record``'s transaction (the row is locked): close the application when every
    guardrail passes. ``asked_before``: an ask to the owner was open before this block (``has_open_ask``).
    Returns the warning the assistant reads, or None when the user still decides."""
    why = await _why_not(db, user.id, application_id, stored, asked_before)
    if why:
        _log(user.id, application_id, "posting_auto_close_skipped", why)
        return None
    actor = actor_for(user)
    now = datetime.now(timezone.utc).isoformat()
    await spine.append_event(
        db, user_id=user.id, application_id=application_id, event_type="withdrawn", detail="posting closed",
        payload={"closed_by": CLOSED_BY, "blocked_event_id": int(block_event_id), "evidence": stored["evidence"],
                 "page_host": stored.get("page_host") or ""},
        occurred_at=now, recorded_by=actor,
    )
    await blocked.record(
        db, user, application_id, "unblocked",
        {"resolution": "skipped", "note": "the posting is closed; your assistant closed the application",
         "assistant": actor},
        occurred_at=now,
    )
    _log(user.id, application_id, "posting_auto_closed", "ok", evidence=stored["evidence"])
    return (f"Job360 closed this application (withdrawn, posting closed: {stored['evidence']} on "
            f"{stored.get('page_host')}). The user can reopen it on the website; move on.")


async def _newest_status_event(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    types = tuple(settings.APPLICATION_STATUS_EVENT_TYPES)
    cur = await db._db.execute(
        "SELECT e.id, e.event_type, e.payload, e.recorded_by FROM application_events e "  # noqa: S608
        f"WHERE e.user_id = ? AND e.application_id = ? AND e.event_type IN ({','.join('?' for _ in types)}) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id "
        "AND c.user_id = e.user_id) ORDER BY e.id DESC LIMIT 1",
        (user_id, application_id, *types),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


def _payload(raw: Any) -> dict[str, Any]:
    try:
        p = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return p if isinstance(p, dict) else {}


async def _our_close(db: JobDatabase, user_id: str, application_id: int, ev: dict[str, Any]) -> bool:
    """``ev`` is a ``withdrawn`` this module wrote: ``closed_by`` alone is caller-writable text, so its
    ``blocked_event_id`` must name a real ``blocked`` posting_closed WITH evidence on this application, and
    the ``withdrawn`` must come right after that block (``close_if_proven`` writes it in the same transaction)."""
    p = _payload(ev["payload"])
    block_id = p.get("blocked_event_id")
    if ev["event_type"] != "withdrawn" or p.get("closed_by") != CLOSED_BY or not isinstance(block_id, int):
        return False
    cur = await db._db.execute(
        "SELECT payload FROM application_events WHERE id = ? AND user_id = ? AND application_id = ? "
        "AND event_type = 'blocked'",
        (block_id, user_id, application_id),
    )
    row = await cur.fetchone()
    block = _payload(row["payload"]) if row else {}
    if block.get("reason") != "posting_closed" or block.get("evidence") not in blocked.CLOSED_EVIDENCE:
        return False
    cur = await db._db.execute(
        "SELECT 1 FROM application_events WHERE user_id = ? AND application_id = ? AND id > ? AND id < ? LIMIT 1",
        (user_id, application_id, block_id, int(ev["id"])),
    )
    return await cur.fetchone() is None


async def undo(db: JobDatabase, user: CurrentUser, application_id: int) -> dict[str, Any]:
    """The user's web Undo: correct the newest surviving status event when it is Job360's own close.
    409 when it is not (the user, or a later event, moved the application since)."""
    async with db._db.transaction():
        cur = await db._db.execute(
            "SELECT id FROM applications WHERE id = ? AND user_id = ? FOR UPDATE", (application_id, user.id)
        )
        if await cur.fetchone() is None:
            raise SpineError(404, "application not found")
        ev = await _newest_status_event(db, user.id, application_id)
        undoes = await _undoable(db, user.id, application_id, ev)
        if ev is None or not undoes:
            _log(user.id, application_id, "auto_undo_refused", "nothing_to_undo")
            raise SpineError(409, NOTHING_TO_UNDO)
        now = datetime.now(timezone.utc).isoformat()
        result = await spine.append_event(
            db, user_id=user.id, application_id=application_id, event_type="note",
            detail=UNDO_DETAIL if undoes == CLOSED_BY else UNDO_SENT_DETAIL, payload={"undoes": undoes},
            occurred_at=now, recorded_by=actor_for(user), corrects_event_id=int(ev["id"]),
        )
        if undoes == CONFIRMED_BY_EMAIL:
            # One click: the user's "It was not sent" too, so neither the confirmation nor the started submit
            # comes back to ask again (kit.pending_confirmations, kit.submit_unconfirmed).
            await spine.append_event(
                db, user_id=user.id, application_id=application_id, event_type="submit_resolved",
                payload={"outcome": "not_sent", "where": "web", "by": actor_for(user)},
                occurred_at=now, recorded_by=actor_for(user),
            )
    _log(user.id, application_id, "auto_undone", "ok", undoes=undoes)
    return result


async def _undoable(db: JobDatabase, user_id: str, application_id: int, ev: Optional[dict[str, Any]]) -> str:
    """What the newest surviving status event is, when Job360 wrote it by itself: "" otherwise. An `applied`
    counts only when recorded by ``system`` (no caller's actor can be that); a close only via ``_our_close``."""
    if ev is None:
        return ""
    p = _payload(ev["payload"])
    if await _our_close(db, user_id, application_id, ev):
        return CLOSED_BY
    if ev["event_type"] == "applied" and p.get("confirmed_by") == "email" and ev.get("recorded_by") == "system":
        return CONFIRMED_BY_EMAIL
    return ""
