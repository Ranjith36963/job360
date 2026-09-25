"""The one line at the top of an application (owner ask, 2026-09-20): what
is the next thing to do here. Read off STORED state only — status, whether a
fit verdict exists, whether a CV version exists, whether a receipt exists,
the interview datetime, whether a lesson was flagged. It is a state machine
over the record, never a judgement of the job (VISION rule 4), and the same
function feeds the web header and ``get_application`` so an agent reading
the record sees the same next step the human does.

``code`` values: judge_fit, write_cv, apply, record_receipt, wait, respond,
schedule, interview, record_outcome, await_outcome, decide, lesson, closed,
follow_up, none.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

_CLOSED = ("rejected", "withdrawn", "ghosted")


def _parse_interview_at(value: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp (a trailing "Z" is accepted as UTC).
    Returns ``None`` when it cannot be parsed — the caller then treats the
    interview as upcoming rather than failing the whole response."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def next_step(
    *,
    status: str,
    has_fit: bool,
    cv_versions: int,
    receipts: int,
    interview_at: Optional[str],
    has_lesson: bool,
    follow_up_due: bool = False,
    now: datetime | None = None,
) -> dict[str, str]:
    """``{"code": …, "label": …}`` — ``code`` is a closed vocabulary an agent
    can branch on; ``label`` is the sentence the web shows. ``now`` defaults
    to the real clock (UTC) and only exists so tests can pin it.

    ``follow_up_due`` (owner decision, 2026-09-25) wins over every other code
    on an OPEN application — a follow-up date that has arrived is always the
    next thing to do, whatever stage the application is otherwise at. Never
    fires for a closed status: the caller (``list_applications`` /
    ``get_application_detail``) already excludes
    ``APPLICATION_FOLLOW_UP_CLOSED_STATUSES`` from ``follow_up_due`` itself,
    and the check here is a second, explicit guard against a closed status
    ever showing "follow up" instead of its own "closed"/"lesson" code."""
    if now is None:
        now = datetime.now(timezone.utc)
    if follow_up_due and status not in _CLOSED:
        return {"code": "follow_up", "label": "Follow-up due — chase them or record what happened"}
    if status == "considering":
        if not has_fit:
            return {"code": "judge_fit", "label": "No fit judged yet — ask your agent to judge it"}
        if cv_versions == 0:
            return {"code": "write_cv", "label": "Fit judged — no CV for this job yet"}
        return {"code": "apply", "label": "CV ready — apply, then mark it applied"}
    if status == "applied":
        if receipts == 0:
            return {"code": "record_receipt", "label": "Applied — record what you sent"}
        return {"code": "wait", "label": "Applied — waiting to hear back"}
    if status == "replied":
        return {"code": "respond", "label": "They replied — respond or record the outcome"}
    if status in ("interview_requested", "interview_scheduled"):
        if interview_at:
            parsed = _parse_interview_at(interview_at)
            if parsed is None or parsed > now:
                return {"code": "interview", "label": "Interview booked — prepare for it"}
            return {"code": "record_outcome", "label": "Interview date has passed — record how it went"}
        return {"code": "schedule", "label": "Interview requested — add the date when you have it"}
    if status == "interview_done":
        return {"code": "await_outcome", "label": "Interview done — waiting for the outcome"}
    if status == "offer":
        return {"code": "decide", "label": "Offer received — decide"}
    if status in _CLOSED:
        if not has_lesson:
            return {"code": "lesson", "label": "Closed — flag a lesson for next time"}
        return {"code": "closed", "label": "Closed"}
    return {"code": "none", "label": ""}


def next_step_for_detail(detail: Mapping[str, Any]) -> dict[str, str]:
    """Derive the inputs from the ``get_application`` shape."""
    events = detail.get("events") or []
    artifacts = detail.get("artifacts") or []
    return next_step(
        status=str(detail.get("status") or ""),
        has_fit=detail.get("fit") is not None,
        cv_versions=sum(1 for a in artifacts if a.get("kind") == "cv"),
        receipts=len(detail.get("receipts") or []),
        interview_at=detail.get("interview_at"),
        has_lesson=any(e.get("event_type") == "lesson" and not e.get("superseded") for e in events),
        follow_up_due=bool(detail.get("follow_up_due")),
    )
