"""What is due now? - the user's settings drive their OWN assistant on a schedule.

Owner decision 2026-10-23. The clock lives at the AI vendor: the user's scheduled task (for
example a Claude cloud task) wakes every ``SCHEDULE_TICK_HOURS`` and asks MCP ``whats_due``.
Job360 has no AI and no worker; it runs nothing. It only answers which duties are due and
stamps them when the assistant says one is done (``mark_done``, migration 0082).

Three duties, each named by the recipe that runs it (``SCHEDULE_DUTY_RECIPES``):

* ``hunt`` (recipe ``hunt``) - ``assistant_settings.hunt_every_hours`` is set and at least that
  many hours have passed since the last hunt (never done = due).
* ``inbox`` (recipe ``daily``) - ``preferences.daily_check`` is ``auto`` (or the older
  ``scheduled``) and ``preferences.check_every`` hours have passed (empty = 24, the daily
  recipe's own "once a day"). ``ask`` / ``paused`` / empty never run on a schedule: nobody is
  there to say yes.
* ``reach`` (recipe ``reach``, owner 2026-10-24: the PEOPLE hunt) - open applications (considering or
  applied) brought or applied in the last ``REACH_WINDOW_DAYS`` that have no assistant-added person and
  no ``reach:<id>`` stamp, newest first, at most ``REACH_MAX_PER_RUN``; listed as ``application_ids``.
  An application whose only people are still an invite pending on LinkedIn is not listed.
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
    # Applications the people hunt should work on this run (newest first, capped).
    reach_ids: list[int] = field(default_factory=list)


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
    """The ``whats_due`` answer. Pure. ``due`` is in run order: inbox, hunt, reach, then batch
    (a hunt fills the queue the batch then works; reach finds people for new applications)."""
    due: list[dict[str, Any]] = []
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
    if f.reach_ids and f.hunt_every_hours is not None:  # people hunting rides the user's hunt schedule
        due.append({"duty": "reach", "recipe": settings.SCHEDULE_DUTY_RECIPES["reach"],
                    "reason": "new_applications", "application_ids": list(f.reach_ids)})
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


# ── Storage (migration 0082) ─────────────────────────────────────────────────


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


def reach_key(application_id: int) -> str:
    """The stamp slot for one application the people hunt has worked."""
    return f"reach:{application_id}"


async def mark(
    db: JobDatabase, user_id: str, duty: str, now: datetime, application_ids: Optional[list[int]] = None
) -> None:
    """Stamp ``duty`` (and the tick) as done ``now``. A slot: replaced, never appended.
    ``duty`` must already be one of ``SCHEDULE_DUTIES`` (the caller validates). ``reach`` also stamps
    each of ``application_ids`` (already checked to be this user's)."""
    stamp = now.astimezone(timezone.utc).isoformat()
    names = [duty, TICK] + [reach_key(a) for a in (application_ids or [])]
    for name in names:
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


async def owned_application_ids(db: JobDatabase, user_id: str, ids: list[int]) -> set[int]:
    """Which of ``ids`` are this user's applications (rule #12)."""
    if not ids:
        return set()
    marks = ",".join("?" for _ in ids)
    cur = await db._db.execute(
        f"SELECT id FROM applications WHERE user_id = ? AND id IN ({marks})",  # noqa: S608 - placeholders only
        (user_id, *ids),
    )
    return {int(dict(r)["id"]) for r in await cur.fetchall()}


async def _reach_ids(db: JobDatabase, user_id: str, now: datetime) -> list[int]:
    """Open applications for the people hunt this run (see the module docstring), newest first."""
    since = (now - timedelta(days=settings.REACH_WINDOW_DAYS)).astimezone(timezone.utc).isoformat()
    cur = await db._db.execute(
        "SELECT a.id FROM applications a WHERE a.user_id = ? AND a.status IN ('considering', 'applied') "
        "AND (a.created_at >= ? OR EXISTS (SELECT 1 FROM application_events e WHERE e.application_id = a.id "
        "     AND e.user_id = a.user_id AND e.event_type = 'applied' AND e.occurred_at >= ?)) "
        "AND NOT EXISTS (SELECT 1 FROM application_contacts c WHERE c.application_id = a.id "
        "     AND c.user_id = a.user_id AND c.added_by <> 'web') "
        "AND NOT EXISTS (SELECT 1 FROM assistant_duty_runs r WHERE r.user_id = a.user_id "
        "     AND r.duty = 'reach:' || a.id) "
        "ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
        (user_id, since, since, settings.REACH_MAX_PER_RUN * 4),
    )
    candidates = [int(dict(r)["id"]) for r in await cur.fetchall()]
    pending_only = await _pending_only(db, user_id, candidates)
    return [a for a in candidates if a not in pending_only][: settings.REACH_MAX_PER_RUN]


async def _pending_only(db: JobDatabase, user_id: str, app_ids: list[int]) -> set[int]:
    """Applications whose people (all of them) are an invite still pending on LinkedIn."""
    if not app_ids:
        return set()
    marks = ",".join("?" for _ in app_ids)
    # Only "?" placeholders are formatted into this query.
    sql = (
        "SELECT c.application_id, (SELECT e.value FROM contact_edits e WHERE e.contact_id = c.id "  # noqa: S608
        "  AND e.user_id = c.user_id AND e.field = 'linkedin_state' ORDER BY e.id DESC LIMIT 1) AS state "
        f"FROM application_contacts c WHERE c.user_id = ? AND c.application_id IN ({marks})"
    )
    cur = await db._db.execute(sql, (user_id, *app_ids))
    states: dict[int, list[str]] = {}
    for r in await cur.fetchall():
        row = dict(r)
        states.setdefault(int(row["application_id"]), []).append(row["state"] or "")
    return {a for a, s in states.items() if s and all(v == "pending" for v in s)}


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
        reach_ids=await _reach_ids(db, user_id, now),
    )
