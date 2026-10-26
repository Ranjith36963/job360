"""The replayable fill script (owner decision 2026-10-26: "Refill the form"), table 0091.

Every form step the user's assistant carried out through Job360's browser for an application is one
APPEND-ONLY row (``application_fill_steps``): the action, a stable locator (browser_agent/refill.py) and
the value. "Refill the form" reads them back: one step per control (the newest wins, kept at its own
place in the order), only on the form's host. Nothing is ever updated or deleted here (erasure aside).
Never a password or one-time code: ``refill.note`` drops those before a row exists. Audit lines carry
ids and counts only, never a value.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

ACTIONS = frozenset({"type", "choose", "tick", "click", "upload"})
# One application's script is bounded: past this, new steps are not kept (the replay still has the first).
MAX_STEPS_PER_APPLICATION = 500
LOCATOR_MAX_CHARS = 2000
VALUE_MAX_CHARS = 5000
# A type, a pick and a tick set one field's value: the newest of them is the field's value.
_SAME_SLOT = {"type": "set", "choose": "set", "tick": "set", "click": "click", "upload": "upload"}


async def append(db: JobDatabase, user_id: str, application_id: int, steps: list[dict[str, Any]]) -> int:
    """Store the steps one browser action produced; returns how many were kept. The caller has proven
    the application is the user's (the browser route's gate)."""
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_fill_steps WHERE user_id = ? AND application_id = ?",
        (user_id, application_id),
    )
    have = int((await cur.fetchone())[0])
    now = datetime.now(timezone.utc).isoformat()
    kept = 0
    for step in steps:
        if have + kept >= MAX_STEPS_PER_APPLICATION:
            break
        where, action = step.get("where"), step.get("action")
        if action not in ACTIONS or not isinstance(where, dict):
            continue
        locator = json.dumps(where, sort_keys=True)
        if len(locator) > LOCATOR_MAX_CHARS:
            continue
        await db._db.execute(
            "INSERT INTO application_fill_steps (user_id, application_id, action, locator, value, host, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, application_id, action, locator, str(step.get("value") or "")[:VALUE_MAX_CHARS],
             str(where.get("host") or ""), now),
        )
        kept += 1
    if kept:
        get_audit_logger().info(
            "fill_steps_recorded",
            extra={"event": "fill_steps_recorded", "user_id": safe_log_value(user_id),
                   "application_id": int(application_id), "count": kept, "result": "ok"},
        )
    return kept


async def load(db: JobDatabase, user_id: str, application_id: int) -> tuple[list[dict[str, Any]], set[str]]:
    """The script to replay, oldest first (one step per control and slot, the newest value at its own
    place), and every host the recorded steps were on. Read-only; scoped by ``user_id``."""
    cur = await db._db.execute(
        "SELECT id, action, locator, value, host FROM application_fill_steps "
        "WHERE user_id = ? AND application_id = ? ORDER BY id",
        (user_id, application_id),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    last: dict[tuple[str, str], int] = {}
    for i, r in enumerate(rows):
        last[(_SAME_SLOT.get(str(r["action"]), str(r["action"])), str(r["locator"]))] = i
    script = []
    for i in sorted(last.values()):
        r = rows[i]
        try:
            where = json.loads(r["locator"])
        except ValueError:
            continue
        script.append({"action": r["action"], "where": where, "value": r["value"], "host": r["host"]})
    return script, {r["host"] for r in rows if r["host"]}


async def apps_with_script(db: JobDatabase, user_id: str, app_ids: list[int]) -> set[int]:
    """Which of these applications have any recorded step. Read-only, one query."""
    if not app_ids:
        return set()
    marks = ",".join("?" for _ in app_ids)
    cur = await db._db.execute(
        f"SELECT DISTINCT application_id FROM application_fill_steps WHERE user_id = ? "  # noqa: S608
        f"AND application_id IN ({marks})",
        (user_id, *app_ids),
    )
    return {int(r[0]) for r in await cur.fetchall()}


async def newest_fill(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """The newest ``form_filled`` payload (parsed), or None."""
    cur = await db._db.execute(
        "SELECT payload FROM application_events WHERE user_id = ? AND application_id = ? "
        "AND event_type = 'form_filled' ORDER BY id DESC LIMIT 1",
        (user_id, application_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    raw = row["payload"]
    try:
        p = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return p if isinstance(p, dict) else None
