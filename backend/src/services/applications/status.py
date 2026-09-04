"""Status cache + the one place the two vocabularies meet (spec R4).

``applications.status`` is a CACHE of the last status-bearing event — never
the source of truth, always rebuildable by replaying the event log. This
module has zero DB access so both the writer (``spine.py``) and the frozen
test (``test_status_is_rebuildable_from_the_event_log``) can call the exact
same function.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

# `brought` is the one status event whose name is NOT the status; every other
# status event's name IS the status (spec R7).
_EVENT_TO_STATUS: dict[str, str] = {"brought": "considering"}

# R4 — the ONLY place the spine's `status` vocabulary and the legacy Kanban
# `stage` vocabulary meet. `considering` is deliberately ABSENT: a
# `considering` row has no legacy stage yet, so `stage` keeps whatever it
# already holds (its `applied` default for a freshly-born row) rather than
# being overwritten.
STATUS_TO_STAGE: dict[str, str] = {
    "applied": "applied",
    "replied": "applied",
    "interview_requested": "interview",
    "interview_scheduled": "interview",
    "interview_done": "interview",
    "offer": "offer",
    "rejected": "rejected",
    "withdrawn": "rejected",
    "ghosted": "ghosted",
}


def status_for_event(event_type: str) -> Optional[str]:
    """The status a STATUS-bearing event moves the application to, or None
    for a non-status (note-family) event — which must never touch the cache."""
    from src.core import settings

    if event_type not in settings.APPLICATION_STATUS_EVENT_TYPES:
        return None
    return _EVENT_TO_STATUS.get(event_type, event_type)


def stage_for_status(status: str) -> Optional[str]:
    """The legacy `stage` a given spine `status` projects to, or None when
    the status has no legacy equivalent yet (`considering`) — the caller must
    then leave `stage` untouched."""
    return STATUS_TO_STAGE.get(status)


def replay_status(
    events: Sequence[Mapping[str, Any]],
    *,
    default: str = "considering",
) -> str:
    """Recompute the cached status by replaying the WHOLE event log.

    Ordered by ``(recorded_at, id)`` — the order events were actually
    RECORDED, not ``occurred_at`` (which may be backdated and would make "the
    last thing that happened" mean something different from "the last thing
    we learned"). A correcting event's TARGET (named by ``corrects_event_id``)
    is skipped entirely, so a mis-recorded status event never contributes.

    Pure function, no DB — the frozen test calls this directly and compares
    against the stored column.
    """
    superseded_ids = {
        e["corrects_event_id"] for e in events if e.get("corrects_event_id") is not None
    }
    ordered = sorted(events, key=lambda e: (e.get("recorded_at") or "", e.get("id") or 0))
    status = default
    for e in ordered:
        if e.get("id") in superseded_ids:
            continue
        mapped = status_for_event(e.get("event_type", ""))
        if mapped is not None:
            status = mapped
    return status
