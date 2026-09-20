"""The one line at the top of an application (owner ask, 2026-09-20): what
is the next thing to do here. Read off STORED state only — status, whether a
fit verdict exists, whether a CV version exists, whether a receipt exists,
the interview datetime, whether a lesson was flagged. It is a state machine
over the record, never a judgement of the job (VISION rule 4), and the same
function feeds the web header and ``get_application`` so an agent reading
the record sees the same next step the human does.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

_CLOSED = ("rejected", "withdrawn", "ghosted")


def next_step(
    *,
    status: str,
    has_fit: bool,
    cv_versions: int,
    receipts: int,
    interview_at: Optional[str],
    has_lesson: bool,
) -> dict[str, str]:
    """``{"code": …, "label": …}`` — ``code`` is a closed vocabulary an agent
    can branch on; ``label`` is the sentence the web shows."""
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
            return {"code": "interview", "label": f"Interview {interview_at}"}
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
    )
