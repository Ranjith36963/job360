"""The hunt and the people search in the user's OWN signed-in browser (owner decision 2026-11-03).

Two more kinds in the ONE own-browser queue (``own_browser.queue_tasks``), the LinkedIn tasks that need the
user's sign-in (``routing.QUEUE_KINDS``): ``linkedin_search`` (read the user's job alerts, notifications and
saved searches, and bring each new job with ``bring_job``) and ``profile_read`` (find people for the user's
open applications, up to ``REACH_TARGET_PEOPLE`` each, and up to ``NETWORK_MAX_PER_RUN`` for their network,
and store them with ``add_contact``). Job360 only says the task is due; the assistant reads and judges (VISION
rule 4: nothing here ranks or recommends a job).

A kind is due at most once an hour, and only while its duty is due by the user's own settings: not paused,
the hunt on (``hunt_every_hours``, its interval, not held by a full queue) or the people hunt on
(``people_hunt``); after a failed task it waits like a failed duty (``schedule.backoff_hours``). No new table:
slots in ``assistant_duty_runs`` (the own-browser run-now slot's neighbours), never read by ``whats_due``:
``own_browser:<kind>`` = the last close (done_at = when its claim started, outcome, reason, failures),
``own_browser:claim:<kind>`` = the live claim (done_at = taken; reason = the session and actor as JSON),
``own_browser:reach:<id>`` = tries per application, like the scheduled ``reach:<id>``. A claim lasts
``settings.DUTY_CLAIM_MINUTES``; an expired one puts the task back. The counts a close reports are derived
from the records the claiming actor wrote since the claim (``brought`` events, ``application_contacts``).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications.spine import SpineError
from src.services.browser_agent import routing

if TYPE_CHECKING:
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

HUNT, PEOPLE = "linkedin_search", "profile_read"
KINDS = (HUNT, PEOPLE)
TASK_PREFIX = "duty:"
SLOT, CLAIM, REACH_STAMP = "own_browser:", "own_browser:claim:", "own_browser:reach:"
# A release reason (own_browser.RELEASE_REASONS) other than "done" closes the task as failed, for this reason.
FAILED_REASONS = {"captcha": "needs_user", "login_wall": "sign_in_needed", "weekly_limit": "rate_limited"}
URLS = {HUNT: "https://www.linkedin.com/jobs/", PEOPLE: "https://www.linkedin.com/search/results/people/"}
WHAT = {
    HUNT: "Read your LinkedIn job alerts, notifications and saved searches, and bring each new job",
    PEOPLE: "Find people on LinkedIn for your applications and your network, and add them",
}


def _when(raw: Any) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat(timespec="microseconds")


async def _slots(db: JobDatabase, user_id: str) -> dict[str, dict[str, Any]]:
    names = [p + k for p in (SLOT, CLAIM) for k in KINDS]
    marks = ",".join("?" for _ in names)
    cur = await db._db.execute(
        "SELECT duty, done_at, reason, failures FROM assistant_duty_runs "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND duty IN ({marks})",
        (user_id, *names),
    )
    return {str(dict(r)["duty"]): dict(r) for r in await cur.fetchall()}


def live_claim(slots: dict[str, dict[str, Any]], kind: str, now: datetime) -> Optional[dict[str, Any]]:
    """``{session, actor, at, until}`` of the kind's claim while it lasts, else ``None``."""
    row = slots.get(CLAIM + kind)
    at = _when(row["done_at"]) if row else None
    if row is None or at is None or now - at >= timedelta(minutes=settings.DUTY_CLAIM_MINUTES):
        return None
    try:
        who = json.loads(str(row["reason"] or "{}"))
    except ValueError:
        who = {}
    until = at + timedelta(minutes=settings.DUTY_CLAIM_MINUTES)
    return {"session": str(who.get("session", "")), "actor": str(who.get("actor", "")), "at": at,
            "until": until.isoformat()}


def is_due(slots: dict[str, dict[str, Any]], kind: str, every_hours: int, now: datetime) -> bool:
    """Pure: never closed, or its interval (at least an hour; the back-off after failures) has passed."""
    from src.services.profile import schedule  # noqa: PLC0415 - profile <-> applications cycle

    row = slots.get(SLOT + kind)
    last = _when(row["done_at"]) if row else None
    failures = int(row["failures"] or 0) if row else 0
    every = schedule.backoff_hours(failures) if failures > 0 else max(1, every_hours)
    return last is None or last + timedelta(hours=every) - schedule.DUE_GRACE <= now


def _task(kind: str, claim: Optional[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    route = routing.browser_for(linkedin=True, queue_kind=kind)  # owner 2026-10-26: LinkedIn, own browser only
    return {
        "task_id": TASK_PREFIX + kind, "kind": kind, "what": WHAT[kind], "url": URLS[kind], "contact_id": None,
        "application_id": None, "name": "", "message_id": None, "draft_text": "", "draft_chars": 0,
        "linkedin_state": None, "why_own_browser": routing.ROUTE_LABELS["linkedin_own_browser"],
        "owner_yes": False, "browser": routing.label(route),
        "claim": {k: claim[k] for k in ("session", "until", "actor")} if claim else None, **extra,
    }


async def tasks(db: JobDatabase, user_id: str, now: datetime) -> list[dict[str, Any]]:
    """The hunt and people tasks due now (or held by a live claim), hunt first. Scoped by ``user_id``."""
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile import schedule  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    cfg = rules.effective((load_profile(user_id) or UserProfile()).assistant_settings)
    if rules.is_paused(cfg.paused_until, now):
        return []
    slots = await _slots(db, user_id)
    out: list[dict[str, Any]] = []
    if cfg.hunt_every_hours is not None:
        claim = live_claim(slots, HUNT, now)
        if claim or (is_due(slots, HUNT, cfg.hunt_every_hours, now)
                     and len(await schedule._open_queue_ids(db, user_id)) < settings.HUNT_QUEUE_MAX):
            out.append(_task(HUNT, claim))
    if cfg.people_hunt:
        claim = live_claim(slots, PEOPLE, now)
        if claim or is_due(slots, PEOPLE, settings.NETWORK_EVERY_HOURS, now):
            ids = await schedule._reach_ids(db, user_id, now, cfg.apply_min_score, stamp=REACH_STAMP)
            out.append(_task(PEOPLE, claim, application_ids=ids, max_people=settings.NETWORK_MAX_PER_RUN))
    return out


async def claim(db: JobDatabase, user_id: str, kind: str, who: dict[str, str], now: datetime) -> None:
    """Take ``kind`` for one session (the caller holds the user row lock and saw no live claim)."""
    await db._db.execute(
        "INSERT INTO assistant_duty_runs (user_id, duty, done_at, reason) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at, reason = EXCLUDED.reason",
        (user_id, CLAIM + kind, _stamp(now), json.dumps(who, sort_keys=True)),
    )


async def _count(db: JobDatabase, user_id: str, kind: str, actor: str, since: datetime) -> int:
    if kind == HUNT:
        sql = ("SELECT COUNT(*) FROM application_events WHERE user_id = ? AND event_type = 'brought' "
               "AND recorded_by = ? AND recorded_at >= ?")
    else:
        sql = "SELECT COUNT(*) FROM application_contacts WHERE user_id = ? AND added_by = ? AND created_at >= ?"
    cur = await db._db.execute(sql, (user_id, actor, _stamp(since)))
    got = await cur.fetchone()
    return int(got[0]) if got is not None else 0


async def close(
    db: JobDatabase, user: CurrentUser, label: str, kind: str, reason: str, now: datetime
) -> dict[str, Any]:
    """Close a hunt or people task this session holds: ``done`` (the count is what this actor stored since the
    claim) or any other release reason (recorded as failed: the task waits, then comes back). 409 unless this
    session holds the live claim; 422 for an unknown kind."""
    from src.services.applications.authorship import actor_for  # noqa: PLC0415
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile import schedule  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    if kind not in KINDS:
        raise SpineError(422, "task_id must be duty:linkedin_search or duty:profile_read")
    actor = actor_for(user)
    count: Optional[int] = None
    async with db._db.transaction():
        await db._db.execute("SELECT id FROM users WHERE id = ? FOR UPDATE", (user.id,))
        slots = await _slots(db, user.id)
        held = live_claim(slots, kind, now)
        if held is None or not label or (held["session"], held["actor"]) != (label, actor):
            raise SpineError(409, "you do not hold a live claim on this task")
        last = slots.get(SLOT + kind)
        if reason == "done":
            count = await _count(db, user.id, kind, actor, held["at"])
            outcome, why, failures = "done", "" if count else "no_work", 0
            if kind == PEOPLE:
                line = rules.effective((load_profile(user.id) or UserProfile()).assistant_settings).apply_min_score
                ids = await schedule._reach_ids(db, user.id, now, line, stamp=REACH_STAMP)
                await schedule.stamp_tries(db, user.id, [REACH_STAMP + str(a) for a in ids], _stamp(now))
        else:
            outcome, why = "failed", FAILED_REASONS.get(reason, "error")
            failures = (int(last["failures"] or 0) if last else 0) + 1
        await db._db.execute(
            "INSERT INTO assistant_duty_runs (user_id, duty, done_at, outcome, reason, failures) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at, "
            "outcome = EXCLUDED.outcome, reason = EXCLUDED.reason, failures = EXCLUDED.failures",
            (user.id, SLOT + kind, _stamp(held["at"]), outcome, why, failures),
        )
        await db._db.execute(
            "DELETE FROM assistant_duty_runs WHERE user_id = ? AND duty = ?", (user.id, CLAIM + kind)
        )
    one, many = ("job", "jobs") if kind == HUNT else ("person", "people")
    noun = f"{one if count == 1 else many} {'brought' if kind == HUNT else 'added'}"
    summary = f"done, {count} {noun}" if outcome == "done" else f"failed ({why}): it comes back after a wait"
    return {"outcome": outcome, "count": count, "summary": summary, "failures_in_row": failures}
