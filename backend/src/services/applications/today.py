"""``stats(view="today")``: where the user stands today, in a handful of numbers.

A pull-style read of stored state (product rules 4 and 5): counts only, never an item list, so the
answer stays small. "Today" is the user's own day in ``users.timezone`` - the window the ``agenda``
list filter uses (``agenda.agenda_window``) - and every count reads a timestamp that falls inside it:

* ``brought`` / ``fit_judged`` / ``replies`` - events of that type recorded today (a retracted event,
  one a ``corrects_event_id`` row names, is skipped).
* ``outreach_sent`` / ``outreach_replies`` - ``contact_outreach`` rows (entry ``sent`` / ``reply``) whose
  ``occurred_at`` is today. That ledger holds cold (network) outreach too; a mark only writes an
  application event when the contact is linked to an application.
* ``applied`` - receipts whose ``sent_at`` is today (the receipt is the proof of an application). This is
  NOT ``queue_summary.submitted_today``, which counts ``applied`` events by ``recorded_at``.
* ``asks_opened`` / ``asks_answered`` - ``application_asks`` rows asked / (last) answered today, general
  questions included.

``queue_summary`` is ``queue_preflight.queue_summary``, the function ``list_applications(queued=true)``
returns. ``waiting_on_you`` is ``schedule._waiting_on_you`` over the open queue: how many queued jobs have
an open ask, an open block, or a newest CV the user has not seen. It is not ``whats_due``'s field of the
same name (that is 0 unless the assistant applies on its own) and not the Needs-you count (which also
holds non-job items). Test applications (``simulation`` events) are left out of the counts, as in the rest
of stats. The query count does not grow with the queue.
Every query is scoped by ``user_id`` (rule #12).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.repositories.database import JobDatabase
from src.services.applications import agenda, queue_preflight, spine
from src.services.applications.test_marker import TEST_APP_SQL, test_app_ids

# count name -> the application_events.event_type it counts (outreach comes from contact_outreach, below)
EVENT_COUNTS = {
    "brought": "brought",
    "fit_judged": "fit_judged",
    "replies": "replied",
}


def _within(raw: Any, start: datetime, end: datetime) -> bool:
    try:
        at = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return start <= at < end


async def _times(db: JobDatabase, sql: str, params: list[Any]) -> list[Any]:
    cur = await db._db.execute(sql, params)
    return [r[0] for r in await cur.fetchall()]


async def build(db: JobDatabase, user_id: str, now: datetime | None = None) -> dict[str, Any]:
    """The ``today`` block (see the module docstring)."""
    import asyncio  # noqa: PLC0415

    from src.services.profile import assistant_settings as settings_rules  # noqa: PLC0415
    from src.services.profile import schedule  # noqa: PLC0415 - profile <-> spine cycle
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415 - heavy (rule 16)

    now = now or datetime.now(timezone.utc)
    zone = await spine.user_zone(db, user_id)
    today = now.astimezone(zone).date()
    start, end, _first, _last = agenda.agenda_window("today", today, zone)
    floor = (start - timedelta(days=1)).isoformat()  # text floor; the exact window is checked below
    test_sql, test_params = TEST_APP_SQL("a")

    counts: dict[str, int] = {}
    for name, event_type in EVENT_COUNTS.items():
        stamps = await _times(
            db,
            "SELECT e.recorded_at FROM application_events e JOIN applications a ON a.id = e.application_id "  # noqa: S608
            "AND a.user_id = e.user_id WHERE e.user_id = ? AND e.event_type = ? AND e.recorded_at >= ? "
            "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
            f"AND NOT {test_sql}",
            [user_id, event_type, floor, *test_params],
        )
        counts[name] = sum(1 for s in stamps if _within(s, start, end))
    receipts = await _times(
        db,
        "SELECT r.sent_at FROM application_receipts r JOIN applications a ON a.id = r.application_id "  # noqa: S608
        f"AND a.user_id = r.user_id WHERE r.user_id = ? AND r.sent_at >= ? AND NOT {test_sql}",
        [user_id, floor, *test_params],
    )
    counts["applied"] = sum(1 for s in receipts if _within(s, start, end))
    for name, entry in (("outreach_sent", "sent"), ("outreach_replies", "reply")):
        stamps = await _times(
            db,
            "SELECT occurred_at FROM contact_outreach WHERE user_id = ? AND entry = ? AND occurred_at >= ?",
            [user_id, entry, floor],
        )
        counts[name] = sum(1 for s in stamps if _within(s, start, end))
    for name, column in (("asks_opened", "asked_at"), ("asks_answered", "answered_at")):
        stamps = await _times(
            db,
            f"SELECT k.{column} FROM application_asks k LEFT JOIN applications a "  # noqa: S608 - constant column
            f"ON a.id = k.application_id AND a.user_id = k.user_id WHERE k.user_id = ? AND k.{column} >= ? "
            f"AND (a.id IS NULL OR NOT {test_sql})",
            [user_id, floor, *test_params],
        )
        counts[name] = sum(1 for s in stamps if _within(s, start, end))

    profile = await asyncio.to_thread(load_profile, user_id)
    queue_order, _queued_at = await spine.open_queue(db, user_id, profile)
    cfg = settings_rules.effective((profile or UserProfile()).assistant_settings)
    summary = await queue_preflight.queue_summary(db, user_id, queue_order, cfg)
    tests = await test_app_ids(db, user_id)  # whats_due's queue leaves test jobs out
    waiting = await schedule._waiting_on_you(db, user_id, [i for i in queue_order if i not in tests])
    return {
        "date": today.isoformat(), "timezone": str(zone), "counts": counts,
        "queue_summary": summary, "waiting_on_you": waiting,
    }
