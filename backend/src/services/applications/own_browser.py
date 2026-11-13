"""The own-browser queue (owner decisions 2026-10-25, 2026-10-26).

What waits for the user's OWN browser (the one their assistant drives on their computer): every LinkedIn
send the user pressed Send on and every batch-queued application whose apply link is on linkedin.com (Easy
Apply), from the start, since Job360's browser never opens linkedin.com (``routing.is_linkedin``); and any
other application only after Job360's browser failed on it (an open ``blocked`` with ``by: browser`` that
``routing.ask_route`` sends to ``own_browser``). The user's per-card Send click on one LinkedIn person is their
yes for THAT message and never expires until it is sent or cancelled; a rewritten draft (a new message id)
needs a new click. A send happens only while the session holds a live claim (:func:`take`, optionally for one
``contact_id``). Easy Apply has no Job360 day cap unless ``EASY_APPLY_DAILY_MAX`` > 0 (then enforced in :func:`take`).

Stored as ``contact_outreach`` entries Job360 writes itself and never accepts from a caller (they are not in
``settings.OUTREACH_ENTRIES``, so ``record_outreach`` refuses them): ``send_approved`` / ``send_declined``
(text = the message row id), ``send_claimed`` (text = the session label), ``send_released`` (text = reason).
Append-only: the state is derived from the newest rows, nothing is updated. Run now is one slot in
``assistant_duty_runs`` named ``own_browser:run_now`` (it is not a scheduled duty). Pace: LinkedIn sends are
at least ``LINKEDIN_MIN_GAP_SECONDS`` apart and at most the user's own ``assistant_settings.linkedin_sends_per_day``
(``rules.linkedin_daily_max``: default 15, operator backstop 100) a day, enforced in :func:`take`.
Assistant-neutral on purpose. Logs ids and closed values only, never message text.

Connect, then message (owner 2026-11-03): a draft saved with ``draft_kind="after_connect"`` carries a Job360
``after_connect`` row (text = its message id). For a person not connected, the send is a request WITHOUT a note;
the session releases the claim with reason ``invited`` and Job360 saves ``linkedin_state`` pending. Once they are
connected the same person is queued again, and the message goes without a new click when the user approved the
request or ``outreach_send`` is auto (``check_outreach`` auto_after_accept, fact-checked). A pending person is never
handed out; nobody gets a second request.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
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
EASY_APPLY_DAILY_MAX = settings.EASY_APPLY_DAILY_MAX  # 0 = no Job360 cap; LinkedIn's own limit decides
AFTER_CONNECT, INVITED = "after_connect", "invited"
RELEASE_REASONS = (
    "captcha", "login_wall", "weekly_limit", "draft_rewritten", "already_pending", "profile_not_found", "other",
    INVITED,  # a request without a note went out: Job360 saves linkedin_state pending
    "done",  # only a hunt or people task (pc_duties): finished; every other reason closes one as failed
)
# A released person is not handed out again for RELEASE_BACKOFF_MINUTES, whatever the reason (2026-11-03: a run
# that takes until empty got the person it had just released straight back and looped). An hour, the same pace the
# hunt and people tasks keep after a failure (pc_duties). A newer approval (the user's click on a rewritten draft)
# ends it. ``weekly_limit`` also parks every LinkedIn INVITE (:func:`weekly_limit_until`, seven days from that
# release: LinkedIn's invitation limit is weekly).
RELEASE_BACKOFF_MINUTES = 60
RUN_NOW_SLOT = "own_browser:run_now"
TAKE_SLOT = "own_browser:take"  # the newest take by any own-browser session (last_pc_activity_at)
# An application a session just let go (hold_released) is not handed straight back: pc-1523, 2026-11-03, take
# returned Coinbase again right after the run stopped on it. It comes back after this many minutes.
APPLICATION_RELEASE_COOLDOWN_MINUTES = 60
DECISIONS = ("approve", "decline")
WHY_WORDS = {
    "job360_login_required": "Job360's browser hit a sign-in page.",
    "job360_bot_check": "Job360's browser hit a CAPTCHA or bot check.",
    "job360_safety_stop": "Job360's browser stopped for safety.",
    "default": "You pressed Send for this message.",
    "auto": "Sent under your outreach auto setting.",
    "linkedin_own_browser": "LinkedIn runs only in your own browser: your choice.",
    "link_unclear": "This link looks broken or disguised: your own browser checks it.",
    "learned_bot_check": "Job360's browser was blocked twice on this site (a CAPTCHA or bot check).",
    "captcha_handoff": "Job360's browser hit a CAPTCHA or bot check: you solve it in your own tab.",
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


def note_over_limit(channel: str, state: Optional[str], chars: int, premium: bool) -> Optional[int]:
    """The connection-note limit a stored LinkedIn draft breaks, else ``None`` (fail closed, 2026-11-02): a
    draft to anyone not known to be connected is a connection note, so an unsaved state counts as not
    connected. ``connected`` (a message) and ``pending`` (nothing can be sent) are never judged here."""
    if channel != "linkedin" or (state or "unknown") not in ("not_connected", "unknown"):
        return None
    limit = settings.LINKEDIN_NOTE_MAX_CHARS_PREMIUM if premium else settings.LINKEDIN_NOTE_MAX_CHARS
    return limit if chars > limit else None


def _when(raw: Any) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


# ── Pure: the state of one person's send, from their outreach rows (oldest first) ──────────────────────


def is_after_connect(rows: list[dict[str, Any]], message_id: int) -> bool:
    """Message ``message_id`` was saved as ``after_connect`` (the request goes without a note)."""
    return any(r["entry"] == AFTER_CONNECT and str(r.get("text")) == str(message_id) for r in rows)


def invite_facts(rows: list[dict[str, Any]]) -> Optional[dict[str, Optional[int]]]:
    """``None`` when no request went to this person; else ``{"approved_message": id}``: the message the user's
    newest decision before the request said yes to (``None``: it went under ``outreach_send`` auto). The user's
    Send binds to that exact text: a draft saved later is a new text the user has not seen."""
    sent = [int(r["id"]) for r in rows if r["entry"] == RELEASED and r.get("text") == INVITED]
    if not sent:
        return None
    before = [r for r in rows if r["entry"] in (APPROVED, DECLINED) and int(r["id"]) < sent[-1]]
    yes = before[-1] if before and before[-1]["entry"] == APPROVED else None
    return {"approved_message": int(yes["text"]) if yes is not None and str(yes["text"]).isdigit() else None}


def follow_up_facts(rows: list[dict[str, Any]], mid: int, now: datetime) -> dict[str, Any]:
    """``{follow_up, linkedin_sent_to_person, days_since_sent}`` for draft ``mid`` (the gate's facts): a follow-up is
    a draft saved after the newest LinkedIn message went."""
    sent = [r for r in rows if r["entry"] == "sent" and r["channel"] == "linkedin"]
    last = _when(sent[-1]["recorded_at"]) if sent else None
    return {
        "follow_up": bool(sent) and mid > int(sent[-1]["id"]), "linkedin_sent_to_person": len(sent),
        "days_since_sent": (now - last).days if last is not None else 0,
    }


def follow_up_due(rows: list[dict[str, Any]], now: datetime) -> bool:
    """Pure: the assistant drafts this person's one follow-up now: ONE LinkedIn message went
    ``LINKEDIN_FOLLOW_UP_DAYS`` or more ago, no reply, and nothing was drafted after it."""
    sent = [r for r in rows if r["entry"] == "sent" and r["channel"] == "linkedin"]
    if len(sent) != 1 or any(r["entry"] == "reply" for r in rows):
        return False
    if any(r["entry"] in ("message", "sent") and int(r["id"]) > int(sent[0]["id"]) for r in rows):
        return False
    at = _when(sent[0]["recorded_at"])
    return at is not None and (now - at).days >= settings.LINKEDIN_FOLLOW_UP_DAYS


def follow_up_held(rows: list[dict[str, Any]], mid: int, now: datetime) -> bool:
    """A follow-up draft the queue must not hand out: a second one, one not yet due, or one after a reply."""
    got = follow_up_facts(rows, mid, now)
    if not got["linkedin_sent_to_person"]:
        return False
    return (got["linkedin_sent_to_person"] >= 2 or got["days_since_sent"] < settings.LINKEDIN_FOLLOW_UP_DAYS
            or any(r["entry"] == "reply" for r in rows))


def _carried(rows: list[dict[str, Any]], mid: int, auto: bool, note_ok: bool = False) -> bool:
    """A yes without a click on this draft: ``outreach_send`` auto (after a request, or an ``after_connect`` draft
    before one, or, owner 2026-11-13, a connection NOTE: ``note_ok``, the caller knows the person is NOT connected),
    or the user's Send on the request for THIS exact message."""
    if invite_facts(rows) is not None:
        return auto or owner_yes_carries(rows, mid)
    return auto and (is_after_connect(rows, mid) or note_ok)


def owner_yes_carries(rows: list[dict[str, Any]], mid: int) -> bool:
    """The user's Send on the request covers draft ``mid``: it is that exact message, or the one follow-up drafted
    after that exact message went (a LinkedIn ``sent`` mark whose newest earlier draft is it)."""
    invited = invite_facts(rows)
    approved = invited["approved_message"] if invited is not None else None
    if approved is None:
        return False
    if mid == approved:
        return True
    for i, row in enumerate(rows):
        if row["entry"] == "sent" and row["channel"] == "linkedin":
            drafts = [int(r["id"]) for r in rows[:i] if r["entry"] == "message"]
            if drafts and drafts[-1] == approved:
                return mid > int(row["id"])
    return False


def derive(
    rows: list[dict[str, Any]], now: datetime, auto: bool = False, note_ok: bool = False
) -> Optional[dict[str, Any]]:
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
    if not decisions and not _carried(rows, mid, auto, note_ok):
        return {**base, "state": "waiting_on_you"}
    if decisions and decisions[-1]["entry"] == DECLINED:
        return {**base, "state": "declined"}
    if decisions:
        anchor = int(decisions[-1]["id"])
        base["approved_at"] = decisions[-1]["recorded_at"]
    else:  # no click: only claims after the draft (and after the request) count
        base["auto"] = True
        anchor = max([mid] + [int(r["id"]) for r in rows if r["entry"] == RELEASED and r.get("text") == INVITED])
    after = [r for r in rows if r["entry"] in (CLAIMED, RELEASED) and int(r["id"]) > anchor]
    if after and after[-1]["entry"] == CLAIMED:
        until = (_when(after[-1]["recorded_at"]) or now) + timedelta(minutes=CLAIM_MINUTES)
        if until > now:
            return {
                **base, "state": "in_progress", "claim_until": until.isoformat(),
                "claimed_by": after[-1]["text"], "claimed_actor": after[-1].get("recorded_by") or "",
            }
    return {**base, "state": "queued"}


def backoff_until(rows: list[dict[str, Any]], now: datetime) -> Optional[datetime]:
    """When a released person may be taken again: the newest claim / release after the newest approval (or the
    newest draft: a person with no click, 2026-11-03) is a release, under :data:`RELEASE_BACKOFF_MINUTES` ago. Else
    ``None``. A release with reason ``invited`` is a request that went, not a give-up: no wait."""
    anchors = [int(r["id"]) for r in rows if r["entry"] in (APPROVED, "message")]
    after = [r for r in rows if r["entry"] in (CLAIMED, RELEASED) and anchors and int(r["id"]) > max(anchors)]
    if not after or after[-1]["entry"] != RELEASED or after[-1].get("text") == INVITED:
        return None
    until = (_when(after[-1]["recorded_at"]) or now) + timedelta(minutes=RELEASE_BACKOFF_MINUTES)
    return until if until > now else None


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
    """``{open_now, why, pc_on_hours, opens_at, run_now_until}``; why = paused | any_time | pc_on_hours |
    run_now | closed."""
    from src.services.applications import spine  # noqa: PLC0415
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    cfg = rules.effective((load_profile(user_id) or UserProfile()).assistant_settings)
    zone = await spine.user_zone(db, user_id)
    is_open, opens_at = rules.pc_on_state(cfg.pc_on_hours, now.astimezone(zone))
    until = await run_now_until(db, user_id, now)
    if rules.is_paused(cfg.paused_until, now):
        why, open_now = "paused", False
    elif cfg.pc_on_hours is None:
        why, open_now = "any_time", True
    elif is_open:
        why, open_now = "pc_on_hours", True
    elif until is not None:
        why, open_now = "run_now", True
    else:
        why, open_now = "closed", False
    return {
        "open_now": open_now, "why": why, "pc_on_hours": cfg.pc_on_hours,
        "opens_at": opens_at.isoformat() if opens_at else None,
        "run_now_until": until.isoformat() if until else None,
    }


# ── Has the user's PC worked the queue? (owner #12, 2026-11-05) ──────────────────────────────────────────


async def last_pc_activity_at(db: JobDatabase, user_id: str) -> Optional[datetime]:
    """The newest sign that a session in the user's own browser worked the queue, from rows only the own-browser
    door writes: any take, whatever it answered (:data:`TAKE_SLOT`), a person claimed or released
    (``contact_outreach``), a hunt / people / pending task claimed or closed (the ``pc_duties`` slots; never the
    user's own Run now slot). A scheduled cloud run never reaches these. ``None``: never."""
    from src.services.applications import pc_duties  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT MAX(recorded_at) FROM contact_outreach WHERE user_id = ? AND entry IN (?, ?)",
        (user_id, CLAIMED, RELEASED),
    )
    got = await cur.fetchone()
    found = [_when(got[0]) if got is not None and got[0] else None]
    slots = [TAKE_SLOT] + [p + k for p in (pc_duties.SLOT, pc_duties.CLAIM) for k in pc_duties.KINDS]
    cur = await db._db.execute(
        "SELECT MAX(done_at) FROM assistant_duty_runs WHERE user_id = ? "  # noqa: S608 - placeholders only
        f"AND duty IN ({','.join('?' for _ in slots)})",
        (user_id, *slots),
    )
    got = await cur.fetchone()
    found.append(_when(got[0]) if got is not None and got[0] else None)
    seen = [at for at in found if at is not None]
    return max(seen) if seen else None


async def stamp_take(db: JobDatabase, user_id: str, now: datetime) -> None:
    """One slot per user: the newest own-browser take, whatever it answered (owner #12: a PC session that is up
    but finds nothing to do, or is told to wait, is alive). Not a scheduled duty: ``whats_due`` never reads it."""
    await db._db.execute(
        "INSERT INTO assistant_duty_runs (user_id, duty, done_at) VALUES (?, ?, ?) "
        "ON CONFLICT (user_id, duty) DO UPDATE SET done_at = EXCLUDED.done_at",
        (user_id, TAKE_SLOT, now.astimezone(timezone.utc).isoformat()),
    )
    await db._db.commit()


def idle_fact(last: Optional[datetime], waiting: int, now: datetime) -> Optional[dict[str, Any]]:
    """Pure: ``{since, waiting, hours, text}`` when tasks wait for the user's own browser and nothing worked it
    for MORE than ``PC_IDLE_WARN_HOURS`` (exactly the limit is not yet idle), or ever; else ``None``."""
    hours = settings.PC_IDLE_WARN_HOURS
    if waiting <= 0 or (last is not None and now - last <= timedelta(hours=hours)):
        return None
    tasks = f"{waiting} task{'' if waiting == 1 else 's'} {'waits' if waiting == 1 else 'wait'}"
    when = f"since {last.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC" if last is not None else "yet"
    return {
        "since": last.isoformat() if last is not None else None, "waiting": waiting, "hours": hours,
        "text": f"Nothing has worked your PC queue {when}; {tasks}. Start your PC browser session.",
    }


async def pc_idle(db: JobDatabase, user_id: str, now: datetime, waiting: int) -> Optional[dict[str, Any]]:
    """:func:`idle_fact` for this user; ``None`` while paused (nothing is meant to run) or nothing waits."""
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    if waiting <= 0:
        return None
    cfg = rules.effective((load_profile(user_id) or UserProfile()).assistant_settings)
    if rules.is_paused(cfg.paused_until, now):
        return None
    return idle_fact(await last_pc_activity_at(db, user_id), waiting, now)


# ── The queue ───────────────────────────────────────────────────────────────────────────────────────────


async def _contact_tasks(db: JobDatabase, user_id: str, now: datetime) -> list[dict[str, Any]]:
    from src.services.applications import contacts  # noqa: PLC0415
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    auto = rules.effective((load_profile(user_id) or UserProfile()).assistant_settings).outreach_send == (
        rules.OUTREACH_SEND_AUTO
    )

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
        (user_id, APPROVED, APPROVED, DECLINED, QUEUE_MAX),
    )
    ids = [int(dict(r)["contact_id"]) for r in await cur.fetchall()]
    # No click on the newest draft (owner 2026-11-03): a request that was approved and went out, or any
    # unsent, unskipped, undeclined LinkedIn draft while outreach_send is auto; derive and the states decide.
    cur = await db._db.execute(
        "SELECT m.contact_id FROM contact_outreach m WHERE m.user_id = ? AND m.entry = 'message' "
        "AND m.channel = 'linkedin' AND m.id = (SELECT MAX(x.id) FROM contact_outreach x "
        "                                       WHERE x.contact_id = m.contact_id AND x.entry = 'message') "
        "AND NOT EXISTS (SELECT 1 FROM contact_outreach s WHERE s.contact_id = m.contact_id AND s.id > m.id "
        "                AND s.entry IN ('sent', 'reply', 'skipped', ?, ?)) "
        "AND (? OR EXISTS (SELECT 1 FROM contact_outreach i WHERE i.contact_id = m.contact_id AND i.entry = ? "
        "                  AND i.text = ?)) ORDER BY m.id ASC LIMIT ?",
        (user_id, APPROVED, DECLINED, auto, RELEASED, INVITED, QUEUE_MAX),
    )
    ids += [c for c in (int(dict(r)["contact_id"]) for r in await cur.fetchall()) if c not in ids]
    out: list[dict[str, Any]] = []
    for contact_id in ids:
        base = await contacts.get_owned_contact(db, user_id, contact_id)
        if base is None:
            continue
        rows = await _rows(db, contact_id)
        task = derive(rows, now, auto, note_ok=True)  # the state filter below drops anyone not not_connected
        if task is None or task["state"] not in ("queued", "in_progress"):
            continue
        if follow_up_held(rows, task["message_id"], now):
            continue  # one follow-up, only once due, never after a reply
        if await contacts.over_limit_notes(db, user_id, [contact_id]):
            continue  # a stored draft over the note limit is the assistant's rewrite, never a send
        view = await contacts._full_contact_view(db, user_id, base)
        state, invited = view.get("linkedin_state"), invite_facts(rows) is not None
        if state == "pending" or (state == "not_connected" and invited):
            continue  # pending: never messaged; nobody gets a second request
        if task.get("auto") and not (state == "connected" if invited else state == "not_connected"):
            continue  # without a click: only the message after a request, or a request without a note
        text = view["outreach"]["messages"][-1]["text"] if view["outreach"]["messages"] else ""
        route = routing.browser_for(linkedin=True)  # owner 2026-10-26: LinkedIn only in the own browser
        out.append({
            "task_id": f"contact:{contact_id}", "kind": "linkedin_outreach",
            "what": f"Send your LinkedIn message to {view['name']}", "url": view.get("linkedin_url") or "",
            "contact_id": contact_id, "application_id": view.get("application_id"), "name": view["name"],
            "message_id": task["message_id"], "draft_text": text, "draft_chars": len(text),
            "linkedin_state": state,
            "why_own_browser": WHY_WORDS["default" if not task.get("auto") else "auto"],
            "owner_yes": not task.get("auto"),
            "no_note": state != "connected" and is_after_connect(rows, task["message_id"]),
            "claim": ({"session": task["claimed_by"], "until": task["claim_until"], "actor": task["claimed_actor"]}
                      if task["state"] == "in_progress" else None),
            "browser": routing.label(route),
            "approved_at": task["approved_at"],  # read by queued_outreach; the route models drop it
        })
    return out


async def queued_outreach(db: JobDatabase, user_id: str, now: datetime) -> list[dict[str, Any]]:
    """The approved, unsent LinkedIn sends (state queued / in_progress) as rows of the ONE batch queue (owner
    2026-10-26: applications and outreach in one list, ``list_applications queued=true``). Read-only: nothing
    here sends; the click, the claim and ``check_outreach`` are unchanged. Email has no approved-but-unsent
    state (``outreach_send`` = auto is a setting, not a per-message yes), so it is never listed. No draft
    text, only names and ids. Scoped by ``user_id``."""
    out: list[dict[str, Any]] = []
    for t in await _contact_tasks(db, user_id, now):
        note = "linkedin_message" if t["linkedin_state"] == "connected" else "linkedin_note"
        out.append({
            "task_id": t["task_id"], "kind": "outreach", "outreach_kind": note,
            "title": f"LinkedIn {'message' if note == 'linkedin_message' else 'note'} to {t['name']}",
            "name": t["name"], "contact_id": t["contact_id"], "application_id": t["application_id"],
            "message_id": t["message_id"], "approved_at": t["approved_at"],
            "state": "in_progress" if t["claim"] else "queued", "browser": t["browser"],
        })
    return out


def merged_order(queued_at: dict[int, str], outreach: list[dict[str, Any]]) -> list[str]:
    """The one queue's order: ``application:<id>`` by when it was queued and ``contact:<id>`` by when the user
    pressed Send, oldest yes first (ties: applications first, then id)."""
    far = datetime.max.replace(tzinfo=timezone.utc)
    keyed = [((_when(at) or far), 0, app_id, f"application:{app_id}") for app_id, at in queued_at.items()]
    keyed += [((_when(o["approved_at"]) or far), 1, int(o["contact_id"]), str(o["task_id"])) for o in outreach]
    return [k[3] for k in sorted(keyed)]


async def _application_tasks(
    db: JobDatabase, user: CurrentUser, now: datetime, session: Optional[str]
) -> list[dict[str, Any]]:
    from src.services.applications import blocked, kit, spine  # noqa: PLC0415
    from src.services.browser_agent import learned  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT id, job_title, job_company, job_url FROM applications WHERE user_id = ? AND status = 'considering' "
        "ORDER BY id DESC LIMIT 500",
        (user.id,),
    )
    apps = {int(dict(r)["id"]): dict(r) for r in await cur.fetchall()}
    blocks = await blocked.open_blocks(db, user.id, list(apps))
    routes: list[tuple[int, routing.Route]] = []
    codes = await blocked.codes_mine(user.id, blocks)
    for app_id, p in sorted(blocks.items()):
        handoff = blocked.captcha_handoff(p)
        code = blocked.assistant_reads_code(p, codes)  # F8: a code box the user's setting makes the assistant's
        if p.get(blocked.BY_KEY) != blocked.BY_BROWSER and not handoff and not code:
            continue
        reason = str(p.get("reason") or "")
        route = routing.ask_route(
            blocked.ASK_KIND_FOR_REASON.get(reason, ""), block_reason=reason, block_by_browser=not handoff,
            handoff=handoff, email_codes=code,
            # While Job360's browser still holds that page the user finishes it on Needs you; once it let go, no.
            finish_here=reason == "captcha" and blocked.browser_hold_live(user.id, app_id),
        )
        if route.kind == "own_browser":
            routes.append((app_id, route))
            if route.reason == "captcha_handoff":
                _log("captcha_handoff_routed", user.id, "own_browser", application_id=app_id,
                     host=p.get("page_host") or "", reason=reason, route_reason=route.reason)
    # Easy Apply (owner 2026-10-26): a batch-queued application on linkedin.com with no open block waits here
    # from the start, oldest queued first; Job360's browser never opens it.
    # A link whose host cannot be named waits here too ("link_unclear"), so the user's own browser can check it.
    linked = [i for i, a in apps.items() if i not in blocks and routing.is_linkedin(str(a.get("job_url") or ""))]
    queued = await spine.queued_at_by_app(db, user.id, linked)
    for app_id in sorted(queued, key=lambda i: (queued[i], i)):
        routes.append((app_id, routing.browser_for(url=str(apps[app_id]["job_url"]), queue_kind="application")))
    # Owner 2026-11-02: a site where Job360's browser was blocked twice (CAPTCHA / bot check) for THIS user:
    # the next queued job there starts here, before Job360's browser meets the wall again.
    hosts = await learned.blocked_hosts(db, user.id, now)
    rest = [i for i, a in apps.items() if i not in blocks and i not in queued
            and learned.route_for(str(a.get("job_url") or ""), hosts) is not None]
    later = await spine.queued_at_by_app(db, user.id, rest)
    for app_id in sorted(later, key=lambda i: (later[i], i)):
        route = routing.browser_for(url=str(apps[app_id]["job_url"]), queue_kind="application",
                                    route=learned.LEARNED_ROUTE)
        if route.kind == "own_browser":
            routes.append((app_id, route))
    out: list[dict[str, Any]] = []
    for app_id, route in routes:
        if session is not None and await kit.live_hold(db, user, app_id, now, session) is not None:
            continue
        a = apps[app_id]
        out.append({
            "task_id": f"application:{app_id}", "kind": "application",
            "what": f"Finish the application for {a['job_title']} at {a['job_company']}",
            "url": a.get("job_url") or "", "contact_id": None, "application_id": app_id, "name": "",
            "message_id": None, "draft_text": "", "draft_chars": 0, "linkedin_state": None,
            "why_own_browser": WHY_WORDS.get(route.reason, ""), "owner_yes": False, "claim": None,
            "browser": routing.label(route, site=routing.site_of(str(a.get("job_url") or ""))[1]),
        })
        if len(out) >= QUEUE_MAX:
            break
    return out


async def queue_tasks(
    db: JobDatabase, user: CurrentUser, now: datetime, session: Optional[str] = None
) -> list[dict[str, Any]]:
    """The tasks that wait for the user's own browser: approved, unsent LinkedIn drafts (oldest approval first),
    then the signed-in LinkedIn hunt and people search when due (``pc_duties``, owner 2026-11-03), then
    applications whose open block Job360's browser wrote and the router sends to ``own_browser``, then
    batch-queued Easy Apply applications (an apply link on linkedin.com, no open block). With a
    ``session`` (an assistant's read) an application another session holds is left out; the website passes none."""
    from src.services.applications import pc_duties  # noqa: PLC0415

    tasks = (await _contact_tasks(db, user.id, now) + await pc_duties.tasks(db, user.id, now)
             + await _application_tasks(db, user, now, session))
    return tasks[:QUEUE_MAX]


# ── Take and release ────────────────────────────────────────────────────────────────────────────────────


# A LinkedIn send, or a claim Job360 wrote itself that was neither released nor followed by a sent mark: a
# send that was never recorded still counts toward the pace.
_SENDS = (
    "o.user_id = ? AND ((o.entry = 'sent' AND o.channel = 'linkedin') OR (o.entry = 'send_released' AND "
    "o.text = 'invited') OR (o.entry = 'send_claimed' AND NOT EXISTS "
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


async def invites_since(db: JobDatabase, user_id: str, since: datetime) -> int:
    """Connection requests since ``since``: those released ``invited`` (no note), plus (owner 2026-11-13) a LinkedIn
    ``sent`` mark for a person with no earlier invite whose saved state was not ``connected`` when it was recorded:
    a connection NOTE is a request too, and LinkedIn's weekly limit counts it."""
    stamp = since.astimezone(timezone.utc).isoformat()
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM contact_outreach WHERE user_id = ? AND entry = ? AND text = ? AND recorded_at >= ?",
        (user_id, RELEASED, INVITED, stamp),
    )
    got = await cur.fetchone()
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM contact_outreach s WHERE s.user_id = ? AND s.entry = 'sent' AND s.channel = 'linkedin' "
        "AND s.recorded_at >= ? AND NOT EXISTS (SELECT 1 FROM contact_outreach i WHERE i.contact_id = s.contact_id "
        "AND i.entry = ? AND i.text = ? AND i.id < s.id) "
        "AND COALESCE((SELECT e.value FROM contact_edits e WHERE e.contact_id = s.contact_id AND e.user_id = s.user_id "
        "AND e.field = 'linkedin_state' AND e.recorded_at <= s.recorded_at ORDER BY e.id DESC LIMIT 1), '') "
        "<> 'connected'",
        (user_id, stamp, RELEASED, INVITED),
    )
    notes = await cur.fetchone()
    return (int(got[0]) if got is not None else 0) + (int(notes[0]) if notes is not None else 0)


_SUBMIT_MARKS = "e.event_type IN ('applied', 'submit_attempted')"


async def easy_apply_today(db: JobDatabase, user_id: str, now: datetime) -> int:
    """How many Easy Apply submits fall on the user's own day: applications whose apply link is on linkedin.com
    with an ``applied`` or ``submit_attempted`` event, each application once, at its first such mark (a click
    that was never recorded still counts, as an unreleased claim does for a LinkedIn send)."""
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415

    floor = (now - timedelta(hours=48)).isoformat()
    cur = await db._db.execute(
        "SELECT DISTINCT a.id, a.job_url FROM applications a JOIN application_events e ON e.application_id = a.id "  # noqa: S608
        f"AND e.user_id = a.user_id WHERE a.user_id = ? AND {_SUBMIT_MARKS} AND e.recorded_at >= ?",
        (user_id, floor),
    )
    ids = [int(dict(r)["id"]) for r in await cur.fetchall() if routing.names_linkedin(str(dict(r)["job_url"] or ""))]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    return await rules.count_on_user_today(
        db, user_id, now,
        "SELECT x.recorded_at FROM (SELECT MIN(e.recorded_at) AS recorded_at FROM application_events e "  # noqa: S608
        f"WHERE e.user_id = ? AND {_SUBMIT_MARKS} AND e.application_id IN ({marks}) GROUP BY e.application_id) x "
        "WHERE x.recorded_at >= ?",
        (user_id, *ids),
    )


async def _pace(db: JobDatabase, user_id: str, now: datetime) -> Optional[tuple[str, int]]:
    """``("wait", seconds_left)`` when the last LinkedIn send was under the minimum gap ago;
    ``("daily_limit", 0)`` at the day's maximum (the user's own day and own ``linkedin_sends_per_day``); else
    ``None``."""
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    last, today = await pace_facts(db, user_id, now)
    if last is not None:
        left = LINKEDIN_MIN_GAP_SECONDS - (now - last).total_seconds()
        if left > 0:
            return "wait", int(math.ceil(left))
    cfg = rules.effective((load_profile(user_id) or UserProfile()).assistant_settings)
    return ("daily_limit", 0) if today >= rules.linkedin_daily_max(cfg) else None


def _answer(status: str, why: str = "", **extra: Any) -> dict[str, Any]:
    return {"status": status, "why": why, "task": None, "next": "", **extra}


def _next_step(task: dict[str, Any]) -> str:
    if task["kind"] in ("linkedin_search", "profile_read", "linkedin_check_pending"):
        what = {"linkedin_search": "bring_job each new job you judge fits",
                "linkedin_check_pending": "update_contact linkedin_state for each of its contact_ids",
                }.get(task["kind"], "add_contact each person you find")
        return (f'get_recipe("own_browser") {task["kind"]}: signed in, in the user\'s own browser only, {what}; '
                f'then own_browser_queue release (session, task_id="{task["task_id"]}", reason="done"), or reason '
                "captcha | login_wall | other when you could not finish it")
    if task["kind"] == "application":
        return f'get_application_kit(application_id={task["application_id"]}, session="<your session>")'
    done = ("Connect, Send without a note, then own_browser_queue release reason invited" if task.get("no_note")
            else "Connect, Add a note, paste the draft exactly, send, then record_event outreach_sent"
            if task.get("linkedin_state") == "not_connected" and not task.get("owner_yes", True)
            else "send the draft, then record_event outreach_sent")
    return (
        f'call check_outreach(contact_id={task["contact_id"]}, message_id={task["message_id"]}, '
        f"session=<your session>); only if it says send, open the person's LinkedIn page, {done}. "
        "Otherwise own_browser_queue release with reason other"
    )


async def take(
    db: JobDatabase, user: CurrentUser, session: str, now: datetime, contact_id: Optional[int] = None
) -> dict[str, Any]:
    """Hand ONE task to the session (15 minutes for a person; an application's kit read is its own hold).
    With ``contact_id`` only that person is claimed (the one the user clicked Send on);
    ``empty`` when they are not waiting for a send. The same locks, pace, pause and window apply.
    ``status``: task | already_yours | empty | closed (outside the PC-on hours, no Run now) | paused | busy
    (another session is sending) | wait (``wait_seconds`` left; ``why`` released_recently:
    :data:`RELEASE_BACKOFF_MINUTES`) | daily_limit (LinkedIn sends, or Easy Apply at
    ``EASY_APPLY_DAILY_MAX``: :func:`easy_apply_today`). The same session asking again
    gets its live claim back. The user row is locked first, so two sessions never take one person."""
    from src.services.applications import kit, pc_duties  # noqa: PLC0415
    from src.services.applications.authorship import actor_for  # noqa: PLC0415

    label = kit.clean_session(session)
    if not label:
        raise SpineError(422, "session is needed: a short label that tells your sessions apart")
    actor = actor_for(user)
    await stamp_take(db, user.id, now)
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
        # One person at a time is judged over ALL the user's people, before any contact_id narrowing: a
        # session holding someone gets that one back, and another session's live claim makes this one busy.
        mine = next(
            (t for t in contacts_ if t["claim"] and (t["claim"]["session"], t["claim"]["actor"]) == (label, actor)),
            None,
        )
        if mine is not None:
            _log("own_browser_take", user.id, "already_yours", contact_id=mine["contact_id"])
            return _answer("already_yours", task=mine, next=_next_step(mine))
        duties = [t for t in tasks if t["kind"] in pc_duties.KINDS] if contact_id is None else []
        held = next(
            (t for t in duties if t["claim"] and (t["claim"]["session"], t["claim"]["actor"]) == (label, actor)), None
        )
        if held is not None:
            _log("own_browser_take", user.id, "already_yours", kind=held["kind"])
            return _answer("already_yours", task=held, next=_next_step(held))
        busy = any(t["claim"] is not None for t in contacts_)  # another session holds a person
        free = [t for t in contacts_ if t["claim"] is None]
        if contact_id is not None:
            free = [t for t in free if t["contact_id"] == contact_id]
            tasks = [t for t in contacts_ if t["contact_id"] == contact_id]
        if free and (await weekly_limit_until(db, user.id, now) is not None or await invites_since(
            db, user.id, now - timedelta(days=7)
        ) >= settings.LINKEDIN_INVITES_PER_WEEK_MAX):
            # LinkedIn's invitation limit is live: an invite would fail again, so only people already
            # connected (a plain message, no invite) are handed out until it is over.
            free = [t for t in free if t.get("linkedin_state") == "connected"]
        chosen: Optional[dict[str, Any]] = None
        refusal: Optional[dict[str, Any]] = None
        backed: list[datetime] = []
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
                    rows = await _rows(db, candidate["contact_id"])
                    noted = candidate.get("linkedin_state") == "not_connected"
                    state = derive(rows, now, not candidate["owner_yes"], noted)
                    if state is None or state["state"] != "queued":
                        continue
                    later = backoff_until(rows, now)
                    if later is not None:  # released a moment ago: never the same person straight back
                        backed.append(later)
                        continue
                    try:
                        await _insert(db, user, candidate["contact_id"], CLAIMED, label, now)
                    except SpineError:  # too many claims on this person: try the next one
                        continue
                    chosen = candidate
                    break
            if chosen is None and refusal is None and backed:
                left = int(math.ceil((min(backed) - now).total_seconds()))
                refusal = _answer("wait", "released_recently", wait_seconds=left)
        elif busy:
            refusal = _answer("busy", "another session is sending")
        duty = next((t for t in duties if t["claim"] is None), None)
        if chosen is None and duty is not None:  # a hunt or people task: one session at a time per kind
            await pc_duties.claim(db, user.id, duty["kind"], {"session": label, "actor": actor}, now)
            until = (now + timedelta(minutes=settings.DUTY_CLAIM_MINUTES)).isoformat()
            chosen = {**duty, "claim": {"session": label, "until": until, "actor": actor}}
    if chosen is not None and chosen["kind"] in pc_duties.KINDS:
        _log("own_browser_take", user.id, "task", kind=chosen["kind"])
        return _answer("task", task=chosen, next=_next_step(chosen))
    if chosen is not None:
        _log("own_browser_take", user.id, "task", contact_id=chosen["contact_id"], actor=actor_for(user))
        return _answer("task", task=chosen, next=_next_step(chosen))
    apps = [t for t in tasks if t["kind"] == "application"]
    if apps:
        released = await _recently_released(db, user.id, [int(t["application_id"]) for t in apps], now)
        apps = [t for t in apps if int(t["application_id"]) not in released]
    if EASY_APPLY_DAILY_MAX > 0 and any(routing.names_linkedin(t["url"]) for t in apps) and (
        await easy_apply_today(db, user.id, now) >= EASY_APPLY_DAILY_MAX
    ):  # Easy Apply's own day cap; any other application is still handed out
        apps = [t for t in apps if not routing.names_linkedin(t["url"])]
        refusal = refusal or _answer("daily_limit", "daily_limit", wait_seconds=0)
    if apps:
        _log("own_browser_take", user.id, "task", application_id=apps[0]["application_id"])
        return _answer("task", task=apps[0], next=_next_step(apps[0]))
    if refusal is not None:
        _log("own_browser_take", user.id, refusal["status"])
        return refusal
    _log("own_browser_take", user.id, "empty")
    return _answer("empty", "nothing is waiting for your own browser")


async def _recently_released(db: JobDatabase, user_id: str, app_ids: list[int], now: datetime) -> set[int]:
    """Applications with a ``hold_released`` inside ``APPLICATION_RELEASE_COOLDOWN_MINUTES``."""
    cutoff = (now - timedelta(minutes=APPLICATION_RELEASE_COOLDOWN_MINUTES)).isoformat()
    marks = ",".join("?" for _ in app_ids)
    cur = await db._db.execute(
        "SELECT DISTINCT application_id FROM application_events WHERE user_id = ? "  # noqa: S608 - placeholders
        f"AND event_type = 'hold_released' AND recorded_at >= ? AND application_id IN ({marks})",
        (user_id, cutoff, *app_ids),
    )
    return {int(dict(r)["application_id"]) for r in await cur.fetchall()}


async def release(
    db: JobDatabase, user: CurrentUser, session: str, task_id: str, reason: str, now: datetime
) -> dict[str, Any]:
    """Give a claimed person back, or close a hunt or people task (``pc_duties.close``: ``done``, else failed).
    409 unless this session holds the live claim; 422 for an unknown reason or task id."""
    from src.services.applications import kit, pc_duties  # noqa: PLC0415
    from src.services.applications.authorship import actor_for  # noqa: PLC0415

    label = kit.clean_session(session)
    if reason not in RELEASE_REASONS:
        raise SpineError(422, "reason must be one of: " + ", ".join(RELEASE_REASONS))
    kind, _, raw = task_id.partition(":")
    if kind + ":" == pc_duties.TASK_PREFIX:
        closed = await pc_duties.close(db, user, label, raw, reason, now)
        _log("own_browser_release", user.id, closed["outcome"], kind=raw, reason=reason)
        return {"released": True, "task_id": task_id, "reason": reason, **closed}
    if reason == "done":
        raise SpineError(422, "a person is finished with record_event outreach_sent; release them with another reason")
    if kind != "contact" or not raw.isdigit():
        raise SpineError(409, "only a claimed person can be released; an application is released with release_hold")
    contact_id = int(raw)
    async with db._db.transaction():
        cur = await db._db.execute(
            "SELECT id FROM application_contacts WHERE id = ? AND user_id = ? FOR UPDATE", (contact_id, user.id)
        )
        if await cur.fetchone() is None:
            raise SpineError(404, "contact not found")
        state = derive(await _rows(db, contact_id), now, auto=True, note_ok=True)  # a claim with no click too
        if state is None or state["state"] != "in_progress" or not label or (
            state["claimed_by"], state["claimed_actor"]
        ) != (label, actor_for(user)):
            _log("own_browser_release", user.id, "refused", contact_id=contact_id)
            raise SpineError(409, "you do not hold a live claim on this person")
        if reason == INVITED:
            from src.services.applications import contacts  # noqa: PLC0415 - contacts imports this module

            rows = await _rows(db, contact_id)
            if invite_facts(rows) is not None or await contacts._linkedin_state_now(db, user.id, contact_id) == (
                "connected"
            ):
                raise SpineError(409, "a request already went to this person, or they are connected: never a second")
            if not is_after_connect(rows, state["message_id"]):
                raise SpineError(
                    409, "invited is for an after_connect draft only; a note is recorded with outreach_sent"
                )
            await db._db.execute(
                "INSERT INTO contact_edits (user_id, contact_id, field, value, recorded_at, recorded_by) "
                "VALUES (?, ?, 'linkedin_state', 'pending', ?, ?)",
                (user.id, contact_id, now.astimezone(timezone.utc).isoformat(), actor_for(user)),
            )
        await _insert(db, user, contact_id, RELEASED, reason, now)
    _log("own_browser_release", user.id, "ok", contact_id=contact_id, reason=reason)
    return {"released": True, "task_id": task_id, "reason": reason}
