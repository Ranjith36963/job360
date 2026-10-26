"""The blocked record (S6, owner decisions 2026-10-10).

When the user's assistant gets stuck on an application form (a CAPTCHA, a bot
check, a login page, a broken site...) it records ``blocked`` and moves on; it
never retries a CAPTCHA or bot check and never bypasses one. Recording
``blocked`` opens ONE Needs-you ask on that application (de-duplicated while an
ask for the same reason is still open), and ``check_submit`` answers ``ask`` /
``blocked`` until an ``unblocked`` follows. ``unblocked`` (any assistant, or the
web's "Mark resolved") closes the open blocked asks.

Both are NOTE events (never move the status). Payloads are closed sets; the
server fills ``assistant`` from the caller and never trusts it from a payload.
Nothing here stores a password, a token or page text: ``page_host`` is a host
name only and every text field is short and capped. Audit lines carry the
user, the application, the reason / resolution, the actor and the result -
never the ``detail`` text.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications import asks as asks_service
from src.services.applications import event_examples, spine
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError
from src.services.browser_agent import routing
from src.services.profile import assistant_settings as settings_rules
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

BLOCKED_EVENT_TYPES = frozenset({"blocked", "unblocked"})
# Closed set, with the plain words the user reads.
BLOCKED_REASONS: dict[str, str] = {
    "captcha": "a CAPTCHA",
    "bot_check": "a bot check",
    "login_needed": "a sign-in page",
    "site_error": "a site error",
    "upload_failed": "a failed upload",
    "unknown_question": "a question it cannot answer",
    "safety_block": "a safety block",
    "quick_apply_warning": "a quick-apply warning",
    # Bulk-apply wave 1 (N1): one word per way a site says no.
    "posting_closed": "a closed posting",
    "account_creation": "a sign-up wall",
    "otp_needed": "a one-time code",
    "id_or_payment_asked": "a request for ID or payment details",
    "assessment_required": "a timed test or video step",
    "manual_only": "a form that cannot be filled for you",
    "site_limit_reached": "the site's own daily application limit",
    "other": "a problem",
}
UNBLOCK_RESOLUTIONS = frozenset({"retried", "user_did_it", "skipped"})
NEVER_RETRIED = frozenset({"captcha", "bot_check", "safety_block", "id_or_payment_asked"})
BLOCKED_KEYS = frozenset({"reason", "step", "page_host", "detail"})
# Written only by Job360's own browser (``record_browser_block``), never accepted from a caller: the
# CAPTCHA hold and "Finish it here" trust a block only when it carries this (review of #1176).
BY_KEY, BY_BROWSER = "by", "browser"
STEP_MAX_CHARS = 120
DETAIL_MAX_CHARS = 300
# Owner, 2026-10-23: clearing a block may say why (shown on the timeline).
UNBLOCKED_KEYS = frozenset({"resolution", "note"})
NOTE_MAX_CHARS = 200
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def _plain(key: str, value: Any, limit: int) -> str:
    """One short line of plain text: control characters dropped, whitespace
    collapsed, refused (422) when longer than ``limit``."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SpineError(422, f"blocked {key} must be text")
    text = " ".join(asks_service.clean_text(value).split())
    if "://" in text:
        raise SpineError(422, f"blocked {key}: no links here; page_host carries the site")
    if len(text) > limit:
        raise SpineError(422, f"blocked {key} must be at most {limit} characters")
    return text


def _page_host(value: Any) -> str:
    if value is None or value == "":
        return ""
    host = value.strip().lower().rstrip(".") if isinstance(value, str) else ""
    # The shared host reader must read the SAME host back: a URL, a port, userinfo
    # or a look-alike never survives as a "host only".
    if not _HOST_RE.match(host) or settings_rules.parse_site_host(host) != host:
        raise SpineError(422, "blocked page_host must be a host name only (e.g. jobs.example.com), never a full URL")
    return host


def check_payload(user: CurrentUser, event_type: str, payload: dict[str, Any], detail: str) -> dict[str, Any]:
    """Validate a ``blocked`` / ``unblocked`` payload; return the payload to STORE
    (``assistant`` server-filled). Raises ``SpineError`` 422."""
    problems: list[str] = []
    if detail:
        problems.append(f"{event_type} keeps its text in the payload; leave the event detail empty")
    actor = actor_for(user)
    if event_type == "unblocked":
        if not set(payload) <= UNBLOCKED_KEYS or payload.get("resolution") not in UNBLOCK_RESOLUTIONS:
            problems.append(
                'unblocked needs payload {"resolution": "retried" | "user_did_it" | "skipped"} and an optional '
                f'"note" (why, plain text, at most {NOTE_MAX_CHARS} characters), nothing else'
            )
        note = event_examples.collect(problems, _plain, "note", payload.get("note"), NOTE_MAX_CHARS)
        if problems:
            raise event_examples.refusal(event_type, problems)
        return {"resolution": payload["resolution"], **({"note": note} if note else {}), "assistant": actor}
    if BY_KEY in payload:
        problems.append(f'"{BY_KEY}" is written by Job360 itself (its own browser), never by a caller')
    unknown = set(payload) - BLOCKED_KEYS - {BY_KEY}
    if unknown:
        problems.append(f"blocked payload keys are {sorted(BLOCKED_KEYS)}; unknown: {sorted(unknown)}")
    reason = payload.get("reason")
    if reason not in BLOCKED_REASONS:
        problems.append("blocked reason must be one of: " + ", ".join(BLOCKED_REASONS))
    step = event_examples.collect(problems, _plain, "step", payload.get("step"), STEP_MAX_CHARS)
    page_host = event_examples.collect(problems, _page_host, payload.get("page_host"))
    text = event_examples.collect(problems, _plain, "detail", payload.get("detail"), DETAIL_MAX_CHARS)
    if problems:
        raise event_examples.refusal(event_type, problems)
    return {"reason": reason, "step": step, "page_host": page_host, "detail": text, "assistant": actor}


_USER_TODO: dict[str, str] = {
    "captcha": "Open the form on {host}, solve the check and press Submit yourself, then mark this resolved.",
    "bot_check": "Open the form on {host}, solve the check and press Submit yourself, then mark this resolved.",
    "safety_block": (
        "Your assistant's own tool would not press Submit on {host}. Open the form, check it and press Submit "
        "yourself, then mark this resolved."
    ),
    "login_needed": "Sign in on {host} yourself (never share the password), then mark this resolved.",
    "upload_failed": "Attach the CV file on {host} yourself, then mark this resolved.",
    "unknown_question": "Answer the question on {host} (the assistant could not), then mark this resolved.",
    "posting_closed": "The posting on {host} is closed. Check it, then mark this resolved to drop it.",
    "account_creation": "Create the account on {host} yourself (never share the password), then mark this resolved.",
    "otp_needed": "Enter the one-time code on {host} yourself, then mark this resolved.",
    "id_or_payment_asked": (
        "{host} asks for ID or payment details. Your assistant never enters these. Decide whether to go on "
        "yourself, then mark this resolved."
    ),
    "assessment_required": "{host} wants a timed test or video. Do it yourself if you want to, then mark it resolved.",
    "manual_only": "The form on {host} cannot be filled for you. Fill it yourself, then mark this resolved.",
    "site_limit_reached": (
        "{host} says you applied too much today. Wait until tomorrow, then mark this resolved."
    ),
}


# The ask kind a blocked step's ask carries (0087), so the web shows buttons, not a text box.
# A reason missing here is not something only the user can clear on the form: a plain ask.
ASK_KIND_FOR_REASON: dict[str, str] = {
    "captcha": "captcha", "bot_check": "captcha", "login_needed": "login", "account_creation": "login",
    "otp_needed": "login",
    "upload_failed": "upload", "safety_block": "safety",
}


def ask_question(stored: dict[str, Any]) -> str:
    step = stored.get("step") or "a step"
    host = stored.get("page_host") or "the job site"
    reason = stored["reason"]
    return f"Your assistant got stuck: {BLOCKED_REASONS[reason]} at {step} on {host}. " + _USER_TODO.get(
        reason, "Check the form on {host} and finish it or tell your assistant what to do."
    ).format(host=host)


async def _block_events(db: JobDatabase, user_id: str, application_id: int) -> list[dict[str, Any]]:
    """This application's blocked / unblocked events, NEWEST first, payload parsed."""
    cur = await db._db.execute(
        "SELECT id, event_type, payload, recorded_at, recorded_by FROM application_events "
        "WHERE application_id = ? AND user_id = ? AND event_type IN ('blocked', 'unblocked') ORDER BY id DESC",
        (application_id, user_id),
    )
    out = []
    for row in await cur.fetchall():
        d = dict(row)
        raw = d.get("payload")
        try:
            p = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            p = {}
        d["payload"] = p if isinstance(p, dict) else {}
        out.append(d)
    return out


async def _uploaded_after(db: JobDatabase, user_id: str, application_id: int, event_id: int) -> bool:
    """A ``document_uploaded`` event on this application is newer than event ``event_id``."""
    cur = await db._db.execute(
        "SELECT 1 FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type = 'document_uploaded' AND id > ? LIMIT 1",
        (application_id, user_id, event_id),
    )
    return await cur.fetchone() is not None


async def _open_block_event(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """The newest ``blocked`` event (payload parsed) with no later ``unblocked``, else None.
    An ``upload_failed`` block counts as cleared once a ``document_uploaded`` for this application is
    newer than it (owner, 2026-10-23: the later upload worked). Read-only."""
    events = await _block_events(db, user_id, application_id)
    if not events or events[0]["event_type"] != "blocked":
        return None
    p = events[0]["payload"]
    if p.get("reason") == "upload_failed" and await _uploaded_after(db, user_id, application_id, int(events[0]["id"])):
        return None
    return events[0]


async def browser_captcha_open(db: JobDatabase, user_id: str, application_id: int) -> bool:
    """The open block is a CAPTCHA that Job360's own browser recorded (``by: browser``). A caller's
    look-alike (``step: "Job360 browser"`` typed by an assistant) never counts, nor does a block from
    before the marker: either would let an assistant keep a browser held. Read-only."""
    ev = await _open_block_event(db, user_id, application_id)
    return ev is not None and ev["payload"].get("reason") == "captcha" and ev["payload"].get(BY_KEY) == BY_BROWSER


async def open_block(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """The newest ``blocked`` with no later ``unblocked`` (what the page shows), else None.
    Read-only: nothing is written here (M3)."""
    ev = await _open_block_event(db, user_id, application_id)
    if ev is None:
        return None
    p = ev["payload"]
    reason = p.get("reason") if p.get("reason") in BLOCKED_REASONS else "other"
    return {
        "reason": reason, "reason_label": BLOCKED_REASONS[reason], "step": p.get("step") or "",
        "page_host": p.get("page_host") or "", "detail": p.get("detail") or "",
        "by": p.get("assistant") or ev.get("recorded_by") or "", "at": ev["recorded_at"],
        "ask_id": p.get("ask_id"),
    }


def open_app_ids(asks: list[dict[str, Any]]) -> list[int]:
    return sorted({int(a["application_id"]) for a in asks if a.get("status") == "open" and a.get("application_id")})


async def open_blocks(db: JobDatabase, user_id: str, app_ids: list[int]) -> dict[int, dict[str, Any]]:
    """The open block's payload (parsed) of each of these applications that has one, in ONE query: the
    newest ``blocked`` / ``unblocked`` event is a ``blocked`` (the same rule as ``_open_block_event``), and an
    ``upload_failed`` one is cleared by a later ``document_uploaded``. Applications with no open block are
    absent. Read-only: callers read it once per request and share the map."""
    if not app_ids:
        return {}
    marks = ",".join("?" for _ in app_ids)
    cur = await db._db.execute(
        f"SELECT e.id, e.application_id, e.payload FROM application_events e WHERE e.user_id = ? "  # noqa: S608
        f"AND e.application_id IN ({marks}) AND e.event_type = 'blocked' "
        "AND e.id = (SELECT MAX(y.id) FROM application_events y WHERE y.application_id = e.application_id "
        "AND y.user_id = e.user_id AND y.event_type IN ('blocked', 'unblocked'))",
        (user_id, *app_ids),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in await cur.fetchall():
        raw = row["payload"]
        try:
            p = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            p = {}
        p = p if isinstance(p, dict) else {}
        app_id = int(row["application_id"])
        if p.get("reason") == "upload_failed" and await _uploaded_after(db, user_id, app_id, int(row["id"])):
            continue
        out[app_id] = p
    return out


def browser_hold_live(user_id: str, application_id: int) -> bool:
    """Job360's own browser still holds this application's page for the user (the 30-minute hand-off hold, in
    memory in this process). Read-only: it opens no browser and renews nothing."""
    from src.services.browser_agent import agent as browser_agent  # noqa: PLC0415 - lazy, heavy

    return browser_agent.held_seconds(browser_agent.get_pool(), user_id, int(application_id)) is not None


async def finish_here_ask_ids(
    db: JobDatabase, user_id: str, asks: list[dict[str, Any]], blocks: Optional[dict[int, dict[str, Any]]] = None,
) -> set[int]:
    """The open asks that are a CAPTCHA in Job360's own browser, still open: the user can finish those on
    Needs you itself (FinishInBrowser). ``blocks`` is the shared ``open_blocks`` map (read here when not
    given). Read-only.
    A block Job360's browser wrote carries ``by: browser``; one written before that marker (no ``by``, step
    "Job360 browser") still shows the button. Display only, so harmless: the finish view is the user's own
    session, acts only on a browser that is already open, and never holds one."""
    if blocks is None:
        blocks = await open_blocks(db, user_id, open_app_ids(asks))
    out: set[int] = set()
    live: dict[int, bool] = {}
    for a in asks:
        if a.get("status") != "open" or not a.get("application_id"):
            continue
        app_id = int(a["application_id"])
        p = blocks.get(app_id, {})
        by = p.get(BY_KEY)
        ours = by == BY_BROWSER or (by is None and p.get("step") == "Job360 browser")
        if p.get("reason") not in FINISH_HERE_REASONS or not ours:
            continue
        # The block's own ask, OR any open CAPTCHA ask on that job: an ask_user (or the one-ask-per-
        # job merge) supersedes the ask the block opened, and the block keeps the old id (2026-10-26, app 138).
        if a["id"] != p.get("ask_id") and a.get("kind") not in FINISH_HERE_KINDS:
            continue
        if app_id not in live:
            live[app_id] = browser_hold_live(user_id, app_id)
        if live[app_id]:
            out.add(int(a["id"]))
    return out


# The open blocks a user can finish in the page Job360's browser holds, and the asks that stand for them. A
# sign-in stays the own browser's (routing.py; passwords are typed on Settings, Job sites, never here).
FINISH_HERE_REASONS = frozenset({"captcha", "bot_check"})
FINISH_HERE_KINDS = frozenset({"captcha"})


async def browser_for_asks(
    db: JobDatabase, user_id: str, asks: list[dict[str, Any]],
    blocks: Optional[dict[int, dict[str, Any]]] = None, finish: Optional[set[int]] = None,
) -> dict[int, dict[str, str]]:
    """Which browser each OPEN ask needs, by ask id (``routing.label``). ``blocks`` is the shared
    ``open_blocks`` map and ``finish`` the ``finish_here_ask_ids`` set (each read here when not given): the
    block counts only for the ask it opened, ``by: browser`` marks Job360's own browser having failed (a
    caller can never write it), and a CAPTCHA the user can finish right now on Needs you is theirs alone.
    Read-only; logs counts only."""
    if blocks is None:
        blocks = await open_blocks(db, user_id, open_app_ids(asks))
    if finish is None:
        finish = await finish_here_ask_ids(db, user_id, asks, blocks)
    out: dict[int, dict[str, str]] = {}
    for a in asks:
        if a.get("status") != "open":
            continue
        p = blocks.get(int(a["application_id"]), {}) if a.get("application_id") else {}
        mine = p if p.get("ask_id") == a["id"] else {}
        route = routing.ask_route(
            a.get("kind") or "", block_reason=str(mine.get("reason") or ""),
            block_by_browser=mine.get(BY_KEY) == BY_BROWSER, finish_here=a["id"] in finish,
        )
        out[int(a["id"])] = routing.label(route)
    get_audit_logger().info(
        "ask_browsers_read",
        extra={
            "event": "ask_browsers_read", "user_id": safe_log_value(user_id), "result": "ok", "count": len(out),
            "own_browser": sum(1 for v in out.values() if v["kind"] == "own_browser"),
            "human": sum(1 for v in out.values() if v["kind"] == "human"),
        },
    )
    return out


async def _open_ask_ids(db: JobDatabase, user_id: str, ask_ids: list[int]) -> set[int]:
    if not ask_ids:
        return set()
    marks = ",".join("?" for _ in ask_ids)
    cur = await db._db.execute(
        f"SELECT id FROM application_asks WHERE user_id = ? AND id IN ({marks}) "  # noqa: S608
        "AND answered_at IS NULL AND withdrawn_at IS NULL",
        (user_id, *ask_ids),
    )
    return {int(r[0]) for r in await cur.fetchall()}


def _ask_ids(events: list[dict[str, Any]], reason: Optional[str] = None) -> list[int]:
    ids = []
    for ev in events:
        p = ev["payload"]
        ask_id = p.get("ask_id")
        if ev["event_type"] == "blocked" and isinstance(ask_id, int) and (reason is None or p.get("reason") == reason):
            ids.append(ask_id)
    return ids


async def before_append(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, stored: dict[str, Any]
) -> dict[str, Any]:
    """``blocked``: open the Needs-you ask (or reuse the open one for the same
    reason) and carry its id in the stored payload. ``unblocked``: 409 when
    nothing is blocked. The caller has proven the application is the user's."""
    events = await _block_events(db, user.id, application_id)
    if event_type == "unblocked":
        if not events or events[0]["event_type"] != "blocked":
            _log(user, application_id, event_type, stored, "refused")
            raise SpineError(409, "this application is not blocked")
        if stored["resolution"] == "retried" and events[0]["payload"].get("reason") in NEVER_RETRIED:
            _log(user, application_id, event_type, stored, "refused")
            raise SpineError(
                422, "a CAPTCHA, bot check, safety block or ID/payment request is never retried by the assistant; "
                "the user resolves it (user_did_it) or it is skipped"
            )
        return stored
    open_ids = await _open_ask_ids(db, user.id, _ask_ids(events, stored["reason"]))
    if open_ids:
        return {**stored, "ask_id": max(open_ids)}
    try:
        ask = await asks_service.create_ask(
            db, user.id, application_id, ask_question(stored), "", stored["assistant"],
            kind=ASK_KIND_FOR_REASON.get(stored["reason"], ""),
        )
    except SpineError as exc:
        if exc.status_code != 429:
            raise
        # Too many open asks: the blocked record still matters more than the ask.
        return {**stored, "ask_id": None}
    return {**stored, "ask_id": int(ask["id"])}


async def after_append(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, stored: dict[str, Any]
) -> None:
    """``unblocked`` closes every still-open blocked ask on this application; both log."""
    if event_type == "unblocked":
        events = await _block_events(db, user.id, application_id)
        for ask_id in sorted(await _open_ask_ids(db, user.id, _ask_ids(events))):
            try:
                await asks_service.withdraw_ask(
                    db, user.id, ask_id, stored["assistant"],
                    str(stored.get("note") or ""),
                )
            except SpineError:  # pragma: no cover - answered or withdrawn in between
                pass
    _log(user, application_id, event_type, stored, "ok")


async def record(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, stored: dict[str, Any],
    *, occurred_at: str, follow_up_on: Any = spine.FOLLOW_UP_UNSET,
) -> dict[str, Any]:
    """Append a checked ``blocked`` / ``unblocked`` with its ask work in ONE
    transaction: a failed append leaves no orphan ask, a failed ask no event.
    The caller has proven the application is the user's."""
    async with db._db.transaction():
        # Lock the application row first: two `blocked` calls at once queue here,
        # so the second sees the first's ask and cannot open another.
        await db._db.execute(
            "SELECT id FROM applications WHERE id = ? AND user_id = ? FOR UPDATE", (application_id, user.id)
        )
        stored = await before_append(db, user, application_id, event_type, stored)
        result = await spine.append_event(
            db, user_id=user.id, application_id=application_id, event_type=event_type,
            # The timeline shows an event's detail: an `unblocked` note is why the block was cleared.
            detail=str(stored.get("note") or "") if event_type == "unblocked" else "",
            payload=stored, occurred_at=occurred_at, recorded_by=actor_for(user), follow_up_on=follow_up_on,
        )
        await after_append(db, user, application_id, event_type, stored)
    return result


def _site(value: Any) -> str:
    """The lower-case host of a link or a bare host, without a leading ``www.``; "" when unreadable."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    text = str(value or "").strip().lower()
    host = (urlsplit(text).hostname or "") if "://" in text else text.rstrip(".")
    return host[4:] if host.startswith("www.") else host


async def release_on_new_link(
    db: JobDatabase, user: CurrentUser, application_id: int, old_url: str, new_url: str
) -> Optional[dict[str, Any]]:
    """Owner 2026-10-26: the job's link moved to another site (``update_job`` apply_url), so a block
    recorded on the OLD link's site no longer stands between the user and the form: close it with
    ``unblocked`` {resolution: skipped} and a note naming both sites. A block on the new link's site,
    or on any site that was not the old link's, stays: only the user clears those. Returns the event,
    or None when nothing was closed."""
    old_site, new_site = _site(old_url), _site(new_url)
    if not old_site or not new_site or old_site == new_site:
        return None
    ev = await _open_block_event(db, user.id, application_id)
    if ev is None or _site(ev["payload"].get("page_host")) != old_site:
        return None
    note = f"the job's link moved from {old_site} to {new_site}"[:NOTE_MAX_CHARS]
    stored = {"resolution": "skipped", "note": note, "assistant": actor_for(user)}
    from datetime import datetime, timezone  # noqa: PLC0415

    return await record(db, user, application_id, "unblocked", stored,
                        occurred_at=datetime.now(timezone.utc).isoformat())


async def record_browser_block(
    db: JobDatabase, user: CurrentUser, application_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """Job360's own browser stopped on this application: check ``payload`` like a caller's, then store it
    with ``by: browser``, the one key a caller can never write (``check_payload`` refuses it)."""
    if await spine.get_owned_application(db, user.id, application_id) is None:
        raise SpineError(404, "application not found")
    stored = {**check_payload(user, "blocked", payload, ""), BY_KEY: BY_BROWSER}
    return await record(db, user, application_id, "blocked", stored, occurred_at=spine.parse_occurred_at(None))


def _log(user: CurrentUser, application_id: int, event_type: str, stored: dict[str, Any], result: str) -> None:
    """The audit line: user, application, reason / resolution, actor, result - never the detail text."""
    # Constant names only: the event type came from the request body.
    name = "blocked" if event_type == "blocked" else "unblocked"
    if result != "ok":
        name = f"{name}_refused"
    extra: dict[str, Any] = {
        "event": name, "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
        "application_id": int(application_id), "result": safe_log_value(result, max_len=20),
    }
    for key in ("reason", "resolution"):
        if stored.get(key):
            extra[key] = safe_log_value(stored[key], max_len=40)
    get_audit_logger().info(name, extra=extra)
