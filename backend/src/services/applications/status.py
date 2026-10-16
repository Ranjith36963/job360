"""Status cache + the one place the two vocabularies meet (spec R4).

``applications.status`` is a CACHE derived from the status-bearing events
("furthest stage wins", see ``replay_status``) — never the source of truth,
always rebuildable by replaying the event log. This module has zero DB
access so both the writer (``spine.py``) and the frozen
test (``test_status_is_rebuildable_from_the_event_log``) can call the exact
same function.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
    "hired": "offer",
    "offer_declined": "rejected",
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


# "Furthest stage wins" (owner decision 2026-10-14). The progress ladder, in
# order: a progress event moves the status only when it is AT LEAST as far up
# this ladder as the furthest stage already reached. `brought` maps to
# `considering`, the bottom rung.
PROGRESS_RANK: dict[str, int] = {
    "considering": 0,
    "applied": 1,
    "replied": 2,
    "interview_requested": 3,
    "interview_scheduled": 4,
    "interview_done": 5,
    "offer": 6,
    "hired": 7,
}
# Endings the employer or the user declared. Always applied (the latest wins).
TERMINAL_STATUSES = frozenset({"rejected", "withdrawn", "offer_declined"})
GHOSTED = "ghosted"
# `ghosted` (silence) only lands while the application is at or below this
# rung: silence after an interview is still news, silence after an OFFER is
# not a fact about the employer (the user withdraws or the offer is declined).
GHOSTED_MAX_RANK = PROGRESS_RANK["interview_done"]


def progress_rank(status: str) -> Optional[int]:
    """The ladder rung of a progress status, or None for rejected/withdrawn/ghosted."""
    return PROGRESS_RANK.get(status)


def _when(raw: Any) -> Optional[datetime]:
    """``occurred_at`` as an aware datetime (a naive value reads as UTC), or None."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _event_when(e: Mapping[str, Any]) -> Optional[datetime]:
    """When an event happened: ``occurred_at``, falling back to ``recorded_at``
    when it is empty or unparseable (so the ending/after-ending comparison
    never silently loses an event)."""
    return _when(e.get("occurred_at")) or _when(e.get("recorded_at"))


def replay_status(
    events: Sequence[Mapping[str, Any]],
    *,
    default: str = "considering",
) -> str:
    """Recompute the cached status by replaying the WHOLE event log.

    Walked in ``(recorded_at, id)`` order — the order events were RECORDED. A
    correcting event's TARGET (named by ``corrects_event_id``) is skipped
    entirely, so a mis-recorded status event never contributes.

    "Furthest stage wins" (owner decision 2026-10-14) — the status never
    slides backwards because an earlier fact was recorded late:

    * A progress event (considering < applied < replied < interview_requested
      < interview_scheduled < interview_done < offer) moves the status only
      when its rung is >= the furthest rung reached so far. From ``ghosted``
      it must be strictly > that rung: a reply after silence moves it
      forward, a late-recorded ``applied`` does not undo the silence.
    * ``rejected`` / ``withdrawn`` always apply; the latest of them wins.
    * After a ``rejected`` / ``withdrawn``, a progress event reopens the
      application only when its ``occurred_at`` is LATER than that ending's
      ``occurred_at`` (news that happened after the ending, not a late
      record of something before it). Reopening resets the furthest rung to
      the reopening event's rung.
    * ``ghosted`` applies over considering..interview_done; it never
      overrides an offer or a rejected/withdrawn.
    * The ONLY way backwards is ``corrects_event_id``: the corrected event is
      skipped as if it never happened. The correcting event itself is
      replayed like any other event — it does NOT force the status, so
      correcting the date of ``applied`` never undoes a later interview.

    Pure function, no DB — the frozen test calls this directly and compares
    against the stored column. Needs ``event_type``, ``id``, ``recorded_at``,
    ``occurred_at`` and ``corrects_event_id`` on each event.
    """
    superseded_ids = {
        e["corrects_event_id"] for e in events if e.get("corrects_event_id") is not None
    }
    ordered = sorted(events, key=lambda e: (e.get("recorded_at") or "", e.get("id") or 0))
    status = default
    best = PROGRESS_RANK.get(default, 0)
    ended_at: Optional[datetime] = None  # occurred_at of the ending in force, if any
    for e in ordered:
        if e.get("id") in superseded_ids:
            continue
        mapped = status_for_event(e.get("event_type", ""))
        if mapped is None:
            continue
        rank = PROGRESS_RANK.get(mapped)
        if mapped in TERMINAL_STATUSES:
            status, ended_at = mapped, _event_when(e)
            continue
        if mapped == GHOSTED:
            if status not in TERMINAL_STATUSES and best <= GHOSTED_MAX_RANK:
                status = GHOSTED
            continue
        if rank is None:  # pragma: no cover — every other status event is a progress rung
            continue
        if status in TERMINAL_STATUSES:
            occurred = _event_when(e)
            if ended_at is not None and occurred is not None and occurred > ended_at:
                status, best, ended_at = mapped, rank, None
            continue
        if status == GHOSTED:
            # Silence is only broken by news BEYOND the furthest rung — a late
            # record of the application itself does not undo `ghosted`.
            if rank > best:
                status, best = mapped, rank
            continue
        if rank >= best:
            status, best = mapped, rank
    return status
