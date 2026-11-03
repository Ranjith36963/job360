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
  submit with no outcome (``kit.submit_unconfirmed``: it may have gone out).

``undo`` is the user's one web action (``POST /applications/{id}/auto/undo``): a ``note`` whose
``corrects_event_id`` is that ``withdrawn``, so the status replays back (append-only, M3).
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
NOTHING_TO_UNDO = "nothing Job360 closed by itself waits to be undone on this application"


def _log(user_id: str, application_id: int, name: str, result: str, **extra: Any) -> None:
    get_audit_logger().info(name, extra={
        "event": name, "user_id": safe_log_value(user_id), "application_id": int(application_id),
        "result": safe_log_value(result, max_len=40), **{k: safe_log_value(v, max_len=40) for k, v in extra.items()},
    })


async def _why_not(db: JobDatabase, user_id: str, application_id: int, stored: dict[str, Any]) -> str:
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
    return ""


async def close_if_proven(
    db: JobDatabase, user: CurrentUser, application_id: int, block_event_id: int, stored: dict[str, Any],
) -> Optional[str]:
    """Inside ``blocked.record``'s transaction (the row is locked): close the application when every
    guardrail passes. Returns the warning the assistant reads, or None when the user still decides."""
    why = await _why_not(db, user.id, application_id, stored)
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
        "SELECT e.id, e.event_type, e.payload FROM application_events e "  # noqa: S608 - placeholders only
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
        if ev is None or ev["event_type"] != "withdrawn" or _payload(ev["payload"]).get("closed_by") != CLOSED_BY:
            _log(user.id, application_id, "auto_undo_refused", "nothing_to_undo")
            raise SpineError(409, NOTHING_TO_UNDO)
        result = await spine.append_event(
            db, user_id=user.id, application_id=application_id, event_type="note", detail=UNDO_DETAIL,
            payload={"undoes": CLOSED_BY}, occurred_at=datetime.now(timezone.utc).isoformat(),
            recorded_by=actor_for(user), corrects_event_id=int(ev["id"]),
        )
    _log(user.id, application_id, "auto_undone", "ok", undoes=CLOSED_BY)
    return result
