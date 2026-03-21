"""Reminder computation for the application pipeline."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta


# Days until next reminder for each outreach stage
OUTREACH_INTERVALS = {
    "applied": 7,
    "outreach_week1": 7,
    "outreach_week2": 7,
}

# Stages where no further reminders are needed
_TERMINAL_STAGES = {"offer", "rejected", "withdrawn"}


def compute_next_reminder(stage, from_date: str) -> str | None:
    """Compute the next reminder ISO timestamp, or None for terminal/interview stages."""
    stage_value = stage.value if hasattr(stage, "value") else str(stage)
    if stage_value in _TERMINAL_STAGES or stage_value == "interview":
        return None
    days = OUTREACH_INTERVALS.get(stage_value)
    if days is None:
        return None
    dt = datetime.fromisoformat(from_date)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(days=days)).isoformat()


def get_pending_reminders(applications: list[dict]) -> list[dict]:
    """Filter applications where next_reminder <= now."""
    now = datetime.now(timezone.utc).isoformat()
    return [
        app for app in applications
        if app.get("next_reminder") and app["next_reminder"] <= now
    ]


def format_reminder_message(app: dict) -> str:
    """Format a human-readable reminder string for an application."""
    job_id = app.get("job_id", "?")
    status = app.get("status", "unknown")
    contact = app.get("contact_name", "")
    notes = app.get("notes", "")
    msg = f"Job #{job_id} — Stage: {status}"
    if contact:
        msg += f" — Contact: {contact}"
    if notes:
        msg += f" — Notes: {notes}"
    return msg
