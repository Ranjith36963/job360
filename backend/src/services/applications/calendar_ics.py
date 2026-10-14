"""The calendar feed's content: a hand-written iCalendar (RFC 5545) writer plus the
query that decides what goes in it. No dependency.

What leaves the building is deliberately small: a title ("Interview: <job> - <company>"),
the time, and a link to the application page. NEVER notes, contact names or emails -
the feed URL is a bearer credential that lives inside third-party calendars.

Writer rules (RFC 5545 section 3.1 and 3.3.11):

* TEXT values escape ``\\``, ``;``, ``,`` and newlines; CR and every other control
  character is stripped, so a hostile job title can never start a new property
  line (CRLF injection);
* content lines fold at 75 OCTETS (not characters), never inside a UTF-8 sequence;
* line endings are CRLF.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from src.core import settings
from src.repositories.database import JobDatabase

PRODID = "-//Job360//Calendar feed//EN"
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u0085\u2028\u2029]")  # all but TAB; \n handled first
_INTERVIEW_MINUTES = 60


@dataclass(frozen=True)
class CalEvent:
    uid: str
    summary: str
    url: str
    stamp: datetime
    start: Optional[datetime] = None   # timed event (UTC)
    day: Optional[date] = None         # all-day event


def escape_text(value: str) -> str:
    """RFC 5545 TEXT escaping. Newlines become the two characters ``\\n``; any other
    control character (CR included) is dropped."""
    value = str(value).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return _CTRL_RE.sub("", value)


def _clean_uri(value: str) -> str:
    """A URI value: no whitespace, no control characters (so it cannot break the line)."""
    return re.sub(r"[\s\x00-\x1f\x7f\u0085\u2028\u2029]", "", str(value))


def fold(line: str) -> str:
    """Fold one content line at 75 octets; continuation lines start with one space
    (which counts toward their 75). Never splits a multi-byte character."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts: list[str] = []
    current = ""
    size = 0
    limit = 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            parts.append(current)
            current, size, limit = "", 0, 74  # the leading space takes one octet
        current += ch
        size += n
    parts.append(current)
    return "\r\n ".join(parts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_calendar(events: list[CalEvent], name: str = "Job360") -> str:
    """The whole VCALENDAR text, CRLF-terminated."""
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(name)}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H",
    ]
    for ev in events:
        lines += ["BEGIN:VEVENT", f"UID:{_clean_uri(ev.uid)}", f"DTSTAMP:{_utc(ev.stamp)}"]
        if ev.start is not None:
            lines += [f"DTSTART:{_utc(ev.start)}", f"DTEND:{_utc(ev.start + timedelta(minutes=_INTERVIEW_MINUTES))}"]
        elif ev.day is not None:
            lines += [
                f"DTSTART;VALUE=DATE:{ev.day.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(ev.day + timedelta(days=1)).strftime('%Y%m%d')}",
            ]
        lines += [f"SUMMARY:{escape_text(ev.summary)}", f"URL:{_clean_uri(ev.url)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


def _parse_dt(raw: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _title(app_row: dict[str, Any]) -> str:
    title = str(app_row.get("job_title") or "").strip() or "Job"
    company = str(app_row.get("job_company") or "").strip()
    return f"{title} \u2013 {company}" if company else title


async def collect_events(
    db: JobDatabase, user_id: str, now: datetime, application_id: Optional[int] = None
) -> list[CalEvent]:
    """The owner's upcoming interviews and follow-ups. ``user_id`` ALWAYS scopes the
    query; ``application_id`` narrows it to one (already ownership-checked) application."""
    closed = tuple(settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES)
    sql = "SELECT id, job_title, job_company, status, follow_up_on FROM applications WHERE user_id = ?"
    params: list[Any] = [user_id]
    if application_id is not None:
        sql += " AND id = ?"
        params.append(application_id)
    cur = await db._db.execute(sql, tuple(params))
    apps = [dict(r) for r in await cur.fetchall() if r["status"] not in closed]
    if not apps:
        return []
    by_id = {int(a["id"]): a for a in apps}
    marks = ",".join("?" for _ in by_id)
    cur = await db._db.execute(
        "SELECT id, application_id, event_type, scheduled_at, recorded_at, corrects_event_id "  # noqa: S608 - marks are '?'
        f"FROM application_events WHERE application_id IN ({marks}) "
        "AND (event_type = 'interview_scheduled' OR corrects_event_id IS NOT NULL) ORDER BY id ASC",
        tuple(by_id),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    superseded = {r["corrects_event_id"] for r in rows if r["corrects_event_id"] is not None}
    latest: dict[int, dict[str, Any]] = {}
    for r in rows:
        if r["event_type"] == "interview_scheduled" and r["scheduled_at"] and r["id"] not in superseded:
            latest[int(r["application_id"])] = r  # ascending id: the last one wins
    lo = now - timedelta(days=settings.CALENDAR_FEED_LOOKBACK_DAYS)
    hi = now + timedelta(seconds=settings.APPLICATION_SCHEDULED_AT_MAX_FUTURE_SECONDS)
    base = settings.SITE_BASE_URL
    out: list[CalEvent] = []
    for app_id, r in latest.items():
        start = _parse_dt(r["scheduled_at"])
        if start is None or not (lo <= start <= hi):
            continue
        out.append(CalEvent(
            uid=f"app-{app_id}-ev-{r['id']}@job360.uk", summary=f"Interview: {_title(by_id[app_id])}",
            url=f"{base}/applications/{app_id}", stamp=_parse_dt(r["recorded_at"]) or now, start=start,
        ))
    for app_id, a in by_id.items():
        raw = a.get("follow_up_on")
        if not raw:
            continue
        try:
            day = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if not (lo.date() <= day <= hi.date()):
            continue
        out.append(CalEvent(
            uid=f"app-{app_id}-followup@job360.uk", summary=f"Follow up: {_title(a)}",
            url=f"{base}/applications/{app_id}", stamp=now, day=day,
        ))
    def _when(e: CalEvent) -> datetime:
        if e.start is not None:
            return e.start
        return datetime.combine(e.day or now.date(), datetime.min.time(), tzinfo=timezone.utc)

    out.sort(key=lambda e: (_when(e), e.uid))
    return out
