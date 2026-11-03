"""The own-browser submit gate (autonomy A8, DESIGN_AUTONOMY 3c): one ``submit`` = one receipt.

Job360's browser runs check_submit in code before it clicks; in the user's own browser (Claude in
Chrome) only recipe text did. Now the server closes the gap:

* check_submit answering ``submit`` appends one server-only ``submit_cleared`` event
  ``{form_url, cv_artifact_id, reason}`` (the frozen CV id: the newest CV at that moment).
* An assistant's receipt (``record_application``, ``record_event`` applied, ``POST /receipts``) needs
  an UNUSED ``submit_cleared`` younger than ``SUBMIT_APPROVAL_TTL_MINUTES``; without one it is 409
  ``submit_not_cleared``. Using it appends ``submit_cleared_used`` ``{clearance_event_id, receipt_id |
  applied_event_id}`` in the receipt's own transaction, after locking the application row, so two
  receipts can never share one clearance. Append-only: nothing is updated.
* The user's own web click (actor ``web``) is never gated: an owner click is the approval.
* Job360's browser needs no exemption: its click calls the same ``submit_check`` route function, so its
  ``submit`` writes the clearance its receipt then uses.

Neither type can be written through ``record_event`` (422, like ``autonomy_shadow``).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from src.core import settings
from src.services.applications import spine
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

EVENT_TYPE = "submit_cleared"
USED_EVENT_TYPE = "submit_cleared_used"
SERVER_ONLY_EVENTS = frozenset({EVENT_TYPE, USED_EVENT_TYPE})
# Always on: tests that record an assistant receipt get a real clearance (tests/_clearance.py).
REQUIRED = True
MESSAGE = (
    "submit_not_cleared: call check_submit right before the final Submit; it must say submit. "
    "One submit answer covers one receipt. If the user already pressed Submit themselves, ask them to "
    "press Mark Applied on Job360."
)


async def record(
    db: JobDatabase, user_id: str, application_id: int, actor: str, form_url: Optional[str], reason: str,
) -> int:
    """Append one ``submit_cleared`` (check_submit said ``submit``); returns its event id."""
    cv = await spine.latest_artifact(db, user_id, application_id, "cv")
    result = await spine.append_event(
        db, user_id=user_id, application_id=application_id, event_type=EVENT_TYPE,
        payload={"form_url": (form_url or "")[:2048], "cv_artifact_id": int(cv["id"]) if cv else None,
                 "reason": reason},
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=actor,
    )
    return int(result["event_id"])


async def _used_ids(db: JobDatabase, user_id: str, application_id: int) -> set[int]:
    cur = await db._db.execute(
        "SELECT payload FROM application_events WHERE user_id = ? AND application_id = ? AND event_type = ?",
        (user_id, application_id, USED_EVENT_TYPE),
    )
    used: set[int] = set()
    for (raw,) in [tuple(r) for r in await cur.fetchall()]:
        try:
            used.add(int(json.loads(raw or "{}")["clearance_event_id"]))
        except (ValueError, KeyError, TypeError):
            continue
    return used


async def unused(db: JobDatabase, user_id: str, application_id: int) -> Optional[int]:
    """The newest unexpired, unused ``submit_cleared`` id, or ``None``."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=settings.SUBMIT_APPROVAL_TTL_MINUTES)).isoformat()
    cur = await db._db.execute(
        "SELECT id FROM application_events WHERE user_id = ? AND application_id = ? AND event_type = ? "
        "AND recorded_at >= ? ORDER BY id DESC LIMIT 50",
        (user_id, application_id, EVENT_TYPE, cutoff),
    )
    ids = [int(tuple(r)[0]) for r in await cur.fetchall()]
    if not ids:
        return None
    used = await _used_ids(db, user_id, application_id)
    return next((i for i in ids if i not in used), None)


def _refuse(user_id: str, application_id: int, actor: str) -> spine.SpineError:
    get_audit_logger().warning("submit_not_cleared", extra={
        "event": "submit_not_cleared", "user_id": safe_log_value(user_id), "application_id": int(application_id),
        "actor": safe_log_value(actor), "status": 409, "result": "refused",
    })
    return spine.SpineError(409, MESSAGE)


async def required(db: JobDatabase, user_id: str, application_id: int, actor: str) -> Optional[int]:
    """Before an assistant's receipt: the clearance id it will use, or 409 ``submit_not_cleared``.
    ``None`` (no gate) for the user's own web click, or when the gate is off."""
    if actor == "web" or not REQUIRED:
        return None
    clearance = await unused(db, user_id, application_id)
    if clearance is None:
        raise _refuse(user_id, application_id, actor)
    return clearance


async def use(
    db: JobDatabase, user_id: str, application_id: int, actor: str, clearance_id: int, ref: dict[str, int],
) -> None:
    """Inside the receipt's transaction: lock the application row, re-check the clearance is unused (a
    concurrent receipt may have taken it: 409), then append ``submit_cleared_used``."""
    await db._db.execute(
        "SELECT id FROM applications WHERE id = ? AND user_id = ? FOR UPDATE", (application_id, user_id)
    )
    if clearance_id in await _used_ids(db, user_id, application_id):
        raise _refuse(user_id, application_id, actor)
    await spine.append_event(
        db, user_id=user_id, application_id=application_id, event_type=USED_EVENT_TYPE,
        payload={"clearance_event_id": int(clearance_id), **ref},
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=actor,
    )
