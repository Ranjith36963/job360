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

import json
import time
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
    rank = _RANK[b["kind"]] + (1 if b["kind"] == "outreach_send" and b["actor"] == "your_pc" else 0)
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
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.event_type, e.payload, e.recorded_at FROM application_events e "  # noqa: S608
        f"WHERE e.user_id = ? AND e.application_id IN ({_marks(ids)}) AND e.event_type IN ({types}) "
        "AND (e.event_type != 'form_filled' OR e.id = (SELECT MAX(x.id) FROM application_events x "
        "WHERE x.application_id = e.application_id AND x.user_id = e.user_id AND x.event_type = 'form_filled')) "
        "ORDER BY e.id ASC",
        (user_id, *ids),
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
) -> dict[int, list[Blocker]]:
    """``{application_id: ordered blockers}`` for the caller's own applications among ``application_ids``
    (others are left out of the result). ``[]`` = nothing waits. A fixed number of queries, never one per id."""
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


async def _fill(  # noqa: C901 - one linear derivation, one rule per branch
    db: JobDatabase, user_id: str, ids: list[int], apps: dict[int, dict[str, Any]],
    result: dict[int, list[Blocker]], now: datetime,
) -> None:
    events = await _events_by_app(db, user_id, ids)
    blocks = await blocked.open_blocks(db, user_id, ids)
    cvs = await ready.newest_cv_by_app(db, user_id, ids)
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
    position = {a: n for n, (a, _t) in enumerate(sorted(queued_at.items(), key=lambda kv: (_when(kv[1]), kv[0])), 1)}
    line = await spine.user_score_line(user_id)
    today_iso = (await spine.user_today(db, user_id)).isoformat()

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
    flags = await ready.answer_flags_for(user_id, fills)

    sent_ids = [i for i in ids if apps[i]["status"] != "considering" and not state[i]["below"]
                and apps[i]["status"] not in _DEAD and state[i]["reason"] != "posting_closed"]
    notes = await _outreach(db, user_id, sent_ids, now)

    confirmed = await kit.pending_confirmations(db, user_id, ids)
    for app_id in ids:
        a, s, out = apps[app_id], state[app_id], result[app_id]
        if (conf := confirmed.get(app_id)) is not None and a["status"] not in _DEAD:
            conf_ref = {"event_id": conf["id"], "subject": conf["source_subject"] or None,
                   "received_at": conf["source_received_at"] or conf["recorded_at"]}
            out.append(_b("confirm_sent", "you", "employer confirmed — did it go out?", conf["recorded_at"], conf_ref))
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
            ref = {k: v for k, v in (("ask_id", site_asks[0]["id"] if site_asks else None),
                                     ("event_id", block_ev["id"] if block_ev else None)) if v is not None}
            since = site_asks[0]["asked_at"] if site_asks and not block_ev else block_since
            out.append(_b("site_step", "you", word, since, ref or None))
        if s["fill_open"] or question_asks or reason == "unknown_question":
            fill_at = s["fill"]["recorded_at"] if s["fill"] else block_since
            since = min((k["asked_at"] for k in question_asks), key=_when, default=fill_at)
            out += _answer_blockers(app_id, question_asks, flags.get(app_id, []), since, reason == "unknown_question")
        if considering and s["cv"] and not s["seen"] and app_id not in receipts:
            cv_ref = {"artifact_id": s["cv"]["artifact_id"]}
            out.append(_b("approve_cv", "you", "approve the CV", s["cv"]["created_at"], cv_ref))
        if s["fill_open"]:
            yes = s["approved"]
            if yes is None:
                out.append(_b("send_yes", "you", "Send", s["fill"]["recorded_at"], {"event_id": s["fill"]["id"]}))
            elif kit._approval_age_over(yes["recorded_at"], now):
                label = "Send again (your yes expired)"
                out.append(_b("send_yes", "you", label, yes["recorded_at"], {"event_id": yes["id"]}))
            elif not s["below"]:
                step = "submit" if s["swept"] else "check form"
                out.append(_b("assistant_step", "assistant", step, yes["recorded_at"], {"event_id": yes["id"]}))
        if considering and app_id in queued_at:
            browser = queue_preflight.route_label(a.get("job_url") or "", s["block"] or {})
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


def settings_line_side(score: Optional[int], line: Optional[int]) -> str:
    from src.services.profile.assistant_settings import line_side  # noqa: PLC0415 - profile imports spine

    return line_side(score, line)


async def _outreach(db: JobDatabase, user_id: str, app_ids: list[int], now: datetime) -> dict[int, list[Blocker]]:
    """The unsent notes of these applications as blockers: on the user (Send, or an email) or, once approved,
    on the user's PC. A declined note waits on nobody."""
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
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for p in people:
        task = own_browser.derive(rows.get(int(p["id"]), []), now)
        if task is not None and task["state"] == "declined":
            continue
        actor = "your_pc" if task is not None and task["state"] in ("queued", "in_progress") else "you"
        grouped.setdefault((int(p["application_id"]), actor), []).append(p)
    out: dict[int, list[Blocker]] = {}
    for (app_id, actor), group in grouped.items():
        n, since = len(group), min((g["occurred_at"] for g in group), key=_when)
        label = _plural(n, "LinkedIn note", "LinkedIn notes") if actor == "your_pc" else f"send {_plural(n, 'note', 'notes')}"  # noqa: E501
        ref = {"contact_ids": [int(g["id"]) for g in group]}
        out.setdefault(app_id, []).append(_b("outreach_send", actor, label, since, ref))
    return out
