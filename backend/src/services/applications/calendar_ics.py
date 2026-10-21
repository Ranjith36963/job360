"""The calendar feed's content: a hand-written iCalendar (RFC 5545) writer plus the
query that decides what goes in it. No dependency.

What leaves the building is deliberately small: a title ("Interview: <job> - <company>",
plus the round when the event names one), the time and length, the http(s) meeting link and
location the owner gave, reminders, and a link to the application page. NEVER notes, contact
names or emails - the feed URL is a bearer credential that lives inside third-party calendars.
Every interview is its own VEVENT (multi-round hunts); each gets two VALARM reminders.

Writer rules (RFC 5545 section 3.1 and 3.3.11):

* TEXT values escape ``\\``, ``;``, ``,`` and newlines; CR and every other control
  character is stripped, so a hostile job title can never start a new property
  line (CRLF injection);
* content lines fold at 75 OCTETS (not characters), never inside a UTF-8 sequence;
* line endings are CRLF.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

from src.core import settings
from src.repositories.database import JobDatabase

PRODID = "-//Job360//Calendar feed//EN"
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u0085\u2028\u2029]")  # all but TAB; \n handled first
_INTERVIEW_MINUTES = 60
_MIN_MINUTES, _MAX_MINUTES = 5, 480
_ROUND_MAX, _LOCATION_MAX, _LINK_MAX = 60, 200, 1000


@dataclass(frozen=True)
class CalEvent:
    uid: str
    summary: str
    url: str
    stamp: datetime
    start: Optional[datetime] = None   # timed event (UTC)
    day: Optional[date] = None         # all-day event
    duration_minutes: int = _INTERVIEW_MINUTES
    description: str = ""
    location: str = ""
    alarms: tuple[str, ...] = ()       # TRIGGER values, one VALARM each


def escape_text(value: str) -> str:
    """RFC 5545 TEXT escaping. Newlines become the two characters ``\\n``; any other
    control character (CR included) is dropped."""
    value = str(value).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return _CTRL_RE.sub("", value)


def _clean_uri(value: str) -> str:
    """A URI value: no whitespace, no control characters (so it cannot break the line)."""
    return re.sub(r"[\s\x00-\x1f\x7f\u0085\u2028\u2029]", "", str(value))


def _trigger_before(minutes: int) -> str:
    """An RFC 5545 negative DURATION: whole days as ``-P1D``, whole hours as ``-PT1H``, else ``-PT90M``."""
    if minutes % 1440 == 0:
        return f"-P{minutes // 1440}D"
    if minutes % 60 == 0:
        return f"-PT{minutes // 60}H"
    return f"-PT{minutes}M"


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
            lines += [f"DTSTART:{_utc(ev.start)}", f"DTEND:{_utc(ev.start + timedelta(minutes=ev.duration_minutes))}"]
        elif ev.day is not None:
            lines += [
                f"DTSTART;VALUE=DATE:{ev.day.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(ev.day + timedelta(days=1)).strftime('%Y%m%d')}",
            ]
        lines += [f"SUMMARY:{escape_text(ev.summary)}", f"URL:{_clean_uri(ev.url)}"]
        if ev.description:
            lines.append(f"DESCRIPTION:{escape_text(ev.description)}")
        if ev.location:
            lines.append(f"LOCATION:{escape_text(ev.location)}")
        for trigger in ev.alarms:
            lines += [
                "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape_text(ev.summary)}",
                f"TRIGGER:{_clean_uri(trigger)}", "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


def _parse_dt(raw: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _payload(raw: Any) -> dict[str, Any]:
    """An event's payload as a dict; anything unreadable is ``{}`` (a bad row never breaks the feed)."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _text(value: Any, cap: int) -> str:
    return " ".join(value.split())[:cap] if isinstance(value, str) else ""


def _duration(value: Any) -> int:
    """``duration_minutes`` clamped to 5..480; anything that is not a number gives the default 60."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return _INTERVIEW_MINUTES
    try:
        number = float(value)
        if not math.isfinite(number):
            return _INTERVIEW_MINUTES
        minutes = int(number)
    except (ValueError, OverflowError):
        return _INTERVIEW_MINUTES
    return max(_MIN_MINUTES, min(_MAX_MINUTES, minutes))


def _http_link(value: Any) -> str:
    """The link when it is an http(s) URL, else '' (``javascript:`` and friends are dropped)."""
    if not isinstance(value, str):
        return ""
    link = _clean_uri(value)[:_LINK_MAX]
    try:
        parts = urlsplit(link)
    except ValueError:
        return ""
    return link if parts.scheme.lower() in ("http", "https") and parts.netloc else ""


def _interview_alarms() -> tuple[str, ...]:
    mins = (settings.CALENDAR_ALARM_INTERVIEW_FIRST_MINUTES, settings.CALENDAR_ALARM_INTERVIEW_SECOND_MINUTES)
    return tuple(_trigger_before(m) for m in mins if m > 0)


def _title(app_row: dict[str, Any]) -> str:
    title = str(app_row.get("job_title") or "").strip() or "Job"
    company = str(app_row.get("job_company") or "").strip()
    return f"{title} \u2013 {company}" if company else title


async def live_interviews(
    db: JobDatabase, user_id: str, application_id: Optional[int] = None
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """The ONE notion of a "live interview", shared by the calendar feed and the agenda read
    (``agenda.py``): every ``interview_scheduled`` event with a ``scheduled_at`` that no later
    event corrects, on an application that is not closed. Returns ``(open applications by id,
    live interview event rows oldest first)``. ``user_id`` ALWAYS scopes the query;
    ``application_id`` narrows it to one (already ownership-checked) application."""
    closed = tuple(settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES)
    sql = "SELECT id, job_title, job_company, status, follow_up_on FROM applications WHERE user_id = ?"
    params: list[Any] = [user_id]
    if application_id is not None:
        sql += " AND id = ?"
        params.append(application_id)
    cur = await db._db.execute(sql, tuple(params))
    apps = [dict(r) for r in await cur.fetchall() if r["status"] not in closed]
    if not apps:
        return {}, []
    by_id = {int(a["id"]): a for a in apps}
    marks = ",".join("?" for _ in by_id)
    cur = await db._db.execute(
        "SELECT id, application_id, event_type, scheduled_at, recorded_at, corrects_event_id, payload "  # noqa: S608 - marks are '?'
        f"FROM application_events WHERE application_id IN ({marks}) "
        "AND (event_type = 'interview_scheduled' OR corrects_event_id IS NOT NULL) ORDER BY id ASC",
        tuple(by_id),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    superseded = {r["corrects_event_id"] for r in rows if r["corrects_event_id"] is not None}
    interviews = [
        r for r in rows
        if r["event_type"] == "interview_scheduled" and r["scheduled_at"] and r["id"] not in superseded
    ]  # every live interview is its own event (multi-round); a correction supersedes only its target
    return by_id, interviews


def interview_fields(row: dict[str, Any]) -> dict[str, Any]:
    """What a live interview row says, cleaned: ``round`` (as recorded, capped), ``link`` (http(s) only,
    else ''), ``location`` (the owner's text, else the link) and ``duration_minutes`` (clamped 5..480)."""
    p = _payload(row.get("payload"))
    link = _http_link(p.get("link"))
    return {
        "round": _text(p.get("round"), _ROUND_MAX), "link": link,
        "location": _text(p.get("location"), _LOCATION_MAX) or link,
        "duration_minutes": _duration(p.get("duration_minutes")),
    }


async def collect_events(
    db: JobDatabase, user_id: str, now: datetime, application_id: Optional[int] = None
) -> list[CalEvent]:
    """The owner's upcoming interviews and follow-ups (see :func:`live_interviews` for scoping)."""
    by_id, interviews = await live_interviews(db, user_id, application_id)
    if not by_id:
        return []
    lo = now - timedelta(days=settings.CALENDAR_FEED_LOOKBACK_DAYS)
    hi = now + timedelta(seconds=settings.APPLICATION_SCHEDULED_AT_MAX_FUTURE_SECONDS)
    base = settings.SITE_BASE_URL
    out: list[CalEvent] = []
    alarms = _interview_alarms()
    for r in interviews:
        app_id = int(r["application_id"])
        start = _parse_dt(r["scheduled_at"])
        if start is None or not (lo <= start <= hi):
            continue
        f = interview_fields(r)
        round_ = f["round"].replace("-", " ").replace("_", " ")
        round_ = round_[:1].upper() + round_[1:]  # "hiring-manager" reads "Hiring manager"
        link, location = f["link"], f["location"]
        summary = f"Interview: {_title(by_id[app_id])}" + (f" ({round_})" if round_ else "")
        out.append(CalEvent(
            uid=f"app-{app_id}-ev-{r['id']}@job360.uk", summary=summary,
            url=f"{base}/applications/{app_id}", stamp=_parse_dt(r["recorded_at"]) or now, start=start,
            duration_minutes=f["duration_minutes"], description=link, location=location,
            alarms=alarms,
        ))
    hour = settings.CALENDAR_ALARM_FOLLOW_UP_HOUR
    follow_up_alarms = (f"PT{hour}H",) if 0 <= hour <= 23 else ()
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
            url=f"{base}/applications/{app_id}", stamp=now, day=day, alarms=follow_up_alarms,
        ))
    def _when(e: CalEvent) -> datetime:
        if e.start is not None:
            return e.start
        return datetime.combine(e.day or now.date(), datetime.min.time(), tzinfo=timezone.utc)

    out.sort(key=lambda e: (_when(e), e.uid))
    return out
