"""Contacts — people met during an application's outreach (spec
docs/plans/2026-09-05-contacts-stats/spec.md R1-R4, S1-S7, S12).

A contact is an add-only row on ``application_contacts``, owned by one
application, one user. Adding one appends a ``contact_added`` event whose
``payload`` is ``{"contact_id": <id>}`` and whose ``detail`` is the contact's
display line (``name — role``) — the event, not the row, is what
``whats_new``/``export_history`` surface (R1). The same non-empty email
(lower-cased, trimmed) on the same application is the SAME contact: the
second add returns the first row (``already_existed: true``), no second row,
no second event (R2). Without an email there is no identity, so every add is
a new row.

No update, no delete at runtime (S12 — grep-guarded by
``tests/test_slice4_contacts.py::test_contacts_are_append_only``, the same
pattern ``test_application_spine.py`` uses for events/artifacts).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.repositories import pg
from src.services.applications.spine import SpineError, append_event, get_owned_application, parse_occurred_at
from src.services.auth import rate_limit
from src.utils.logger import get_audit_logger

if TYPE_CHECKING:  # pragma: no cover — type-only, same reasoning as spine.py
    from src.repositories.database import JobDatabase

# S5 — the simple shape check the spec names; not a full RFC 5322 validator
# (this is a contact's stated email, not a login credential). Structural, not a
# regex: `^[^@\s]+@[^@\s]+\.[^@\s]+$` is what it means, but that pattern backtracks
# polynomially on `a@!.!.!.…` (CodeQL py/polynomial-redos), and this is O(n).
_WHITESPACE_RE = re.compile(r"\s")


def _looks_like_email(email: str) -> bool:
    """Same set as the old regex: no whitespace, one `@`, non-empty local part,
    and a dot inside the domain that is neither its first nor its last char."""
    if _WHITESPACE_RE.search(email):
        return False
    local, sep, domain = email.partition("@")
    if not sep or not local or not domain or "@" in domain:
        return False
    return "." in domain[1:-1]


def _validate_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name or len(name) > settings.CONTACT_NAME_MAX_CHARS:
        raise SpineError(
            422, f"name must be 1-{settings.CONTACT_NAME_MAX_CHARS} chars (CONTACT_NAME_MAX_CHARS) after trim"
        )
    return name


def _validate_role(raw: str) -> str:
    role = (raw or "").strip()
    if len(role) > settings.CONTACT_ROLE_MAX_CHARS:
        raise SpineError(422, f"role exceeds CONTACT_ROLE_MAX_CHARS ({settings.CONTACT_ROLE_MAX_CHARS} chars)")
    return role


def _validate_email(raw: str) -> str:
    email = (raw or "").strip()
    if not email:
        return ""
    if len(email) > settings.CONTACT_EMAIL_MAX_CHARS:
        raise SpineError(422, f"email exceeds CONTACT_EMAIL_MAX_CHARS ({settings.CONTACT_EMAIL_MAX_CHARS} chars)")
    if not _looks_like_email(email):
        raise SpineError(422, "email must look like a real address (e.g. name@example.com)")
    # R2 — stored lower-cased + trimmed so identity is case/whitespace-insensitive.
    return email.lower()


def _validate_linkedin_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    if len(url) > settings.CONTACT_LINKEDIN_URL_MAX_CHARS:
        raise SpineError(
            422,
            f"linkedin_url exceeds CONTACT_LINKEDIN_URL_MAX_CHARS "
            f"({settings.CONTACT_LINKEDIN_URL_MAX_CHARS} chars)",
        )
    # S5 — http(s) only; refuses javascript:/ftp:/data:/bare-host schemes.
    if not (url.startswith("https://") or url.startswith("http://")):
        raise SpineError(422, "linkedin_url must start with http:// or https://")
    return url


def _validate_notes(raw: str) -> str:
    notes = raw or ""
    if len(notes) > settings.CONTACT_NOTES_MAX_CHARS:
        raise SpineError(422, f"notes exceeds CONTACT_NOTES_MAX_CHARS ({settings.CONTACT_NOTES_MAX_CHARS} chars)")
    return notes


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "application_id": row["application_id"],
        "name": row["name"],
        "role": row.get("role") or "",
        "email": row.get("email") or "",
        "linkedin_url": row.get("linkedin_url") or "",
        "notes": row.get("notes") or "",
        "added_by": row["added_by"],
        "created_at": row["created_at"],
    }


async def _find_contact_by_email(db: JobDatabase, application_id: int, email: str) -> Optional[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, application_id, name, role, email, linkedin_url, notes, added_by, created_at "
        "FROM application_contacts WHERE application_id = ? AND email = ?",
        (application_id, email),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _count_contacts(db: JobDatabase, application_id: int) -> int:
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_contacts WHERE application_id = ?", (application_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def add_contact(
    db: JobDatabase,
    user_id: str,
    application_id: int,
    actor: str,
    *,
    name: str,
    role: str = "",
    email: str = "",
    linkedin_url: str = "",
    notes: str = "",
    occurred_at: Optional[str] = None,
) -> dict[str, Any]:
    """R1/R2 — add a contact (idempotent on lower/trim email) and append the
    ``contact_added`` event naming it.

    Validation order mirrors ``record_event``'s route: pure input checks
    first, then the ownership lookup (S1 — a foreign/unknown application id
    reads as 404 with the SAME detail ``get_application`` uses, no existence
    oracle), then the idempotency check (which bypasses the per-application
    cap — the same email answering 200 is not a new row), then the cap itself
    (S4), then the per-user rate limit (S7), then the write.

    THE BUDGET IS SPENT ON A WRITE, NOT ON A LOOKUP. The rate limit sits
    immediately before the INSERT, after every branch that answers without
    writing a row: an agent replaying the same contact (a retry, a resumed
    conversation, two tool calls racing) gets its 200 back for free, and a
    422/404/409 costs nothing either. Only a real create spends.

    THE ROW AND ITS EVENT ARE ONE TRANSACTION. R1 says the EVENT, not the
    row, is what ``whats_new``/``export_history`` show — so a contact whose
    ``append_event`` failed is invisible everywhere while still occupying the
    unique email index, which makes the agent's retry answer
    ``already_existed`` forever and the contact can never be recorded. The
    ``pg`` shim is autocommit, so this needs the explicit block.
    """
    clean_name = _validate_name(name)
    clean_role = _validate_role(role)
    clean_email = _validate_email(email)
    clean_linkedin = _validate_linkedin_url(linkedin_url)
    clean_notes = _validate_notes(notes)
    occurred = parse_occurred_at(occurred_at)

    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")

    if clean_email:
        existing = await _find_contact_by_email(db, application_id, clean_email)
        if existing is not None:
            get_audit_logger().info(
                "contact_already_existed",
                extra={
                    "event": "contact_already_existed", "application_id": application_id,
                    "contact_id": existing["id"],
                },
            )
            return {"contact": _serialize(existing), "already_existed": True, "event_id": None}

    count = await _count_contacts(db, application_id)
    if count >= settings.CONTACTS_PER_APPLICATION_MAX:
        raise SpineError(
            409,
            f"contact cap reached; CONTACTS_PER_APPLICATION_MAX is {settings.CONTACTS_PER_APPLICATION_MAX}",
        )

    key = f"add_contact:{user_id}"
    if not rate_limit.check_and_record(key, max_in_window=settings.CONTACTS_MAX_PER_HOUR, window_seconds=3600):
        raise SpineError(429, "contact rate limit exceeded; try again in an hour")

    now = datetime.now(timezone.utc).isoformat()
    try:
        async with db._db.transaction():
            cur = await db._db.execute(
                "INSERT INTO application_contacts "
                "(user_id, application_id, name, role, email, linkedin_url, notes, added_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, application_id, clean_name, clean_role, clean_email,
                    clean_linkedin, clean_notes, actor, now,
                ),
            )
            contact_id = int(cur.lastrowid or 0)
            event = await append_event(
                db, user_id=user_id, application_id=application_id, event_type="contact_added",
                detail=f"{clean_name} — {clean_role}", payload={"contact_id": contact_id},
                occurred_at=occurred, recorded_by=actor,
            )
    except pg.IntegrityError:
        # A race: the same email landed between the pre-check above and this
        # insert (C4-style retry, same reasoning as save_artifact's version
        # race — see spine.py). The transaction block already rolled the
        # statement back, so the connection is usable again.
        if clean_email:
            existing = await _find_contact_by_email(db, application_id, clean_email)
            if existing is not None:
                return {"contact": _serialize(existing), "already_existed": True, "event_id": None}
        raise
    get_audit_logger().info(
        "contact_added",
        extra={
            "event": "contact_added", "application_id": application_id, "contact_id": contact_id,
            "has_email": bool(clean_email), "name_chars": len(clean_name),
        },
    )
    contact = {
        "id": contact_id, "application_id": application_id, "name": clean_name, "role": clean_role,
        "email": clean_email, "linkedin_url": clean_linkedin, "notes": clean_notes,
        "added_by": actor, "created_at": now,
    }
    return {"contact": contact, "already_existed": False, "event_id": event["event_id"]}


async def list_contacts(db: JobDatabase, user_id: str, application_id: int) -> list[dict[str, Any]]:
    """R3 — every contact on the application, oldest first."""
    cur = await db._db.execute(
        "SELECT id, application_id, name, role, email, linkedin_url, notes, added_by, created_at "
        "FROM application_contacts WHERE user_id = ? AND application_id = ? ORDER BY id ASC",
        (user_id, application_id),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    return [_serialize(r) for r in rows]
