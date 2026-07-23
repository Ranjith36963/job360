"""Ghost-listing state machine (Pillar 3 Batch 1).

Per docs/research/pillar_3_batch_1.md §3, 18-22% of job postings are ghosts
(listings that stay visible but are no longer being recruited for). This
module provides the pure-function state transition logic; the integration
with scrape loops lives in `src.main` via the `JobDatabase.update_last_seen`
and `mark_missed_for_source` helpers.

Key design decisions (fixed per the research blueprint):
  - A single miss is noise — don't act until 2+ misses.
  - 2+ misses with 12h absence → POSSIBLY_STALE (soft flag).
  - 3+ misses with 24h absence → LIKELY_STALE (exclude from 24h bucket).
  - CONFIRMED_EXPIRED is sticky (set by a later direct-URL verification step;
    not demoted back to ACTIVE even if the job reappears).

Callers MUST gate `mark_missed_for_source` behind a scrape-completeness
check (rolling-average result count / canary presence) — a failed scrape
should never be interpreted as jobs disappearing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class StalenessState(str, Enum):
    ACTIVE = "active"
    POSSIBLY_STALE = "possibly_stale"
    LIKELY_STALE = "likely_stale"
    CONFIRMED_EXPIRED = "confirmed_expired"


def transition(consecutive_misses: int, age_hours_since_last_seen: float) -> StalenessState:
    """Pure function: misses + absence duration → next state.

    Does NOT handle CONFIRMED_EXPIRED (that is set by a direct URL check,
    not by absence). The caller should short-circuit to CONFIRMED_EXPIRED
    before calling this if it has such a signal.
    """
    if consecutive_misses >= 3 and age_hours_since_last_seen >= 24:
        return StalenessState.LIKELY_STALE
    if consecutive_misses >= 2 and age_hours_since_last_seen >= 12:
        return StalenessState.POSSIBLY_STALE
    return StalenessState.ACTIVE


def should_exclude_from_24h(state: StalenessState) -> bool:
    """Jobs in LIKELY_STALE or CONFIRMED_EXPIRED must NOT appear in the 24h bucket."""
    return state in (StalenessState.LIKELY_STALE, StalenessState.CONFIRMED_EXPIRED)


def evaluate_job_state(
    row: dict[str, Any],
    now: Optional[datetime] = None,
) -> StalenessState:
    """Compute the state of a single DB row.

    Expects the row to have `consecutive_misses`, `last_seen_at`, and
    `staleness_state`. Treats CONFIRMED_EXPIRED as sticky.
    """
    current = row.get("staleness_state") or "active"
    if current == StalenessState.CONFIRMED_EXPIRED.value:
        return StalenessState.CONFIRMED_EXPIRED

    misses = int(row.get("consecutive_misses") or 0)
    last_seen = row.get("last_seen_at")
    if not last_seen:
        # M6: a NULL last_seen_at used to short-circuit to ACTIVE, so a job missed
        # many times but lacking that timestamp could NEVER transition to stale —
        # the nightly sweep silently excluded it forever. Fall back to
        # first_seen_at as the age proxy; if there's no timestamp at all, let
        # consecutive_misses alone decide (a job missed 3+ times is stale
        # regardless of when we last saw it). A brand-new job has a recent
        # first_seen_at, so it correctly stays ACTIVE.
        last_seen = row.get("first_seen_at") or row.get("first_seen")
        if not last_seen:
            if misses >= 3:
                return StalenessState.LIKELY_STALE
            if misses >= 2:
                return StalenessState.POSSIBLY_STALE
            return StalenessState.ACTIVE

    now = now or datetime.now(timezone.utc)
    try:
        last_seen_dt = datetime.fromisoformat(last_seen)
        if last_seen_dt.tzinfo is None:
            last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
        age_hours = (now - last_seen_dt).total_seconds() / 3600
    except (ValueError, TypeError):
        age_hours = 0.0

    return transition(misses, age_hours)
