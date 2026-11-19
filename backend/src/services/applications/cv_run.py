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

One run, one start (2026-11-19, run 05:33: "N of 10 stored strong" restarted at 1 after each record call): a
first-shaped read (no cursor, ``include_profile`` true) inside a run begun under ``RUN_MINUTES`` ago CONTINUES that
run. Before, it moved the run's start to now, so every CV stored strong earlier in the run stopped counting.

Progress (2026-11-19, run 03:33 stopped after one batch of 5 with ~181 left): each ``cv_work`` read saves its
page's last id and whether more pages follow in the slot's ``state`` (migration 0094's column); each
``cv_reviewed`` reply then says how many CVs are stored strong this run and hands back the next ``cv_work`` call,
so the assistant has no decision point. Progress never changes what ends the run or what starts the batch.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.utils.logger import get_audit_logger, safe_log_value

SLOT = "cv_run:"
TARGET = 10  # new CVs a run writes (cv.md step 2)
RUN_MINUTES = 50  # a scheduled run is hourly: an open run begun this recently is still the same run


async def begin(db: Any, user_id: str) -> None:
    """The run's first ``cv_work`` read: a new run starts now, replacing a run begun ``RUN_MINUTES`` or more ago
    (one that never ended). A first-shaped read inside a younger run continues it. Never raises."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=RUN_MINUTES)).isoformat()
    try:
        cur = await db._db.execute(
            "INSERT INTO assistant_duty_runs (user_id, duty, done_at, state) VALUES (?, ?, ?, '') "
            "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at, state = '' "
            "WHERE assistant_duty_runs.done_at < ? RETURNING duty",
            (user_id, SLOT, now.isoformat(), cutoff),
        )
        begun = await cur.fetchone() is not None
        await db._db.commit()
    except Exception:  # noqa: BLE001 - the read is never broken; this run's end then starts nothing
        get_audit_logger().warning("cv_run_begin_failed", extra={
            "event": "cv_run_begin_failed", "user_id": safe_log_value(user_id), "reason": "error"})
        return
    get_audit_logger().info("cv_run_begin", extra={
        "event": "cv_run_begin", "user_id": safe_log_value(user_id), "result": "begun" if begun else "continued"})


async def note_page(db: Any, user_id: str, last_id: Optional[int], more: bool) -> None:
    """A ``cv_work`` read of the open run: keep its page's last id and whether pages remain. Never raises."""
    try:
        await db._db.execute(
            "UPDATE assistant_duty_runs SET state = ? WHERE user_id = ? AND duty = ?",
            (json.dumps({"after": last_id, "more": bool(more)}), user_id, SLOT),
        )
        await db._db.commit()
    except Exception:  # noqa: BLE001 - the read is never broken; the review reply then names no cursor
        get_audit_logger().warning("cv_run_page_failed", extra={
            "event": "cv_run_page_failed", "user_id": safe_log_value(user_id), "reason": "error"})


async def strong_cvs(db: Any, user_id: str, since: str) -> int:
    """Jobs reviewed since ``since`` whose newest CV is stored strong now (new CVs and weak ones fixed)."""
    from src.services.applications import cv_review  # noqa: PLC0415 - lazy (rule #16)

    cur = await db._db.execute(
        "SELECT DISTINCT application_id FROM application_events "
        "WHERE user_id = ? AND event_type = 'cv_reviewed' AND recorded_at >= ?",
        (user_id, since),
    )
    ids = [int(r[0]) for r in await cur.fetchall()]
    states = await cv_review.states_for_apps(db, user_id, ids)
    return sum(1 for s in states.values() if s == cv_review.STRONG)


async def progress(db: Any, user_id: str) -> Optional[dict[str, Any]]:
    """``{since, strong, after, more}`` of the user's open CV run, or ``None`` (no run open). Never raises."""
    try:
        cur = await db._db.execute(
            "SELECT done_at, state FROM assistant_duty_runs WHERE user_id = ? AND duty = ?", (user_id, SLOT)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        try:
            page = json.loads(str(row[1] or "") or "{}")
        except ValueError:
            page = {}
        page = page if isinstance(page, dict) else {}
        since = str(row[0])
        return {"since": since, "strong": await strong_cvs(db, user_id, since),
                "after": page.get("after"), "more": bool(page.get("more", True))}
    except Exception:  # noqa: BLE001 - the review is never broken; its reply then carries no next
        get_audit_logger().warning("cv_run_progress_failed", extra={
            "event": "cv_run_progress_failed", "user_id": safe_log_value(user_id), "reason": "error"})
        return None


def next_text(prog: dict[str, Any], *, ended: bool, user_id: str, application_id: int) -> str:
    """The ``next`` a ``cv_reviewed`` reply carries during a CV run; logs ``cv_run_progress``."""
    strong = int(prog["strong"])
    done = ended or strong >= TARGET
    result = "complete" if done else ("not_done" if prog["more"] else "last_page")
    get_audit_logger().info(f"cv_run_progress strong={strong} target={TARGET}", extra={
        "event": "cv_run_progress", "user_id": safe_log_value(user_id),
        "application_id": safe_log_value(application_id), "strong": strong, "target": TARGET, "result": result})
    if done:
        return f"CV run complete: {strong} stored strong. Write the summary."
    if not prog["more"]:  # the last page is out: its weak fixes (step 6) are what is left
        return (f"CV run: {strong} of {TARGET} stored strong. cv_work has no further page: finish this page's "
                "weak fixes (step 6), then write the summary.")
    after = prog["after"] if isinstance(prog["after"], int) else application_id
    return (f"CV run: {strong} of {TARGET} stored strong. Not done. Call cv_work(limit=5, after={after}, "
            "include_profile=false) now and work that page.")


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
