"""One complete, valid example payload per validated event type, and the one
422 message every such event reports its problems in: ALL of them at once, then
the example. An assistant that had to learn a payload one refusal at a time
(four calls for ``form_filled``, 2026-10-14) fixes it in one.

The examples are pinned valid by ``tests/test_kit_event_errors.py`` - a payload
that the validator refuses is not an example."""

from __future__ import annotations

import json
from typing import Any

from src.services.applications.spine import SpineError

EVENT_EXAMPLES: dict[str, dict[str, Any]] = {
    "form_filled": {
        "form_url": "https://jobs.example.com/apply/123",
        "fields_count": 2,
        "answers": [
            {"question": "Right to work in the UK?", "answer": "Yes", "source": "profile", "key": "work_authorization"},
            {"question": "Why this role?", "answer": "I like the product.", "source": "written"},
        ],
    },
    "form_swept": {"fields_checked": 12, "fixed": 1, "notes": "fixed the phone number format"},
    "form_checked": {
        "questions_count": 12, "answered": 11, "missing": 1,
        "answers": [
            {"question": "Salary expectation?", "key": "salary.GB", "source": "memory", "needs_confirmation": True},
        ],
    },
    "cv_seen": {"artifact_id": 7, "sha256": "<sha256 from get_application_kit>", "where": "chat"},
    "submit_approved": {"artifact_id": 7, "sha256": "<sha256 from get_application_kit>", "where": "chat"},
    "submit_declined": {"where": "chat"},
    "autofill_set": {"mode": "deny"},
    "hold_released": {"reason": "done"},
    "submit_attempted": {},
    "submit_resolved": {"outcome": "not_sent"},
    "email_drafted": {
        "to": "jobs@example.com", "subject": "Application: Data Engineer - Ada Lovelace",
        "attachments": [{"artifact_id": 7, "sha256": "9f2c" + "0" * 60}], "gmail_draft_id": "r-123456789",
    },
    "site_account": {"host": "jobs.example.com"},
    "account_needed": {"host": "jobs.example.com"},
    "blocked": {
        "reason": "captcha", "step": "submit form", "page_host": "jobs.example.com", "detail": "CAPTCHA before submit",
    },
    "unblocked": {"resolution": "retried"},
    "proof_text": {"text": "Thank you, your application has been received.", "page_host": "jobs.example.com"},
    # Feedback loop slice 4 (interview side); checked by interview_payloads.validate.
    "interview_session": {
        "round": "technical", "date": "2026-10-14", "source": "debrief", "input_source": "recall",
        "interviewer_role": "engineering manager", "competency_tags": ["system-design", "sql"],
        "gaps": [{"topic": "query planning", "status": "gap"}],
        "signals": [{"signal": "scope_ambiguity", "quote": "We will see what the role becomes."}],
    },
    "process_friction": {"reason": "rescheduled twice"},
    "assessment": {
        "platform": "HackerRank", "subject": "SQL", "threshold_pct": 70, "score_pct": 82,
        "stale_note": "questions looked out of date",
    },
}


def refusal(event_type: str, problems: list[str]) -> SpineError:
    """The 422 for a payload: every problem, then one complete valid example."""
    lines = [f"{event_type} payload has {len(problems)} problem{'s' if len(problems) != 1 else ''}:"]
    lines += [f"- {p}" for p in problems]
    example = EVENT_EXAMPLES.get(event_type)
    if example is not None:
        lines.append(f"Valid example payload: {json.dumps(example)}")
    return SpineError(422, "\n".join(lines))


def with_example(event_type: str, exc: SpineError) -> SpineError:
    """A 422 raised mid-validation (a rule that needs the database) gets the example too, once."""
    if exc.status_code != 422 or event_type not in EVENT_EXAMPLES or "Valid example payload:" in exc.detail:
        return exc
    return refusal(event_type, [exc.detail])


def collect(problems: list[str], fn: Any, *args: Any, prefix: str = "") -> Any:
    """Run one validator; a 422 becomes a problem in the list instead of an exception."""
    try:
        return fn(*args)
    except SpineError as exc:
        if exc.status_code != 422:
            raise
        problems.append(prefix + exc.detail)
        return None
