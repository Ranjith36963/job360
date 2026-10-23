"""Feature 9 (G073): an assistant that is not sure what an email means SUGGESTS an outcome.

``outcome_suggested`` {event_type, quote, why} is a NOTE event: it never moves
``applications.status``. The email it came from is kept in ``payload.email``,
NOT in the ``source_*`` columns: the real event the user's Accept records later
carries the same message id, and ``(application_id, source_message_id)`` is the
duplicate key (partial UNIQUE index, 0041), so a suggestion holding it would
block the real event. ``outcome_dismissed`` {suggestion_event_id} is the
user's Ignore. Open vs closed is derived at read time from append-only rows;
nothing is updated or deleted. Job360 reads no email: it stores what it is told.

Audit lines carry ids and closed codes only, never the quote, why or email fields.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import event_examples, spine
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

EVENT_TYPES = ("outcome_suggested", "outcome_dismissed")
SUGGESTION_KEYS = frozenset({"event_type", "quote", "why"})


def check_suggestion_payload(payload: Any) -> dict[str, Any]:
    """Every problem at once, then one valid example (``event_examples.refusal``)."""
    problems: list[str] = []
    data = payload if isinstance(payload, dict) else {}
    if not isinstance(payload, dict) or set(data) != SUGGESTION_KEYS:
        problems.append("outcome_suggested needs payload keys ['event_type', 'quote', 'why'] and nothing else")
    allowed = list(settings.APPLICATION_OUTCOME_SUGGESTION_EVENT_TYPES)
    event_type = data.get("event_type")
    if event_type not in allowed:
        problems.append(f"event_type must be one of {allowed}")
    quote = spine.normalize_quote(data.get("quote")) if isinstance(data.get("quote"), str) else ""
    cap = settings.APPLICATION_EVENT_SOURCE_QUOTE_MAX_CHARS
    if not quote:
        problems.append("quote must be the email's deciding sentence as text, not empty")
    elif spine._has_control_chars(quote):
        problems.append("quote must not contain control characters")
    elif len(quote) > cap:
        problems.append(f"quote exceeds APPLICATION_EVENT_SOURCE_QUOTE_MAX_CHARS ({cap} chars)")
    why = spine.normalize_quote(data.get("why")) if isinstance(data.get("why"), str) else ""
    why_cap = settings.APPLICATION_OUTCOME_SUGGESTION_WHY_MAX_CHARS
    if not why:
        problems.append("why must say, as text, why you are not sure")
    elif spine._has_control_chars(why):
        problems.append("why must not contain control characters")
    elif len(why) > why_cap:
        problems.append(f"why exceeds APPLICATION_OUTCOME_SUGGESTION_WHY_MAX_CHARS ({why_cap} chars)")
    if problems:
        raise event_examples.refusal("outcome_suggested", problems)
    return {"event_type": event_type, "quote": quote, "why": why}


def _log(user: CurrentUser, application_id: int, event_type: str, *, result: str,
         reason: Optional[str] = None, event_id: Any = None, suggested_type: Optional[str] = None,
         suggestion_event_id: Optional[int] = None, status: Optional[int] = None) -> None:
    extra: dict[str, Any] = {
        "event": "outcome_suggestion_refused" if result == "refused" else "outcome_suggestion",
        "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
        "application_id": application_id, "event_id": event_id,
        "event_type": safe_log_value(event_type), "suggested_type": suggested_type,
        "suggestion_event_id": suggestion_event_id, "result": result, "reason": reason,
    }
    if status is not None:
        extra["status"] = status
    logger = get_audit_logger()
    (logger.warning if result == "refused" else logger.info)(extra["event"], extra=extra)


def _refuse(user: CurrentUser, application_id: int, event_type: str, code: int, reason: str,
            detail: str) -> SpineError:
    _log(user, application_id, event_type, result="refused", reason=reason, status=code)
    return SpineError(code, detail)


async def _suggestions(db: JobDatabase, user_id: Any, application_id: int) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, event_type, detail, occurred_at, recorded_at, recorded_by, scheduled_at, payload "
        "FROM application_events WHERE user_id = ? AND application_id = ? AND event_type = 'outcome_suggested'",
        (user_id, application_id),
    )
    return [dict(r) for r in await cur.fetchall()]


def _loads(raw: Any) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _email_id(payload: dict[str, Any]) -> str:
    email = payload.get("email")
    return str(email.get("message_id") or "") if isinstance(email, dict) else ""


async def _dismissed_ids(db: JobDatabase, user_id: Any) -> set[int]:
    cur = await db._db.execute(
        "SELECT payload FROM application_events WHERE user_id = ? AND event_type = 'outcome_dismissed'",
        (user_id,),
    )
    out: set[int] = set()
    for r in await cur.fetchall():
        sid = _loads(dict(r)["payload"]).get("suggestion_event_id")
        if isinstance(sid, int) and not isinstance(sid, bool):
            out.add(sid)
    return out


async def record(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, payload: dict[str, Any],
    source: Optional[dict[str, Any]], *, detail: str, occurred_at: str, follow_up_on_set: bool,
    corrects_event_id: Optional[int],
) -> dict[str, Any]:
    """The route has checked ownership, payload size, occurred_at and scheduled_at."""
    if corrects_event_id is not None or follow_up_on_set:
        raise _refuse(user, application_id, event_type, 422, "takes_no_correction",
                      f"{event_type} takes no corrects_event_id or follow_up_on")
    if event_type == "outcome_dismissed":
        return await _dismiss(db, user, application_id, payload, source, detail, occurred_at)
    if source is None:
        raise _refuse(user, application_id, event_type, 422, "no_source",
                      "outcome_suggested needs source (the email it came from)")
    if source["quote"]:
        raise _refuse(user, application_id, event_type, 422, "quote_in_source",
                      "outcome_suggested takes the quote in payload.quote, not source.quote")
    try:
        stored = check_suggestion_payload(payload)
    except SpineError as exc:
        raise _refuse(user, application_id, event_type, exc.status_code, "bad_payload", exc.detail) from exc
    actor = actor_for(user)
    existing = await spine._event_by_source_message_id(db, application_id, source["message_id"])
    if existing is not None:
        _log(user, application_id, event_type, result="duplicate", reason="message_recorded",
             event_id=existing["id"], suggested_type=stored["event_type"])
        return await spine._duplicate_event_result(db, application_id, actor, existing, "outcome_suggested")
    for row in await _suggestions(db, user.id, application_id):
        if _email_id(_loads(row["payload"])) == source["message_id"]:
            _log(user, application_id, event_type, result="duplicate", reason="already_suggested",
                 event_id=row["id"], suggested_type=stored["event_type"])
            return await spine._duplicate_event_result(db, application_id, actor, row, None)
    stored["email"] = {
        "kind": source["kind"], "message_id": source["message_id"], "thread_id": source["thread_id"],
        "sender": source["sender"], "subject": source["subject"],
        "received_at": source["received_at"] or None,
    }
    result = await spine.append_event(
        db, user_id=user.id, application_id=application_id, event_type=event_type, detail=detail,
        payload=stored, occurred_at=occurred_at, recorded_by=actor,
    )
    _log(user, application_id, event_type, result="ok", event_id=result.get("event_id"),
         suggested_type=stored["event_type"])
    return result


async def _dismiss(
    db: JobDatabase, user: CurrentUser, application_id: int, payload: dict[str, Any],
    source: Optional[dict[str, Any]], detail: str, occurred_at: str,
) -> dict[str, Any]:
    et = "outcome_dismissed"
    if source is not None:
        raise _refuse(user, application_id, et, 422, "takes_no_source", "outcome_dismissed takes no source")
    sid = payload.get("suggestion_event_id") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or set(payload) != {"suggestion_event_id"} \
            or not isinstance(sid, int) or isinstance(sid, bool):
        raise _refuse(user, application_id, et, 422, "bad_payload",
                      'outcome_dismissed needs payload {"suggestion_event_id": <id>} and nothing else')
    suggestion = next((r for r in await _suggestions(db, user.id, application_id) if r["id"] == sid), None)
    if suggestion is None:
        raise _refuse(user, application_id, et, 422, "not_a_suggestion",
                      "suggestion_event_id must be an outcome_suggested event on this application")
    if sid in await _dismissed_ids(db, user.id):
        raise _refuse(user, application_id, et, 409, "already_ignored", "already ignored")
    mid = _email_id(_loads(suggestion["payload"]))
    if mid and await spine._event_by_source_message_id(db, application_id, mid) is not None:
        raise _refuse(user, application_id, et, 409, "already_recorded", "already recorded")
    result = await spine.append_event(
        db, user_id=user.id, application_id=application_id, event_type=et, detail=detail,
        payload={"suggestion_event_id": sid}, occurred_at=occurred_at, recorded_by=actor_for(user),
    )
    _log(user, application_id, et, result="ok", event_id=result.get("event_id"), suggestion_event_id=sid)
    return result


async def open_suggestions(db: JobDatabase, user_id: Any, limit: int) -> tuple[list[dict[str, Any]], bool]:
    """Open = not ignored and its email not yet recorded on that application (newest first)."""
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.payload, e.occurred_at, e.recorded_at, e.recorded_by, "
        "a.job_title, a.job_company, a.status FROM application_events e "
        "JOIN applications a ON a.id = e.application_id "
        "WHERE e.user_id = ? AND a.user_id = ? AND e.event_type = 'outcome_suggested' ORDER BY e.id DESC",
        (user_id, user_id),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    dismissed = await _dismissed_ids(db, user_id)
    cur = await db._db.execute(
        "SELECT application_id, source_message_id FROM application_events "
        "WHERE user_id = ? AND source_message_id <> ''",
        (user_id,),
    )
    recorded = {(r["application_id"], r["source_message_id"]) for r in await cur.fetchall()}
    items: list[dict[str, Any]] = []
    for r in rows:
        data = _loads(r["payload"])
        raw_email = data.get("email")
        email: dict[str, Any] = raw_email if isinstance(raw_email, dict) else {}
        if r["id"] in dismissed or (r["application_id"], _email_id(data)) in recorded:
            continue
        items.append({
            "event_id": r["id"], "application_id": r["application_id"], "job_title": r["job_title"],
            "job_company": r["job_company"], "status": r["status"],
            "event_type": str(data.get("event_type") or ""), "quote": str(data.get("quote") or ""),
            "why": str(data.get("why") or ""), "occurred_at": r["occurred_at"],
            "recorded_at": r["recorded_at"], "recorded_by": r["recorded_by"],
            "email": {
                "kind": email.get("kind") or "email", "message_id": email.get("message_id") or "",
                "thread_id": email.get("thread_id") or None, "sender": email.get("sender") or "",
                "subject": email.get("subject") or "", "received_at": email.get("received_at") or None,
                "quote": None,
            },
        })
    return items[:limit], len(items) > limit
