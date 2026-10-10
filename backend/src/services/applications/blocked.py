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
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError
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
    "other": "a problem",
}
UNBLOCK_RESOLUTIONS = frozenset({"retried", "user_did_it", "skipped"})
BLOCKED_KEYS = frozenset({"reason", "step", "page_host", "detail"})
STEP_MAX_CHARS = 120
DETAIL_MAX_CHARS = 300
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def _plain(key: str, value: Any, limit: int) -> str:
    """One short line of plain text: control characters dropped, whitespace
    collapsed, refused (422) when longer than ``limit``."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SpineError(422, f"blocked {key} must be text")
    text = " ".join(asks_service.clean_text(value).split())
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
    if detail:
        raise SpineError(422, f"{event_type} keeps its text in the payload; leave the event detail empty")
    actor = actor_for(user)
    if event_type == "unblocked":
        if set(payload) != {"resolution"} or payload.get("resolution") not in UNBLOCK_RESOLUTIONS:
            raise SpineError(
                422, 'unblocked needs payload {"resolution": "retried" | "user_did_it" | "skipped"} and nothing else'
            )
        return {"resolution": payload["resolution"], "assistant": actor}
    unknown = set(payload) - BLOCKED_KEYS
    if unknown:
        raise SpineError(422, f"blocked payload keys are {sorted(BLOCKED_KEYS)}; unknown: {sorted(unknown)}")
    reason = payload.get("reason")
    if reason not in BLOCKED_REASONS:
        raise SpineError(422, "blocked reason must be one of: " + ", ".join(BLOCKED_REASONS))
    return {
        "reason": reason,
        "step": _plain("step", payload.get("step"), STEP_MAX_CHARS),
        "page_host": _page_host(payload.get("page_host")),
        "detail": _plain("detail", payload.get("detail"), DETAIL_MAX_CHARS),
        "assistant": actor,
    }


def ask_question(stored: dict[str, Any]) -> str:
    step = stored.get("step") or "a step"
    host = stored.get("page_host") or "the job site"
    return f"Your assistant got stuck: {BLOCKED_REASONS[stored['reason']]} at {step} on {host}. What should it do?"


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


async def open_block(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """The newest ``blocked`` with no later ``unblocked`` (what the page shows), else None."""
    events = await _block_events(db, user_id, application_id)
    if not events or events[0]["event_type"] != "blocked":
        return None
    p = events[0]["payload"]
    reason = p.get("reason") if p.get("reason") in BLOCKED_REASONS else "other"
    return {
        "reason": reason, "reason_label": BLOCKED_REASONS[reason], "step": p.get("step") or "",
        "page_host": p.get("page_host") or "", "detail": p.get("detail") or "",
        "by": p.get("assistant") or events[0].get("recorded_by") or "", "at": events[0]["recorded_at"],
        "ask_id": p.get("ask_id"),
    }


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
        return stored
    open_ids = await _open_ask_ids(db, user.id, _ask_ids(events, stored["reason"]))
    if open_ids:
        return {**stored, "ask_id": max(open_ids)}
    try:
        ask = await asks_service.create_ask(
            db, user.id, application_id, ask_question(stored), "", stored["assistant"]
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
                await asks_service.withdraw_ask(db, user.id, ask_id, stored["assistant"])
            except SpineError:  # pragma: no cover - answered or withdrawn in between
                pass
    _log(user, application_id, event_type, stored, "ok")


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
