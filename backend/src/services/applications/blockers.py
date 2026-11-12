"""What waits on whom, per application: one ordered list, derived on read (slice 3 of the blockers work).

``blockers_for(db, user_id, ids)`` reads existing rows only (events, asks, CVs, receipts, people) in a fixed
number of queries whatever ``len(ids)`` is, and returns ``{application_id: [Blocker, ...]}`` ordered by what the
owner does first. Nothing is written (M3) and nothing here is permission to submit: ``may_submit`` stays the one
gate. Always scoped by ``user_id``: an id that is not the caller's is ignored (rules #12/#25).

Owner answers 2026-10-31 applied here:
* a note for a job below the user's score line, or for a dead job (rejected / withdrawn / posting closed), is
  never a blocker; a note waits until the application is sent (status past ``considering``);
* ``confirm_sent`` (the employer's "we received it" email is stored as proof while the job is not applied; the
  user clicks Mark Applied or It was not sent) comes first of all;
* order: posting_closed, site_step, approve_cv, answers, send_yes, apply_by_hand, notes on you, notes on your PC,
  then assistant, then employer; ties oldest first;
* a CAPTCHA / sign-in / code / account / upload / safety stop is ALWAYS the user's (actor ``you``), whichever
  browser hit it, and its open ask and its ``blocked`` event are ONE blocker.
A closed application (rejected, withdrawn, ghosted, hired, declined) has none. A form yes older than its
time-to-live reads ``Send`` again; the finer fingerprint (changed cover letter, form page) stays with the gate.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, TypedDict

from src.core import settings
from src.services.applications import (
    asks,
    blocked,
    kit,
    needs_you_count,
    own_browser,
    queue_preflight,
    ready,
    spine,
    test_marker,
)
from src.services.browser_agent import learned
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

Actor = str  # "you" | "your_pc" | "assistant" | "job360" | "employer"


class Blocker(TypedDict):
    kind: str
    actor: Actor
    label: str
    since: str
    ref: Optional[dict[str, Any]]


_DEAD = ("rejected", "withdrawn")
# kind order (lower = sooner); notes on the user's PC come after the ones on the user, then the others.
_RANK = {
    "confirm_sent": -1, "posting_closed": 0, "site_step": 1, "approve_cv": 2, "answers": 3, "send_yes": 4,
    "apply_by_hand": 5, "outreach_send": 6, "assistant_step": 8, "queued": 9, "follow_up": 10, "waiting_employer": 11,
}
_SITE_WORDS = {
    "captcha": "solve the CAPTCHA", "bot_check": "solve the CAPTCHA", "login_needed": "sign in",
    "login": "sign in", "otp_needed": "enter the code", "account_creation": "create an account",
    "upload_failed": "attach your CV", "upload": "attach your CV",
    blocked.ALREADY_APPLIED: "mark it applied",
}
_FILL_TYPES = ("blocked", "unblocked", "form_filled", "form_swept", "submit_approved", "submit_declined", "cv_seen")


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


def _when(value: Any) -> datetime:
    try:
        at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _b(kind: str, actor: Actor, label: str, since: Any, ref: Optional[dict[str, Any]] = None) -> Blocker:
    return {"kind": kind, "actor": actor, "label": label, "since": _iso(since), "ref": ref}


def _sort_key(b: Blocker) -> tuple[int, int, datetime]:
    # outreach_send: on the user first, then on the user's PC, then a rewrite that is the assistant's.
    rank = _RANK[b["kind"]] + ({"your_pc": 1, "assistant": 2}.get(b["actor"], 0) if b["kind"] == "outreach_send" else 0)
    sub = 1 if b["kind"] == "answers" and b["label"].startswith("OK") else 0  # "answer N" before "OK N"
    return rank, sub, _when(b["since"])


def _json(raw: Any) -> dict[str, Any]:
    try:
        p = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return p if isinstance(p, dict) else {}


def _marks(placeholders: list[int]) -> str:
    return ",".join("?" for _ in placeholders)


async def _events_by_app(db: JobDatabase, user_id: str, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    types = ",".join(f"'{t}'" for t in _FILL_TYPES)  # a fixed constant tuple, never user input
    # Every fill event, but only the NEWEST form_filled of each application. Its id comes from one grouped
    # scan joined in, never a correlated ``e.id = (SELECT MAX(..) WHERE .. = e.application_id)``: Postgres
    # re-runs that per row, so the cost grew with the SQUARE of the history (prod, 2026-11-12).
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.event_type, e.payload, e.recorded_at FROM application_events e "  # noqa: S608
        "LEFT JOIN (SELECT x.application_id, MAX(x.id) AS last_id FROM application_events x WHERE x.user_id = ? "
        f"AND x.application_id IN ({_marks(ids)}) AND x.event_type = 'form_filled' "
        "GROUP BY x.application_id) m ON m.application_id = e.application_id "
        f"WHERE e.user_id = ? AND e.application_id IN ({_marks(ids)}) AND e.event_type IN ({types}) "
        "AND (e.event_type != 'form_filled' OR e.id = m.last_id) "
        "ORDER BY e.id ASC",
        (user_id, *ids, user_id, *ids),
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for r in await cur.fetchall():
        d = dict(r)
        d["payload"] = _json(d["payload"])
        out.setdefault(int(d["application_id"]), []).append(d)
    return out


def _question_class(row: dict[str, Any]) -> Optional[str]:
    """``answer`` (only the user can say it), ``ok`` (the user confirms it) or None (verified already)."""
    source, text = row.get("source"), str(row.get("answer") or "").strip()
    if source in (asks.NOT_KNOWN, "none") or not text:
        return "answer"
    if kit._question_is_sensitive(str(row.get("question") or ""), str(row.get("key") or "") or None):
        return "answer"
    return "ok" if source in ("guess", "draft", "memory") else None


def _answer_blockers(
    app_id: int, ask_rows: list[dict[str, Any]], flags: list[dict[str, Any]], since: Any, unknown_block: bool,
) -> list[Blocker]:
    need: dict[str, str] = {}
    ask_id: Optional[int] = None
    for ask in ask_rows:
        ask_id = ask_id or int(ask["id"])
        rows = ask["questions"] or [{"key": f"ask_{ask['id']}", "question": ask["question"], "source": "none"}]
        for q in rows:
            if (cls := _question_class(q)) is not None:
                need[str(q.get("key") or q.get("question"))] = cls
    for f in flags:
        cls = {"blank": "answer", "sensitive": "answer", "guessed": "ok", "unapproved": "ok"}.get(f["code"])
        key = str(f.get("key") or f["text"])
        if cls and need.get(key) != "answer":
            need[key] = cls
    to_answer = sum(1 for c in need.values() if c == "answer") or (1 if unknown_block else 0)
    to_ok = sum(1 for c in need.values() if c == "ok")
    ref = {"ask_id": ask_id} if ask_id else None
    out: list[Blocker] = []
    if to_answer:
        out.append(_b("answers", "you", f"answer {_plural(to_answer, 'question', 'questions')}", since, ref))
    if to_ok:
        out.append(_b("answers", "you", f"OK {_plural(to_ok, 'answer', 'answers')}", since, ref))
    return out


async def blockers_for(
    db: JobDatabase, user_id: str, application_ids: list[int], *, now: Optional[datetime] = None,
    tests: Optional[set[int]] = None,
) -> dict[int, list[Blocker]]:
    """``{application_id: ordered blockers}`` for the caller's own applications among ``application_ids``
    (others are left out of the result). ``[]`` = nothing waits. A fixed number of queries, never one per id,
    and at most two profile reads (the score line's, one shared by the rest). ``tests``: the caller's own
    ``test_marker.test_app_ids`` answer (None = read it here)."""
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    wanted = sorted({int(i) for i in application_ids})
    if not wanted:
        return {}
    cur = await db._db.execute(
        "SELECT id, status, fit_score, fit_recorded_at, job_url, job_country, job_company, follow_up_on, "  # noqa: S608
        f"updated_at FROM applications WHERE user_id = ? AND id IN ({_marks(wanted)})",  # noqa: S608
        (user_id, *wanted),
    )
    apps = {int(r["id"]): dict(r) for r in await cur.fetchall()}
    ids = sorted(apps)
    result: dict[int, list[Blocker]] = {i: [] for i in ids}
    if not ids:
        return result
    if tests is None:
        tests = await test_marker.test_app_ids(db, user_id)
    ids = [i for i in ids if i not in tests and apps[i]["status"] not in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES]
    if ids:
        await _fill(db, user_id, ids, apps, result, now)
    for lst in result.values():
        lst.sort(key=_sort_key)
    get_audit_logger().info(
        "blockers_derived",
        extra={"event": "blockers_derived", "user_id": safe_log_value(user_id), "ids": len(wanted),
               "blockers": sum(len(v) for v in result.values()), "ms": int((time.monotonic() - started) * 1000),
               "result": "ok"},
    )
    return result


# Where a caller that wants them (the needs-you badge's Server-Timing header) collects the milliseconds of
# each part of ``_fill``. None = nobody asked: nothing is recorded. Durations only, never a value.
SUBSTEPS: ContextVar[Optional[dict[str, float]]] = ContextVar("blockers_substeps", default=None)


class _Lap:
    """Times the parts of one ``_fill`` run into ``SUBSTEPS`` (a no-op when nobody asked)."""

    def __init__(self) -> None:
        self._into = SUBSTEPS.get()
        self._at = time.perf_counter()

    def __call__(self, part: str) -> None:
        now = time.perf_counter()
        if self._into is not None:
            self._into[part] = round(self._into.get(part, 0.0) + (now - self._at) * 1000, 1)
        self._at = now


async def _fill(  # noqa: C901 - one linear derivation, one rule per branch
    db: JobDatabase, user_id: str, ids: list[int], apps: dict[int, dict[str, Any]],
    result: dict[int, list[Blocker]], now: datetime,
) -> None:
    lap = _Lap()
    events = await _events_by_app(db, user_id, ids)
    lap("events")
    blocks = await blocked.open_blocks(db, user_id, ids)
    lap("blocks")
    cvs = await ready.newest_cv_by_app(db, user_id, ids)
    lap("cvs")
    cur = await db._db.execute(
        "SELECT DISTINCT application_id FROM application_receipts "  # noqa: S608
        f"WHERE user_id = ? AND application_id IN ({_marks(ids)})",
        (user_id, *ids),
    )
    receipts = {int(r[0]) for r in await cur.fetchall()}
    cur = await db._db.execute(
        "SELECT id, application_id, kind, question, questions, asked_at FROM application_asks "  # noqa: S608
        f"WHERE user_id = ? AND answered_at IS NULL AND withdrawn_at IS NULL AND application_id IN ({_marks(ids)}) "
        "ORDER BY id ASC",
        (user_id, *ids),
    )
    open_asks: dict[int, list[dict[str, Any]]] = {}
    for r in await cur.fetchall():
        d = dict(r)
        d["questions"] = asks._json_or(d.get("questions"), [])
        open_asks.setdefault(int(d["application_id"]), []).append(d)
    queued_at = await spine.queued_at_by_app(db, user_id, None)
    hosts = await learned.blocked_hosts(db, user_id) if queued_at else frozenset()
    position = {a: n for n, (a, _t) in enumerate(sorted(queued_at.items(), key=lambda kv: (_when(kv[1]), kv[0])), 1)}
    lap("asks_queue")
    line = await spine.user_score_line(user_id)
    today_iso = (await spine.user_today(db, user_id)).isoformat()
    lap("score_today")

    fills: dict[int, dict[str, Any]] = {}
    state: dict[int, dict[str, Any]] = {}
    for app_id in ids:
        a, evs = apps[app_id], events.get(app_id, [])
        last = {t: [e for e in evs if e["event_type"] == t] for t in _FILL_TYPES}
        fill = last["form_filled"][-1] if last["form_filled"] else None
        fill_id = int(fill["id"]) if fill else 0
        declined = any(int(e["id"]) > fill_id for e in last["submit_declined"])
        approved = [e for e in last["submit_approved"] if int(e["id"]) > fill_id]
        considering = a["status"] == "considering"
        fill_open = bool(fill) and considering and app_id not in receipts and not declined
        cv = cvs.get(app_id)
        marks = [*last["cv_seen"], *last["submit_approved"]]
        seen = cv is not None and any(kit.cv_mark_matches(e, cv["artifact_id"], cv["sha"]) for e in marks)
        reason = str(blocks.get(app_id, {}).get("reason") or "")
        state[app_id] = {
            "fill": fill, "fill_open": fill_open, "approved": approved[-1] if approved else None, "cv": cv,
            "seen": seen, "block": blocks.get(app_id), "reason": reason,
            "block_ev": last["blocked"][-1] if last["blocked"] and blocks.get(app_id) is not None else None,
            "swept": bool(fill) and any(int(e["id"]) > fill_id for e in last["form_swept"]),
            "below": settings_line_side(a.get("fit_score"), line) == "below",
        }
        if fill_open and fill and isinstance(fill["payload"].get("answers"), list):
            fills[app_id] = {
                "job_country": a.get("job_country"), "job_company": a.get("job_company"),
                "filled_at": fill["recorded_at"], "answers": fill["payload"]["answers"],
            }
    # Owner 2026-11-09: a strong review of the exact newest CV clears "approve the CV" (cv_auto_approve on).
    unseen = {i: state[i]["cv"] for i in ids if state[i]["cv"] and not state[i]["seen"]}
    # ONE profile read for the answer flags and the CV-review rule (2026-11-12: each read its own, a fresh
    # connection each, on the menu badge of every page); none when neither has anything to judge.
    loaded: Optional[tuple[Any, list[dict[str, Any]]]] = None
    lap("state")
    if fills or unseen:
        from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415 - profile imports spine

        loaded = await asyncio.to_thread(load_profile_with_overlay, user_id)
    lap("profile")
    flags = await ready.answer_flags_for(db, user_id, fills, loaded=loaded)
    lap("flags")
    reviewed = await kit.reviews_clearing(db, user_id, unseen, profile=_or_empty(loaded))
    lap("reviews")

    sent_ids = [i for i in ids if apps[i]["status"] != "considering" and not state[i]["below"]
                and apps[i]["status"] not in _DEAD and state[i]["reason"] != "posting_closed"]
    notes = await _outreach(db, user_id, sent_ids, now)
    for app_id, due in (await _linkedin_follow_ups(db, user_id, sent_ids, now)).items():
        notes.setdefault(app_id, []).extend(due)

    confirmed = await kit.pending_confirmations(db, user_id, ids)
    from src.services.applications import auto_applied  # noqa: PLC0415 - email_match imports contacts

    silent = await auto_applied.silent_submits(db, user_id, ids, now)
    lap("outreach_confirm")
    for app_id in ids:
        a, s, out = apps[app_id], state[app_id], result[app_id]
        if (conf := confirmed.get(app_id)) is not None and a["status"] not in _DEAD:
            conf_ref = {"event_id": conf["id"], "subject": conf["source_subject"] or None,
                   "received_at": conf["source_received_at"] or conf["recorded_at"]}
            out.append(_b("confirm_sent", "you", "employer confirmed — did it go out?", conf["recorded_at"], conf_ref))
        elif (quiet := silent.get(app_id)) is not None and a["status"] not in _DEAD:
            # Owner 2026-11-02: a started submit with no confirmation email in CONFIRM_SENT_WAIT_DAYS asks the user.
            quiet_ref = {"event_id": quiet["id"], "reason": "no_confirmation_email"}
            label = f"no confirmation email in {settings.CONFIRM_SENT_WAIT_DAYS} days — did it go out?"
            out.append(_b("confirm_sent", "you", label, quiet["recorded_at"], quiet_ref))
        considering = a["status"] == "considering"
        reason, block_ev = s["reason"], s["block_ev"]
        block_since = block_ev["recorded_at"] if block_ev else a["updated_at"]
        site_asks = [k for k in open_asks.get(app_id, []) if k["kind"] in asks.BLOCKER_FAMILY]
        question_asks = [k for k in open_asks.get(app_id, []) if k["kind"] in asks.QUESTION_FAMILY]
        if considering and reason == "posting_closed":
            ref = {"event_id": block_ev["id"]} if block_ev else None
            out.append(_b("posting_closed", "you", "skip or keep", block_since, ref))
        elif considering and (site_asks or (s["block"] is not None and reason != "unknown_question")):
            ask_kind = site_asks[0]["kind"] if site_asks else ""
            word = _SITE_WORDS.get(reason) or _SITE_WORDS.get(ask_kind) or "finish on the site"
            ask_id = site_asks[0]["id"] if site_asks else None
            if reason == blocked.ALREADY_APPLIED:  # its ask is a plain question, linked by the block itself
                ask_id = (s["block"] or {}).get("ask_id")
            ref = {k: v for k, v in (("ask_id", ask_id),
                                     ("event_id", block_ev["id"] if block_ev else None)) if v is not None}
            since = site_asks[0]["asked_at"] if site_asks and not block_ev else block_since
            out.append(_b("site_step", "you", word, since, ref or None))
        if s["fill_open"] or question_asks or reason == "unknown_question":
            fill_at = s["fill"]["recorded_at"] if s["fill"] else block_since
            since = min((k["asked_at"] for k in question_asks), key=_when, default=fill_at)
            out += _answer_blockers(app_id, question_asks, flags.get(app_id, []), since, reason == "unknown_question")
        if considering and s["cv"] and not s["seen"] and app_id not in receipts and app_id not in reviewed:
            cv_ref = {"artifact_id": s["cv"]["artifact_id"]}
            out.append(_b("approve_cv", "you", "approve the CV", s["cv"]["created_at"], cv_ref))
        if s["fill_open"]:
            yes = s["approved"]
            if yes is None:
                out.append(_b("send_yes", "you", "Send", s["fill"]["recorded_at"], {"event_id": s["fill"]["id"]}))
            elif kit._approval_age_over(yes["recorded_at"], now, yes.get("payload")):
                label = "Send again (your yes expired)"
                out.append(_b("send_yes", "you", label, yes["recorded_at"], {"event_id": yes["id"]}))
            elif not s["below"]:
                step = "submit" if s["swept"] else "check form"
                out.append(_b("assistant_step", "assistant", step, yes["recorded_at"], {"event_id": yes["id"]}))
        if considering and app_id in queued_at:
            browser = queue_preflight.route_label(a.get("job_url") or "", s["block"] or {}, hosts)
            if browser["kind"] == "own_browser":
                out.append(_b("queued", "your_pc", "apply on the site", queued_at[app_id]))
            else:
                out.append(_b("queued", "assistant", f"in batch #{position[app_id]}", queued_at[app_id]))
        elif considering and s["cv"] and not s["fill"]:
            cv_ref = {"artifact_id": s["cv"]["artifact_id"]}
            out.append(_b("apply_by_hand", "you", "apply on the site", s["cv"]["created_at"], cv_ref))
        if considering and not s["below"] and app_id not in queued_at:
            if not a.get("fit_recorded_at"):
                out.append(_b("assistant_step", "assistant", "judge the fit", a["updated_at"]))
            elif not s["cv"]:
                out.append(_b("assistant_step", "assistant", "write the CV", a["updated_at"]))
        if a["status"] == "applied" and app_id not in receipts:
            out.append(_b("assistant_step", "assistant", "record the receipt", a["updated_at"]))
        elif a["status"] in ("applied", "interview_done"):
            days = max(0, (now - _when(a["updated_at"])).days)
            out.append(_b("waiting_employer", "employer", f"waiting {days} d", a["updated_at"]))
        if spine._follow_up_due(a.get("follow_up_on"), a["status"], today_iso):
            out.append(_b("follow_up", "assistant", "follow up", a["follow_up_on"]))
        out += notes.get(app_id, [])
        if considering and reason == blocked.ALREADY_APPLIED:
            # The site says the user already applied: only their answer waits ("mark it applied"), nothing else.
            out[:] = [b for b in out if b["kind"] in ("confirm_sent", "site_step")]
    lap("derive")


def _or_empty(loaded: Optional[tuple[Any, list[dict[str, Any]]]]) -> Any:
    """The profile of a ``load_profile_with_overlay`` answer (an empty one when the user has none); None when
    nothing was read, so the reader reads it itself."""
    if loaded is None:
        return None
    from src.services.profile.models import UserProfile  # noqa: PLC0415 - profile imports spine

    return loaded[0] or UserProfile()


def settings_line_side(score: Optional[int], line: Optional[int]) -> str:
    from src.services.profile.assistant_settings import line_side  # noqa: PLC0415 - profile imports spine

    return line_side(score, line)


async def _linkedin_follow_ups(
    db: JobDatabase, user_id: str, app_ids: list[int], now: datetime
) -> dict[int, list[Blocker]]:
    """The people of these applications who got ONE LinkedIn message ``LINKEDIN_FOLLOW_UP_DAYS`` or more ago, never
    replied and have no newer draft (``own_browser.follow_up_due``): the assistant drafts their one follow-up (owner
    2026-11-03). ``follow_up``, actor ``assistant``, ``ref.reason`` linkedin_follow_up. A person with no job carries
    the same fact on their card (``linkedin_follow_up_due``). One query; scoped by ``user_id``."""
    if not app_ids:
        return {}
    cur = await db._db.execute(
        "SELECT c.id AS contact_id, c.application_id, o.id, o.entry, o.channel, o.recorded_at "  # noqa: S608
        "FROM application_contacts c JOIN contact_outreach o ON o.contact_id = c.id "  # placeholders only
        f"WHERE c.user_id = ? AND o.user_id = ? AND c.application_id IN ({_marks(app_ids)}) "
        "AND o.entry IN ('message', 'sent', 'reply') ORDER BY o.id ASC",
        (user_id, user_id, *app_ids),
    )
    rows: dict[int, list[dict[str, Any]]] = {}
    for r in await cur.fetchall():
        rows.setdefault(int(r["contact_id"]), []).append(dict(r))
    grouped: dict[int, list[tuple[int, str]]] = {}
    for cid, mine in rows.items():
        if own_browser.follow_up_due(mine, now):
            sent = [r for r in mine if r["entry"] == "sent"][-1]
            grouped.setdefault(int(mine[0]["application_id"]), []).append((cid, sent["recorded_at"]))
    out: dict[int, list[Blocker]] = {}
    for app_id, people in grouped.items():
        label = f"draft {_plural(len(people), 'LinkedIn follow-up', 'LinkedIn follow-ups')}"
        ref = {"contact_ids": [c for c, _ in people], "reason": "linkedin_follow_up"}
        out[app_id] = [_b("follow_up", "assistant", label, min((s for _, s in people), key=_when), ref)]
    return out


async def _outreach(db: JobDatabase, user_id: str, app_ids: list[int], now: datetime) -> dict[int, list[Blocker]]:
    """The unsent notes of these applications as blockers: on the user (Send, or an email) or, once approved,
    on the user's PC. A declined note waits on nobody. A LinkedIn note over the connection-note limit to
    someone not known to be connected is the ASSISTANT's (label "rewrite ...", ``ref.reason`` note_too_long):
    the machine's mistake is never the user's job (2026-11-02)."""
    people = [p for p in await needs_you_count.unsent_for_apps(db, user_id, app_ids) if str(p["text"] or "").strip()]
    if not people:
        return {}
    cur = await db._db.execute(
        "SELECT id, contact_id, entry, channel, text, recorded_at, recorded_by FROM contact_outreach "  # noqa: S608
        f"WHERE user_id = ? AND contact_id IN ({_marks([int(p['id']) for p in people])}) ORDER BY id ASC",
        (user_id, *[int(p["id"]) for p in people]),
    )
    rows: dict[int, list[dict[str, Any]]] = {}
    for r in await cur.fetchall():
        rows.setdefault(int(r["contact_id"]), []).append(dict(r))
    from src.services.applications import contacts  # noqa: PLC0415 - contacts imports the spine package

    too_long = await contacts.over_limit_notes(db, user_id, [int(p["id"]) for p in people])
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for p in people:
        task = own_browser.derive(rows.get(int(p["id"]), []), now)
        if task is not None and task["state"] == "declined":
            continue
        actor = "your_pc" if task is not None and task["state"] in ("queued", "in_progress") else "you"
        if int(p["id"]) in too_long:
            actor = "assistant"
        grouped.setdefault((int(p["application_id"]), actor), []).append(p)
    out: dict[int, list[Blocker]] = {}
    for (app_id, actor), group in grouped.items():
        n, since = len(group), min((g["occurred_at"] for g in group), key=_when)
        label = _plural(n, "LinkedIn note", "LinkedIn notes") if actor == "your_pc" else f"send {_plural(n, 'note', 'notes')}"  # noqa: E501
        ref: dict[str, Any] = {"contact_ids": [int(g["id"]) for g in group]}
        if actor == "assistant":
            label = f"rewrite {_plural(n, 'LinkedIn note', 'LinkedIn notes')} (over the limit)"
            ref["reason"] = "note_too_long"
        out.setdefault(app_id, []).append(_b("outreach_send", actor, label, since, ref))
    return out
