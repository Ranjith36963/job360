"""Proof of application (S7, owner decision 2026-10-10).

Proof backs up "applied": an EMAIL (an ``applied`` event with an email source, or any note starting
"submission confirmed"), pasted TEXT (a ``proof_text`` event) or a SCREENSHOT (stored here). :func:`proof_for` gives
the strongest as a ``level``; :func:`proof_missing` lists applied jobs still without any after
``PROOF_NO_PROOF_AFTER_DAYS`` so the user is asked once. Screenshots arrive by a single-use
5-minute link (``proof_upload_links``, token hash only); the image kind is read off the magic bytes, never the
client. Logs never carry text, bytes or tokens.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import kit, spine
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError
from src.services.auth import rate_limit as auth_rate_limit
from src.services.profile import assistant_settings as settings_rules
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

SERVER_ONLY_EVENTS = frozenset({"proof_screenshot"})
MIMES = ("image/png", "image/jpeg", "image/webp")
MISSING_CAP = 20
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# ``has_text`` = pasted page text OR a receipt confirmation; the two parts are named apart so a
# surface never claims "thank-you page saved" from a confirmation number alone (S5e).
NO_PROOF: dict[str, Any] = {
    "has_text": False, "has_page_text": False, "has_confirmation": False,
    "has_email": False, "email_seen_at": None, "screenshots": 0, "level": "none",
}
_GONE = "upload link used or expired - ask your assistant for a new one"
PROOF_LOCK_CLASS = 360_007  # int32 class id for pg_advisory_xact_lock(class, application_id); the other lock users
# in the repo use the one-key or hashtext form (a separate key space), so this pair cannot clash with them.
_COLS = "id, mime, size, sha256, created_by, created_at, deleted_at, delete_note"


def _audit(event: str, level: str = "info", **fields: Any) -> None:
    """One audit line; every value goes through ``safe_log_value`` - ids too (CodeQL py/log-injection)."""
    extra = {k: None if v is None else safe_log_value(str(v)) for k, v in fields.items()}
    getattr(get_audit_logger(), level)(event, extra={"event": event, **extra})


def _refuse(event: str, status: int, reason: str, detail: Optional[str] = None, **ids: Any) -> SpineError:
    _audit(event, "warning", status=status, reason=reason, result="refused", **ids)
    return SpineError(status, detail or {404: "not found", 429: "too many requests"}.get(status, reason))


def sniff_mime(data: bytes) -> Optional[str]:
    """The image kind from its first bytes (png / jpeg / webp), else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None


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


def check_image(data: bytes) -> str:
    """Size and kind of an upload, no DB: 422 empty, 413 too big, 415 not png/jpeg/webp."""
    if not data:
        raise SpineError(422, "the file is empty")
    if len(data) > settings.PROOF_SCREENSHOT_MAX_BYTES:
        raise SpineError(413, f"file exceeds PROOF_SCREENSHOT_MAX_BYTES ({settings.PROOF_SCREENSHOT_MAX_BYTES} bytes)")
    if (mime := sniff_mime(data)) is None:
        raise SpineError(415, "png, jpeg or webp only")
    return mime


async def _room(db: JobDatabase, user_id: str, application_id: int) -> None:
    """404 unless the application is the caller's; 409 when it already holds the maximum."""
    if await spine.get_owned_application(db, user_id, application_id) is None:
        raise SpineError(404, "application not found")
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_proof_screenshots "
        "WHERE user_id = ? AND application_id = ? AND deleted_at IS NULL",
        (user_id, application_id),
    )
    if int((await cur.fetchone())[0]) >= settings.PROOF_SCREENSHOTS_MAX_LIVE:
        raise SpineError(
            409, f"this application already has {settings.PROOF_SCREENSHOTS_MAX_LIVE} screenshots "
            "(PROOF_SCREENSHOTS_MAX_LIVE); delete one first",
        )


async def screenshot_meta(
    db: JobDatabase, user_id: str, application_id: int, sid: Optional[int] = None
) -> list[dict[str, Any]]:
    """Metadata rows (never ``bytes``), deleted ones included, oldest first."""
    sql = f"SELECT {_COLS} FROM application_proof_screenshots WHERE user_id = ? AND application_id = ?"  # noqa: S608
    params: list[Any] = [user_id, application_id]
    if sid is not None:
        sql, params = sql + " AND id = ?", [*params, sid]
    cur = await db._db.execute(sql + " ORDER BY id", params)
    return [dict(r) for r in await cur.fetchall()]


async def store_screenshot(
    db: JobDatabase, user_id: str, application_id: int, data: bytes, created_by: str, via: str, now: datetime,
) -> dict[str, Any]:
    """Validate, store and put a ``proof_screenshot`` event on the timeline. Returns the metadata row."""
    mime = check_image(data)
    await _room(db, user_id, application_id)  # cheap pre-check; the locked recount below is authoritative
    async with db._db.transaction():
        await db._db.execute("SELECT pg_advisory_xact_lock(?, ?)", (PROOF_LOCK_CLASS, application_id))
        await _room(db, user_id, application_id)
        cur = await db._db.execute(
            "INSERT INTO application_proof_screenshots "
            "(user_id, application_id, mime, bytes, sha256, size, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, application_id, mime, data, hashlib.sha256(data).hexdigest(), len(data), created_by,
             now.isoformat()),
        )
        sid = int(cur.lastrowid or 0)
        await spine.append_event(
            db, user_id=user_id, application_id=application_id, event_type="proof_screenshot",
            payload={"screenshot_id": sid, "via": via, "size": len(data), "mime": mime},
            occurred_at=now.isoformat(), recorded_by=created_by,
        )
    _audit(
        "proof_upload", user_id=user_id, actor=created_by, via=via, application_id=application_id,
        screenshot_id=sid, mime=mime, size=len(data), result="ok",
    )
    return (await screenshot_meta(db, user_id, application_id, sid))[0]


async def mint_link(db: JobDatabase, user: CurrentUser, application_id: int, now: datetime) -> dict[str, Any]:
    """A fresh single-use link for ONE application. 404 not the caller's, 429 over the hourly cap."""
    actor = actor_for(user)
    ids: dict[str, Any] = {"user_id": user.id, "actor": actor, "application_id": application_id}
    if await spine.get_owned_application(db, user.id, application_id) is None:
        raise _refuse("proof_link_refused", 404, "not_owner", **ids)
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM proof_upload_links WHERE user_id = ? AND created_at >= ?",
        (user.id, (now - timedelta(hours=1)).isoformat()),
    )
    if int((await cur.fetchone())[0]) >= settings.PROOF_LINKS_MAX_PER_HOUR:
        raise _refuse(
            "proof_link_refused", 429, "rate_limited",
            f"too many upload links this hour (PROOF_LINKS_MAX_PER_HOUR = {settings.PROOF_LINKS_MAX_PER_HOUR})", **ids,
        )
    token = secrets.token_urlsafe(32)
    expires = (now + timedelta(minutes=settings.PROOF_LINK_TTL_MINUTES)).isoformat()
    cur = await db._db.execute(
        "INSERT INTO proof_upload_links (token_hash, user_id, application_id, expires_at, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (kit.hash_token(token), user.id, application_id, expires, actor, now.isoformat()),
    )
    _audit("proof_link_created", link_id=int(cur.lastrowid or 0), result="ok", **ids)
    return {
        "url": f"{settings.SITE_BASE_URL}/api/proof/{token}", "expires_at": expires,
        "max_bytes": settings.PROOF_SCREENSHOT_MAX_BYTES, "accepts": list(MIMES),
    }


async def find_link(db: JobDatabase, token: str, client: str, now: datetime) -> dict[str, Any]:
    """Resolve a token to its LIVE link row, before any body is read. Unknown = 404 and counts against a per-IP
    lockout (consulted only for tokens that do not resolve, as in files.py); used or expired = 410."""
    key = f"proof_bad:{client}"

    def _bad(reason: str) -> SpineError:
        locked = auth_rate_limit.is_locked(key, max_failures=settings.FILE_BAD_TOKEN_MAX_PER_MIN, window_seconds=60)
        if not locked:
            auth_rate_limit.record_failure(key)
        status, why = (429, "locked_out") if locked else (404, reason)
        return _refuse("proof_upload_refused", status, why, client=client)

    if not kit.TOKEN_RE.match(token):
        raise _bad("bad_shape")
    computed = kit.hash_token(token)
    cur = await db._db.execute(
        "SELECT id, token_hash, user_id, application_id, expires_at, used_at, created_by "
        "FROM proof_upload_links WHERE token_hash = ?",
        (computed,),
    )
    found = await cur.fetchone()
    if found is None or not hmac.compare_digest(str(found["token_hash"]), computed):
        raise _bad("unknown_token")
    link = dict(found)
    if link["used_at"] or link["expires_at"] <= now.isoformat():
        reason = "used" if link["used_at"] else "expired"
        raise _refuse(f"proof_link_{reason}", 410, reason, _GONE, user_id=link["user_id"],
                      application_id=link["application_id"], via="link")
    return link


async def store_via_link(db: JobDatabase, link: dict[str, Any], data: bytes, now: datetime) -> dict[str, Any]:
    """Store an upload against its link. The file is checked BEFORE the link is
    spent (a wrong type does not burn it); then ONE atomic UPDATE spends it, so
    two parallel uploads can never both win. Once spent, the link is spent even
    if the store then fails (single-use means single-use) - ask for a new one."""
    user_id, app_id = str(link["user_id"]), int(link["application_id"])
    ids: dict[str, Any] = {"user_id": user_id, "application_id": app_id, "via": "link"}
    try:
        check_image(data)
        await _room(db, user_id, app_id)
    except SpineError as exc:
        raise _refuse("proof_upload_refused", exc.status_code, "bad_file", exc.detail, **ids) from None
    cur = await db._db.execute(
        "UPDATE proof_upload_links SET used_at = ? WHERE id = ? AND used_at IS NULL AND expires_at > ?",
        (now.isoformat(), link["id"], now.isoformat()),
    )
    if not cur.rowcount:
        reason = "used" if link["used_at"] or link["expires_at"] > now.isoformat() else "expired"
        raise _refuse(f"proof_link_{reason}", 410, reason, _GONE, **ids)
    return await store_screenshot(db, user_id, app_id, data, str(link["created_by"]), "link", now)


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
