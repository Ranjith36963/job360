"""The own-browser queue (owner decisions 2026-10-25).

Job360's own browser goes FIRST for every task. A task reaches the user's OWN browser (the one their
assistant drives on their computer) only after Job360's browser failed on it (an open ``blocked`` with
``by: browser`` that ``routing.ask_route`` sends to ``own_browser``, or a LinkedIn person it gave back with
``release``). The user's per-card Send click on one LinkedIn person is their yes for THAT message and never
expires until it is sent or cancelled; a rewritten draft (a new message id) needs a new click. Either browser
sends only while it holds a live claim (:func:`take`, optionally for one ``contact_id``).

Stored as ``contact_outreach`` entries Job360 writes itself and never accepts from a caller (they are not in
``settings.OUTREACH_ENTRIES``, so ``record_outreach`` refuses them): ``send_approved`` / ``send_declined``
(text = the message row id), ``send_claimed`` (text = the session label), ``send_released`` (text = reason).
Append-only: the state is derived from the newest rows, nothing is updated. Run now is one slot in
``assistant_duty_runs`` named ``own_browser:run_now`` (it is not a scheduled duty). Pace: LinkedIn sends are
at least ``LINKEDIN_MIN_GAP_SECONDS`` apart and at most ``LINKEDIN_DAILY_MAX`` a day, enforced in :func:`take`.
Assistant-neutral on purpose. Logs ids and closed values only, never message text.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications.spine import SpineError
from src.services.browser_agent import routing
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

APPROVED, DECLINED, CLAIMED, RELEASED = "send_approved", "send_declined", "send_claimed", "send_released"
ENTRIES = (APPROVED, DECLINED, CLAIMED, RELEASED)
CLAIM_MINUTES = 15
RUN_NOW_MINUTES = 60
QUEUE_MAX = 50
ROWS_PER_CONTACT_MAX = 200
LINKEDIN_MIN_GAP_SECONDS = 120
LINKEDIN_DAILY_MAX = 10
RELEASE_REASONS = (
    "captcha", "login_wall", "weekly_limit", "draft_rewritten", "already_pending", "profile_not_found", "other",
)
RUN_NOW_SLOT = "own_browser:run_now"
DECISIONS = ("approve", "decline")
WHY_WORDS = {
    "job360_login_required": "Job360's browser hit a sign-in page.",
    "job360_bot_check": "Job360's browser hit a CAPTCHA or bot check.",
    "job360_safety_stop": "Job360's browser stopped for safety.",
    "default": "You pressed Send for this message.",
}


def _log(event: str, user_id: str, result: str, **extra: Any) -> None:
    fields = {k: (v if isinstance(v, (int, bool)) else safe_log_value(str(v), max_len=40)) for k, v in extra.items()}
    get_audit_logger().info(
        event,
        extra={"event": event, "user_id": safe_log_value(user_id), "result": safe_log_value(result, max_len=30),
               **fields},
    )


def session_label(raw: str) -> str:
    """The session's own short label (``kit.clean_session``: 422 when malformed)."""
    from src.services.applications import kit  # noqa: PLC0415

    return kit.clean_session(raw)


def log_read(user_id: str, count: int) -> None:
    """One audit line for a queue read: who, how many (never the tasks)."""
    _log("own_browser_queue_read", user_id, "ok", count=count)


def _when(raw: Any) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


# ── Pure: the state of one person's send, from their outreach rows (oldest first) ──────────────────────


def derive(rows: list[dict[str, Any]], now: datetime) -> Optional[dict[str, Any]]:
    """The contact's own-browser send state, or ``None`` when there is nothing to send: the newest message is
    not a LinkedIn one, or a ``sent`` / ``reply`` mark is newer than it. Else ``{state, message_id,
    approved_at, claim_until, claimed_by}`` with state waiting_on_you (no click yet) | queued | in_progress
    (a live claim newer than the latest approval) | declined."""
    messages = [r for r in rows if r["entry"] == "message"]
    if not messages or messages[-1]["channel"] != "linkedin":
        return None
    mid = int(messages[-1]["id"])
    if any(r["entry"] in ("sent", "reply") and int(r["id"]) > mid for r in rows):
        return None
    decisions = [r for r in rows if r["entry"] in (APPROVED, DECLINED) and str(r.get("text")) == str(mid)]
    base: dict[str, Any] = {
        "message_id": mid, "approved_at": None, "claim_until": None, "claimed_by": "", "claimed_actor": "",
    }
    if not decisions:
        return {**base, "state": "waiting_on_you"}
    last = decisions[-1]
    if last["entry"] == DECLINED:
        return {**base, "state": "declined"}
    base["approved_at"] = last["recorded_at"]
    after = [r for r in rows if r["entry"] in (CLAIMED, RELEASED) and int(r["id"]) > int(last["id"])]
    if after and after[-1]["entry"] == CLAIMED:
        until = (_when(after[-1]["recorded_at"]) or now) + timedelta(minutes=CLAIM_MINUTES)
        if until > now:
            return {
                **base, "state": "in_progress", "claim_until": until.isoformat(),
                "claimed_by": after[-1]["text"], "claimed_actor": after[-1].get("recorded_by") or "",
            }
    return {**base, "state": "queued"}


def public_task(task: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The four fields ``ContactOut.own_browser_task`` carries (never the claiming session's label)."""
    if task is None:
        return None
    return {k: task[k] for k in ("state", "message_id", "approved_at", "claim_until")}


# ── Rows ────────────────────────────────────────────────────────────────────────────────────────────────


async def _rows(db: JobDatabase, contact_id: int) -> list[dict[str, Any]]:
    from src.services.applications import contacts  # noqa: PLC0415 - contacts imports this module

    return await contacts._outreach_rows(db, contact_id)


async def _insert(
    db: JobDatabase, user: CurrentUser, contact_id: int, entry: str, text: str, now: datetime
) -> None:
    from src.services.applications.authorship import actor_for  # noqa: PLC0415

    if entry == CLAIMED:  # the user's approve / decline and a release are never blocked by this cap
        cur = await db._db.execute(
            "SELECT COUNT(*) FROM contact_outreach WHERE contact_id = ? AND entry = ?", (contact_id, CLAIMED)
        )
        got = await cur.fetchone()
        if got is not None and int(got[0]) >= ROWS_PER_CONTACT_MAX:
            raise SpineError(429, "too many own-browser claims for this person")
    stamp = now.astimezone(timezone.utc).isoformat()
    await db._db.execute(
        "INSERT INTO contact_outreach (user_id, contact_id, entry, channel, text, version_no, occurred_at, "
        "recorded_at, recorded_by, source_message_id, label) VALUES (?, ?, ?, 'linkedin', ?, NULL, ?, ?, ?, '', '')",
        (user.id, contact_id, entry, text, stamp, stamp, actor_for(user)),
    )


# ── The user's click ────────────────────────────────────────────────────────────────────────────────────


async def decide(
    db: JobDatabase, user: CurrentUser, contact_id: int, message_id: int, decision: str
) -> dict[str, Any]:
    """The user's yes (``approve``) or no (``decline``) to sending ONE LinkedIn message in their own browser.
    404 for a contact that is not theirs or a message that is not that contact's; 409 when ``message_id`` is no
    longer the newest draft or the message was already sent / replied to; 422 for a non-LinkedIn draft.
    Idempotent: the same decision again writes nothing."""
    from src.services.applications import contacts  # noqa: PLC0415

    if decision not in DECISIONS:
        raise SpineError(422, "decision must be approve or decline")
    now = datetime.now(timezone.utc)
    async with db._db.transaction():
        cur = await db._db.execute(
            "SELECT id FROM application_contacts WHERE id = ? AND user_id = ? FOR UPDATE", (contact_id, user.id)
        )
        if await cur.fetchone() is None:
            _log("own_browser_decide", user.id, "not_found", contact_id=contact_id)
            raise SpineError(404, "contact not found")
        rows = await contacts._outreach_rows(db, contact_id)
        messages = [r for r in rows if r["entry"] == "message"]
        target = next((r for r in messages if int(r["id"]) == int(message_id)), None)
        if target is None:
            raise SpineError(404, "message not found")
        if int(messages[-1]["id"]) != int(message_id):
            _log("own_browser_decide", user.id, "stale", contact_id=contact_id)
            raise SpineError(409, "the draft changed since you looked - look again")
        if target["channel"] != "linkedin":
            raise SpineError(422, "only a LinkedIn message is sent from your own browser")
        if any(r["entry"] in ("sent", "reply") and int(r["id"]) > int(message_id) for r in rows):
            raise SpineError(409, "this message was already sent")
        current = derive(rows, now)
        wanted = APPROVED if decision == "approve" else DECLINED
        decisions = [r for r in rows if r["entry"] in (APPROVED, DECLINED) and str(r["text"]) == str(message_id)]
        if not decisions or decisions[-1]["entry"] != wanted:
            await _insert(db, user, contact_id, wanted, str(message_id), now)
            rows = await contacts._outreach_rows(db, contact_id)
            current = derive(rows, now)
    _log("own_browser_decide", user.id, decision, contact_id=contact_id)
    return public_task(current) or {}


# ── Run now, and when the queue is open ─────────────────────────────────────────────────────────────────


async def run_now(db: JobDatabase, user_id: str, now: datetime) -> datetime:
    """Open a ``RUN_NOW_MINUTES`` window: one slot per user in ``assistant_duty_runs`` (its ``done_at`` is the
    END of the window). Not a scheduled duty: ``whats_due`` and ``load_runs`` never read it."""
    until = now + timedelta(minutes=RUN_NOW_MINUTES)
    await db._db.execute(
        "INSERT INTO assistant_duty_runs (user_id, duty, done_at) VALUES (?, ?, ?) "
        "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at",
        (user_id, RUN_NOW_SLOT, until.astimezone(timezone.utc).isoformat()),
    )
    await db._db.commit()
    _log("own_browser_run_now", user_id, "ok")
    return until


async def run_now_until(db: JobDatabase, user_id: str, now: datetime) -> Optional[datetime]:
    cur = await db._db.execute(
        "SELECT done_at FROM assistant_duty_runs WHERE user_id = ? AND duty = ?", (user_id, RUN_NOW_SLOT)
    )
    row = await cur.fetchone()
    until = _when(dict(row)["done_at"]) if row else None
    return until if until is not None and until > now else None


async def window(db: JobDatabase, user_id: str, now: datetime) -> dict[str, Any]:
    """``{open_now, why, pc_on_hours, opens_at, run_now_until}``; why = paused | any_time | run_now.

    The PC-on hours setting is wired in once #1231 merges: until then the window is "not set" (any time)."""
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    cfg = rules.effective((load_profile(user_id) or UserProfile()).assistant_settings)
    until = await run_now_until(db, user_id, now)
    if rules.is_paused(cfg.paused_until, now):
        why, open_now = "paused", False
    else:
        why, open_now = "any_time", True
    return {
        "open_now": open_now, "why": why, "pc_on_hours": None,
        "opens_at": None,
        "run_now_until": until.isoformat() if until else None,
    }


# ── The queue ───────────────────────────────────────────────────────────────────────────────────────────


async def _contact_tasks(db: JobDatabase, user: CurrentUser, now: datetime) -> list[dict[str, Any]]:
    from src.services.applications import contacts  # noqa: PLC0415

    # Only a person whose NEWEST decision is an approval of their NEWEST message, a LinkedIn one with no
    # sent / reply mark since: finished approvals must never fill the LIMIT.
    cur = await db._db.execute(
        "SELECT a.contact_id, a.id AS newest FROM contact_outreach a WHERE a.user_id = ? AND a.entry = ? "
        "AND a.id = (SELECT MAX(d.id) FROM contact_outreach d WHERE d.contact_id = a.contact_id "
        "            AND d.entry IN (?, ?)) "
        "AND a.text = CAST((SELECT MAX(m.id) FROM contact_outreach m WHERE m.contact_id = a.contact_id "
        "                   AND m.entry = 'message') AS TEXT) "
        "AND EXISTS (SELECT 1 FROM contact_outreach m2 WHERE m2.contact_id = a.contact_id "
        "            AND CAST(m2.id AS TEXT) = a.text AND m2.channel = 'linkedin') "
        "AND NOT EXISTS (SELECT 1 FROM contact_outreach s WHERE s.contact_id = a.contact_id "
        "                AND s.entry IN ('sent', 'reply') AND s.id > CAST(a.text AS INTEGER)) "
        "ORDER BY a.id ASC LIMIT ?",
        (user.id, APPROVED, APPROVED, DECLINED, QUEUE_MAX),
    )
    out: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        contact_id = int(dict(r)["contact_id"])
        base = await contacts.get_owned_contact(db, user.id, contact_id)
        if base is None:
            continue
        task = derive(await _rows(db, contact_id), now)
        if task is None or task["state"] not in ("queued", "in_progress"):
            continue
        view = await contacts._full_contact_view(db, user.id, base)
        text = view["outreach"]["messages"][-1]["text"] if view["outreach"]["messages"] else ""
        route = routing.Route("own_browser", "default")
        out.append({
            "task_id": f"contact:{contact_id}", "kind": "linkedin_outreach",
            "what": f"Send your LinkedIn message to {view['name']}", "url": view.get("linkedin_url") or "",
            "contact_id": contact_id, "application_id": view.get("application_id"), "name": view["name"],
            "message_id": task["message_id"], "draft_text": text, "draft_chars": len(text),
            "linkedin_state": view.get("linkedin_state"), "why_own_browser": WHY_WORDS["default"],
            "owner_yes": True,
            "claim": ({"session": task["claimed_by"], "until": task["claim_until"], "actor": task["claimed_actor"]}
                      if task["state"] == "in_progress" else None),
            "browser": routing.label(route),
        })
    return out


async def _application_tasks(
    db: JobDatabase, user: CurrentUser, now: datetime, session: Optional[str]
) -> list[dict[str, Any]]:
    from src.services.applications import blocked, kit  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT id, job_title, job_company, job_url FROM applications WHERE user_id = ? AND status = 'considering' "
        "ORDER BY id DESC LIMIT 500",
        (user.id,),
    )
    apps = {int(dict(r)["id"]): dict(r) for r in await cur.fetchall()}
    blocks = await blocked.open_blocks(db, user.id, list(apps))
    out: list[dict[str, Any]] = []
    for app_id, p in sorted(blocks.items()):
        if p.get(blocked.BY_KEY) != blocked.BY_BROWSER:
            continue
        reason = str(p.get("reason") or "")
        route = routing.ask_route(
            blocked.ASK_KIND_FOR_REASON.get(reason, ""), block_reason=reason, block_by_browser=True,
            # While Job360's browser still holds that page the user finishes it on Needs you; once it let go, no.
            finish_here=reason == "captcha" and blocked.browser_hold_live(user.id, app_id),
        )
        if route.kind != "own_browser":
            continue
        if session is not None and await kit.live_hold(db, user, app_id, now, session) is not None:
            continue
        a = apps[app_id]
        out.append({
            "task_id": f"application:{app_id}", "kind": "application",
            "what": f"Finish the application for {a['job_title']} at {a['job_company']}",
            "url": a.get("job_url") or "", "contact_id": None, "application_id": app_id, "name": "",
            "message_id": None, "draft_text": "", "draft_chars": 0, "linkedin_state": None,
            "why_own_browser": WHY_WORDS.get(route.reason, ""), "owner_yes": False, "claim": None,
            "browser": routing.label(route),
        })
        if len(out) >= QUEUE_MAX:
            break
    return out


async def queue_tasks(
    db: JobDatabase, user: CurrentUser, now: datetime, session: Optional[str] = None
) -> list[dict[str, Any]]:
    """The tasks that wait for the user's own browser: approved, unsent LinkedIn drafts (oldest approval first)
    then applications whose open block Job360's browser wrote and the router sends to ``own_browser``. With a
    ``session`` (an assistant's read) an application another session holds is left out; the website passes none."""
    tasks = await _contact_tasks(db, user, now) + await _application_tasks(db, user, now, session)
    return tasks[:QUEUE_MAX]


# ── Take and release ────────────────────────────────────────────────────────────────────────────────────


# A LinkedIn send, or a claim Job360 wrote itself that was neither released nor followed by a sent mark: a
# send that was never recorded still counts toward the pace.
_SENDS = (
    "o.user_id = ? AND ((o.entry = 'sent' AND o.channel = 'linkedin') OR (o.entry = 'send_claimed' AND NOT EXISTS "
    "(SELECT 1 FROM contact_outreach x WHERE x.contact_id = o.contact_id AND x.id > o.id "
    "AND x.entry IN ('send_released', 'sent'))))"
)


async def pace_facts(
    db: JobDatabase, user_id: str, now: datetime, exclude_contact_id: Optional[int] = None
) -> tuple[Optional[datetime], int]:
    """``(last LinkedIn send or unreleased claim, how many today)`` for the user's own day. The ONE count
    :func:`take` and ``check_outreach`` share. ``exclude_contact_id`` leaves one person's rows out: the gate
    asks about a person the caller has just claimed, and that claim must not make the gate say "too soon"."""
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415

    skip, params = (" AND o.contact_id <> ?", (user_id, exclude_contact_id)) if exclude_contact_id is not None else (
        "", (user_id,)
    )
    cur = await db._db.execute(
        f"SELECT MAX(o.recorded_at) FROM contact_outreach o WHERE {_SENDS}{skip}",  # noqa: S608 - constant text
        params,
    )
    got = await cur.fetchone()
    last = _when(got[0]) if got is not None and got[0] else None
    today = await rules.count_on_user_today(
        db, user_id, now,
        f"SELECT o.recorded_at FROM contact_outreach o WHERE {_SENDS}{skip} AND o.recorded_at >= ?",  # noqa: S608
        params,
    )
    return last, today


async def weekly_limit_until(db: JobDatabase, user_id: str, now: datetime) -> Optional[datetime]:
    """When LinkedIn's weekly invitation limit, which the user's assistant reported by releasing a task with
    reason ``weekly_limit``, is over (seven days after the newest such release); ``None`` when none is live."""
    cur = await db._db.execute(
        "SELECT MAX(recorded_at) FROM contact_outreach WHERE user_id = ? AND entry = ? AND text = 'weekly_limit'",
        (user_id, RELEASED),
    )
    got = await cur.fetchone()
    at = _when(got[0]) if got is not None and got[0] else None
    until = at + timedelta(days=7) if at is not None else None
    return until if until is not None and until > now else None


async def _pace(db: JobDatabase, user_id: str, now: datetime) -> Optional[tuple[str, int]]:
    """``("wait", seconds_left)`` when the last LinkedIn send was under the minimum gap ago;
    ``("daily_limit", 0)`` at the day's maximum (the user's own day); else ``None``."""
    last, today = await pace_facts(db, user_id, now)
    if last is not None:
        left = LINKEDIN_MIN_GAP_SECONDS - (now - last).total_seconds()
        if left > 0:
            return "wait", int(math.ceil(left))
    return ("daily_limit", 0) if today >= LINKEDIN_DAILY_MAX else None


def _answer(status: str, why: str = "", **extra: Any) -> dict[str, Any]:
    return {"status": status, "why": why, "task": None, "next": "", **extra}


def _next_step(task: dict[str, Any]) -> str:
    if task["kind"] == "application":
        return f'get_application_kit(application_id={task["application_id"]}, session="<your session>")'
    return (
        f'call check_outreach(contact_id={task["contact_id"]}, message_id={task["message_id"]}, '
        "session=<your session>); only if it says send, open the person's LinkedIn page and send the draft, "
        "then record_event outreach_sent. Otherwise own_browser_queue release with reason other"
    )


async def take(
    db: JobDatabase, user: CurrentUser, session: str, now: datetime, contact_id: Optional[int] = None
) -> dict[str, Any]:
    """Hand ONE task to the session (15 minutes for a person; an application's kit read is its own hold).
    With ``contact_id`` only that person is claimed (Job360's browser takes the one the user clicked Send on);
    ``empty`` when they are not waiting for a send. The same locks, pace, pause and window apply.
    ``status``: task | already_yours | empty | closed (outside the PC-on hours, no Run now) | paused | busy
    (another session is sending) | wait (``wait_seconds`` left) | daily_limit. The same session asking again
    gets its live claim back. The user row is locked first, so two sessions never take one person."""
    from src.services.applications import kit  # noqa: PLC0415
    from src.services.applications.authorship import actor_for  # noqa: PLC0415

    label = kit.clean_session(session)
    if not label:
        raise SpineError(422, "session is needed: a short label that tells your sessions apart")
    actor = actor_for(user)
    win = await window(db, user.id, now)
    if win["why"] == "paused":
        _log("own_browser_take", user.id, "paused")
        return _answer("paused", "paused", opens_at=None)
    if not win["open_now"]:
        _log("own_browser_take", user.id, "closed")
        return _answer("closed", "closed", opens_at=win["opens_at"])
    async with db._db.transaction():
        await db._db.execute("SELECT id FROM users WHERE id = ? FOR UPDATE", (user.id,))
        tasks = await queue_tasks(db, user, now, label)
        contacts_ = [t for t in tasks if t["kind"] == "linkedin_outreach"]
        if contact_id is not None:
            contacts_ = [t for t in contacts_ if t["contact_id"] == contact_id]
            tasks = contacts_
        mine = next(
            (t for t in contacts_ if t["claim"] and (t["claim"]["session"], t["claim"]["actor"]) == (label, actor)),
            None,
        )
        if mine is not None:
            _log("own_browser_take", user.id, "already_yours", contact_id=mine["contact_id"])
            return _answer("already_yours", task=mine, next=_next_step(mine))
        free = [t for t in contacts_ if t["claim"] is None]
        busy = bool(contacts_) and len(free) < len(contacts_)  # another session holds a person
        chosen: Optional[dict[str, Any]] = None
        refusal: Optional[dict[str, Any]] = None
        if free and not busy:
            pace = await _pace(db, user.id, now)
            if pace is not None:
                refusal = _answer(pace[0], pace[0], wait_seconds=pace[1])
            else:
                for candidate in free:  # the first that is still queued once its row is locked
                    await db._db.execute(
                        "SELECT id FROM application_contacts WHERE id = ? AND user_id = ? FOR UPDATE",
                        (candidate["contact_id"], user.id),
                    )
                    state = derive(await _rows(db, candidate["contact_id"]), now)
                    if state is None or state["state"] != "queued":
                        continue
                    try:
                        await _insert(db, user, candidate["contact_id"], CLAIMED, label, now)
                    except SpineError:  # too many claims on this person: try the next one
                        continue
                    chosen = candidate
                    break
        elif busy:
            refusal = _answer("busy", "another session is sending")
    if chosen is not None:
        _log("own_browser_take", user.id, "task", contact_id=chosen["contact_id"], actor=actor_for(user))
        return _answer("task", task=chosen, next=_next_step(chosen))
    apps = [t for t in tasks if t["kind"] == "application"]
    if apps:
        _log("own_browser_take", user.id, "task", application_id=apps[0]["application_id"])
        return _answer("task", task=apps[0], next=_next_step(apps[0]))
    if refusal is not None:
        _log("own_browser_take", user.id, refusal["status"])
        return refusal
    _log("own_browser_take", user.id, "empty")
    return _answer("empty", "nothing is waiting for your own browser")


async def release(
    db: JobDatabase, user: CurrentUser, session: str, task_id: str, reason: str, now: datetime
) -> dict[str, Any]:
    """Give a claimed person back. 409 unless this session holds the live claim; 422 for an unknown reason
    or task id."""
    from src.services.applications import kit  # noqa: PLC0415
    from src.services.applications.authorship import actor_for  # noqa: PLC0415

    label = kit.clean_session(session)
    if reason not in RELEASE_REASONS:
        raise SpineError(422, "reason must be one of: " + ", ".join(RELEASE_REASONS))
    kind, _, raw = task_id.partition(":")
    if kind != "contact" or not raw.isdigit():
        raise SpineError(409, "only a claimed person can be released; an application is released with release_hold")
    contact_id = int(raw)
    async with db._db.transaction():
        cur = await db._db.execute(
            "SELECT id FROM application_contacts WHERE id = ? AND user_id = ? FOR UPDATE", (contact_id, user.id)
        )
        if await cur.fetchone() is None:
            raise SpineError(404, "contact not found")
        state = derive(await _rows(db, contact_id), now)
        if state is None or state["state"] != "in_progress" or not label or (
            state["claimed_by"], state["claimed_actor"]
        ) != (label, actor_for(user)):
            _log("own_browser_release", user.id, "refused", contact_id=contact_id)
            raise SpineError(409, "you do not hold a live claim on this person")
        await _insert(db, user, contact_id, RELEASED, reason, now)
    _log("own_browser_release", user.id, "ok", contact_id=contact_id, reason=reason)
    return {"released": True, "task_id": task_id, "reason": reason}
