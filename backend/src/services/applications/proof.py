"""Proof of application (S7, owner decision 2026-10-10).

Proof backs up "applied": an EMAIL (an ``applied`` event with an email source, or any note starting
"submission confirmed"), pasted TEXT (a ``proof_text`` event) or a SCREENSHOT. :func:`proof_for` gives the strongest as
a ``level`` (screenshots are only counted here; storing them comes with the upload link); :func:`proof_missing` lists
applied jobs still without any after ``PROOF_NO_PROOF_AFTER_DAYS`` so the user is asked once. Logs never carry text.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from src.core import settings
from src.services.applications import spine
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError
from src.services.profile import assistant_settings as settings_rules
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

SERVER_ONLY_EVENTS = frozenset({"proof_screenshot"})
MISSING_CAP = 20
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# ``has_text`` = pasted page text OR a receipt confirmation; the two parts are named apart so a
# surface never claims "thank-you page saved" from a confirmation number alone (S5e).
NO_PROOF: dict[str, Any] = {
    "has_text": False, "has_page_text": False, "has_confirmation": False,
    "has_email": False, "email_seen_at": None, "screenshots": 0, "level": "none",
}


def _audit(event: str, level: str = "info", **fields: Any) -> None:
    """One audit line; every value goes through ``safe_log_value`` - ids too (CodeQL py/log-injection)."""
    extra = {k: None if v is None else safe_log_value(str(v)) for k, v in fields.items()}
    getattr(get_audit_logger(), level)(event, extra={"event": event, **extra})


def check_proof_text(user: CurrentUser, payload: Any) -> dict[str, Any]:
    """Validate a ``proof_text`` payload and return the one to STORE (the server
    adds ``by``). The cap is in characters, so this replaces the generic byte cap."""
    if not isinstance(payload, dict) or set(payload) - {"text", "page_host"}:
        raise SpineError(422, 'proof_text takes a JSON object with only "text" and "page_host"')
    text = spine._strip_control_chars(payload["text"]).strip() if isinstance(payload.get("text"), str) else ""
    text = _URL.sub("[link removed]", text)  # a link can carry a token; the cap counts what is kept
    if not text:
        raise SpineError(422, "proof_text needs non-empty text")
    if len(text) > settings.PROOF_TEXT_MAX_CHARS:
        raise SpineError(422, f"text exceeds PROOF_TEXT_MAX_CHARS ({settings.PROOF_TEXT_MAX_CHARS} chars)")
    page = payload.get("page_host")
    host = settings_rules.parse_site_host(page) if isinstance(page, str) and page.strip() else None
    if page not in (None, "") and host is None:
        raise SpineError(422, "proof_text page_host could not be read")
    return {"text": text, "page_host": host or "", "by": actor_for(user)}


def log_proof_text(user: CurrentUser, application_id: int, event_id: int, stored: dict[str, Any]) -> None:
    _audit(
        "proof_text_recorded", user_id=user.id, application_id=application_id, event_id=event_id,
        page_host=stored["page_host"], chars=len(stored["text"]), result="ok",
    )


async def proof_for(db: JobDatabase, user_id: str, application_ids: list[int]) -> dict[int, dict[str, Any]]:
    """``{has_text, has_page_text, has_confirmation, has_email, email_seen_at, screenshots, level}`` per
    application, three queries for any number of them. ``email_seen_at`` is the earliest email proof's
    received time (else its recorded time)."""
    out = {i: dict(NO_PROOF) for i in application_ids}
    if not out:
        return out
    marks, ids = ",".join("?" for _ in out), list(out)
    cur = await db._db.execute(
        "SELECT id, application_id, event_type, source_kind, corrects_event_id, "  # noqa: S608 - placeholders only
        "(LOWER(TRIM(detail)) LIKE ?), COALESCE(NULLIF(source_received_at, ''), recorded_at) FROM application_events "
        f"WHERE user_id = ? AND application_id IN ({marks}) "
        "AND (event_type IN ('proof_text', 'applied', 'note') OR corrects_event_id IS NOT NULL)",
        ["submission confirmed%", user_id, *ids],
    )
    rows = [tuple(r) for r in await cur.fetchall()]
    superseded = {r[4] for r in rows if r[4] is not None}
    for ev_id, app_id, kind, source, _, confirmed, seen_at in rows:
        if ev_id in superseded:
            continue
        p = out[app_id]
        if kind == "proof_text":
            p["has_text"] = p["has_page_text"] = True
        elif (kind == "applied" and source == "email") or (kind == "note" and confirmed):
            p["has_email"] = True
            if seen_at and (p["email_seen_at"] is None or str(seen_at) < p["email_seen_at"]):
                p["email_seen_at"] = str(seen_at)
    cur = await db._db.execute(  # a receipt that carries a confirmation (ID / portal reference) is text proof
        "SELECT DISTINCT application_id FROM application_receipts "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND application_id IN ({marks}) AND TRIM(COALESCE(confirmation, '')) <> ''",
        [user_id, *ids],
    )
    for (app_id,) in await cur.fetchall():
        out[app_id]["has_text"] = out[app_id]["has_confirmation"] = True
    cur = await db._db.execute(
        "SELECT application_id, COUNT(*) FROM application_proof_screenshots "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND application_id IN ({marks}) AND deleted_at IS NULL GROUP BY application_id",
        [user_id, *ids],
    )
    for app_id, count in await cur.fetchall():
        out[app_id]["screenshots"] = int(count)
    for p in out.values():
        weakest = "screenshot_only" if p["screenshots"] else "none"
        p["level"] = "email" if p["has_email"] else "text" if p["has_text"] else weakest
    return out


async def proof_missing(db: JobDatabase, user_id: str, now: datetime) -> list[dict[str, Any]]:
    """Applied jobs with no proof after ``PROOF_NO_PROOF_AFTER_DAYS`` and no ask about it yet, oldest first."""
    days = settings.PROOF_NO_PROOF_AFTER_DAYS
    cur = await db._db.execute(
        "SELECT e.application_id, MIN(e.occurred_at), a.job_company FROM application_events e "
        "JOIN applications a ON a.id = e.application_id AND a.user_id = e.user_id "
        "WHERE e.user_id = ? AND a.status = 'applied' AND e.event_type = 'applied' AND e.occurred_at <= ? "
        "AND e.id NOT IN "
        "(SELECT corrects_event_id FROM application_events WHERE user_id = ? AND corrects_event_id IS NOT NULL) "
        "GROUP BY e.application_id, a.job_company ORDER BY MIN(e.occurred_at), e.application_id",
        (user_id, (now - timedelta(days=days)).isoformat(), user_id),
    )
    applied = [tuple(r) for r in await cur.fetchall()]
    if not applied:
        return []
    cur = await db._db.execute(
        "SELECT context FROM application_asks WHERE user_id = ? AND context LIKE ?", (user_id, "proof_missing:%")
    )
    asked = {r[0] for r in await cur.fetchall()}
    levels = await proof_for(db, user_id, [int(a[0]) for a in applied])
    out = []
    for app_id, applied_at, company in applied:
        if levels[app_id]["level"] != "none" or f"proof_missing:{app_id}" in asked:
            continue
        out.append({
            "application_id": app_id, "company": company or "", "applied_at": applied_at,
            "question": f"No proof yet for {company or 'this job'} after {days} days. "
            "Do you have the confirmation email or a screenshot?",
            "context": f"proof_missing:{app_id}",
        })
    return out[:MISSING_CAP]
