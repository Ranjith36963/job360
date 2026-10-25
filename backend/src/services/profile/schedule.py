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
* ``reach`` (recipe ``reach``, owner 2026-10-24: the PEOPLE hunt) - only while the user's own switch
  ``assistant_settings.people_hunt`` is on: open applications (considering or
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
RUNNING = "running:"  # + duty: the slot that says a run has this duty now (owner 2026-10-25)
INBOX_AUTO_MODES = frozenset({"auto", "scheduled"})
INBOX_DEFAULT_HOURS = 24
_EVERY_RE = re.compile(r"^(\d{1,3})h$")
# How a run ended (mark_done, owner 2026-10-25), and why - closed sets, never free text.
RUN_OUTCOMES: tuple[str, ...] = ("done", "partial", "failed")
RUN_REASONS: tuple[str, ...] = (
    "no_work", "tool_unavailable", "sign_in_needed", "paid_plan_needed", "network_blocked", "rate_limited",
    "needs_user", "error",
)
BACKOFF_CAP_HOURS = 24
FAILURE_CARD_AT = 3
# The plain words a problem card shows: what happened, and what the user can do.
REASON_WORDS: dict[str, tuple[str, str]] = {
    "tool_unavailable": ("your assistant could not use a tool it needs",
                         "Check its connectors (Job360, Gmail, its browser) are connected, then press Run now."),
    "sign_in_needed": ("a connected app needs you to sign in again",
                       "Open your assistant's connector settings, sign in again, then press Run now."),
    "paid_plan_needed": ("a tool it uses needs a paid plan",
                         "Check the plan on your assistant's account, or turn this job off."),
    "network_blocked": ("it could not reach a website it needs",
                        "Check your routine's network access allows the sites it uses."),
    "rate_limited": ("a site or tool said it was getting too many requests",
                     "Nothing to do: it waits longer each time and tries again."),
    "needs_user": ("it needs an answer only you can give", "Answer its question on Needs you."),
    "error": ("something went wrong in the run",
              "Open your assistant's last run to see what happened, then press Run now."),
    "no_work": ("it had nothing to do", "Nothing to do."),
}
DUTY_WORDS: dict[str, str] = {"hunt": "Hunt", "inbox": "Gmail check", "reach": "People hunt", "batch": "Apply"}


def backoff_hours(failures: int) -> int:
    """Hours a duty waits after ``failures`` failed runs in a row: 2, 4, 8, 16, then 24 at most."""
    return min(1 << max(1, min(failures, 5)), BACKOFF_CAP_HOURS)


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
    # The user's people-hunt switch, and the applications it should work on this run (newest first, capped).
    people_hunt: bool = False
    reach_ids: list[int] = field(default_factory=list)
    # Failed runs in a row per duty (0 or missing = the last run was done or partial).
    failures: dict[str, int] = field(default_factory=dict)
    # Duties a run took within DUTY_CLAIM_MINUTES and has not marked done yet: {duty: taken_at}.
    running: dict[str, datetime] = field(default_factory=dict)


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

    def waiting_after_failure(duty: str) -> bool:
        """After a failed run a duty waits (2h, 4h, 8h... at most 24h) instead of retrying every hour."""
        n, last = f.failures.get(duty, 0), f.stamps.get(duty)
        if n <= 0 or last is None:
            return False
        until = last + timedelta(hours=backoff_hours(n))
        if until > now:
            next_due[duty] = _iso(until)
            return True
        return False

    intervals = {"inbox": inbox_every_hours(f.inbox_mode, f.check_every), "hunt": f.hunt_every_hours}
    # Back-pressure: no new jobs while the queue is already full; due again as it drains.
    hunt_hold = "queue_full" if f.hunt_every_hours is not None and f.queued >= settings.HUNT_QUEUE_MAX else None
    for duty in ("inbox", "hunt"):
        every = intervals[duty]
        if every is None:
            continue
        last = f.stamps.get(duty)
        if f.failures.get(duty, 0) > 0:  # a failed run: the back-off decides, not the interval
            every = backoff_hours(f.failures[duty])
        nxt = now if last is None else last + timedelta(hours=every)
        next_due[duty] = _iso(nxt)
        if duty == "hunt" and hunt_hold:
            continue
        if nxt <= now:
            due.append({"duty": duty, "recipe": settings.SCHEDULE_DUTY_RECIPES[duty],
                        "reason": "never_done" if last is None else "interval_elapsed"})
    if f.people_hunt and f.reach_ids and not waiting_after_failure("reach"):
        due.append({"duty": "reach", "recipe": settings.SCHEDULE_DUTY_RECIPES["reach"],
                    "reason": "new_applications", "application_ids": list(f.reach_ids)})
    hold = batch_hold(f)
    if hold is None and not waiting_after_failure("batch"):
        due.append({"duty": "batch", "recipe": settings.SCHEDULE_DUTY_RECIPES["batch"], "reason": "queue_waiting"})
    live = {d: at for d, at in f.running.items() if now - at < timedelta(minutes=settings.DUTY_CLAIM_MINUTES)}
    due = [d for d in due if d["duty"] not in live]  # another run has it now: one run per duty at a time
    return {
        "due": due,
        "nothing_due": not due,
        "running": {d: _iso(at) for d, at in sorted(live.items())},
        "next_due_at": next_due,
        "batch_hold": hold,
        "hunt_hold": hunt_hold,
        "last_done_at": {d: _iso(f.stamps.get(d)) for d in settings.SCHEDULE_DUTIES},
        "last_tick_at": _iso(f.stamps.get(TICK)),
        "failures_in_row": {d: int(f.failures.get(d, 0)) for d in settings.SCHEDULE_DUTIES},
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


async def claim(db: JobDatabase, user_id: str, duties: list[str], now: datetime) -> set[str]:
    """Take each of ``duties`` for this run, in ONE statement per duty: a duty another run took less than
    ``DUTY_CLAIM_MINUTES`` ago is not taken. Returns the duties this run got (rule #12: scoped by user)."""
    stamp = now.astimezone(timezone.utc).isoformat(timespec="microseconds")
    cutoff = (now - timedelta(minutes=settings.DUTY_CLAIM_MINUTES)).astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )
    got: set[str] = set()
    for duty in duties:
        cur = await db._db.execute(
            "INSERT INTO assistant_duty_runs (user_id, duty, done_at) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at "
            "WHERE assistant_duty_runs.done_at < ? RETURNING duty",
            (user_id, RUNNING + duty, stamp, cutoff),
        )
        if await cur.fetchone() is not None:
            got.add(duty)
    await db._db.commit()
    return got


def reach_key(application_id: int) -> str:
    """The stamp slot for one application the people hunt has worked."""
    return f"reach:{application_id}"


async def mark(
    db: JobDatabase, user_id: str, duty: str, now: datetime, application_ids: Optional[list[int]] = None,
    *, outcome: str = "done", reason: str = "",
) -> None:
    """Stamp ``duty`` (and the tick) as run ``now``, with how it ended, and free the duty for the next run.
    A slot: replaced, never appended.
    ``duty``, ``outcome`` and ``reason`` are already closed values (the caller validates). A failed run
    adds one to the duty's failures in a row; done or partial resets it. ``reach`` also stamps each of
    ``application_ids`` (already checked to be this user's) unless the run failed."""
    stamp = now.astimezone(timezone.utc).isoformat()
    failed = outcome == "failed"
    await db._db.execute(
        "INSERT INTO assistant_duty_runs (user_id, duty, done_at, outcome, reason, failures) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at, outcome = EXCLUDED.outcome, "
        "reason = EXCLUDED.reason, "
        "failures = CASE WHEN EXCLUDED.outcome = 'failed' THEN assistant_duty_runs.failures + 1 ELSE 0 END",
        (user_id, duty, stamp, outcome, reason, 1 if failed else 0),
    )
    await db._db.execute(  # the run is over: the duty is free for the next run
        "DELETE FROM assistant_duty_runs WHERE user_id = ? AND duty = ?", (user_id, RUNNING + duty)
    )
    names = [TICK] + ([] if failed else [reach_key(a) for a in (application_ids or [])])
    for name in names:
        await db._db.execute(
            "INSERT INTO assistant_duty_runs (user_id, duty, done_at) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at",
            (user_id, name, stamp),
        )
    await db._db.commit()


async def load_runs(db: JobDatabase, user_id: str) -> dict[str, dict[str, Any]]:
    """``{duty: {outcome, reason, failures, done_at}}`` for the user's four duties (rule #12)."""
    marks = ",".join("?" for _ in settings.SCHEDULE_DUTIES)
    cur = await db._db.execute(
        "SELECT duty, done_at, outcome, reason, failures FROM assistant_duty_runs "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND duty IN ({marks})",
        (user_id, *settings.SCHEDULE_DUTIES),
    )
    return {str(dict(r)["duty"]): dict(r) for r in await cur.fetchall()}


def problems(runs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One plain card per duty that failed ``FAILURE_CARD_AT`` times in a row (closed values only)."""
    out: list[dict[str, Any]] = []
    for duty in settings.SCHEDULE_DUTIES:
        row = runs.get(duty)
        n = int(row["failures"]) if row else 0
        if n < FAILURE_CARD_AT:
            continue
        reason = str(row["reason"]) if row and row["reason"] in REASON_WORDS else "error"
        what, todo = REASON_WORDS[reason]
        out.append({
            "duty": duty, "label": DUTY_WORDS[duty], "failures_in_row": n, "reason": reason,
            "title": f"{DUTY_WORDS[duty]} failed {n} times in a row: {what}.", "what_to_do": todo,
            "last_failed_at": row["done_at"] if row else None,
        })
    return out


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
    stamps = await load_stamps(db, user_id)
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
        stamps=stamps,
        running={k[len(RUNNING):]: v for k, v in stamps.items() if k.startswith(RUNNING)},
        failures={d: int(r["failures"] or 0) for d, r in (await load_runs(db, user_id)).items()},
        people_hunt=cfg.people_hunt,
        reach_ids=await _reach_ids(db, user_id, now) if cfg.people_hunt else [],
    )
