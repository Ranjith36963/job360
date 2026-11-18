"""The CV run's end, seen on the server (owner 2026-11-18): hunt -> CV review -> apply.

The CV recipe (``recipes/cv.md``) never calls ``mark_done``, so Job360 marks the run itself:
- it BEGINS at the run's first ``cv_work`` read (``include_profile`` true, no cursor; recipe step 2), which
  stamps one slot per user in ``assistant_duty_runs`` (duty ``cv_run:``; no migration, like
  ``own_browser:li_state``);
- it ENDS at the first of the recipe's two end conditions Job360 can see: a ``cv_work`` read with no items
  left, or a ``cv_reviewed`` that makes ``TARGET`` new CVs reviewed since the run began (a CV saved this run
  on a job with no review before the run: fixes of earlier CVs do not count, as the recipe says).

The end claims the slot in ONE statement (``DELETE ... RETURNING``), so an end reached twice starts the batch
once; the batch's own cool-down and hourly cap still apply. A run that stops early leaves its slot; the next
run's first read replaces it. Every query is scoped by ``user_id`` (rule #12).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src.utils.logger import get_audit_logger, safe_log_value

SLOT = "cv_run:"
TARGET = 10  # new CVs a run writes (cv.md step 2)


async def begin(db: Any, user_id: str) -> None:
    """The run's first ``cv_work`` read: a new run starts now (replacing a run that never ended). Never raises."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db._db.execute(
            "INSERT INTO assistant_duty_runs (user_id, duty, done_at) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at",
            (user_id, SLOT, now),
        )
        await db._db.commit()
    except Exception:  # noqa: BLE001 - the read is never broken; this run's end then starts nothing
        get_audit_logger().warning("cv_run_begin_failed", extra={
            "event": "cv_run_begin_failed", "user_id": safe_log_value(user_id), "reason": "error"})
        return
    get_audit_logger().info("cv_run_begin", extra={"event": "cv_run_begin", "user_id": safe_log_value(user_id)})


async def new_cvs(db: Any, user_id: str, since: str) -> int:
    """Jobs whose CV was saved and reviewed since ``since`` and that had no review before it."""
    cur = await db._db.execute(
        "SELECT COUNT(DISTINCT e.application_id) FROM application_events e "
        "WHERE e.user_id = ? AND e.event_type = 'cv_reviewed' AND e.recorded_at >= ? "
        "AND EXISTS (SELECT 1 FROM application_artifacts a WHERE a.user_id = e.user_id "
        "    AND a.application_id = e.application_id AND a.kind = 'cv' AND a.created_at >= ?) "
        "AND NOT EXISTS (SELECT 1 FROM application_events p WHERE p.user_id = e.user_id "
        "    AND p.application_id = e.application_id AND p.event_type = 'cv_reviewed' AND p.recorded_at < ?)",
        (user_id, since, since, since),
    )
    row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def end_if_reached(db: Any, user_id: str, *, no_items_left: bool) -> Optional[str]:
    """End the user's open CV run when it is over and start the batch once. Returns the reason, or ``None``.
    Never raises: the read or the review that reached the end is never broken by it."""
    try:
        return await _end(db, user_id, no_items_left)
    except Exception:  # noqa: BLE001 - the hourly batch run is the backup
        get_audit_logger().warning("cv_run_end_failed", extra={
            "event": "cv_run_end_failed", "user_id": safe_log_value(user_id), "reason": "error"})
        return None


async def _end(db: Any, user_id: str, no_items_left: bool) -> Optional[str]:
    cur = await db._db.execute(
        "SELECT done_at FROM assistant_duty_runs WHERE user_id = ? AND duty = ?", (user_id, SLOT)
    )
    row = await cur.fetchone()
    if row is None:
        return None  # no run begun (or this one already ended)
    since = str(row[0])
    reason = "no_items_left" if no_items_left else (
        "target" if await new_cvs(db, user_id, since) >= TARGET else None)
    if reason is None:
        return None
    cur = await db._db.execute(
        "DELETE FROM assistant_duty_runs WHERE user_id = ? AND duty = ? AND done_at = ? RETURNING duty",
        (user_id, SLOT, since),
    )
    claimed = await cur.fetchone() is not None
    await db._db.commit()
    get_audit_logger().info("cv_run_end", extra={
        "event": "cv_run_end", "user_id": safe_log_value(user_id), "reason": reason,
        "result": "batch_ignited" if claimed else "already_ended"})
    if not claimed:
        return None
    from src.services.browser_agent import duty_trigger  # noqa: PLC0415 - lazy (rule #16)

    await duty_trigger.ignite(user_id, ("batch",), "cv_done", db=db)
    return reason
