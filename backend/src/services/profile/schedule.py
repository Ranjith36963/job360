"""What is due now? - the user's settings drive their OWN assistant on a schedule.

Owner decision 2026-10-23. The clock lives at the AI vendor: the user's scheduled task (for
example a Claude cloud task) wakes every ``SCHEDULE_TICK_HOURS`` and asks MCP ``whats_due``.
Job360 has no AI and no worker; it runs nothing. It only answers which duties are due and
stamps them when the assistant says one is done (``mark_done``, migration 0081).

Three duties, each named by the recipe that runs it (``SCHEDULE_DUTY_RECIPES``):

* ``hunt`` (recipe ``hunt``) - ``assistant_settings.hunt_every_hours`` is set and at least that
  many hours have passed since the last hunt (never done = due).
* ``inbox`` (recipe ``daily``) - ``preferences.daily_check`` is ``auto`` (or the older
  ``scheduled``) and ``preferences.check_every`` hours have passed (empty = 24, the daily
  recipe's own "once a day"). ``ask`` / ``paused`` / empty never run on a schedule: nobody is
  there to say yes.
* ``batch`` (recipe ``batch``) - the queue holds open applications AND the assistant may apply
  on its own (``submit_mode`` ``auto_when_sure`` or a live auto grant) AND not paused AND the
  daily and weekly limits have room AND now is inside ``apply_hours``. No interval: it is due
  while all of that holds; ``check_submit`` still gates every single submit.

Due is ELAPSED time since last done >= the interval, never fixed slots, so a late or skipped
run heals itself. The answer carries closed values only - never instructions, never a setting
change, never a place to look for jobs (VISION rule 4). Consent: nothing here widens what a run
may do; a scheduled prompt is never an approval to submit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.profile import assistant_settings as rules

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

TICK = "tick"
INBOX_AUTO_MODES = frozenset({"auto", "scheduled"})
INBOX_DEFAULT_HOURS = 24
_EVERY_RE = re.compile(r"^(\d{1,3})h$")
# Why a batch is not due (closed set, first that applies).
BATCH_HOLDS: tuple[str, ...] = (
    "paused", "not_allowed", "queue_empty", "daily_cap_reached", "weekly_cap_reached", "outside_hours",
)


@dataclass(frozen=True)
class DueFacts:
    """Everything :func:`due_now` reads, already loaded. Pure input."""

    hunt_every_hours: Optional[int]
    inbox_mode: str
    check_every: str
    paused: bool
    apply_on_own: bool
    queued: int
    daily_left: Optional[int]
    weekly_left: Optional[int]
    inside_hours: bool
    stamps: dict[str, datetime] = field(default_factory=dict)


def inbox_every_hours(mode: str, check_every: str) -> Optional[int]:
    """Hours between Gmail checks when the inbox runs on its own, else ``None``."""
    if (mode or "") not in INBOX_AUTO_MODES:
        return None
    m = _EVERY_RE.match(check_every or "")
    return int(m.group(1)) if m and int(m.group(1)) > 0 else INBOX_DEFAULT_HOURS


def batch_hold(f: DueFacts) -> Optional[str]:
    """The first reason the queue may not be worked on its own now (``BATCH_HOLDS``), or ``None``."""
    if f.paused:
        return "paused"
    if not f.apply_on_own:
        return "not_allowed"
    if f.queued <= 0:
        return "queue_empty"
    if f.daily_left is not None and f.daily_left <= 0:
        return "daily_cap_reached"
    if f.weekly_left is not None and f.weekly_left <= 0:
        return "weekly_cap_reached"
    if not f.inside_hours:
        return "outside_hours"
    return None


def _iso(when: Optional[datetime]) -> Optional[str]:
    return None if when is None else when.astimezone(timezone.utc).isoformat(timespec="seconds")


def due_now(f: DueFacts, now: datetime) -> dict[str, Any]:
    """The ``whats_due`` answer. Pure. ``due`` is in run order: inbox, then hunt, then batch
    (a hunt fills the queue the batch then works)."""
    due: list[dict[str, str]] = []
    next_due: dict[str, Optional[str]] = {}
    intervals = {"inbox": inbox_every_hours(f.inbox_mode, f.check_every), "hunt": f.hunt_every_hours}
    for duty in ("inbox", "hunt"):
        every = intervals[duty]
        if every is None:
            continue
        last = f.stamps.get(duty)
        nxt = now if last is None else last + timedelta(hours=every)
        next_due[duty] = _iso(nxt)
        if nxt <= now:
            due.append({"duty": duty, "recipe": settings.SCHEDULE_DUTY_RECIPES[duty],
                        "reason": "never_done" if last is None else "interval_elapsed"})
    hold = batch_hold(f)
    if hold is None:
        due.append({"duty": "batch", "recipe": settings.SCHEDULE_DUTY_RECIPES["batch"], "reason": "queue_waiting"})
    return {
        "due": due,
        "nothing_due": not due,
        "next_due_at": next_due,
        "batch_hold": hold,
        "last_done_at": {d: _iso(f.stamps.get(d)) for d in settings.SCHEDULE_DUTIES},
        "last_tick_at": _iso(f.stamps.get(TICK)),
        "expected_every_hours": settings.SCHEDULE_TICK_HOURS,
        "checked_at": _iso(now),
    }


# ── Storage (migration 0081) ─────────────────────────────────────────────────


async def load_stamps(db: JobDatabase, user_id: str) -> dict[str, datetime]:
    """``{duty: done_at}`` for this user (rule #12: scoped by ``user_id``). Unreadable rows are skipped."""
    cur = await db._db.execute("SELECT duty, done_at FROM assistant_duty_runs WHERE user_id = ?", (user_id,))
    out: dict[str, datetime] = {}
    for row in await cur.fetchall():
        r = dict(row)
        try:
            at = datetime.fromisoformat(str(r["done_at"]))
        except ValueError:
            continue
        out[str(r["duty"])] = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return out


async def mark(db: JobDatabase, user_id: str, duty: str, now: datetime) -> None:
    """Stamp ``duty`` (and the tick) as done ``now``. A slot: replaced, never appended.
    ``duty`` must already be one of ``SCHEDULE_DUTIES`` (the caller validates)."""
    stamp = now.astimezone(timezone.utc).isoformat()
    for name in (duty, TICK):
        await db._db.execute(
            "INSERT INTO assistant_duty_runs (user_id, duty, done_at) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at",
            (user_id, name, stamp),
        )
    await db._db.commit()


async def _open_queue_count(db: JobDatabase, user_id: str) -> int:
    """Applications in the batch queue that are still ``considering`` (as the batch list shows it)."""
    from src.services.applications import spine  # noqa: PLC0415 - spine imports the profile package

    queued = await spine.queued_at_by_app(db, user_id)
    if not queued:
        return 0
    marks = ",".join("?" for _ in queued)
    cur = await db._db.execute(
        f"SELECT COUNT(*) FROM applications WHERE user_id = ? AND status = 'considering' AND id IN ({marks})",  # noqa: S608
        (user_id, *queued),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def load_facts(db: JobDatabase, user_id: str, now: datetime) -> DueFacts:
    """Read everything :func:`due_now` needs for ONE user (rule #12)."""
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    profile = load_profile(user_id) or UserProfile()
    cfg = rules.effective(profile.assistant_settings)
    counts = await rules.load_submit_counts(db, user_id, now)
    apply_on_own = cfg.submit_mode == rules.AUTO_SUBMIT_MODE or rules.grant_active(cfg, counts, now)
    weekly_left = None if cfg.weekly_cap is None else max(0, cfg.weekly_cap - counts.submitted_this_week)
    hours = cfg.apply_hours
    inside = hours is None or rules._in_hours(now.astimezone(rules._zone(counts.user_tz)), hours)
    return DueFacts(
        hunt_every_hours=cfg.hunt_every_hours,
        inbox_mode=profile.preferences.daily_check or "",
        check_every=profile.preferences.check_every or "",
        paused=rules.is_paused(cfg.paused_until, now),
        apply_on_own=apply_on_own,
        queued=await _open_queue_count(db, user_id),
        daily_left=rules.daily_left(cfg, counts),
        weekly_left=weekly_left,
        inside_hours=inside,
        stamps=await load_stamps(db, user_id),
    )
