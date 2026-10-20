"""The agenda read: upcoming and just-passed interviews, and the ``agenda`` list filter.

A pull-style read of stored state (product rules 4 and 5): nothing is sourced or scored, and
nothing is pushed. It reuses :func:`calendar_ics.live_interviews`, so "a live interview" means
exactly what the calendar feed means by it: a non-superseded ``interview_scheduled`` with a
``scheduled_at``, on an application that is not closed. Every query is scoped by ``user_id``
(hard rules 12/25).

* ``upcoming``    - live interviews with ``scheduled_at`` in ``[now, now + 7 days]``.
* ``just_passed`` - live interviews with ``scheduled_at`` in ``[now - 3 days, now)`` whose
  application has no later OUTCOME event. An outcome event is one the user (or their agent)
  recorded AFTER the interview time that says what happened: ``interview_done``,
  ``interview_session``, ``replied``, ``offer``, ``rejected``, ``withdrawn``, ``ghosted``,
  ``hired``, ``offer_declined``. A later live ``interview_scheduled`` (the next round) counts
  too. Notes, lessons and outreach do not: they do not say how the interview went.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.repositories.database import JobDatabase
from src.services.applications.calendar_ics import _parse_dt, interview_fields, live_interviews
from src.utils.logger import get_audit_logger, safe_log_value

UPCOMING_DAYS = 7
JUST_PASSED_DAYS = 3
# Events that mean "what happened is on the record" (see the module docstring).
OUTCOME_EVENT_TYPES = (
    "interview_done", "interview_session", "replied", "offer", "rejected",
    "withdrawn", "ghosted", "hired", "offer_declined",
)
AGENDA_RANGES = ("today", "tomorrow", "week")


def _item(app_id: int, app: dict[str, Any], row: dict[str, Any], start: datetime) -> dict[str, Any]:
    f = interview_fields(row)
    return {
        "application_id": app_id, "company": str(app.get("job_company") or ""),
        "title": str(app.get("job_title") or ""), "scheduled_at": start.isoformat(),
        "round": f["round"], "link": f["link"], "duration_minutes": f["duration_minutes"],
    }


async def _answered_apps(
    db: JobDatabase, user_id: str, passed: list[tuple[dict[str, Any], datetime]], later: dict[int, list[datetime]]
) -> set[int]:
    """Event ids of the ``passed`` interviews that already have an outcome (see the module docstring)."""
    app_ids = sorted({int(r["application_id"]) for r, _ in passed})
    marks = ",".join("?" for _ in app_ids)
    cur = await db._db.execute(
        "SELECT id, application_id, event_type, recorded_at, corrects_event_id "  # noqa: S608 - marks are '?'
        f"FROM application_events WHERE user_id = ? AND application_id IN ({marks}) "
        f"AND (event_type IN ({','.join('?' for _ in OUTCOME_EVENT_TYPES)}) OR corrects_event_id IS NOT NULL)",
        (user_id, *app_ids, *OUTCOME_EVENT_TYPES),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    superseded = {r["corrects_event_id"] for r in rows if r["corrects_event_id"] is not None}
    outcomes = [r for r in rows if r["event_type"] in OUTCOME_EVENT_TYPES and r["id"] not in superseded]
    answered: set[int] = set()
    for row, start in passed:
        app_id, ev_id = int(row["application_id"]), int(row["id"])
        if any(start < nxt for nxt in later.get(app_id, ())):
            answered.add(ev_id)  # the next round is booked
            continue
        for o in outcomes:
            when = _parse_dt(o["recorded_at"])
            if int(o["application_id"]) == app_id and int(o["id"]) > ev_id and when is not None and when >= start:
                answered.add(ev_id)
                break
    return answered


async def interview_agenda(
    db: JobDatabase, user_id: str, now: Optional[datetime] = None
) -> dict[str, list[dict[str, Any]]]:
    """``{"upcoming": [...], "just_passed": [...]}`` for ``user_id`` (both oldest first, empty lists
    when none). Each item: application_id, company, title, scheduled_at, round, link, duration_minutes."""
    now = now or datetime.now(timezone.utc)
    by_id, interviews = await live_interviews(db, user_id)
    starts = [(r, _parse_dt(r["scheduled_at"])) for r in interviews]
    live = [(r, s) for r, s in starts if s is not None]
    later: dict[int, list[datetime]] = {}
    for r, s in live:
        later.setdefault(int(r["application_id"]), []).append(s)
    upcoming = [(r, s) for r, s in live if now <= s <= now + timedelta(days=UPCOMING_DAYS)]
    passed = [(r, s) for r, s in live if now - timedelta(days=JUST_PASSED_DAYS) <= s < now]
    answered = await _answered_apps(db, user_id, passed, later) if passed else set()
    unanswered = [(r, s) for r, s in passed if int(r["id"]) not in answered]
    out = {
        "upcoming": [_item(int(r["application_id"]), by_id[int(r["application_id"])], r, s)
                     for r, s in sorted(upcoming, key=lambda x: (x[1], x[0]["id"]))],
        "just_passed": [_item(int(r["application_id"]), by_id[int(r["application_id"])], r, s)
                        for r, s in sorted(unanswered, key=lambda x: (x[1], x[0]["id"]))],
    }
    get_audit_logger().info(
        "interview_agenda_read",
        extra={"event": "interview_agenda_read", "user_id": safe_log_value(user_id),
               "upcoming": len(out["upcoming"]), "just_passed": len(out["just_passed"])},
    )
    return out


def agenda_window(agenda: str, today: date, zone: ZoneInfo) -> tuple[datetime, datetime, date, date]:
    """``(start, end)`` instants (UTC, end exclusive) and ``(first, last_exclusive)`` local dates for
    ``agenda`` = today | tomorrow | week (``week`` = today and the next six days) in the user's zone."""
    first = {"today": today, "tomorrow": today + timedelta(days=1), "week": today}[agenda]
    last = first + timedelta(days=7 if agenda == "week" else 1)

    def at(d: date) -> datetime:
        return datetime.combine(d, time.min, tzinfo=zone).astimezone(timezone.utc)

    return at(first), at(last), first, last


async def agenda_application_ids(
    db: JobDatabase, user_id: str, agenda: str, today: date, zone: ZoneInfo
) -> list[int]:
    """Ids of the user's open applications with a live interview, or a ``follow_up_on``, inside the
    ``agenda`` range (computed in the user's own time zone)."""
    start, end, first, last = agenda_window(agenda, today, zone)
    by_id, interviews = await live_interviews(db, user_id)
    ids: set[int] = set()
    for r in interviews:
        s = _parse_dt(r["scheduled_at"])
        if s is not None and start <= s < end:
            ids.add(int(r["application_id"]))
    for app_id, a in by_id.items():
        raw = a.get("follow_up_on")
        try:
            day = date.fromisoformat(str(raw)[:10]) if raw else None
        except ValueError:
            day = None
        if day is not None and first <= day < last:
            ids.add(app_id)
    return sorted(ids)
