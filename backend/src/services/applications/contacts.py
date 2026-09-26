"""Contacts — people met during an application's outreach (spec
docs/plans/2026-09-05-contacts-stats/spec.md R1-R4, S1-S7, S12), extended
2026-09-25 for outreach tracking (owner decisions): a contact can be linked
to a job or to none (cold networking), carries an append-only outreach
ledger (message versions, sent marks, reply marks) and an append-only edit
history over its own fields.

A contact is an add-only row on ``application_contacts``, owned by one
user, optionally one application. Adding a LINKED contact appends a
``contact_added`` event whose ``payload`` is ``{"contact_id": <id>}`` and
whose ``detail`` is the contact's display line (``name — role``) — the
event, not the row, is what ``whats_new``/``export_history`` surface (R1).
The same non-empty email (lower-cased, trimmed) on the same application is
the SAME contact: the second add returns the first row
(``already_existed: true``), no second row, no second event (R2). A COLD
contact (no ``application_id``) dedupes the same way but scoped to the
USER instead of an application (migration 0046's partial unique index) and
appends no event (there is no job timeline to write to). Without an email
there is no identity, so every add is a new row.

No update, no delete at runtime on ``application_contacts``,
``contact_outreach`` or ``contact_edits`` (S12 — grep-guarded by
``tests/test_slice4_contacts.py::test_contacts_are_append_only``, the same
pattern ``test_application_spine.py`` uses for events/artifacts). Editing a
contact's own fields (name/role/email/linkedin_url/notes) appends a
``contact_edits`` row instead of touching the base row — the CURRENT value
of a field is its newest edit, or the base row's value if never edited; the
full history ("was X") is every edit plus the base value, oldest first.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.repositories import pg
from src.services.applications.spine import (
    FOLLOW_UP_UNSET,
    SpineError,
    append_event,
    get_owned_application,
    parse_follow_up_on,
    parse_occurred_at,
    user_today,
)
from src.services.auth import rate_limit
from src.utils.logger import get_audit_logger

if TYPE_CHECKING:  # pragma: no cover — type-only, same reasoning as spine.py
    from src.repositories.database import JobDatabase

# S5 — the simple shape check the spec names; not a full RFC 5322 validator
# (this is a contact's stated email, not a login credential). Structural, not a
# regex: `^[^@\s]+@[^@\s]+\.[^@\s]+$` is what it means, but that pattern backtracks
# polynomially on `a@!.!.!.…` (CodeQL py/polynomial-redos), and this is O(n).
_WHITESPACE_RE = re.compile(r"\s")

# Same actor string authorship.actor_for gives a signed-in browser session
# (imported there as a type-only annotation, so re-declared as a plain
# constant here rather than pulling in src.api.auth_deps at runtime).
_WEB_ACTOR = "web"

_CRLF_RE = re.compile(r"[\r\n]+")


def _strip_crlf(value: str) -> str:
    """CodeQL py/log-injection (coordinator review, 2026-09-26) — collapse
    embedded CR/LF to a single space. A name/role/email/linkedin_url
    legitimately spans one line, so this costs nothing real; it also means
    every value that reaches an audit log via these fields (``has_email``,
    ``name_chars``, ``fields``, ``entry``, ``channel``, …) is provably free
    of injected newlines at the SOURCE, not just at the handler
    (``CRLFScrubFilter`` in ``utils/logger.py`` already scrubs every
    logger's OUTPUT for this; this closes the same gap explicitly, in-line,
    so a static scanner's dataflow sees the sanitizing step too — the same
    idea ``spine.py``'s ``_has_control_chars``/``validate_source`` already
    applies to an event's email-evidence fields)."""
    return _CRLF_RE.sub(" ", value)


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
    name = _strip_crlf((raw or "").strip())
    if not name or len(name) > settings.CONTACT_NAME_MAX_CHARS:
        raise SpineError(
            422, f"name must be 1-{settings.CONTACT_NAME_MAX_CHARS} chars (CONTACT_NAME_MAX_CHARS) after trim"
        )
    return name


def _validate_role(raw: str) -> str:
    role = _strip_crlf((raw or "").strip())
    if len(role) > settings.CONTACT_ROLE_MAX_CHARS:
        raise SpineError(422, f"role exceeds CONTACT_ROLE_MAX_CHARS ({settings.CONTACT_ROLE_MAX_CHARS} chars)")
    return role


def _validate_email(raw: str) -> str:
    email = _strip_crlf((raw or "").strip())
    if not email:
        return ""
    if len(email) > settings.CONTACT_EMAIL_MAX_CHARS:
        raise SpineError(422, f"email exceeds CONTACT_EMAIL_MAX_CHARS ({settings.CONTACT_EMAIL_MAX_CHARS} chars)")
    if not _looks_like_email(email):
        raise SpineError(422, "email must look like a real address (e.g. name@example.com)")
    # R2 — stored lower-cased + trimmed so identity is case/whitespace-insensitive.
    return email.lower()


def _validate_linkedin_url(raw: str) -> str:
    url = _strip_crlf((raw or "").strip())
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
        "application_id": row.get("application_id"),
        "name": row["name"],
        "role": row.get("role") or "",
        "email": row.get("email") or "",
        "linkedin_url": row.get("linkedin_url") or "",
        "notes": row.get("notes") or "",
        "added_by": row["added_by"],
        "created_at": row["created_at"],
    }


async def _find_contact_by_current_email(
    db: JobDatabase,
    application_id: Optional[int],
    user_id: str,
    email: str,
    *,
    exclude_contact_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """R2 — the SAME contact by CURRENT email: the base row's email UNLESS a
    ``contact_edits`` row has since overridden it, in which case the LATEST
    edit wins (bug fix, coordinator review 2026-09-26 — a base-row-only
    lookup let an edited-away email keep matching its old owner forever, and
    let a just-edited-TO email silently create a duplicate instead of
    finding its new one). Scoped to the application when linked, to the USER
    when cold (migration 0046's two partial unique indexes). ``exclude_
    contact_id`` is used by ``update_contact``'s collision check — a contact
    never collides with its own current email."""
    if application_id is not None:
        scope_sql = "ac.application_id = ?"
        scope_param: Any = application_id
    else:
        scope_sql = "ac.application_id IS NULL AND ac.user_id = ?"
        scope_param = user_id
    exclude_sql = ""
    params: list[Any] = [user_id, scope_param, email]
    if exclude_contact_id is not None:
        exclude_sql = " AND ac.id != ?"
        params.append(exclude_contact_id)
    cur = await db._db.execute(
        f"SELECT ac.id, ac.application_id, ac.name, ac.role, ac.email, ac.linkedin_url, ac.notes, "  # noqa: S608
        f"ac.added_by, ac.created_at FROM application_contacts ac "
        f"LEFT JOIN ("
        f"  SELECT contact_id, value FROM ("
        f"    SELECT contact_id, value, ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY id DESC) AS rn "
        f"    FROM contact_edits WHERE field = 'email' AND user_id = ?"
        f"  ) t WHERE rn = 1"
        f") le ON le.contact_id = ac.id "
        f"WHERE {scope_sql} AND COALESCE(le.value, ac.email) = ?{exclude_sql} "
        f"ORDER BY ac.id ASC LIMIT 1",
        params,
    )
    row = await cur.fetchone()
    return dict(row) if row else None


def _email_lock_key(user_id: str, application_id: Optional[int], email: str) -> str:
    """Bug fix (coordinator review, 2026-09-26) — the key ``pg_advisory_xact_
    lock(hashtext(...))`` locks on: check-then-write on the SAME (user, scope,
    email) must serialize, so two concurrent callers claiming the same
    address can never both win. Scope is the application id when linked, the
    literal string ``"cold"`` when not — matching ``_find_contact_by_current_
    email``'s own scoping exactly."""
    scope = str(application_id) if application_id is not None else "cold"
    return f"{user_id}:{scope}:{email.strip().lower()}"


async def _lock_email_scope(db: JobDatabase, user_id: str, application_id: Optional[int], email: str) -> None:
    """Acquire the advisory lock for this (user, scope, email) — MUST be the
    first statement inside the transaction that will check-then-write, and
    only ever called with a non-empty ``email`` (no identity, no lock)."""
    await db._db.execute(
        "SELECT pg_advisory_xact_lock(hashtext(?))", (_email_lock_key(user_id, application_id, email),)
    )


async def _count_contacts(db: JobDatabase, application_id: int) -> int:
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_contacts WHERE application_id = ?", (application_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _count_unlinked_contacts(db: JobDatabase, user_id: str) -> int:
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_contacts WHERE user_id = ? AND application_id IS NULL", (user_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _create_contact_with_deferred_email(
    db: JobDatabase,
    user_id: str,
    application_id: Optional[int],
    actor: str,
    *,
    name: str,
    role: str,
    email: str,
    linkedin_url: str,
    notes: str,
    occurred_at: str,
) -> dict[str, Any]:
    """The rare recovery path ``add_contact`` falls into when the base-row
    UNIQUE index collides with a STALE identity — a different, still-existing
    contact whose CURRENT email has since moved away via an edit, so nobody
    owns ``email`` right now even though its raw slot is still taken (bug
    fix, coordinator review 2026-09-26). Inserts the new row with an EMPTY
    base email (never collides — 0046's partial index excludes ``''``) and
    immediately records the real address as a ``contact_edits`` row, so the
    CURRENT view is correct from the first read; the base row and its own
    ``contact_added`` event go in the SAME transaction, same as the normal
    path."""
    now = datetime.now(timezone.utc).isoformat()
    event_id: Optional[int] = None
    async with db._db.transaction():
        cur = await db._db.execute(
            "INSERT INTO application_contacts "
            "(user_id, application_id, name, role, email, linkedin_url, notes, added_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, application_id, name, role, "", linkedin_url, notes, actor, now),
        )
        contact_id = int(cur.lastrowid or 0)
        # Bug fix (coordinator review, 2026-09-26) — `is_initial=TRUE` marks
        # this as the creation-time record of the REAL address, not a later
        # correction, so `_full_contact_view` folds it into the base history
        # entry instead of showing a spurious "was (empty)" first.
        await db._db.execute(
            "INSERT INTO contact_edits (user_id, contact_id, field, value, recorded_at, recorded_by, is_initial) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, contact_id, "email", email, now, actor, True),
        )
        if application_id is not None:
            event = await append_event(
                db, user_id=user_id, application_id=application_id, event_type="contact_added",
                detail=f"{name} — {role}", payload={"contact_id": contact_id},
                occurred_at=occurred_at, recorded_by=actor,
            )
            event_id = event["event_id"]
    get_audit_logger().info(
        "contact_added",
        extra={
            "event": "contact_added", "application_id": application_id, "contact_id": contact_id,
            "has_email": bool(email), "name_chars": len(name), "deferred_email": True,
        },
    )
    base_row = {
        "id": contact_id, "application_id": application_id, "name": name, "role": role,
        "email": "", "linkedin_url": linkedin_url, "notes": notes, "added_by": actor, "created_at": now,
    }
    view = await _full_contact_view(db, user_id, base_row)
    return {"contact": view, "already_existed": False, "event_id": event_id}


async def add_contact(
    db: JobDatabase,
    user_id: str,
    application_id: Optional[int],
    actor: str,
    *,
    name: str,
    role: str = "",
    email: str = "",
    linkedin_url: str = "",
    notes: str = "",
    occurred_at: Optional[str] = None,
) -> dict[str, Any]:
    """R1/R2 — add a contact (idempotent on lower/trim email) and, for a
    LINKED contact, append the ``contact_added`` event naming it.

    ``application_id=None`` (owner decision, 2026-09-25) — cold networking: a
    person with no job yet. Dedupe scopes to the USER instead of the
    application, the cap is ``CONTACTS_UNLINKED_MAX`` instead of
    ``CONTACTS_PER_APPLICATION_MAX``, and no event is appended — there is no
    job timeline to write to.

    Validation order mirrors ``record_event``'s route: pure input checks
    first, then the ownership lookup for a LINKED contact (S1 — a
    foreign/unknown application id reads as 404 with the SAME detail
    ``get_application`` uses, no existence oracle), then the idempotency
    check (which bypasses the cap — the same email answering 200 is not a
    new row), then the cap itself (S4), then the per-user rate limit (S7),
    then the write.

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

    if application_id is not None:
        app_row = await get_owned_application(db, user_id, application_id)
        if app_row is None:
            raise SpineError(404, "application not found")

    if not clean_email:
        # No email = no identity, so no possible collision and no lock is
        # needed — every call is unconditionally a new row.
        if application_id is not None:
            count = await _count_contacts(db, application_id)
            if count >= settings.CONTACTS_PER_APPLICATION_MAX:
                raise SpineError(
                    409,
                    f"contact cap reached; CONTACTS_PER_APPLICATION_MAX is "
                    f"{settings.CONTACTS_PER_APPLICATION_MAX}",
                )
        else:
            count = await _count_unlinked_contacts(db, user_id)
            if count >= settings.CONTACTS_UNLINKED_MAX:
                raise SpineError(
                    409, f"cold-contact cap reached; CONTACTS_UNLINKED_MAX is {settings.CONTACTS_UNLINKED_MAX}"
                )
        key = f"add_contact:{user_id}"
        if not rate_limit.check_and_record(
            key, max_in_window=settings.CONTACTS_MAX_PER_HOUR, window_seconds=3600
        ):
            raise SpineError(429, "contact rate limit exceeded; try again in an hour")

        now = datetime.now(timezone.utc).isoformat()
        event_id: Optional[int] = None
        async with db._db.transaction():
            cur = await db._db.execute(
                "INSERT INTO application_contacts "
                "(user_id, application_id, name, role, email, linkedin_url, notes, added_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, application_id, clean_name, clean_role, "", clean_linkedin, clean_notes, actor, now),
            )
            contact_id = int(cur.lastrowid or 0)
            if application_id is not None:
                event = await append_event(
                    db, user_id=user_id, application_id=application_id, event_type="contact_added",
                    detail=f"{clean_name} — {clean_role}", payload={"contact_id": contact_id},
                    occurred_at=occurred, recorded_by=actor,
                )
                event_id = event["event_id"]
        get_audit_logger().info(
            "contact_added",
            extra={
                "event": "contact_added", "application_id": application_id, "contact_id": contact_id,
                "has_email": False, "name_chars": len(clean_name),
            },
        )
        base_row = {
            "id": contact_id, "application_id": application_id, "name": clean_name, "role": clean_role,
            "email": "", "linkedin_url": clean_linkedin, "notes": clean_notes,
            "added_by": actor, "created_at": now,
        }
        view = await _full_contact_view(db, user_id, base_row)
        return {"contact": view, "already_existed": False, "event_id": event_id}

    # Bug fix (coordinator review, 2026-09-26) — clean_email is set, so an
    # identity IS at stake: hold the advisory xact lock for (user, scope,
    # email) across the ENTIRE check-then-write (pre-check, cap, rate limit,
    # insert, event) so two concurrent callers claiming the same address can
    # never both win. The lock releases automatically on commit OR rollback
    # (a 409/429 raised inside this block still releases it).
    now = datetime.now(timezone.utc).isoformat()
    event_id = None
    async with db._db.transaction():
        await _lock_email_scope(db, user_id, application_id, clean_email)

        existing = await _find_contact_by_current_email(db, application_id, user_id, clean_email)
        if existing is not None:
            get_audit_logger().info(
                "contact_already_existed",
                extra={
                    "event": "contact_already_existed", "application_id": application_id,
                    "contact_id": existing["id"],
                },
            )
            view = await _full_contact_view(db, user_id, existing)
            return {"contact": view, "already_existed": True, "event_id": None}

        if application_id is not None:
            count = await _count_contacts(db, application_id)
            if count >= settings.CONTACTS_PER_APPLICATION_MAX:
                raise SpineError(
                    409,
                    f"contact cap reached; CONTACTS_PER_APPLICATION_MAX is "
                    f"{settings.CONTACTS_PER_APPLICATION_MAX}",
                )
        else:
            count = await _count_unlinked_contacts(db, user_id)
            if count >= settings.CONTACTS_UNLINKED_MAX:
                raise SpineError(
                    409, f"cold-contact cap reached; CONTACTS_UNLINKED_MAX is {settings.CONTACTS_UNLINKED_MAX}"
                )

        # NOTE: unlike the outreach cap, this pre-existing limit (slice 4) is
        # pinned by tests/test_slice4_contacts.py::test_add_contact_is_rate_
        # limited_per_user to apply to a web session too — left as-is here.
        key = f"add_contact:{user_id}"
        if not rate_limit.check_and_record(
            key, max_in_window=settings.CONTACTS_MAX_PER_HOUR, window_seconds=3600
        ):
            raise SpineError(429, "contact rate limit exceeded; try again in an hour")

        try:
            # A SAVEPOINT scope (nested inside the outer, lock-holding
            # transaction) for just the risky INSERT: on IntegrityError only
            # THIS nested scope rolls back — the outer transaction (and the
            # lock it holds) stays open and queryable for the recovery below.
            # Catching the error without this nested scope would leave the
            # WHOLE outer transaction aborted at the Postgres level, breaking
            # every statement after it (found in review).
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
                if application_id is not None:
                    event = await append_event(
                        db, user_id=user_id, application_id=application_id, event_type="contact_added",
                        detail=f"{clean_name} — {clean_role}", payload={"contact_id": contact_id},
                        occurred_at=occurred, recorded_by=actor,
                    )
                    event_id = event["event_id"]
        except pg.IntegrityError:
            # Under the lock, this can no longer be a same-key race — it can
            # only be the stale-identity collision `_create_contact_with_
            # deferred_email` exists for (a DIFFERENT contact's base row,
            # left behind by an edit that moved its email elsewhere).
            existing = await _find_contact_by_current_email(db, application_id, user_id, clean_email)
            if existing is not None:
                view = await _full_contact_view(db, user_id, existing)
                return {"contact": view, "already_existed": True, "event_id": None}
            return await _create_contact_with_deferred_email(
                db, user_id, application_id, actor, name=clean_name, role=clean_role, email=clean_email,
                linkedin_url=clean_linkedin, notes=clean_notes, occurred_at=occurred,
            )

    get_audit_logger().info(
        "contact_added",
        extra={
            "event": "contact_added", "application_id": application_id, "contact_id": contact_id,
            "has_email": True, "name_chars": len(clean_name),
        },
    )
    base_row = {
        "id": contact_id, "application_id": application_id, "name": clean_name, "role": clean_role,
        "email": clean_email, "linkedin_url": clean_linkedin, "notes": clean_notes,
        "added_by": actor, "created_at": now,
    }
    view = await _full_contact_view(db, user_id, base_row)
    return {"contact": view, "already_existed": False, "event_id": event_id}


async def get_owned_contact(db: JobDatabase, user_id: str, contact_id: int) -> Optional[dict[str, Any]]:
    """S2 — the only lookup every contact-scoped function below builds on. A
    foreign or unknown id reads as None (the route turns that into 404 —
    ``"contact not found"``, never 403)."""
    cur = await db._db.execute(
        "SELECT id, application_id, name, role, email, linkedin_url, notes, added_by, created_at "
        "FROM application_contacts WHERE id = ? AND user_id = ?",
        (contact_id, user_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_contacts(
    db: JobDatabase, user_id: str, application_id: int, *, include_text: bool = True
) -> list[dict[str, Any]]:
    """R3 — every contact on the application, oldest first, each carrying its
    current (edit-overlaid) details, edit history, and outreach ledger.
    ``include_text=False`` (export_history only — every other caller keeps
    the default) strips outreach message/sent/reply text."""
    cur = await db._db.execute(
        "SELECT id, application_id, name, role, email, linkedin_url, notes, added_by, created_at "
        "FROM application_contacts WHERE user_id = ? AND application_id = ? ORDER BY id ASC",
        (user_id, application_id),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    out = []
    for r in rows:
        out.append(await _full_contact_view(db, user_id, r, include_text=include_text))
    return out


# ── Contact edits (owner decision, 2026-09-25) — an append-only overlay, ────
# same shape as ``profile_edits`` (0038): the CURRENT value of a field is its
# newest row; the base ``application_contacts`` row is never touched.


async def _edit_history(db: JobDatabase, user_id: str, contact_id: int) -> dict[str, list[dict[str, Any]]]:
    """Every edit ever recorded for this contact, grouped by field, OLDEST
    first — the shape a reader turns into "current = last, was = the rest".
    ``is_initial`` (bug fix, coordinator review 2026-09-26) marks the one
    creation-time row ``_create_contact_with_deferred_email`` writes; the
    caller (``_full_contact_view``) folds it into the base entry."""
    cur = await db._db.execute(
        "SELECT field, value, recorded_at, recorded_by, is_initial FROM contact_edits "
        "WHERE user_id = ? AND contact_id = ? ORDER BY id ASC",
        (user_id, contact_id),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    by_field: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_field.setdefault(r["field"], []).append(
            {
                "value": r["value"], "recorded_at": r["recorded_at"], "recorded_by": r["recorded_by"],
                "is_initial": bool(r.get("is_initial")),
            }
        )
    return by_field


def _validators_by_field() -> dict[str, Any]:
    return {
        "name": _validate_name,
        "role": _validate_role,
        "email": _validate_email,
        "linkedin_url": _validate_linkedin_url,
        "notes": _validate_notes,
    }


async def _count_edits(db: JobDatabase, contact_id: int) -> int:
    cur = await db._db.execute("SELECT COUNT(*) FROM contact_edits WHERE contact_id = ?", (contact_id,))
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def update_contact(
    db: JobDatabase,
    user_id: str,
    contact_id: int,
    actor: str,
    *,
    name: Optional[str] = None,
    role: Optional[str] = None,
    email: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Owner decision, 2026-09-25 — contacts ARE editable, but the base row
    stays append-only (S12): each provided field appends one ``contact_edits``
    row. Same bounds as ``add_contact`` (a contact's fields cost the same
    whether set at creation or edited later). Only fields explicitly named
    are touched; the rest keep whatever their newest value already was."""
    contact = await get_owned_contact(db, user_id, contact_id)
    if contact is None:
        raise SpineError(404, "contact not found")

    given: dict[str, str] = {}
    validators = _validators_by_field()
    if name is not None:
        given["name"] = validators["name"](name)
    if role is not None:
        given["role"] = validators["role"](role)
    if email is not None:
        given["email"] = validators["email"](email)
    if linkedin_url is not None:
        given["linkedin_url"] = validators["linkedin_url"](linkedin_url)
    if notes is not None:
        given["notes"] = validators["notes"](notes)
    if not given:
        raise SpineError(422, "at least one field must be given")

    new_email = given.get("email")

    existing_count = await _count_edits(db, contact_id)
    if existing_count + len(given) > settings.CONTACT_EDITS_PER_CONTACT_MAX:
        raise SpineError(
            409,
            f"contact edit cap reached; CONTACT_EDITS_PER_CONTACT_MAX is "
            f"{settings.CONTACT_EDITS_PER_CONTACT_MAX}",
        )

    now = datetime.now(timezone.utc).isoformat()
    async with db._db.transaction():
        # Bug fix (coordinator review, 2026-09-26) — the SAME advisory xact
        # lock add_contact takes, held across the collision check AND the
        # write: two concurrent edits claiming the same email (same scope)
        # can never both win. Raising here (a 409) still releases the lock —
        # it rolls the whole transaction back, so no edit rows land either.
        if new_email:
            await _lock_email_scope(db, user_id, contact.get("application_id"), new_email)
            collision = await _find_contact_by_current_email(
                db, contact.get("application_id"), user_id, new_email, exclude_contact_id=contact_id
            )
            if collision is not None:
                raise SpineError(409, "another contact already has that email")
        for field, value in given.items():
            await db._db.execute(
                "INSERT INTO contact_edits (user_id, contact_id, field, value, recorded_at, recorded_by) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, contact_id, field, value, now, actor),
            )
    get_audit_logger().info(
        "contact_edited",
        extra={
            "event": "contact_edited", "contact_id": contact_id,
            # CodeQL py/log-injection — field NAMES only (S4: never values),
            # and stripped defensively even though every key here is one of
            # settings.CONTACT_EDIT_FIELDS, not free text.
            "fields": [_strip_crlf(f) for f in sorted(given)],
        },
    )
    return await _full_contact_view(db, user_id, contact)


# ── Outreach ledger (owner decisions, 2026-09-25) ───────────────────────────
# ``contact_outreach`` — append-only: a message VERSION the assistant drafted
# (``entry="message"``), a ``sent`` mark (the USER told the assistant it went
# out), or a ``reply`` mark (LinkedIn: the user says so; email: the daily
# check, carrying a ``source_message_id`` for idempotent re-reads).


def _serialize_outreach(row: dict[str, Any], *, include_text: bool = True) -> dict[str, Any]:
    return {
        "id": row["id"],
        "contact_id": row["contact_id"],
        "entry": row["entry"],
        "channel": row["channel"],
        # Bug fix (coordinator review, 2026-09-26) — export_history's
        # `include_text=False` must strip outreach text the same way it
        # already strips artifact/receipt text; the structure (entry,
        # channel, dates) stays so the shape is uniform either way.
        "text": (row.get("text") or "") if include_text else "",
        "version_no": row.get("version_no"),
        "occurred_at": row["occurred_at"],
        "recorded_at": row["recorded_at"],
        "recorded_by": row["recorded_by"],
        "source_message_id": row.get("source_message_id") or "",
    }


async def _outreach_rows(db: JobDatabase, contact_id: int) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, contact_id, entry, channel, text, version_no, occurred_at, recorded_at, recorded_by, "
        "source_message_id FROM contact_outreach WHERE contact_id = ? ORDER BY id ASC",
        (contact_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


def _outreach_view(rows: list[dict[str, Any]], *, include_text: bool = True) -> dict[str, Any]:
    messages = [_serialize_outreach(r, include_text=include_text) for r in rows if r["entry"] == "message"]
    sent = [_serialize_outreach(r, include_text=include_text) for r in rows if r["entry"] == "sent"]
    replies = [_serialize_outreach(r, include_text=include_text) for r in rows if r["entry"] == "reply"]
    return {
        "messages": messages,
        "sent": sent,
        "replies": replies,
        "message_count": len(messages),
        "last_sent": sent[-1] if sent else None,
        "replied": bool(replies),
        "last_reply": replies[-1] if replies else None,
    }


async def _find_outreach_by_source(
    db: JobDatabase, contact_id: int, source_message_id: str
) -> Optional[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, contact_id, entry, channel, text, version_no, occurred_at, recorded_at, recorded_by, "
        "source_message_id FROM contact_outreach WHERE contact_id = ? AND source_message_id = ?",
        (contact_id, source_message_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _outreach_entry_count(db: JobDatabase, contact_id: int) -> int:
    cur = await db._db.execute("SELECT COUNT(*) FROM contact_outreach WHERE contact_id = ?", (contact_id,))
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _message_version_count(db: JobDatabase, contact_id: int) -> int:
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM contact_outreach WHERE contact_id = ? AND entry = 'message'", (contact_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def record_outreach(
    db: JobDatabase,
    user_id: str,
    contact_id: int,
    actor: str,
    *,
    entry: str,
    channel: str,
    text: str = "",
    occurred_at: Optional[str] = None,
    source_message_id: str = "",
    follow_up_on: Optional[str] = None,
    application_id_hint: Optional[int] = None,
) -> dict[str, Any]:
    """The one door every outreach write goes through — the direct
    ``POST /api/contacts/{contact_id}/outreach`` route, ``save_artifact``
    (``entry="message"``) and ``record_event`` (``entry="sent"``/``"reply"``)
    with a ``contact_id`` all call this (M5 parity).

    ``entry``: ``"message"`` a drafted version (numbered, capped by
    ``OUTREACH_VERSIONS_PER_CONTACT_MAX``, no timeline event — a version is
    not news); ``"sent"``/``"reply"`` a mark (unversioned — a person can be
    sent to or replied to many times) that ALSO appends a job-timeline event
    (``outreach_sent``/``outreach_replied``) when the contact is linked to an
    application, in the SAME transaction. A person's reply NEVER changes the
    job's status (both event types are NOTE-family — see
    ``settings.APPLICATION_NOTE_EVENT_TYPES``).

    ``follow_up_on`` is only meaningful on a ``"sent"`` mark, and only for a
    LINKED contact — a cold contact has no job to chase, so this raises 422
    naming exactly why (owner decision).

    ``source_message_id`` — idempotent re-read of the same email: a second
    call naming the same id returns the EXISTING row (``already_existed``),
    same pattern as ``spine.append_event``'s ``source``.
    """
    contact = await get_owned_contact(db, user_id, contact_id)
    if contact is None:
        raise SpineError(404, "contact not found")
    if application_id_hint is not None and contact.get("application_id") != application_id_hint:
        raise SpineError(422, "application_id does not match this contact's linked job")

    if entry not in ("message", "sent", "reply"):
        raise SpineError(422, "entry must be one of 'message', 'sent', 'reply'")
    if channel not in settings.OUTREACH_CHANNELS:
        raise SpineError(422, f"channel must be one of OUTREACH_CHANNELS {settings.OUTREACH_CHANNELS}")
    if follow_up_on is not None and entry != "sent":
        raise SpineError(422, "follow_up_on is only allowed when recording a sent message")
    if follow_up_on is not None and contact.get("application_id") is None:
        raise SpineError(422, "cold contacts have no follow-up date")

    if source_message_id and entry == "message":
        raise SpineError(422, "source_message_id is only meaningful on 'sent'/'reply' entries")

    if source_message_id:
        existing = await _find_outreach_by_source(db, contact_id, source_message_id)
        if existing is not None:
            get_audit_logger().info(
                "outreach_duplicate",
                extra={"event": "outreach_duplicate", "contact_id": contact_id, "outreach_id": existing["id"]},
            )
            return {
                "outreach": _serialize_outreach(existing), "already_existed": True, "event_id": None,
                "follow_up_on": None,
            }

    clean_text = text or ""
    is_message = entry == "message"
    if is_message:
        if not clean_text.strip():
            raise SpineError(422, "text must not be empty for a message version")
        if len(clean_text) > settings.OUTREACH_MESSAGE_MAX_CHARS:
            raise SpineError(
                422, f"text exceeds OUTREACH_MESSAGE_MAX_CHARS ({settings.OUTREACH_MESSAGE_MAX_CHARS} chars)"
            )
        version_count = await _message_version_count(db, contact_id)
        if version_count >= settings.OUTREACH_VERSIONS_PER_CONTACT_MAX:
            raise SpineError(
                429,
                f"too many message versions; cap is OUTREACH_VERSIONS_PER_CONTACT_MAX "
                f"({settings.OUTREACH_VERSIONS_PER_CONTACT_MAX})",
            )
    else:
        if len(clean_text) > settings.OUTREACH_MESSAGE_MAX_CHARS:
            raise SpineError(
                422, f"text exceeds OUTREACH_MESSAGE_MAX_CHARS ({settings.OUTREACH_MESSAGE_MAX_CHARS} chars)"
            )

    total = await _outreach_entry_count(db, contact_id)
    if total >= settings.OUTREACH_ENTRIES_PER_CONTACT_MAX:
        raise SpineError(
            429,
            f"too many outreach entries; cap is OUTREACH_ENTRIES_PER_CONTACT_MAX "
            f"({settings.OUTREACH_ENTRIES_PER_CONTACT_MAX})",
        )

    # Bug fix (coordinator review, 2026-09-26) — every input must be VALIDATED
    # before the rate limit is SPENT: a 422/404/429-cap answer must cost
    # nothing, same reasoning as add_contact's own ordering. `occurred_at`/
    # `follow_up_on` are parsed here, above the check_and_record call below,
    # not after it.
    occurred = parse_occurred_at(occurred_at)
    application_id = contact.get("application_id")

    parsed_follow_up: Any = FOLLOW_UP_UNSET
    if follow_up_on is not None and application_id is not None:
        today = await user_today(db, user_id)
        parsed_follow_up = parse_follow_up_on(follow_up_on, today)

    # Owner decision, 2026-09-25 — same web-session exemption as add_contact
    # above: this hourly cap is the ASSISTANT's budget, never the human's.
    key = f"outreach:{user_id}"
    if actor != _WEB_ACTOR and not rate_limit.check_and_record(
        key, max_in_window=settings.OUTREACH_MAX_PER_HOUR, window_seconds=3600
    ):
        raise SpineError(429, "outreach rate limit exceeded; try again in an hour")

    now = datetime.now(timezone.utc).isoformat()
    event_id: Optional[int] = None
    final_follow_up_on: Optional[str] = None
    outreach_id: Optional[int] = None
    version_no: Optional[int] = None

    if is_message:
        # Bug fix (coordinator review, 2026-09-26) — C4-style retry, same
        # reasoning as spine.save_artifact's version race: two concurrent
        # drafts for the same contact must never surface a raw 500, the
        # loser just gets renumbered on retry. No `async with transaction()`
        # here (same as save_artifact) — a single INSERT is already atomic
        # under the autocommit shim; on a UNIQUE clash `rollback()` alone
        # clears the error state so the connection is reusable for the retry.
        for _attempt in range(5):
            version_no = await _message_version_count(db, contact_id) + 1
            try:
                ins = await db._db.execute(
                    "INSERT INTO contact_outreach "
                    "(user_id, contact_id, entry, channel, text, version_no, occurred_at, recorded_at, "
                    " recorded_by, source_message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id, contact_id, entry, channel, clean_text, version_no, occurred, now, actor,
                        source_message_id or "",
                    ),
                )
            except pg.IntegrityError:
                await db._db.rollback()
                continue
            outreach_id = int(ins.lastrowid or 0)
            break
        if outreach_id is None:
            raise SpineError(429, "could not allocate a message version under concurrent writes — retry")
    else:
        try:
            async with db._db.transaction():
                cur = await db._db.execute(
                    "INSERT INTO contact_outreach "
                    "(user_id, contact_id, entry, channel, text, version_no, occurred_at, recorded_at, "
                    " recorded_by, source_message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id, contact_id, entry, channel, clean_text, None, occurred, now, actor,
                        source_message_id or "",
                    ),
                )
                outreach_id = int(cur.lastrowid or 0)
                if application_id is not None:
                    event_type = "outreach_sent" if entry == "sent" else "outreach_replied"
                    event = await append_event(
                        db, user_id=user_id, application_id=application_id, event_type=event_type,
                        payload={"contact_id": contact_id, "outreach_id": outreach_id, "channel": channel},
                        occurred_at=occurred, recorded_by=actor, follow_up_on=parsed_follow_up,
                    )
                    event_id = event["event_id"]
                    final_follow_up_on = event["follow_up_on"]
        except pg.IntegrityError:
            if source_message_id:
                existing = await _find_outreach_by_source(db, contact_id, source_message_id)
                if existing is not None:
                    return {
                        "outreach": _serialize_outreach(existing), "already_existed": True, "event_id": None,
                        "follow_up_on": None,
                    }
            raise

    get_audit_logger().info(
        "outreach_recorded",
        extra={
            "event": "outreach_recorded", "contact_id": contact_id,
            # CodeQL py/log-injection — `entry`/`channel` are already closed-
            # enum-validated above, but stripped again here defensively so
            # the sanitizing step is visible at this call site too.
            "entry": _strip_crlf(entry), "channel": _strip_crlf(channel),
            "chars": len(clean_text),
        },
    )
    outreach = {
        "id": outreach_id, "contact_id": contact_id, "entry": entry, "channel": channel, "text": clean_text,
        "version_no": version_no, "occurred_at": occurred, "recorded_at": now, "recorded_by": actor,
        "source_message_id": source_message_id or "",
    }
    return {
        "outreach": outreach, "already_existed": False, "event_id": event_id,
        "follow_up_on": final_follow_up_on,
    }


# ── Full-record readers ─────────────────────────────────────────────────────


async def _full_contact_view(
    db: JobDatabase, user_id: str, base: dict[str, Any], *, include_text: bool = True
) -> dict[str, Any]:
    """One contact row, current details + edit history + outreach ledger —
    the shape ``get_application``/``export_history``/``list_people(contact_id=…)``
    all share. ``include_text=False`` (export_history only) strips outreach
    message/sent/reply text; every other reader keeps the default (full)."""
    contact_id = base["id"]
    edits = await _edit_history(db, user_id, contact_id)
    view = _serialize(base)
    history: dict[str, list[dict[str, Any]]] = {}
    for field in settings.CONTACT_EDIT_FIELDS:
        field_edits = edits.get(field, [])
        if field_edits:
            view[field] = field_edits[-1]["value"]
        # Bug fix (coordinator review, 2026-09-26) — the deferred-email
        # creation-time edit (`is_initial=True`) IS the real starting value;
        # fold it into the base entry instead of listing the base row's own
        # (necessarily blank) value as a separate "was ''" history step.
        if field_edits and field_edits[0]["is_initial"]:
            initial = field_edits[0]
            base_entry = {
                "value": initial["value"], "recorded_at": initial["recorded_at"],
                "recorded_by": initial["recorded_by"],
            }
            rest = field_edits[1:]
        else:
            base_entry = {
                "value": _serialize(base)[field], "recorded_at": base["created_at"],
                "recorded_by": base["added_by"],
            }
            rest = field_edits
        history[field] = [
            base_entry, *({"value": e["value"], "recorded_at": e["recorded_at"], "recorded_by": e["recorded_by"]}
                           for e in rest)
        ]
    view["edit_history"] = history
    rows = await _outreach_rows(db, contact_id)
    view["outreach"] = _outreach_view(rows, include_text=include_text)
    return view


def _group_key(row: dict[str, Any]) -> str:
    email = (row.get("email") or "").strip().lower()
    if email:
        return f"email:{email}"
    linkedin = (row.get("linkedin_url") or "").strip()
    if linkedin:
        return f"linkedin:{linkedin}"
    return f"row:{row['id']}"


async def _latest_edits_for_user(db: JobDatabase, user_id: str) -> dict[int, dict[str, str]]:
    """The CURRENT value of every edited field, for every contact this user
    owns — ONE query (bug fix, coordinator review 2026-09-26), so list_people's
    grouping/filtering/display never goes N+1 reading contact_edits per row."""
    cur = await db._db.execute(
        "SELECT contact_id, field, value FROM ("
        "  SELECT contact_id, field, value, "
        "         ROW_NUMBER() OVER (PARTITION BY contact_id, field ORDER BY id DESC) AS rn "
        "  FROM contact_edits WHERE user_id = ?"
        ") ranked WHERE rn = 1",
        (user_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    by_contact: dict[int, dict[str, str]] = {}
    for r in rows:
        by_contact.setdefault(r["contact_id"], {})[r["field"]] = r["value"]
    return by_contact


def _apply_current_fields(row: dict[str, Any], edits: dict[str, str]) -> None:
    """Overlay one contact row's fields with its latest edits, in place."""
    for field in settings.CONTACT_EDIT_FIELDS:
        if field in edits:
            row[field] = edits[field]


async def _contacts_by_current_email(db: JobDatabase, user_id: str, email: str) -> list[dict[str, Any]]:
    """``list_people(email=…)``'s targeted lookup (bug fix, coordinator review
    2026-09-26) — searches the CURRENT email (base row or its latest edit),
    unbounded by ``LIST_PEOPLE_MAX``: a person's own linked-job count is
    naturally small, so there is nothing here to page."""
    clean = email.strip().lower()
    cur = await db._db.execute(
        "SELECT ac.id, ac.application_id, ac.name, ac.role, ac.email, ac.linkedin_url, ac.notes, "
        "ac.added_by, ac.created_at, a.job_title, a.job_company "
        "FROM application_contacts ac LEFT JOIN applications a ON a.id = ac.application_id "
        "LEFT JOIN ("
        "  SELECT contact_id, value FROM ("
        "    SELECT contact_id, value, ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY id DESC) AS rn "
        "    FROM contact_edits WHERE field = 'email' AND user_id = ?"
        "  ) t WHERE rn = 1"
        ") le ON le.contact_id = ac.id "
        "WHERE ac.user_id = ? AND COALESCE(le.value, ac.email) = ? "
        "ORDER BY ac.id DESC",
        (user_id, user_id, clean),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    edits_by_contact = await _latest_edits_for_user(db, user_id)
    for r in rows:
        _apply_current_fields(r, edits_by_contact.get(r["id"], {}))
    return rows


async def list_people(
    db: JobDatabase, user_id: str, *, contact_id: Optional[int] = None, email: Optional[str] = None
) -> dict[str, Any]:
    """``GET /api/people`` / the ``list_people`` MCP tool.

    No ``contact_id`` — every person the user has ever added, grouped by
    CURRENT lower(email) else linkedin_url (the SAME recruiter linked to two
    jobs is two rows but one person here — decision: "list_people groups by
    lower(email) else linkedin_url"): jobs linked, CURRENT details (base row
    overlaid with the latest edit per field), last sent date + channel,
    replied yes/no, message count, all aggregated across the person's rows.

    ``email`` (bug fix, coordinator review 2026-09-26) narrows to one person
    by CURRENT email, via a targeted SQL lookup that is never subject to
    ``LIST_PEOPLE_MAX`` — a person's own linked-job count is naturally small.
    Without ``email``, the newest ``LIST_PEOPLE_MAX`` contacts are listed
    (newest first) and ``truncated`` says whether more exist.

    With ``contact_id`` — the full record for that ONE row (not merged):
    message versions, sent/reply marks, detail-edit history. Each person's
    reply is its own row's own history; a merge is a LIST-view convenience
    only.
    """
    if contact_id is not None:
        contact = await get_owned_contact(db, user_id, contact_id)
        if contact is None:
            raise SpineError(404, "contact not found")
        job = None
        if contact.get("application_id") is not None:
            app_row = await get_owned_application(db, user_id, contact["application_id"])
            if app_row is not None:
                job = {
                    "application_id": app_row["id"], "job_title": app_row.get("job_title") or "",
                    "job_company": app_row.get("job_company") or "",
                }
        person = await _full_contact_view(db, user_id, contact)
        person["jobs"] = [job] if job else []
        return {"person": person}

    truncated = False
    if email:
        # Bug fix (coordinator review, 2026-09-26) — a targeted lookup by
        # CURRENT email, unbounded by LIST_PEOPLE_MAX: filtering in SQL means
        # a match outside the newest page is never silently missed.
        rows = await _contacts_by_current_email(db, user_id, email)
    else:
        # Bug fix (coordinator review, 2026-09-26) — newest first (was oldest
        # first), and one extra row fetched to detect truncation honestly
        # instead of silently dropping the tail.
        cur = await db._db.execute(
            "SELECT ac.id, ac.application_id, ac.name, ac.role, ac.email, ac.linkedin_url, ac.notes, "
            "ac.added_by, ac.created_at, a.job_title, a.job_company "
            "FROM application_contacts ac LEFT JOIN applications a ON a.id = ac.application_id "
            "WHERE ac.user_id = ? ORDER BY ac.id DESC LIMIT ?",
            (user_id, settings.LIST_PEOPLE_MAX + 1),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        truncated = len(rows) > settings.LIST_PEOPLE_MAX
        rows = rows[: settings.LIST_PEOPLE_MAX]
        # Bug fix (coordinator review, 2026-09-26) — ONE query for every
        # contact's latest edits (never N+1), applied before grouping so a
        # person is found/grouped/displayed by their CURRENT details.
        edits_by_contact = await _latest_edits_for_user(db, user_id)
        for r in rows:
            _apply_current_fields(r, edits_by_contact.get(r["id"], {}))

    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for r in rows:
        key = _group_key(r)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    people = []
    for key in order:
        group_rows = groups[key]
        primary = group_rows[0]
        jobs = [
            {
                "application_id": r["application_id"], "job_title": r.get("job_title") or "",
                "job_company": r.get("job_company") or "",
            }
            for r in group_rows
            if r["application_id"] is not None
        ]
        contact_ids = [r["id"] for r in group_rows]
        outreach_rows: list[dict[str, Any]] = []
        for cid in contact_ids:
            outreach_rows.extend(await _outreach_rows(db, cid))
        outreach_rows.sort(key=lambda r: r["id"])
        summary = _outreach_view(outreach_rows)
        people.append(
            {
                "contact_ids": contact_ids,
                "name": primary["name"],
                "role": primary.get("role") or "",
                "email": primary.get("email") or "",
                "linkedin_url": primary.get("linkedin_url") or "",
                "notes": primary.get("notes") or "",
                "jobs": jobs,
                "message_count": summary["message_count"],
                "last_sent": summary["last_sent"],
                "replied": summary["replied"],
                "last_reply": summary["last_reply"],
            }
        )
    return {"people": people, "truncated": truncated}


async def list_unlinked_contacts_page(
    db: JobDatabase, user_id: str, *, after_id: int, limit: int
) -> list[dict[str, Any]]:
    """``export_history``'s top-level ``unlinked_contacts`` — ONE PAGE of base
    rows (bug fix, coordinator review 2026-09-26: the old ``list_unlinked_
    contacts`` loaded every cold contact, with its full outreach ledger,
    into memory before the caller ever got to apply a byte budget). ``after_
    id`` is INCLUSIVE — a page cut short by the byte budget re-offers its
    first un-included row next time by naming that row's own id."""
    cur = await db._db.execute(
        "SELECT id, application_id, name, role, email, linkedin_url, notes, added_by, created_at "
        "FROM application_contacts WHERE user_id = ? AND application_id IS NULL AND id >= ? "
        "ORDER BY id ASC LIMIT ?",
        (user_id, after_id, limit),
    )
    return [dict(r) for r in await cur.fetchall()]
