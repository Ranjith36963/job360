"""Automatic employer emails (feedback loop slice 2; owner decisions #5, #6, #8, #9, #10, #14, #15).

Job360 never sends mail: the user's assistant sends from the user's own Gmail. Job360 stores the
send (``checkin_sent`` / ``feedback_asked`` / ``feedback_followup_sent``, exact text in the payload),
GATES the next one (``decide``, behind ``check_auto_email``) and WATCHES (``due_for_user``,
``violations``, both shown by doctor).

Everything here is pure except ``load_facts`` / ``due_for_user`` / ``violations`` (reads). Nothing is
written. Logs carry ids and closed codes only - never an address, a company or the email text.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications.stats import _SIMULATED_SQL
from src.services.applications.status import _event_when
from src.services.profile import assistant_settings as settings_rules
from src.utils.logger import get_logger, safe_log_value

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase

log = get_logger(__name__)

KINDS: tuple[str, ...] = ("checkin", "feedback_request", "feedback_followup")
# The event each kind records once it is sent.
SENT_EVENT_FOR_KIND: dict[str, str] = {
    "checkin": "checkin_sent",
    "feedback_request": "feedback_asked",
    "feedback_followup": "feedback_followup_sent",
}
SENT_EVENT_TYPES: tuple[str, ...] = tuple(SENT_EVENT_FOR_KIND.values())
SKIPPED_EVENT_TYPE = "auto_email_skipped"
DUE_MAX = 200
VIOLATIONS_MAX = 50
RULES: tuple[str, ...] = (
    "sent_twice", "checkin_too_early", "asked_without_rejection", "followup_too_early",
)
DECISIONS: tuple[str, ...] = ("send", "ask", "stop")
REASONS: tuple[str, ...] = (
    "paused", "unknown_kind", "already_sent", "already_skipped", "daily_cap_reached", "not_due",
    "application_moved_on", "not_rejected", "they_replied", "no_feedback_request", "auto_off", "auto_on",
)
_STATUS_TYPES = tuple(settings.APPLICATION_STATUS_EVENT_TYPES)
_RELEVANT_TYPES = tuple(
    dict.fromkeys((*_STATUS_TYPES, *SENT_EVENT_TYPES, SKIPPED_EVENT_TYPE))
)


@dataclass(frozen=True)
class AutoEvent:
    """One non-retracted event, reduced to what the gate reads."""

    id: int
    event_type: str
    when: Optional[datetime]
    has_source: bool = False
    kind: str = ""  # an auto_email_skipped event's payload.kind


@dataclass(frozen=True)
class AutoEmailFacts:
    """What :func:`decide` needs about ONE application (events oldest first, retracted ones dropped)."""

    application_id: int
    status: str = ""
    events: tuple[AutoEvent, ...] = field(default_factory=tuple)
    sent_today: int = 0


@dataclass(frozen=True)
class AutoVerdict:
    decision: str  # send | ask | stop
    reason: str
    detail: str


def _newest(events: tuple[AutoEvent, ...], types: set[str] | frozenset[str]) -> Optional[AutoEvent]:
    found: Optional[AutoEvent] = None
    for e in events:
        if e.event_type in types:
            found = e  # events are oldest first, so the last match is the newest
    return found


def _day(when: datetime) -> str:
    return when.astimezone(timezone.utc).date().isoformat()


def due_since(kind: str, facts: AutoEmailFacts) -> Optional[datetime]:
    """When this kind became (or becomes) due, or None when its starting event does not exist."""
    if kind == "checkin":
        applied = _newest(facts.events, {"applied"})
        if applied is None or applied.when is None:
            return None
        return applied.when + timedelta(days=settings.AUTO_EMAIL_CHECKIN_DAYS)
    if kind == "feedback_request":
        last = _newest(facts.events, set(_STATUS_TYPES))
        if last is None or last.event_type != "rejected":
            return None
        return last.when
    if kind == "feedback_followup":
        asked = _newest(facts.events, {"feedback_asked"})
        if asked is None or asked.when is None:
            return None
        return asked.when + timedelta(days=settings.AUTO_EMAIL_FOLLOWUP_DAYS)
    return None


def decide(
    kind: str, facts: AutoEmailFacts, cfg: settings_rules.EffectiveSettings, now: datetime
) -> AutoVerdict:
    """May the assistant send this email now? First match wins:

    paused, unknown kind, already sent, already skipped, daily cap, then the kind's own timing rule, and
    last the owner's switch: off = ``ask`` (a draft only), on = ``send``."""
    if settings_rules.is_paused(cfg.paused_until, now):
        return AutoVerdict("stop", "paused", "Your assistant is paused. Send nothing.")
    if kind not in KINDS:
        return AutoVerdict("stop", "unknown_kind", f"Unknown kind. Use one of: {', '.join(KINDS)}.")
    if _newest(facts.events, {SENT_EVENT_FOR_KIND[kind]}) is not None:
        return AutoVerdict(
            "stop", "already_sent", "This email was already sent for this application. Never send it twice."
        )
    if any(e.event_type == SKIPPED_EVENT_TYPE and e.kind == kind for e in facts.events):
        return AutoVerdict("stop", "already_skipped", "This email was already skipped for this application.")
    if facts.sent_today >= settings.AUTO_EMAILS_MAX_PER_DAY:
        return AutoVerdict("stop", "daily_cap_reached", "The daily limit of automatic emails is reached. Try tomorrow.")
    if kind == "checkin":
        verdict = _checkin_timing(facts, now)
    elif kind == "feedback_request":
        verdict = _feedback_request_timing(facts)
    else:
        verdict = _followup_timing(facts, now)
    if verdict is not None:
        return verdict
    if cfg.auto_employer_emails:
        return AutoVerdict(
            "send", "auto_on", "Automatic employer emails are on. Write it in the owner's name, same thread."
        )
    return AutoVerdict(
        "ask", "auto_off", "Automatic employer emails are off: save a Gmail draft and ask the owner; do not send."
    )


def _checkin_timing(facts: AutoEmailFacts, now: datetime) -> Optional[AutoVerdict]:
    applied = _newest(facts.events, {"applied"})
    if applied is None or applied.when is None:
        return AutoVerdict("stop", "not_due", "No applied date is recorded, so a check-in is not due.")
    if facts.status != "applied":
        return AutoVerdict("stop", "application_moved_on", "The application is no longer just applied.")
    after = [
        e for e in facts.events
        if e.event_type in _STATUS_TYPES and e.event_type not in ("brought", "applied") and e.id > applied.id
    ]
    if after:
        return AutoVerdict("stop", "application_moved_on", "There is news on this application since it was applied.")
    due = (applied.when + timedelta(days=settings.AUTO_EMAIL_CHECKIN_DAYS))
    if now < due:
        return AutoVerdict("stop", "not_due", f"A check-in is due from {_day(due)}.")
    return None


def _feedback_request_timing(facts: AutoEmailFacts) -> Optional[AutoVerdict]:
    last = _newest(facts.events, set(_STATUS_TYPES))
    if last is None or last.event_type != "rejected":
        return AutoVerdict("stop", "not_rejected", "The application is not rejected, so no feedback request.")
    return None


def _followup_timing(facts: AutoEmailFacts, now: datetime) -> Optional[AutoVerdict]:
    asked = _newest(facts.events, {"feedback_asked"})
    if asked is None:
        return AutoVerdict(
            "stop", "no_feedback_request", "No feedback request was sent, so there is nothing to follow up."
        )
    if any(e.has_source and e.id > asked.id for e in facts.events):
        return AutoVerdict("stop", "they_replied", "The employer has written since the feedback request.")
    if asked.when is None:
        return AutoVerdict("stop", "not_due", "The feedback request has no usable date.")
    due = asked.when + timedelta(days=settings.AUTO_EMAIL_FOLLOWUP_DAYS)
    if now < due:
        return AutoVerdict("stop", "not_due", f"A follow-up is due from {_day(due)}.")
    return None


# ── Loading ──────────────────────────────────────────────────────────────────


def _payload_kind(raw: Any) -> str:
    try:
        data = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        return ""
    value = data.get("kind") if isinstance(data, dict) else ""
    return value if isinstance(value, str) else ""


async def count_sent_today(db: JobDatabase, user_id: str, now: datetime) -> int:
    marks = ", ".join("?" for _ in SENT_EVENT_TYPES)
    return await settings_rules.count_on_user_today(
        db, user_id, now,
        "SELECT e.recorded_at FROM application_events e "  # noqa: S608 - placeholders only
        f"WHERE e.user_id = ? AND e.event_type IN ({marks}) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        "AND e.recorded_at >= ?",
        (user_id, *SENT_EVENT_TYPES),
    )


async def _events_by_app(
    db: JobDatabase, user_id: str, app_ids: list[int], *, types: tuple[str, ...] = _RELEVANT_TYPES
) -> dict[int, list[AutoEvent]]:
    """Non-retracted events (plus any event carrying an email source) per application, oldest first."""
    out: dict[int, list[AutoEvent]] = {a: [] for a in app_ids}
    if not app_ids:
        return out
    app_marks = ", ".join("?" for _ in app_ids)
    type_marks = ", ".join("?" for _ in types)
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.event_type, e.occurred_at, e.recorded_at, "  # noqa: S608 - placeholders only
        "e.source_message_id, e.payload FROM application_events e "
        f"WHERE e.user_id = ? AND e.application_id IN ({app_marks}) "
        f"AND (e.event_type IN ({type_marks}) OR e.source_message_id <> '') "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        "ORDER BY e.id",
        (user_id, *app_ids, *types),
    )
    for row in await cur.fetchall():
        r = dict(row)
        out[int(r["application_id"])].append(
            AutoEvent(
                id=int(r["id"]),
                event_type=str(r["event_type"]),
                when=_event_when({"occurred_at": r["occurred_at"], "recorded_at": r["recorded_at"]}),
                has_source=bool(r["source_message_id"]),
                kind=_payload_kind(r["payload"]) if r["event_type"] == SKIPPED_EVENT_TYPE else "",
            )
        )
    return out


async def load_facts(
    db: JobDatabase, user_id: str, application_id: int, now: Optional[datetime] = None
) -> Optional[AutoEmailFacts]:
    """The facts for one of the user's applications, or None when it is not theirs (scoped by user)."""
    when = now or datetime.now(timezone.utc)
    cur = await db._db.execute(
        "SELECT status FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    events = (await _events_by_app(db, user_id, [application_id]))[application_id]
    return AutoEmailFacts(
        application_id=application_id,
        status=str(dict(row)["status"] or ""),
        events=tuple(events),
        sent_today=await count_sent_today(db, user_id, when),
    )


async def _candidate_apps(db: JobDatabase, user_id: str) -> list[tuple[int, str]]:
    """Applications that could owe an email: applied, rejected, or with a feedback request out.
    Simulations are excluded. Capped at DUE_MAX (newest first)."""
    cur = await db._db.execute(
        "SELECT a.id, a.status FROM applications a "  # noqa: S608 - constants only
        "WHERE a.user_id = ? AND (a.status IN ('applied', 'rejected') OR EXISTS ("
        "SELECT 1 FROM application_events f WHERE f.application_id = a.id AND f.user_id = ? "
        "AND f.event_type = 'feedback_asked')) "
        f"AND NOT {_SIMULATED_SQL.format(app='a.id')} ORDER BY a.id DESC LIMIT ?",
        (user_id, user_id, user_id, settings.SIMULATION_EVENT_TYPE, DUE_MAX),
    )
    return [(int(dict(r)["id"]), str(dict(r)["status"] or "")) for r in await cur.fetchall()]


async def due_for_user(
    db: JobDatabase, user_id: str, cfg: Optional[settings_rules.EffectiveSettings] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """``[{application_id, kind, due_since}]``: every email whose gate is not a ``stop`` right now
    (so ``send`` or ``ask``), at most DUE_MAX. Nothing is written."""
    when = now or datetime.now(timezone.utc)
    if cfg is None:
        from src.services.profile.storage import load_profile  # noqa: PLC0415

        profile = load_profile(user_id)
        cfg = settings_rules.effective(profile.assistant_settings) if profile else settings_rules.effective(
            _default_settings()
        )
    candidates = await _candidate_apps(db, user_id)
    events = await _events_by_app(db, user_id, [a for a, _ in candidates])
    sent_today = await count_sent_today(db, user_id, when)
    due: list[dict[str, Any]] = []
    for app_id, status in candidates:
        facts = AutoEmailFacts(app_id, status, tuple(events[app_id]), sent_today)
        for kind in KINDS:
            verdict = decide(kind, facts, cfg, when)
            if verdict.decision == "stop":
                continue
            since = due_since(kind, facts)
            due.append({"application_id": app_id, "kind": kind, "due_since": since.isoformat() if since else ""})
    log.info(
        "auto_emails_due",
        extra={"event": "auto_emails_due", "user_id": safe_log_value(user_id), "count": len(due)},
    )
    return due[:DUE_MAX]


def _default_settings() -> Any:
    from src.services.profile.models import AssistantSettings  # noqa: PLC0415

    return AssistantSettings()


async def violations(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """Sends that broke a guardrail, computed from the events alone: ``[{event_id, application_id, rule}]``.

    ``sent_twice``, ``checkin_too_early``, ``asked_without_rejection``, ``followup_too_early``. A send
    while the application was paused is not knowable from events, so it is never reported."""
    marks = ", ".join("?" for _ in SENT_EVENT_TYPES)
    cur = await db._db.execute(
        "SELECT DISTINCT e.application_id FROM application_events e "  # noqa: S608 - placeholders only
        f"WHERE e.user_id = ? AND e.event_type IN ({marks}) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        f"AND NOT {_SIMULATED_SQL.format(app='e.application_id')}",
        (user_id, *SENT_EVENT_TYPES, user_id, settings.SIMULATION_EVENT_TYPE),
    )
    app_ids = sorted(int(dict(r)["application_id"]) for r in await cur.fetchall())
    events = await _events_by_app(db, user_id, app_ids)
    found: list[dict[str, Any]] = []
    checkin_gap = timedelta(days=settings.AUTO_EMAIL_CHECKIN_DAYS)
    followup_gap = timedelta(days=settings.AUTO_EMAIL_FOLLOWUP_DAYS)
    for app_id in app_ids:
        seen: set[str] = set()
        for ev in events[app_id]:
            if ev.event_type not in SENT_EVENT_TYPES:
                continue
            before = [e for e in events[app_id] if e.id < ev.id]

            def flag(rule: str, ev: AutoEvent = ev, app_id: int = app_id) -> None:
                found.append({"event_id": ev.id, "application_id": app_id, "rule": rule})

            if ev.event_type in seen:
                flag("sent_twice")
            seen.add(ev.event_type)
            if ev.event_type == "checkin_sent":
                applied = _newest(tuple(before), {"applied"})
                if applied is None or applied.when is None or ev.when is None or ev.when - applied.when < checkin_gap:
                    flag("checkin_too_early")
            elif ev.event_type == "feedback_asked":
                if _newest(tuple(before), {"rejected"}) is None:
                    flag("asked_without_rejection")
            else:
                asked = _newest(tuple(before), {"feedback_asked"})
                if asked is None or asked.when is None or ev.when is None or ev.when - asked.when < followup_gap:
                    flag("followup_too_early")
    found.sort(key=lambda v: (v["event_id"], v["rule"]))
    if found:
        log.warning(
            "auto_email_violations",
            extra={"event": "auto_email_violations", "user_id": safe_log_value(user_id), "count": len(found)},
        )
    return found[:VIOLATIONS_MAX]


async def recent_sends(db: JobDatabase, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """The newest non-retracted sends: ``[{event_id, application_id, company, kind, at}]``. The company
    name, never the address or the text."""
    marks = ", ".join("?" for _ in SENT_EVENT_TYPES)
    kind_for = {v: k for k, v in SENT_EVENT_FOR_KIND.items()}
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.event_type, e.recorded_at, a.job_company "  # noqa: S608
        "FROM application_events e JOIN applications a ON a.id = e.application_id "
        f"WHERE e.user_id = ? AND a.user_id = ? AND e.event_type IN ({marks}) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        f"AND NOT {_SIMULATED_SQL.format(app='a.id')} ORDER BY e.id DESC LIMIT ?",
        (user_id, user_id, *SENT_EVENT_TYPES, user_id, settings.SIMULATION_EVENT_TYPE, limit),
    )
    return [
        {
            "event_id": int(dict(r)["id"]), "application_id": int(dict(r)["application_id"]),
            "company": str(dict(r)["job_company"] or ""), "kind": kind_for[str(dict(r)["event_type"])],
            "at": str(dict(r)["recorded_at"]),
        }
        for r in await cur.fetchall()
    ]
