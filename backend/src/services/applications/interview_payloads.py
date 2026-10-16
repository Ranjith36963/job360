"""Feedback loop slice 4 (interview side): the checked payloads of the three new
note events (``interview_session``, ``process_friction``, ``assessment``), the
optional ``offer`` figures and the ``silence_confirmed`` note.

Job360 stores and counts; the assistant reads transcripts, judges and tags. This
module only VALIDATES. Wired in the record_event ROUTE (never mcp_server.py), so
the MCP tool inherits it. Every refusal lists ALL problems, then one valid
example (event_examples.refusal). career-ops references (file:line) are in the
slice contract; ``NOT IN REFERENCE`` marks a bound that is ours.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications import event_examples
from src.services.applications.spine import SpineError
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase

INTERVIEW_EVENT_TYPES = ("interview_session", "process_friction", "assessment")

# weekly-digest.mjs:58
ROUNDS = ("screen", "hiring-manager", "technical", "system-design", "behavioral", "onsite", "final")
SOURCES = ("debrief", "practice", "mock", "manual")  # sessions/README.md:30
DEFAULT_SOURCE = "debrief"
INPUT_SOURCES = ("transcript", "recall")  # debrief.md:38, :54
GAP_STATUSES = ("gap", "solid", "strong")  # debrief.md:91
SIGNALS = ("scope_ambiguity", "defensive_closure", "evaluator_gap", "process_signals", "protected_grounds")
OFFER_PERIODS = ("year", "month", "week", "day", "hour")

ROLE_MAX = 120
TAGS_MAX_ITEMS = 40
TAG_MAX_CHARS = 60
GAPS_MAX_ITEMS = 20
GAP_TOPIC_MAX = 120
SIGNALS_MAX_ITEMS = 5
QUOTE_MAX = 300
FRICTION_REASON_MAX = 200
PLATFORM_MAX = 80
SUBJECT_MAX = 120
STALE_NOTE_MAX = 300
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _str_field(
    payload: dict[str, Any], key: str, max_chars: int, problems: list[str], *, required: bool = False
) -> Optional[str]:
    if key not in payload or payload[key] is None:
        if required:
            problems.append(f"{key} is required")
        return None
    value = payload[key]
    if not isinstance(value, str):
        problems.append(f"{key} must be a string")
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_chars:
        problems.append(f"{key} must be 1 to {max_chars} characters")
        return None
    return cleaned


def _optional_int(payload: dict[str, Any], key: str, problems: list[str]) -> None:
    if key in payload and not _is_int(payload[key]):
        problems.append(f"{key} must be a whole number")


def _tags(payload: dict[str, Any], problems: list[str]) -> None:
    if "competency_tags" not in payload:
        return
    raw = payload["competency_tags"]
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        problems.append("competency_tags must be a list of strings")
        return
    if len(raw) > TAGS_MAX_ITEMS:
        problems.append(f"competency_tags has more than {TAGS_MAX_ITEMS} items")
        return
    cleaned = [re.sub(r"\s+", "-", t.strip().lower()) for t in raw]
    cleaned = [t for t in cleaned if t]  # duplicates stay: one per answer (weekly-digest.mjs:132-136)
    if any(len(t) > TAG_MAX_CHARS for t in cleaned):
        problems.append(f"each competency tag must be 1 to {TAG_MAX_CHARS} characters")
        return
    payload["competency_tags"] = cleaned


def _gaps(payload: dict[str, Any], problems: list[str]) -> None:
    if "gaps" not in payload:
        return
    raw = payload["gaps"]
    if not isinstance(raw, list):
        problems.append("gaps must be a list of {topic, status}")
        return
    if len(raw) > GAPS_MAX_ITEMS:
        problems.append(f"gaps has more than {GAPS_MAX_ITEMS} items")
        return
    cleaned: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"topic", "status"}:
            problems.append(f"gaps[{i}] must be exactly {{topic, status}}")
            continue
        topic = item["topic"]
        if not isinstance(topic, str) or not topic.strip() or len(topic.strip()) > GAP_TOPIC_MAX:
            problems.append(f"gaps[{i}].topic must be 1 to {GAP_TOPIC_MAX} characters")
            continue
        if item["status"] not in GAP_STATUSES:
            problems.append(f"gaps[{i}].status must be one of {list(GAP_STATUSES)}")
            continue
        cleaned.append({"topic": topic.strip(), "status": item["status"]})
    if len(cleaned) == len(raw):
        payload["gaps"] = cleaned


def _signals(payload: dict[str, Any], problems: list[str]) -> None:
    if "signals" not in payload:
        return
    raw = payload["signals"]
    if not isinstance(raw, list):
        problems.append("signals must be a list of {signal, quote}")
        return
    if len(raw) > SIGNALS_MAX_ITEMS:
        problems.append(f"signals has more than {SIGNALS_MAX_ITEMS} items")
        return
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"signal", "quote"}:
            problems.append(f"signals[{i}] must be exactly {{signal, quote}}")
            continue
        if item["signal"] not in SIGNALS:
            problems.append(f"signals[{i}].signal must be one of {list(SIGNALS)}")
        elif item["signal"] in seen:
            problems.append(f"signal {item['signal']} appears twice; mark each signal at most once per session")
        else:
            seen.add(item["signal"])
        quote = item["quote"]
        if not isinstance(quote, str) or not quote.strip() or len(quote) > QUOTE_MAX:
            problems.append(f"signals[{i}].quote must be 1 to {QUOTE_MAX} characters (the verbatim words)")


def _session(payload: dict[str, Any], problems: list[str]) -> None:
    if payload.get("round") is None:
        problems.append("round is required")
    elif payload["round"] not in ROUNDS:
        problems.append(f"round must be one of {list(ROUNDS)}")
    raw_date = payload.get("date")
    if raw_date is None:
        problems.append("date is required (YYYY-MM-DD)")
    else:
        parsed: Optional[date] = None
        if isinstance(raw_date, str) and _DATE.match(raw_date):
            try:
                parsed = date.fromisoformat(raw_date)
            except ValueError:
                parsed = None
        if parsed is None:
            problems.append("date must be a real calendar date, YYYY-MM-DD")
        elif parsed > datetime.now(timezone.utc).date() + timedelta(days=1):
            problems.append("date must not be in the future")
    if "source" in payload and payload["source"] not in SOURCES:
        problems.append(f"source must be one of {list(SOURCES)}")
    payload.setdefault("source", DEFAULT_SOURCE)
    if "input_source" in payload and payload["input_source"] not in INPUT_SOURCES:
        problems.append(f"input_source must be one of {list(INPUT_SOURCES)}")
    if "interviewer_role" in payload:
        role = _str_field(payload, "interviewer_role", ROLE_MAX, problems)
        if role is not None:
            payload["interviewer_role"] = role
    _tags(payload, problems)
    _gaps(payload, problems)
    _signals(payload, problems)
    _optional_int(payload, "interview_event_id", problems)
    _optional_int(payload, "transcript_artifact_id", problems)


def _friction(payload: dict[str, Any], problems: list[str]) -> None:
    if set(payload) - {"reason"}:
        problems.append("process_friction is company level only: payload takes only `reason`")
    if "reason" in payload:
        raw = payload["reason"]
        if not isinstance(raw, str):
            problems.append("reason must be a string")
        elif len(raw.strip()) > FRICTION_REASON_MAX:
            problems.append(f"reason must be at most {FRICTION_REASON_MAX} characters")
        elif raw.strip():
            payload["reason"] = raw.strip()
        else:
            del payload["reason"]


def _assessment(payload: dict[str, Any], problems: list[str]) -> None:
    for key, cap in (("platform", PLATFORM_MAX), ("subject", SUBJECT_MAX)):
        cleaned = _str_field(payload, key, cap, problems, required=True)
        if cleaned is not None:
            payload[key] = cleaned
    for key in ("threshold_pct", "score_pct"):
        value = payload.get(key)
        if value is None:
            payload[key] = None  # unknown, never 0 (assessment-log.mjs:55; hard rule #29)
        elif not _is_number(value) or not 0 <= value <= 100:
            problems.append(f"{key} must be a number from 0 to 100 (leave it out if unknown)")
    if payload.get("stale_note") is not None:
        if not isinstance(payload["stale_note"], str) or len(payload["stale_note"]) > STALE_NOTE_MAX:
            problems.append(f"stale_note must be a string of at most {STALE_NOTE_MAX} characters")


def _offer(payload: dict[str, Any], problems: list[str]) -> None:
    keys = ("offer_amount", "offer_currency", "offer_period")
    if not any(k in payload for k in keys):
        return
    for key in keys:
        if payload.get(key) is None:
            problems.append(f"{key} is required when any offer figure is given")
    amount = payload.get("offer_amount")
    if amount is not None and (not _is_number(amount) or amount <= 0):
        problems.append("offer_amount must be a number greater than 0")
    currency = payload.get("offer_currency")
    if currency is not None and not (isinstance(currency, str) and _CURRENCY.match(currency)):
        problems.append("offer_currency must be a 3-letter upper-case code such as GBP")
    period = payload.get("offer_period")
    if period is not None and period not in OFFER_PERIODS:
        problems.append(f"offer_period must be one of {list(OFFER_PERIODS)}")


def validate(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Pure: check one event's payload, return the cleaned payload (422 listing every problem)."""
    problems: list[str] = []
    if event_type == "interview_session":
        _session(payload, problems)
    elif event_type == "process_friction":
        _friction(payload, problems)
    elif event_type == "assessment":
        _assessment(payload, problems)
    elif event_type == "offer":
        _offer(payload, problems)
    elif event_type == "note" and "silence_confirmed" in payload:
        if payload["silence_confirmed"] is not True:
            problems.append("silence_confirmed must be true")
        _optional_int(payload, "interview_event_id", problems)
    else:
        return payload
    if problems:
        raise event_examples.refusal(event_type, problems)
    return payload


async def _is_interview_event(db: JobDatabase, user_id: str, application_id: int, event_id: Any) -> bool:
    cur = await db._db.execute(
        "SELECT 1 FROM application_events WHERE id = ? AND user_id = ? AND application_id = ? "
        "AND event_type IN ('interview_done', 'interview_scheduled')",
        (event_id, user_id, application_id),
    )
    return await cur.fetchone() is not None


async def check_refs(
    db: JobDatabase,
    user_id: str,
    application_id: int,
    event_type: str,
    payload: dict[str, Any],
    corrects_event_id: Optional[int],
) -> None:
    """The rules that need the database (422, with the example when the type has one)."""
    is_session = event_type == "interview_session"
    is_silence = event_type == "note" and "silence_confirmed" in payload
    if not (is_session or is_silence):
        return
    try:
        if "interview_event_id" in payload and not await _is_interview_event(
            db, user_id, application_id, payload["interview_event_id"]
        ):
            raise SpineError(
                422, "interview_event_id must be an interview_done or interview_scheduled event on this application"
            )
        if not is_session:
            return
        if "transcript_artifact_id" in payload:
            cur = await db._db.execute(
                "SELECT 1 FROM application_artifacts WHERE id = ? AND user_id = ? AND application_id = ? "
                "AND kind = 'interview_transcript'",
                (payload["transcript_artifact_id"], user_id, application_id),
            )
            if await cur.fetchone() is None:
                raise SpineError(
                    422, "transcript_artifact_id must be an interview_transcript artifact on this application"
                )
        cur = await db._db.execute(
            "SELECT e.id, e.payload FROM application_events e "
            "WHERE e.user_id = ? AND e.application_id = ? AND e.event_type = 'interview_session' "
            "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
            "AND e.id <> ? ORDER BY e.id",
            # a correction replaces its target, so only the corrected event is exempt
            (user_id, application_id, corrects_event_id if corrects_event_id is not None else -1),
        )
        wanted = (payload["round"], payload["date"], payload.get("source", DEFAULT_SOURCE))
        for row in await cur.fetchall():
            try:
                other = json.loads(row["payload"] or "{}")
            except ValueError:
                continue
            if isinstance(other, dict) and (
                other.get("round"), other.get("date"), other.get("source", DEFAULT_SOURCE)
            ) == wanted:
                raise SpineError(
                    422,
                    f"this interview_session is already recorded as event {int(row['id'])}; "
                    f"to change it record_event(..., corrects_event_id={int(row['id'])})",
                )
    except SpineError as exc:
        raise event_examples.with_example(event_type, exc) from None


def log_recorded(user_id: str, application_id: int, event_id: int, event_type: str, payload: dict[str, Any]) -> None:
    """One audit line, counts only - never the quotes, topics, reasons or roles."""
    get_audit_logger().info(
        "interview_event_recorded",
        extra={
            "event": "interview_event_recorded", "user_id": safe_log_value(user_id),
            "application_id": safe_log_value(application_id), "event_id": safe_log_value(event_id),
            "event_type": safe_log_value(event_type, max_len=40),
            "tags": len(payload.get("competency_tags") or []), "gaps": len(payload.get("gaps") or []),
            "signals": len(payload.get("signals") or []),
        },
    )
