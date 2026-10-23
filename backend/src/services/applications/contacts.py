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
appends no event (there is no job timeline to write to). Without an email,
the same normalised CURRENT linkedin_url, else the same CURRENT phone (7+ digits), else the same CURRENT name (linked:
unless both sides carry different LinkedIn URLs; cold: only when neither side
has one), in the same scope (application, or the user's cold contacts), is
the same contact.

No update, no delete at runtime on ``application_contacts``,
``contact_outreach`` or ``contact_edits`` (S12 — grep-guarded by
``tests/test_slice4_contacts.py::test_contacts_are_append_only``, the same
pattern ``test_application_spine.py`` uses for events/artifacts). Editing a
contact's own fields (name/role/email/linkedin_url/notes/found_via/source_url/company/email_source/
relationship/shared/phone/links) appends a
``contact_edits`` row instead of touching the base row — the CURRENT value
of a field is its newest edit, or the base row's value if never edited; the
full history ("was X") is every edit plus the base value, oldest first.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parseaddr
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
from src.services.applications.templates import clean_variant_label
from src.services.auth import rate_limit
from src.services.profile import assistant_settings as settings_rules
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover — type-only, same reasoning as spine.py
    from src.repositories.database import JobDatabase

# S5 — the simple shape check the spec names; not a full RFC 5322 validator
# (this is a contact's stated email, not a login credential). Structural, not a
# regex: `^[^@\s]+@[^@\s]+\.[^@\s]+$` is what it means, but that pattern backtracks
# polynomially on `a@!.!.!.…` (CodeQL py/polynomial-redos), and this is O(n).
_WHITESPACE_RE = re.compile(r"\s")
_SHARED_RE = re.compile(r"^(cv_data\.(?:cv_positions|cv_projects|education))\[(\d{1,4})\](?::.*)?$", re.DOTALL)

# Same actor string authorship.actor_for gives a signed-in browser session
# (imported there as a type-only annotation, so re-declared as a plain
# constant here rather than pulling in src.api.auth_deps at runtime).
_WEB_ACTOR = "web"

# CodeQL py/log-injection (coordinator review, 2026-09-26; switched to the
# SHARED helper 2026-09-26) — every value below that can reach an audit log
# (directly, or via ``has_email``/``name_chars``/``fields``/``entry``/
# ``channel``, …) is run through ``safe_log_value`` first, which collapses
# embedded control characters (CR/LF included) to a single space and caps
# length. A name/role/email/linkedin_url legitimately spans one line, so this
# costs nothing real; it also means every value is provably free of injected
# newlines at the SOURCE, not just at the handler (``CRLFScrubFilter`` in
# ``utils/logger.py`` already scrubs every logger's OUTPUT for this — this
# closes the same gap explicitly, in-line, so a static scanner's dataflow
# sees the sanitizing step too).


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
    name = safe_log_value((raw or "").strip())
    if not name or len(name) > settings.CONTACT_NAME_MAX_CHARS:
        raise SpineError(
            422, f"name must be 1-{settings.CONTACT_NAME_MAX_CHARS} chars (CONTACT_NAME_MAX_CHARS) after trim"
        )
    return name


def _validate_role(raw: str) -> str:
    role = safe_log_value((raw or "").strip())
    if len(role) > settings.CONTACT_ROLE_MAX_CHARS:
        raise SpineError(422, f"role exceeds CONTACT_ROLE_MAX_CHARS ({settings.CONTACT_ROLE_MAX_CHARS} chars)")
    return role


def _validate_email(raw: str) -> str:
    email = safe_log_value((raw or "").strip())
    if not email:
        return ""
    if len(email) > settings.CONTACT_EMAIL_MAX_CHARS:
        raise SpineError(422, f"email exceeds CONTACT_EMAIL_MAX_CHARS ({settings.CONTACT_EMAIL_MAX_CHARS} chars)")
    if not _looks_like_email(email):
        raise SpineError(422, "email must look like a real address (e.g. name@example.com)")
    # R2 — stored lower-cased + trimmed so identity is case/whitespace-insensitive.
    return email.lower()


def _validate_linkedin_url(raw: str) -> str:
    url = safe_log_value((raw or "").strip())
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


def _validate_found_via(raw: Optional[str]) -> Optional[str]:
    """Owner decision 2026-10-04 — where this person was found. ``None``/'' →
    ``None`` (not said, rule #29); a case/space/hyphen-insensitive spelling of
    a ``CONTACT_FOUND_VIA`` member → that member; anything else → 422."""
    if raw is None or not raw.strip():
        return None
    slug = re.sub(r"[\s\-]+", "_", raw.strip().lower())
    if slug not in settings.CONTACT_FOUND_VIA:
        raise SpineError(422, f"found_via must be one of CONTACT_FOUND_VIA {settings.CONTACT_FOUND_VIA}")
    return slug


def _validate_email_source(raw: Optional[str]) -> Optional[str]:
    """Outreach pack #7 — where the EMAIL came from. None/'' -> None (rule #29);
    a case/space/hyphen-insensitive CONTACT_EMAIL_SOURCES member -> that member;
    anything else -> 422."""
    if raw is None or not raw.strip():
        return None
    slug = re.sub(r"[\s\-]+", "_", raw.strip().lower())
    if slug not in settings.CONTACT_EMAIL_SOURCES:
        raise SpineError(422, f"email_source must be one of CONTACT_EMAIL_SOURCES {settings.CONTACT_EMAIL_SOURCES}")
    return slug


async def _current_email(db: JobDatabase, user_id: str, contact: dict[str, Any]) -> str:
    """The contact's CURRENT email (newest `email` edit, else the base row), lower/trimmed."""
    cur = await db._db.execute(
        "SELECT value FROM contact_edits WHERE user_id = ? AND contact_id = ? AND field = 'email' "
        "ORDER BY id DESC LIMIT 1",
        (user_id, int(contact["id"])),
    )
    row = await cur.fetchone()
    return str((row[0] if row else contact.get("email")) or "").strip().lower()


def _validate_source_url(raw: Optional[str]) -> Optional[str]:
    """The page the assistant found this person on. ``None``/blank -> ``None``
    (rule #29). Stored only, never fetched (M2)."""
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if len(value) > settings.CONTACT_SOURCE_URL_MAX_CHARS:
        raise SpineError(
            422, f"source_url exceeds CONTACT_SOURCE_URL_MAX_CHARS ({settings.CONTACT_SOURCE_URL_MAX_CHARS} chars)"
        )
    if not value.startswith(("https://", "http://")):
        raise SpineError(422, "source_url must start with http:// or https://")
    if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in value):
        raise SpineError(422, "source_url must not contain spaces or control characters")
    return value


def _validate_company(raw: Optional[str]) -> Optional[str]:
    """The employer this person works at, as given (inner whitespace collapsed).
    None/blank -> None (rule #29). Matching uses company_key, never this text."""
    if raw is None or not raw.strip():
        return None
    value = safe_log_value(" ".join(raw.split()))
    if len(value) > settings.CONTACT_COMPANY_MAX_CHARS:
        raise SpineError(
            422, f"company exceeds CONTACT_COMPANY_MAX_CHARS ({settings.CONTACT_COMPANY_MAX_CHARS} chars)"
        )
    return value


def _validate_relationship(raw: Optional[str]) -> Optional[str]:
    """Outreach pack #12 — how the user knows this person. None/'' -> None
    (rule #29); a case/space/hyphen-insensitive CONTACT_RELATIONSHIP member ->
    that member; anything else -> 422."""
    if raw is None or not raw.strip():
        return None
    slug = re.sub(r"[\s\-]+", "_", raw.strip().lower())
    if slug not in settings.CONTACT_RELATIONSHIP:
        raise SpineError(422, f"relationship must be one of CONTACT_RELATIONSHIP {settings.CONTACT_RELATIONSHIP}")
    return slug


def _shared_label(profile: Any, source: str, index: int) -> str:
    """The record's own words, read off the (overlaid) profile; '' = no such record."""
    cv = getattr(profile, "cv_data", None)
    items = list(getattr(cv, source.split(".", 1)[1], None) or []) if cv is not None else []
    if index >= len(items):
        return ""
    rec = items[index]
    if source == "cv_data.education":
        label = str(rec or "")
    elif not isinstance(rec, dict):
        return ""
    elif source == "cv_data.cv_positions":
        label = " — ".join(p for p in (str(rec.get("company") or "").strip(), str(rec.get("title") or "").strip()) if p)
    else:  # cv_data.cv_projects
        label = str(rec.get("name") or "")
    return " ".join(safe_log_value(label, max_len=10_000).split())


def _shared_refused(reason: str, source: str = "") -> None:
    get_audit_logger().info(
        "contact_shared_refused",
        extra={"event": "contact_shared_refused", "reason": safe_log_value(reason), "source": safe_log_value(source)},
    )


async def _validate_shared(user_id: str, raw: Optional[str]) -> Optional[str]:
    """Outreach pack #12 — `shared` must name a REAL profile record; Job360 never
    invents a shared history (mistakes.md). Returns "<pointer>: <label>" with the
    label read from the user's current profile (any caller text after the
    pointer is ignored). None/'' -> None."""
    if raw is None or not raw.strip():
        return None
    m = _SHARED_RE.match(raw.strip())
    if m is None or m.group(1) not in settings.CONTACT_SHARED_SOURCES:
        _shared_refused("bad_pointer")
        raise SpineError(
            422,
            "shared must name a profile record: cv_data.cv_positions[i], cv_data.cv_projects[i] "
            "or cv_data.education[i] (see get_profile fields)",
        )
    source, index = m.group(1), int(m.group(2))
    pointer = f"{source}[{index}]"
    import asyncio  # noqa: PLC0415
    try:
        from src.services.profile.storage import load_profile  # noqa: PLC0415 - rule #16
        profile = await asyncio.to_thread(load_profile, user_id)
    except Exception:  # noqa: BLE001 - never store an unchecked pointer
        _shared_refused("profile_unavailable", source)
        raise SpineError(503, "profile unavailable; shared was not checked, try again") from None
    label = _shared_label(profile, source, index) if profile is not None else ""
    if not label:
        _shared_refused("not_in_profile", source)
        raise SpineError(422, f"shared names no record in your profile: {pointer}")
    room = max(settings.CONTACT_SHARED_MAX_CHARS - len(pointer) - 2, 1)
    return f"{pointer}: {label[:room]}"


_PHONE_RE = re.compile(r"^\+?[0-9 ().\-]+$")  # one char class: linear, no ReDoS


def _validate_phone(raw: Optional[str]) -> Optional[str]:
    """None/blank -> None (rule #29). Only digits, spaces, + ( ) . - ; stored as
    an optional leading '+' then digits only. Stored only, never dialled."""
    if raw is None or not raw.strip():
        return None
    value = safe_log_value(raw.strip())
    if len(value) > settings.CONTACT_PHONE_MAX_CHARS:
        raise SpineError(422, f"phone exceeds CONTACT_PHONE_MAX_CHARS ({settings.CONTACT_PHONE_MAX_CHARS} chars)")
    digits = "".join(ch for ch in value if "0" <= ch <= "9")
    if not _PHONE_RE.match(value) or not digits:
        raise SpineError(422, "phone may hold only digits, spaces and + ( ) . -")
    return ("+" if value.startswith("+") else "") + digits


def phone_identity_key(phone: Optional[str]) -> str:
    """Digits only; '' when fewer than CONTACT_PHONE_DEDUPE_MIN_DIGITS (a short
    number carries no identity). The ONE normalisation used by dedupe, the lock
    key and list_people grouping."""
    digits = "".join(ch for ch in (phone or "") if "0" <= ch <= "9")
    return digits if len(digits) >= settings.CONTACT_PHONE_DEDUPE_MIN_DIGITS else ""


def _validate_links(raw: Optional[list[str]]) -> Optional[list[str]]:
    """None -> None (not given). Each item is stripped; blanks are dropped; exact
    duplicates are dropped (first wins, order kept)."""
    if raw is None:
        return None
    out: list[str] = []
    for item in raw:
        value = safe_log_value((item or "").strip())
        if not value:
            continue
        if len(value) > settings.CONTACT_LINK_MAX_CHARS:
            raise SpineError(422, f"a link exceeds CONTACT_LINK_MAX_CHARS ({settings.CONTACT_LINK_MAX_CHARS} chars)")
        if not value.startswith(("https://", "http://")):
            raise SpineError(422, "every link must start with http:// or https://")
        if value not in out:
            out.append(value)
    if len(out) > settings.CONTACT_LINKS_MAX:
        raise SpineError(422, f"links holds at most {settings.CONTACT_LINKS_MAX} URLs (CONTACT_LINKS_MAX)")
    return out


def _links_text(links: Optional[list[str]]) -> Optional[str]:
    """The base-row value: JSON text of the list, NULL when none."""
    return json.dumps(links) if links else None


def _decode_links(raw: Any) -> list[str]:
    """list -> itself; non-empty str -> json.loads, kept only if a list of str; anything else -> []."""
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, str)]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        if isinstance(data, list) and all(isinstance(x, str) for x in data):
            return list(data)
    return []


def match_identity(
    candidates: Any, li_key: str, ph_key: str, nm_key: str, *, cold: bool
) -> tuple[Any, Optional[str]]:
    """candidates: iterable of (item, cur_li_key, cur_ph_key, cur_nm_key) in id order.
    LinkedIn first, then phone (7+ digits), then name (cold: neither side carries a
    LinkedIn URL or a phone; linked: unless both sides carry different ones)."""
    items = list(candidates)
    if li_key:
        for item, cur_li, _ph, _nm in items:
            if cur_li == li_key:
                return item, "linkedin_url"
    if ph_key:
        for item, _li, cur_ph, _nm in items:
            if cur_ph == ph_key:
                return item, "phone"
    for item, cur_li, cur_ph, cur_nm in items:
        if cur_nm != nm_key:
            continue
        if cold:
            ok = not li_key and not cur_li and not ph_key and not cur_ph
        else:
            ok = (not li_key or not cur_li or cur_li == li_key) and (
                not ph_key or not cur_ph or cur_ph == ph_key
            )
        if ok:
            return item, "name"
    return None, None


def linkedin_identity_key(url: str) -> str:
    """The ONE normalisation used by add_contact's dedupe, the lock key and
    list_people's grouping: trim, lower-case, http:// -> https://, drop the
    query and fragment, strip trailing '/', and collapse www./country
    subdomains of linkedin.com to linkedin.com. '' stays ''."""
    u = (url or "").strip().lower()
    if not u:
        return ""
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    u = u.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    scheme, sep, rest = u.partition("://")
    host, slash, path = rest.partition("/")
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"
    return f"{scheme}{sep}{host}{slash}{path}"


def name_identity_key(name: str) -> str:
    """lower(name) with inner whitespace collapsed."""
    return " ".join((name or "").split()).lower()


def company_key(company: str) -> str:
    """THE company key (rule #1: kit.normalized_company_key = Job.normalized_key()[0]).
    '' and 'unknown' read as '' - they never match (same rule as company_applied_count)."""
    from src.services.applications.kit import normalized_company_key  # noqa: PLC0415 - kit imports spine

    key = normalized_company_key(company or "")
    return "" if key in ("", "unknown") else key


def contact_company_key(row: dict[str, Any]) -> str:
    """A contact's effective company key: its CURRENT own `company`, else its linked
    application's `job_company` (the row must carry both, edits already applied)."""
    return company_key(str(row.get("company") or "")) or company_key(str(row.get("job_company") or ""))


COLD_COMPANY_REQUIRED = (
    "company is required for a contact with no application_id (cold networking): pass the employer they work at"
)
COLD_COMPANY_KEEP = "a contact with no application_id must keep a company: pass the employer they work at"


def is_reachable(row: dict[str, Any]) -> bool:
    """A way to reach this person is saved: CURRENT email or linkedin_url non-empty."""
    return bool((row.get("email") or "").strip() or (row.get("linkedin_url") or "").strip())


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "application_id": row.get("application_id"),
        "name": row["name"],
        "role": row.get("role") or "",
        "email": row.get("email") or "",
        "linkedin_url": row.get("linkedin_url") or "",
        "notes": row.get("notes") or "",
        # None when unset (rule #29) — never a default source.
        "found_via": row.get("found_via") or None,
        "source_url": row.get("source_url") or None,
        "company": row.get("company") or None,
        "email_source": row.get("email_source") or None,
        "relationship": row.get("relationship") or None,
        "shared": row.get("shared") or None,
        "phone": row.get("phone") or None,
        "links": _decode_links(row.get("links")),
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
        f"SELECT ac.id, ac.application_id, ac.name, ac.role, ac.email, ac.linkedin_url, ac.notes, ac.found_via, "  # noqa: S608
        f"ac.source_url, ac.company, ac.email_source, ac.relationship, ac.shared, ac.phone, ac.links, "
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


def _identity_lock_keys(
    user_id: str, application_id: Optional[int], li_key: str, ph_key: str, nm_key: str
) -> list[str]:
    scope = str(application_id) if application_id is not None else "cold"  # same scoping as _email_lock_key
    keys = [f"{user_id}:{scope}:name:{nm_key}"]
    if li_key:
        keys.append(f"{user_id}:{scope}:li:{li_key}")
    if ph_key:
        keys.append(f"{user_id}:{scope}:ph:{ph_key}")
    return sorted(keys)  # one global order, so two callers can never deadlock


async def _lock_identity_scope(
    db: JobDatabase, user_id: str, application_id: Optional[int], li_key: str, ph_key: str, nm_key: str
) -> None:
    """Advisory xact locks for a no-email add: MUST be the first statement in
    its transaction. Two calls that could match each other always share at
    least one key, so check-then-write is serialised."""
    for key in _identity_lock_keys(user_id, application_id, li_key, ph_key, nm_key):
        await db._db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (key,))


async def _find_contact_by_current_identity(
    db: JobDatabase, application_id: Optional[int], user_id: str, li_key: str, ph_key: str, nm_key: str
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    keyed = await _keyed_identity_candidates(db, application_id, user_id)
    return match_identity(keyed, li_key, ph_key, nm_key, cold=application_id is None)


async def _keyed_identity_candidates(
    db: JobDatabase, application_id: Optional[int], user_id: str
) -> list[tuple[dict[str, Any], str, str, str]]:
    """No-email identity: the first (lowest id) contact in scope whose CURRENT
    linkedin_url key equals ``li_key``, else whose CURRENT phone key (7+ digits)
    equals ``ph_key``, else whose CURRENT name key equals ``nm_key`` (linked
    scope: unless both sides carry different LinkedIn URLs or phones; cold
    scope: only when neither side carries one) — see ``match_identity``. CURRENT = newest
    ``contact_edits`` row, else the base row. Email is deliberately not
    compared."""
    cold = application_id is None
    scope_sql = "ac.user_id = ? AND ac.application_id IS NULL" if cold else "ac.user_id = ? AND ac.application_id = ?"
    params: list[Any] = [user_id, user_id] if cold else [user_id, user_id, application_id]
    cur = await db._db.execute(
        f"SELECT ac.id, ac.application_id, ac.name, ac.role, ac.email, ac.linkedin_url, ac.notes, ac.found_via, "  # noqa: S608
        f"ac.source_url, ac.company, ac.email_source, ac.relationship, ac.shared, ac.phone, ac.links, "
        f"ac.added_by, ac.created_at, le.cur_name, le.cur_linkedin, le.cur_phone "
        f"FROM application_contacts ac "
        f"LEFT JOIN ("
        f"  SELECT contact_id, "
        f"         MAX(CASE WHEN field = 'name' THEN value END) AS cur_name, "
        f"         MAX(CASE WHEN field = 'linkedin_url' THEN value END) AS cur_linkedin, "
        f"         MAX(CASE WHEN field = 'phone' THEN value END) AS cur_phone "
        f"  FROM ("
        f"    SELECT contact_id, field, value, "
        f"           ROW_NUMBER() OVER (PARTITION BY contact_id, field ORDER BY id DESC) AS rn "
        f"    FROM contact_edits WHERE user_id = ? AND field IN ('name', 'linkedin_url', 'phone')"
        f"  ) t WHERE rn = 1 GROUP BY contact_id"
        f") le ON le.contact_id = ac.id "
        f"WHERE {scope_sql} ORDER BY ac.id ASC",
        params,
    )
    rows = [dict(r) for r in await cur.fetchall()]
    keyed = []
    for r in rows:
        cur_li = linkedin_identity_key(r["cur_linkedin"] if r["cur_linkedin"] is not None else r["linkedin_url"])
        cur_ph = phone_identity_key(r["cur_phone"] if r["cur_phone"] is not None else r["phone"])
        cur_nm = name_identity_key(r["cur_name"] if r["cur_name"] is not None else r["name"])
        base = {k: v for k, v in r.items() if k not in ("cur_name", "cur_linkedin", "cur_phone")}
        keyed.append((base, cur_li, cur_ph, cur_nm))
    return keyed


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
    found_via: Optional[str] = None,
    source_url: Optional[str] = None,
    company: Optional[str] = None,
    email_source: Optional[str] = None,
    relationship: Optional[str] = None,
    shared: Optional[str] = None,
    phone: Optional[str] = None,
    links: Optional[str] = None,
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
            "(user_id, application_id, name, role, email, linkedin_url, notes, found_via, source_url, company, "
            " email_source, relationship, shared, phone, links, added_by, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, application_id, name, role, "", linkedin_url, notes, found_via, source_url, company,
                email_source, relationship, shared, phone, links, actor, now,
            ),
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
            "has_email_source": email_source is not None,
        },
    )
    base_row = {
        "id": contact_id, "application_id": application_id, "name": name, "role": role,
        "email": "", "linkedin_url": linkedin_url, "notes": notes, "found_via": found_via,
        "source_url": source_url, "company": company, "email_source": email_source,
        "relationship": relationship, "shared": shared, "phone": phone, "links": links, "added_by": actor,
        "created_at": now,
    }
    view = await _full_contact_view(db, user_id, base_row)
    return {"contact": view, "already_existed": False, "event_id": event_id, "matched_on": None}


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
    found_via: Optional[str] = None,
    source_url: Optional[str] = None,
    company: Optional[str] = None,
    email_source: Optional[str] = None,
    relationship: Optional[str] = None,
    shared: Optional[str] = None,
    phone: Optional[str] = None,
    links: Optional[list[str]] = None,
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
    check (which bypasses the cap — the same person answering 200 is not a
    new row; on BOTH the email and the no-email path the lookup comes before
    the cap and the rate limit), then the cap itself (S4), then the per-user rate limit (S7),
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
    clean_found_via = _validate_found_via(found_via)
    clean_source_url = _validate_source_url(source_url)
    clean_company = _validate_company(company)
    clean_email_source = _validate_email_source(email_source)
    if clean_email_source and not clean_email:
        raise SpineError(422, "email_source needs an email: pass email too, or leave email_source out")
    clean_relationship = _validate_relationship(relationship)
    clean_phone = _validate_phone(phone)
    clean_links = _validate_links(links)
    occurred = parse_occurred_at(occurred_at)
    if application_id is None and not company_key(clean_company or ""):
        raise SpineError(422, COLD_COMPANY_REQUIRED)

    if application_id is not None:
        app_row = await get_owned_application(db, user_id, application_id)
        if app_row is None:
            raise SpineError(404, "application not found")
    clean_shared = await _validate_shared(user_id, shared)

    if not clean_email:
        # No email: the same normalised CURRENT linkedin_url, else the same
        # CURRENT phone (7+ digits), else the same CURRENT name (see _find_contact_by_current_identity), in the same
        # scope is the same contact. Lookup under advisory locks, before the
        # cap and the rate limit.
        li_key = linkedin_identity_key(clean_linkedin)
        ph_key = phone_identity_key(clean_phone)
        nm_key = name_identity_key(clean_name)
        now = datetime.now(timezone.utc).isoformat()
        event_id: Optional[int] = None
        async with db._db.transaction():
            await _lock_identity_scope(db, user_id, application_id, li_key, ph_key, nm_key)
            existing, matched_on = await _find_contact_by_current_identity(
                db, application_id, user_id, li_key, ph_key, nm_key
            )
            if existing is not None:
                get_audit_logger().info(
                    "contact_already_existed",
                    extra={
                        # CodeQL py/log-injection: ids and a closed code, sanitized like every logged value here.
                        "event": "contact_already_existed", "application_id": safe_log_value(application_id),
                        "contact_id": safe_log_value(existing["id"]), "matched_on": safe_log_value(matched_on),
                    },
                )
                view = await _full_contact_view(db, user_id, existing)
                return {"contact": view, "already_existed": True, "event_id": None, "matched_on": matched_on}

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

            cur = await db._db.execute(
                "INSERT INTO application_contacts "
                "(user_id, application_id, name, role, email, linkedin_url, notes, found_via, source_url, "
                " company, email_source, relationship, shared, phone, links, added_by, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, application_id, clean_name, clean_role, "", clean_linkedin, clean_notes,
                    clean_found_via, clean_source_url, clean_company, None, clean_relationship, clean_shared,
                    clean_phone, _links_text(clean_links), actor, now,
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
        get_audit_logger().info(
            "contact_added",
            extra={
                "event": "contact_added", "application_id": application_id, "contact_id": contact_id,
                "has_email": False, "has_linkedin": bool(clean_linkedin),
                "has_source_url": clean_source_url is not None, "has_company": clean_company is not None,
                "name_chars": len(clean_name),
                "has_email_source": False,
                "has_relationship": clean_relationship is not None, "has_shared": clean_shared is not None,
                "has_phone": clean_phone is not None, "links": len(clean_links or []),
            },
        )
        base_row = {
            "id": contact_id, "application_id": application_id, "name": clean_name, "role": clean_role,
            "email": "", "linkedin_url": clean_linkedin, "notes": clean_notes, "found_via": clean_found_via,
            "source_url": clean_source_url, "company": clean_company, "email_source": None,
            "relationship": clean_relationship, "shared": clean_shared, "phone": clean_phone,
            "links": _links_text(clean_links), "added_by": actor,
            "created_at": now,
        }
        view = await _full_contact_view(db, user_id, base_row)
        return {"contact": view, "already_existed": False, "event_id": event_id, "matched_on": None}

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
            return {"contact": view, "already_existed": True, "event_id": None, "matched_on": "email"}

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
                    "(user_id, application_id, name, role, email, linkedin_url, notes, found_via, source_url, "
                    " company, email_source, relationship, shared, phone, links, added_by, created_at) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id, application_id, clean_name, clean_role, clean_email,
                        clean_linkedin, clean_notes, clean_found_via, clean_source_url, clean_company,
                        clean_email_source, clean_relationship, clean_shared,
                        clean_phone, _links_text(clean_links), actor, now,
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
                return {"contact": view, "already_existed": True, "event_id": None, "matched_on": "email"}
            return await _create_contact_with_deferred_email(
                db, user_id, application_id, actor, name=clean_name, role=clean_role, email=clean_email,
                linkedin_url=clean_linkedin, notes=clean_notes, occurred_at=occurred, found_via=clean_found_via,
                source_url=clean_source_url, company=clean_company, email_source=clean_email_source,
                relationship=clean_relationship, shared=clean_shared,
                phone=clean_phone, links=_links_text(clean_links),
            )

    get_audit_logger().info(
        "contact_added",
        extra={
            "event": "contact_added", "application_id": application_id, "contact_id": contact_id,
            "has_email": True, "has_company": clean_company is not None, "name_chars": len(clean_name),
            "has_email_source": clean_email_source is not None,
            "has_relationship": clean_relationship is not None, "has_shared": clean_shared is not None,
        },
    )
    base_row = {
        "id": contact_id, "application_id": application_id, "name": clean_name, "role": clean_role,
        "email": clean_email, "linkedin_url": clean_linkedin, "notes": clean_notes, "found_via": clean_found_via,
        "source_url": clean_source_url, "company": clean_company, "email_source": clean_email_source,
        "relationship": clean_relationship, "shared": clean_shared,
        "phone": clean_phone, "links": _links_text(clean_links),
        "added_by": actor, "created_at": now,
    }
    view = await _full_contact_view(db, user_id, base_row)
    return {"contact": view, "already_existed": False, "event_id": event_id, "matched_on": None}


# Outreach pack #15 — at most this many people a job ad names (bring_job `listed_people`) per call.
LISTED_PEOPLE_MAX = 10


def validate_listed_people(people: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Outreach pack #15 — the people a job ad names, checked BEFORE bring_job
    writes anything (a bad item must not leave a half-brought job). Same rules
    as add_contact: name 1-CONTACT_NAME_MAX_CHARS, role, http(s) linkedin_url;
    at most LISTED_PEOPLE_MAX. A 422 names the item."""
    if len(people) > LISTED_PEOPLE_MAX:
        raise SpineError(422, f"listed_people takes at most {LISTED_PEOPLE_MAX} people (LISTED_PEOPLE_MAX)")
    clean: list[dict[str, str]] = []
    for i, p in enumerate(people):
        try:
            clean.append({
                "name": _validate_name(p.get("name") or ""),
                "role": _validate_role(p.get("role") or ""),
                "linkedin_url": _validate_linkedin_url(p.get("linkedin_url") or ""),
            })
        except SpineError as exc:
            raise SpineError(422, f"listed_people[{i}]: {exc.detail}") from None
    return clean


async def add_listed_people(
    db: JobDatabase, user_id: str, application_id: int, actor: str,
    people: list[dict[str, str]], *, source_url: Optional[str],
) -> list[dict[str, Any]]:
    """Outreach pack #15 — store each person the ad names as a contact on this
    application with found_via='job_ad', through add_contact (so the slice-2
    identity dedupe applies: a re-bring, or someone already added, answers
    already_existed and changes nothing). ``people`` is validate_listed_people's
    output. A cap (409) or rate-limit (429) refusal is reported per person,
    never fails the bring (the job is already stored). ``source_url`` = the
    ad link; dropped when it is not a valid contact source_url."""
    try:
        src = _validate_source_url(source_url)
    except SpineError:
        src = None
    out: list[dict[str, Any]] = []
    for p in people:
        try:
            r = await add_contact(
                db, user_id, application_id, actor, name=p["name"], role=p["role"],
                linkedin_url=p["linkedin_url"], found_via="job_ad", source_url=src,
            )
        except SpineError as exc:
            if exc.status_code not in (409, 429):
                raise
            out.append({
                "name": p["name"], "contact_id": None, "already_existed": False, "matched_on": None,
                "error": "contact_cap" if exc.status_code == 409 else "rate_limited",
            })
            continue
        out.append({
            "name": p["name"], "contact_id": int(r["contact"]["id"]), "already_existed": bool(r["already_existed"]),
            "matched_on": r["matched_on"], "error": None,
        })
    return out


async def get_owned_contact(db: JobDatabase, user_id: str, contact_id: int) -> Optional[dict[str, Any]]:
    """S2 — the only lookup every contact-scoped function below builds on. A
    foreign or unknown id reads as None (the route turns that into 404 —
    ``"contact not found"``, never 403)."""
    cur = await db._db.execute(
        "SELECT id, application_id, name, role, email, linkedin_url, notes, found_via, source_url, "
        "company, email_source, relationship, shared, phone, links, "
        "added_by, created_at "
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
        "SELECT id, application_id, name, role, email, linkedin_url, notes, found_via, source_url, "
        "company, email_source, relationship, shared, phone, links, "
        "added_by, created_at "
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
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM contact_edits WHERE contact_id = ? AND field <> 'confirmed'", (contact_id,)
    )
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
    found_via: Optional[str] = None,
    source_url: Optional[str] = None,
    company: Optional[str] = None,
    email_source: Optional[str] = None,
    relationship: Optional[str] = None,
    shared: Optional[str] = None,
    phone: Optional[str] = None,
    links: Optional[list[str]] = None,
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
    if found_via is not None:
        # '' clears it (stored as the edit value '', read back as None).
        given["found_via"] = _validate_found_via(found_via) or ""
    if source_url is not None:
        given["source_url"] = _validate_source_url(source_url) or ""  # '' clears it
    if company is not None:
        given["company"] = _validate_company(company) or ""  # '' clears it (read back as None)
        if contact.get("application_id") is None and not company_key(given["company"]):
            raise SpineError(422, COLD_COMPANY_KEEP)
    if email_source is not None:
        given["email_source"] = _validate_email_source(email_source) or ""  # '' clears it
    if "email" in given or given.get("email_source"):
        current_email = await _current_email(db, user_id, contact)
        new_or_current = given.get("email", current_email)
        if given.get("email_source") and not new_or_current:
            raise SpineError(422, "email_source needs an email: pass email too, or leave email_source out")
        # The source belongs to the ADDRESS: a new address without its own source clears the old one.
        if "email" in given and email_source is None and given["email"] != current_email:
            given["email_source"] = ""
    if relationship is not None:
        given["relationship"] = _validate_relationship(relationship) or ""  # '' clears it
    if shared is not None:
        given["shared"] = (await _validate_shared(user_id, shared)) or ""  # '' clears it
    if phone is not None:
        given["phone"] = _validate_phone(phone) or ""  # '' clears it
    if links is not None:
        v = _validate_links(links)
        given["links"] = json.dumps(v) if v else ""  # [] clears it
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
            "fields": [safe_log_value(f) for f in sorted(given)],
        },
    )
    return await _full_contact_view(db, user_id, contact)


async def confirm_contact(db: JobDatabase, user_id: str, contact_id: int, actor: str) -> dict[str, Any]:
    """The user's own "Confirm" on a contact their assistant added (web only).
    Append-only and idempotent: one contact_edits row (field='confirmed',
    value='true'); a contact already confirmed, or one the user added on the
    web, writes nothing. Not counted toward CONTACT_EDITS_PER_CONTACT_MAX (``_count_edits``
    skips it; at most one row ever). One-way: there is no un-confirm."""
    contact = await get_owned_contact(db, user_id, contact_id)
    if contact is None:
        raise SpineError(404, "contact not found")
    if actor != _WEB_ACTOR:
        raise SpineError(403, "session_required")
    already = contact["added_by"] == _WEB_ACTOR
    wrote = False
    if not already:
        # Serialised per contact: two simultaneous Confirms can never both
        # pass the check and write two rows.
        async with db._db.transaction():
            await db._db.execute(
                "SELECT pg_advisory_xact_lock(hashtext(?))", (f"{user_id}:confirm:{contact_id}",)
            )
            cur = await db._db.execute(
                "SELECT 1 FROM contact_edits WHERE user_id = ? AND contact_id = ? AND field = 'confirmed' "
                "AND value = 'true' LIMIT 1",
                (user_id, contact_id),
            )
            if await cur.fetchone() is None:
                now = datetime.now(timezone.utc).isoformat()
                await db._db.execute(
                    "INSERT INTO contact_edits (user_id, contact_id, field, value, recorded_at, recorded_by) "
                    "VALUES (?, ?, 'confirmed', 'true', ?, ?)",
                    (user_id, contact_id, now, actor),
                )
                wrote = True
    if wrote:
        get_audit_logger().info(
            "contact_confirmed", extra={"event": "contact_confirmed", "contact_id": safe_log_value(contact_id)}
        )
    return await _full_contact_view(db, user_id, contact)


_BATCH_FIELDS = (
    "name", "role", "email", "linkedin_url", "notes", "occurred_at", "found_via", "source_url", "phone", "links",
    "company", "email_source", "relationship", "shared",
)


async def add_contacts_batch(
    db: JobDatabase,
    user_id: str,
    application_id: Optional[int],
    actor: str,
    *,
    people: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    """Many ``add_contact`` calls in one: ``dry_run`` previews (nothing written,
    no rate limit spent), a real run stores each person in its own transaction
    (each ``add_contact`` is idempotent, so a retry never doubles up). Items are
    already-validated ``AddContactRequest`` field dicts carrying ``index``.
    Counts only in the log, never a name, phone or link."""
    if application_id is not None:
        if await get_owned_application(db, user_id, application_id) is None:
            raise SpineError(404, "application not found")
    cold = application_id is None

    would_add: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    clean_items: list[dict[str, Any]] = []
    for raw in people:
        item = dict(raw)
        index = int(item.pop("index"))
        kwargs = {k: item[k] for k in _BATCH_FIELDS if k in item}
        try:
            parse_occurred_at(kwargs.get("occurred_at"))
            clean = {
                "index": index,
                "name": _validate_name(kwargs.get("name") or ""),
                "email": _validate_email(kwargs.get("email") or ""),
                "linkedin": _validate_linkedin_url(kwargs.get("linkedin_url") or ""),
                "phone": _validate_phone(kwargs.get("phone")),
                "kwargs": kwargs,
            }
            _validate_role(kwargs.get("role") or "")
            _validate_notes(kwargs.get("notes") or "")
            _validate_found_via(kwargs.get("found_via"))
            _validate_source_url(kwargs.get("source_url"))
            _validate_links(kwargs.get("links"))
            # What add_contact would check, so a dry run never promises a row the real run refuses.
            clean_company = _validate_company(kwargs.get("company"))
            if _validate_email_source(kwargs.get("email_source")) and not clean["email"]:
                raise SpineError(422, "email_source needs an email: pass email too, or leave email_source out")
            _validate_relationship(kwargs.get("relationship"))
            if cold and not company_key(clean_company or ""):
                raise SpineError(422, COLD_COMPANY_REQUIRED)
            await _validate_shared(user_id, kwargs.get("shared"))
        except SpineError as exc:
            invalid.append({"index": index, "status": exc.status_code, "detail": exc.detail})
            continue
        clean_items.append(clean)

    if dry_run:
        # (index, li_key, ph_key, nm_key, email) of every earlier would-add item.
        earlier: list[tuple[int, str, str, str, str]] = []
        db_keyed = await _keyed_identity_candidates(db, application_id, user_id)
        for c in clean_items:
            li_key = linkedin_identity_key(c["linkedin"])
            ph_key = phone_identity_key(c["phone"])
            nm_key = name_identity_key(c["name"])
            if c["email"]:
                hit = await _find_contact_by_current_email(db, application_id, user_id, c["email"])
                if hit is not None:
                    duplicates.append({"index": c["index"], "contact_id": hit["id"], "matched_on": "email"})
                    continue
                twin = next((e for e in earlier if e[4] == c["email"]), None)
                if twin is not None:
                    duplicates.append(
                        {"index": c["index"], "duplicate_of_index": twin[0], "matched_on": "email"}
                    )
                    continue
            else:
                # ONE id-ordered list (DB rows, then earlier would-add items),
                # exactly what add_contact sees in a real run.
                combined = [(("db", r["id"]), li, ph, nm) for r, li, ph, nm in db_keyed] + [
                    (("batch", e[0]), e[1], e[2], e[3]) for e in earlier
                ]
                tag, matched = match_identity(combined, li_key, ph_key, nm_key, cold=cold)
                if tag is not None:
                    if tag[0] == "db":
                        duplicates.append({"index": c["index"], "contact_id": tag[1], "matched_on": matched})
                    else:
                        duplicates.append(
                            {"index": c["index"], "duplicate_of_index": tag[1], "matched_on": matched}
                        )
                    continue
            earlier.append((c["index"], li_key, ph_key, nm_key, c["email"]))
            would_add.append({"index": c["index"], "name": c["name"]})
        if cold:
            cap, current = settings.CONTACTS_UNLINKED_MAX, await _count_unlinked_contacts(db, user_id)
            cap_detail = f"cold-contact cap reached; CONTACTS_UNLINKED_MAX is {cap}"
        else:
            cap, current = settings.CONTACTS_PER_APPLICATION_MAX, await _count_contacts(db, int(application_id))  # type: ignore[arg-type]
            cap_detail = f"contact cap reached; CONTACTS_PER_APPLICATION_MAX is {cap}"
        remaining = max(cap - current, 0)
        for over in would_add[remaining:]:
            invalid.append({"index": over["index"], "status": 409, "detail": cap_detail})
        would_add = would_add[:remaining]
    else:
        created_by_contact: dict[int, int] = {}
        for c in clean_items:
            try:
                res = await add_contact(db, user_id, application_id, actor, **c["kwargs"])
            except SpineError as exc:
                invalid.append({"index": c["index"], "status": exc.status_code, "detail": exc.detail})
                continue
            contact_id = int(res["contact"]["id"])
            if res["already_existed"]:
                duplicates.append(
                    {
                        "index": c["index"], "contact_id": contact_id, "matched_on": res["matched_on"],
                        "duplicate_of_index": created_by_contact.get(contact_id),
                    }
                )
            else:
                created_by_contact[contact_id] = c["index"]
                added.append({"index": c["index"], "contact_id": contact_id})

    def by_index(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: r["index"])

    get_audit_logger().info(
        "contacts_batch",
        extra={
            "event": "contacts_batch", "application_id": application_id, "dry_run": dry_run,
            "total": len(people), "would_add": len(would_add), "added": len(added),
            "duplicates": len(duplicates), "invalid": len(invalid),
        },
    )
    return {
        "dry_run": dry_run, "would_add": by_index(would_add), "added": by_index(added),
        "duplicates": by_index(duplicates), "invalid": by_index(invalid),
    }


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
        "follow_up_on": row.get("follow_up_on"),
        "label": row.get("label") or "",
    }


# Outreach pack #5/#6 (2026-10-20): the ledger entries beyond message/sent/reply.
_OUTREACH_ENTRIES = settings.OUTREACH_ENTRIES  # message/sent/reply/bounced
_NEW_ENTRIES = settings.CONTACT_INTERACTION_ENTRIES + settings.CONTACT_REFERRAL_ENTRIES


async def _outreach_rows(db: JobDatabase, contact_id: int) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, contact_id, entry, channel, text, version_no, occurred_at, recorded_at, recorded_by, "
        "source_message_id, follow_up_on, label FROM contact_outreach WHERE contact_id = ? ORDER BY id ASC",
        (contact_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


def _outreach_view(rows: list[dict[str, Any]], *, include_text: bool = True) -> dict[str, Any]:
    messages = [_serialize_outreach(r, include_text=include_text) for r in rows if r["entry"] == "message"]
    sent = [_serialize_outreach(r, include_text=include_text) for r in rows if r["entry"] == "sent"]
    replies = [_serialize_outreach(r, include_text=include_text) for r in rows if r["entry"] == "reply"]
    bounced = [_serialize_outreach(r, include_text=include_text) for r in rows if r["entry"] == "bounced"]
    return {
        "messages": messages,
        "sent": sent,
        "replies": replies,
        "bounced": bounced,
        "message_count": len(messages),
        "last_sent": sent[-1] if sent else None,
        "replied": bool(replies),
        "last_reply": replies[-1] if replies else None,
        "interactions": [_serialize_outreach(r, include_text=include_text) for r in rows if r["entry"] in _NEW_ENTRIES],
    }


async def mark_reply_from_sender(
    db: JobDatabase,
    user_id: str,
    application_id: int,
    actor: str,
    *,
    sender: str,
    message_id: str,
    occurred_at: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """A ``replied`` event on an application whose email ``source.sender`` is a
    contact of THAT application we already sent outreach to: ALSO record the
    reply on the person (the same door as ``outreach_replied``), so the person
    card stops saying "No reply yet". Best effort and append-only: no match, no
    ``sent`` mark, or any refusal means nothing extra is written and the caller's
    ``replied`` event stands. The same ``message_id`` makes a re-read idempotent."""
    address = parseaddr(sender or "")[1].strip().lower()
    if not address or not message_id:
        return None
    contact = await _find_contact_by_current_email(db, application_id, user_id, address)
    if contact is None:
        return None
    cur = await db._db.execute(
        "SELECT 1 FROM contact_outreach WHERE contact_id = ? AND entry = 'sent' LIMIT 1", (contact["id"],)
    )
    if await cur.fetchone() is None:
        return None
    try:
        return await record_outreach(
            db, user_id, int(contact["id"]), actor, entry="reply", channel="email", text="",
            occurred_at=occurred_at, source_message_id=message_id, application_id_hint=application_id,
        )
    except SpineError:
        return None


async def _find_outreach_by_source(
    db: JobDatabase, contact_id: int, source_message_id: str
) -> Optional[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, contact_id, entry, channel, text, version_no, occurred_at, recorded_at, recorded_by, "
        "source_message_id, follow_up_on, label FROM contact_outreach WHERE contact_id = ? AND source_message_id = ?",
        (contact_id, source_message_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


def _refuse_other_entry_for_source(existing: dict[str, Any], entry: str) -> None:
    """A message id already stored under a different entry kind is a conflict, never a silent no-op."""
    if existing["entry"] != entry:
        raise SpineError(
            409, f"this message id is already recorded as '{existing['entry']}' for this contact"
        )


async def _outreach_entry_count(db: JobDatabase, contact_id: int, entries: tuple[str, ...]) -> int:
    marks = ", ".join("?" for _ in entries)
    cur = await db._db.execute(
        f"SELECT COUNT(*) FROM contact_outreach WHERE contact_id = ? AND entry IN ({marks})",  # noqa: S608 - placeholders only
        (contact_id, *entries),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _message_version_count(db: JobDatabase, contact_id: int) -> int:
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM contact_outreach WHERE contact_id = ? AND entry = 'message'", (contact_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _linkedin_premium(user_id: str, contact_id: int) -> bool:
    """``preferences.linkedin_premium`` off the MERGED profile (agent-edit overlay
    applied: load_profile's default with_overlay=True). Not set, no profile, or a
    failed read = False: the free-account limit, the safer budget (career-ops
    modes/contacto.md:168). Never raises: a warn-only check must never break a save
    (same rule as spine._run_save_checks)."""
    import asyncio  # noqa: PLC0415

    try:
        from src.services.profile.storage import load_profile  # noqa: PLC0415 - rule #16

        profile = await asyncio.to_thread(load_profile, user_id)
    except Exception:  # noqa: BLE001 - advisory; a profile read must not break a save
        # CodeQL py/log-injection: contact_id is a route int, sanitized like every other id here.
        logging.getLogger("job360.contacts").exception(
            "linkedin_premium_unavailable", extra={"contact_id": safe_log_value(contact_id)},
        )
        return False
    prefs = getattr(profile, "preferences", None)
    return bool(getattr(prefs, "linkedin_premium", False))


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
    label: str = "",
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

    ``entry`` also takes ``"meeting"``/``"call"``/``"intro"`` and
    ``"referral_asked"``/``"referral_given"`` (outreach pack #5/#6): stored only,
    one channel set (``CONTACT_LEDGER_CHANNELS``), their own budgets, a
    NOTE-family timeline event when the contact is linked. ``referral_given``
    needs a LINKED contact.

    ``follow_up_on`` on a ``"sent"`` mark is only for a LINKED contact — a cold
    contact has no job to chase, so this raises 422 naming exactly why (owner
    decision). On a meeting/call/intro/referral entry it is kept on the ledger
    row (a cold contact may carry one too).

    ``"bounced"`` (outreach pack #7) is a ledger-only mark: NO timeline event, email
    channel, needs ``source_message_id`` (the bounce email's id) and takes no
    ``text`` — the server stores the contact's CURRENT email as ``text``.

    ``label`` — the caller's variant name on a message version (stats ``by_outreach_variant``).

    ``source_message_id`` — idempotent re-read of the same email: a second
    call naming the same id returns the EXISTING row (``already_existed``),
    same pattern as ``spine.append_event``'s ``source``.
    """
    contact = await get_owned_contact(db, user_id, contact_id)
    if contact is None:
        raise SpineError(404, "contact not found")
    if application_id_hint is not None and contact.get("application_id") != application_id_hint:
        raise SpineError(422, "application_id does not match this contact's linked job")

    if entry not in _OUTREACH_ENTRIES + _NEW_ENTRIES:
        raise SpineError(422, "entry must be one of " + ", ".join(f"'{e}'" for e in _OUTREACH_ENTRIES + _NEW_ENTRIES))
    is_new = entry in _NEW_ENTRIES
    if is_new:
        if channel not in settings.CONTACT_LEDGER_CHANNELS:
            raise SpineError(
                422, f"channel must be one of CONTACT_LEDGER_CHANNELS {settings.CONTACT_LEDGER_CHANNELS} for a {entry}"
            )
    elif channel not in settings.OUTREACH_CHANNELS:
        raise SpineError(422, f"channel must be one of OUTREACH_CHANNELS {settings.OUTREACH_CHANNELS}")
    if follow_up_on is not None and entry not in ("sent",) + _NEW_ENTRIES:
        raise SpineError(422, "follow_up_on is only allowed on a sent mark or a meeting/call/intro/referral entry")
    if follow_up_on is not None and entry == "sent" and contact.get("application_id") is None:
        raise SpineError(422, "cold contacts have no follow-up date")
    if entry == "referral_given" and contact.get("application_id") is None:
        raise SpineError(422, "referral_given needs a contact linked to the job; add_contact with application_id first")

    if source_message_id and entry not in ("sent", "reply", "bounced"):
        raise SpineError(422, "source_message_id is only meaningful on 'sent'/'reply'/'bounced' entries")

    bounced_address = ""
    if entry == "bounced":
        if channel != "email":
            raise SpineError(422, "a bounce is an email entry: channel must be 'email'")
        if not source_message_id:
            raise SpineError(422, "a bounce needs the bounce email's message id as source")
        if text:
            raise SpineError(
                422, "a bounce takes no text: Job360 stores the contact's current email as the bounced address"
            )
    clean_label = clean_variant_label(label)
    if clean_label and entry != "message":
        raise SpineError(422, "label is only meaningful on a message version")

    if source_message_id:
        existing = await _find_outreach_by_source(db, contact_id, source_message_id)
        if existing is not None:
            _refuse_other_entry_for_source(existing, entry)
            get_audit_logger().info(
                "outreach_duplicate",
                extra={"event": "outreach_duplicate", "contact_id": contact_id, "outreach_id": existing["id"]},
            )
            return {
                "outreach": _serialize_outreach(existing), "already_existed": True, "event_id": None,
                "follow_up_on": None,
            }

    if entry == "bounced":
        # After the idempotent lookup, so a repeat bounce id is a no-op even once the email is cleared.
        bounced_address = await _current_email(db, user_id, contact)
        if not bounced_address:
            raise SpineError(422, "this contact has no email address to mark bounced")
    clean_text = text or ""
    if entry == "bounced":
        clean_text = bounced_address
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

    if is_new:
        if await _outreach_entry_count(db, contact_id, _NEW_ENTRIES) >= settings.CONTACT_INTERACTIONS_PER_CONTACT_MAX:
            raise SpineError(
                429,
                f"too many interaction entries; cap is CONTACT_INTERACTIONS_PER_CONTACT_MAX "
                f"({settings.CONTACT_INTERACTIONS_PER_CONTACT_MAX})",
            )
    else:
        total = await _outreach_entry_count(db, contact_id, _OUTREACH_ENTRIES)
        # A bounce is a safety fact for the gate: never blocked by the ledger cap (message-id idempotency
        # and the hourly rate limit already bound it).
        if entry != "bounced" and total >= settings.OUTREACH_ENTRIES_PER_CONTACT_MAX:
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
    if follow_up_on is not None and (application_id is not None or is_new):
        today = await user_today(db, user_id)
        parsed_follow_up = parse_follow_up_on(follow_up_on, today)

    # Owner decision, 2026-09-25 — same web-session exemption as add_contact
    # above: this hourly cap is the ASSISTANT's budget, never the human's.
    # Interaction / referral entries spend their OWN budget, never a send's.
    if is_new:
        if actor != _WEB_ACTOR and not rate_limit.check_and_record(
            f"contact_interaction:{user_id}", max_in_window=settings.CONTACT_INTERACTIONS_MAX_PER_HOUR,
            window_seconds=3600,
        ):
            raise SpineError(429, "interaction rate limit exceeded; try again in an hour")
    elif actor != _WEB_ACTOR and not rate_limit.check_and_record(
        f"outreach:{user_id}", max_in_window=settings.OUTREACH_MAX_PER_HOUR, window_seconds=3600
    ):
        raise SpineError(429, "outreach rate limit exceeded; try again in an hour")
    stored_follow_up: Optional[str] = (
        parsed_follow_up if is_new and parsed_follow_up is not FOLLOW_UP_UNSET else None
    )

    now = datetime.now(timezone.utc).isoformat()
    event_id: Optional[int] = None
    final_follow_up_on: Optional[str] = None
    outreach_id: Optional[int] = None
    version_no: Optional[int] = None

    if is_message:
        # Bug fix (coordinator review, 2026-09-26) — C4-style retry, same
        # reasoning as spine.save_artifact's version race: two concurrent
        # drafts for the same contact must never surface a raw 500, the
        # loser just gets renumbered on retry. The INSERT is scoped in its own
        # `transaction()` (same as save_artifact): a SAVEPOINT inside a
        # client_key transaction (slice 25), where a bare `rollback()` is
        # forbidden; on a UNIQUE clash the block rolls back only this INSERT.
        for _attempt in range(5):
            version_no = await _message_version_count(db, contact_id) + 1
            try:
                async with db._db.transaction():
                    ins = await db._db.execute(
                        "INSERT INTO contact_outreach "
                        "(user_id, contact_id, entry, channel, text, version_no, occurred_at, recorded_at, "
                        " recorded_by, source_message_id, label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            user_id, contact_id, entry, channel, clean_text, version_no, occurred, now, actor,
                            source_message_id or "", clean_label,
                        ),
                    )
            except pg.IntegrityError:
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
                    " recorded_by, source_message_id, follow_up_on, label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id, contact_id, entry, channel, clean_text, None, occurred, now, actor,
                        source_message_id or "", stored_follow_up, clean_label,
                    ),
                )
                outreach_id = int(cur.lastrowid or 0)
                if application_id is not None and entry != "bounced":
                    event_type = settings.CONTACT_LEDGER_EVENT_TYPES[entry]
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
                    _refuse_other_entry_for_source(existing, entry)
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
            "entry": safe_log_value(entry), "channel": safe_log_value(channel),
            "chars": len(clean_text),
            "follow_up_set": stored_follow_up is not None or final_follow_up_on is not None,
        },
    )
    if final_follow_up_on is None:
        final_follow_up_on = stored_follow_up
    outreach = {
        "id": outreach_id, "contact_id": contact_id, "entry": entry, "channel": channel, "text": clean_text,
        "version_no": version_no, "occurred_at": occurred, "recorded_at": now, "recorded_by": actor,
        "source_message_id": source_message_id or "", "follow_up_on": stored_follow_up, "label": clean_label,
    }
    if entry == "message":
        # Warn-only writing checks (cv_checks.check_text): stock phrases, leaked chat.
        # Returned with the reply, not stored; the message is saved as written.
        from src.services.applications import cv_checks  # noqa: PLC0415

        checks = cv_checks.check_text("outreach", clean_text, None)
        if channel == "linkedin":
            # S16 - the connection-note limit; the profile is read only for this channel.
            note = cv_checks.linkedin_note_too_long(
                clean_text, premium=await _linkedin_premium(user_id, contact_id)
            )
            if note is not None:
                checks.append(note)
        outreach["checks"] = checks
    return {
        "outreach": outreach, "already_existed": False, "event_id": event_id,
        "follow_up_on": final_follow_up_on,
    }


async def outreach_gate_facts(
    db: JobDatabase, user_id: str, contact: dict[str, Any], message_id: int
) -> settings_rules.OutreachFacts:
    """The closed facts ``may_send_outreach`` reads for one OWNED contact (the
    caller already checked ownership via ``get_owned_contact``) and one saved
    message version. Read-only; never returns the message text or the email
    address, only whether they exist. A message row that belongs to another
    contact (or user) reads as ``message_found=False`` (no existence oracle)."""
    contact_id = int(contact["id"])
    rows = await _outreach_rows(db, contact_id)
    messages = [r for r in rows if r["entry"] == "message"]
    target = next((r for r in messages if int(r["id"]) == int(message_id)), None)
    if target is None:
        return settings_rules.OutreachFacts(message_found=False)
    current = dict(contact)
    _apply_current_fields(current, (await _latest_edits_for_user(db, user_id)).get(contact_id, {}))
    application_id = contact.get("application_id")
    closed = False
    job_company = ""
    if application_id is not None:
        app_row = await get_owned_application(db, user_id, int(application_id))
        if app_row is not None:
            closed = str(app_row.get("status") or "") in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES
            job_company = str(app_row.get("job_company") or "")
    company = contact_company_key({"company": current.get("company"), "job_company": job_company})
    email = (current.get("email") or "").strip().lower()
    bounced = False
    if email:
        cur = await db._db.execute(
            "SELECT 1 FROM contact_outreach WHERE user_id = ? AND entry = 'bounced' AND text = ? LIMIT 1",
            (user_id, email),
        )
        bounced = await cur.fetchone() is not None
    return settings_rules.OutreachFacts(
        message_found=True,
        latest_draft=int(target["id"]) == max(int(r["id"]) for r in messages),
        channel=str(target.get("channel") or ""),
        has_email=bool((current.get("email") or "").strip()),
        already_sent=any(r["entry"] == "sent" for r in rows),
        replied=any(r["entry"] == "reply" for r in rows),
        linked=application_id is not None,
        application_closed=closed,
        found_via=current.get("found_via") or None,
        company=company,
        email_source=current.get("email_source") or None,
        email_bounced=bounced,
    )


async def count_company_sends(db: JobDatabase, user_id: str, key: str, since: str) -> int:
    """Recorded `sent` marks since `since` to this user's contacts whose effective
    company key (contact_company_key, edits applied) equals `key`. Cold contacts count."""
    cur = await db._db.execute(
        "SELECT ac.id AS contact_id, ac.company, a.job_company FROM contact_outreach co "
        "JOIN application_contacts ac ON ac.id = co.contact_id "
        "LEFT JOIN applications a ON a.id = ac.application_id "
        "WHERE co.user_id = ? AND ac.user_id = ? AND co.entry = 'sent' AND co.recorded_at >= ?",
        (user_id, user_id, since),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    edits = await _latest_edits_for_user(db, user_id)
    total = 0
    for r in rows:
        own = edits.get(r["contact_id"], {}).get("company", r.get("company"))
        if contact_company_key({"company": own, "job_company": r.get("job_company")}) == key:
            total += 1
    return total


async def referred_by(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """The kit's ``application.referred_by``: the contact the newest LIVE (not
    corrected) ``referral_given`` event on this application names, with its CURRENT
    name (contact_edits overlay). None when there is none (rule #29). Read-only."""
    cur = await db._db.execute(
        "SELECT e.payload FROM application_events e WHERE e.user_id = ? AND e.application_id = ? "
        "AND e.event_type = ? AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        "ORDER BY e.id DESC LIMIT 1",
        (user_id, application_id, settings.CONTACT_LEDGER_EVENT_TYPES["referral_given"]),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    try:
        cid = json.loads(dict(row)["payload"] or "{}").get("contact_id")
    except (ValueError, AttributeError):
        return None
    if not isinstance(cid, int) or isinstance(cid, bool):
        return None
    contact = await get_owned_contact(db, user_id, cid)
    if contact is None:
        return None
    current = dict(contact)
    _apply_current_fields(current, (await _latest_edits_for_user(db, user_id)).get(cid, {}))
    return {"contact_id": cid, "name": current["name"]}


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
            if field in ("found_via", "source_url", "company", "email_source", "relationship", "shared", "phone"):
                view[field] = view[field] or None  # a cleared value reads as unset
            if field == "links":
                view[field] = _decode_links(view[field])
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
            base_view = _serialize(base)
            if field == "links":
                base_value = json.dumps(base_view["links"]) if base_view["links"] else ""
            else:
                base_value = base_view[field] or ""
            base_entry = {
                "value": base_value, "recorded_at": base["created_at"],
                "recorded_by": base["added_by"],
            }
            rest = field_edits
        history[field] = [
            base_entry, *({"value": e["value"], "recorded_at": e["recorded_at"], "recorded_by": e["recorded_by"]}
                           for e in rest)
        ]
    view["edit_history"] = history
    # The web-only Confirm mark: a ``contact_edits`` row, never in CONTACT_EDIT_FIELDS.
    confirm_edits = edits.get("confirmed") or []
    view["confirmed"] = base["added_by"] == _WEB_ACTOR or (
        bool(confirm_edits) and confirm_edits[-1]["value"] == "true"
    )
    view["reachable"] = is_reachable(view)
    rows = await _outreach_rows(db, contact_id)
    view["outreach"] = _outreach_view(rows, include_text=include_text)
    return view


def _group_key(row: dict[str, Any]) -> str:
    email = (row.get("email") or "").strip().lower()
    if email:
        return f"email:{email}"
    linkedin = linkedin_identity_key(row.get("linkedin_url") or "")
    if linkedin:
        return f"linkedin:{linkedin}"
    ph = phone_identity_key(row.get("phone"))
    if ph:
        return f"phone:{ph}"
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
    if "found_via" in edits:
        row["found_via"] = edits["found_via"] or None
    if "source_url" in edits:
        row["source_url"] = edits["source_url"] or None
    if "company" in edits:
        row["company"] = edits["company"] or None
    if "email_source" in edits:
        row["email_source"] = edits["email_source"] or None
    if "relationship" in edits:
        row["relationship"] = edits["relationship"] or None
    if "shared" in edits:
        row["shared"] = edits["shared"] or None
    if "phone" in edits:
        row["phone"] = edits["phone"] or None
    if "links" in edits:
        row["links"] = _decode_links(edits["links"])
    row["confirmed"] = row.get("added_by") == _WEB_ACTOR or edits.get("confirmed") == "true"


async def _contacts_by_current_email(db: JobDatabase, user_id: str, email: str) -> list[dict[str, Any]]:
    """``list_people(email=…)``'s targeted lookup (bug fix, coordinator review
    2026-09-26) — searches the CURRENT email (base row or its latest edit),
    unbounded by ``LIST_PEOPLE_MAX``: a person's own linked-job count is
    naturally small, so there is nothing here to page."""
    clean = email.strip().lower()
    cur = await db._db.execute(
        "SELECT ac.id, ac.application_id, ac.name, ac.role, ac.email, ac.linkedin_url, ac.notes, ac.found_via, "
        "ac.source_url, ac.company, ac.email_source, ac.relationship, ac.shared, ac.phone, ac.links, "
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


async def _all_contacts_current(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """Every contact the user owns (newest first, no LIMIT), edits applied. Used only by
    ``list_people(company=...)`` so a company match is never missed past LIST_PEOPLE_MAX."""
    cur = await db._db.execute(
        "SELECT ac.id, ac.application_id, ac.name, ac.role, ac.email, ac.linkedin_url, ac.notes, ac.found_via, "
        "ac.source_url, ac.company, ac.added_by, ac.created_at, a.job_title, a.job_company "
        "FROM application_contacts ac LEFT JOIN applications a ON a.id = ac.application_id "
        "WHERE ac.user_id = ? ORDER BY ac.id DESC",
        (user_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    edits_by_contact = await _latest_edits_for_user(db, user_id)
    for r in rows:
        _apply_current_fields(r, edits_by_contact.get(r["id"], {}))
    return rows


async def list_people(
    db: JobDatabase,
    user_id: str,
    *,
    contact_id: Optional[int] = None,
    email: Optional[str] = None,
    company: Optional[str] = None,
    exclude_application_id: Optional[int] = None,
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

    ``company`` keeps only people whose CURRENT own company (else their linked
    job's company) has the same company_key as jobs; it scans ALL the user's
    contacts (never truncated). ``exclude_application_id`` is internal
    (known_people): a person already on that application is left out.

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
    clean_company = (company or "").strip()
    if clean_company and len(clean_company) > settings.CONTACT_COMPANY_MAX_CHARS:
        raise SpineError(
            422, f"company exceeds CONTACT_COMPANY_MAX_CHARS ({settings.CONTACT_COMPANY_MAX_CHARS} chars)"
        )
    if email:
        # Bug fix (coordinator review, 2026-09-26) — a targeted lookup by
        # CURRENT email, unbounded by LIST_PEOPLE_MAX: filtering in SQL means
        # a match outside the newest page is never silently missed.
        rows = await _contacts_by_current_email(db, user_id, email)
    elif clean_company:
        rows = await _all_contacts_current(db, user_id)
    else:
        # Bug fix (coordinator review, 2026-09-26) — newest first (was oldest
        # first), and one extra row fetched to detect truncation honestly
        # instead of silently dropping the tail.
        cur = await db._db.execute(
            "SELECT ac.id, ac.application_id, ac.name, ac.role, ac.email, ac.linkedin_url, ac.notes, ac.found_via, "
            "ac.source_url, ac.company, ac.email_source, ac.relationship, ac.shared, ac.phone, ac.links, "
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

    want = company_key(clean_company) if clean_company else ""

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
        # Company filter on the WHOLE group (a person matches when any of their
        # rows does), so exclusion, jobs and outreach see every row.
        if clean_company and not (
            want and any(contact_company_key(r) == want for r in group_rows)
        ):
            continue
        if exclude_application_id is not None and any(
            r["application_id"] == exclude_application_id for r in group_rows
        ):
            continue
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
                "found_via": primary.get("found_via") or None,
                "source_url": primary.get("source_url") or None,
                "company": primary.get("company") or None,
                "email_source": primary.get("email_source") or None,
                "relationship": primary.get("relationship") or None,
                "shared": primary.get("shared") or None,
                "phone": primary.get("phone") or None,
                "links": _decode_links(primary.get("links")),
                "confirmed": bool(primary.get("confirmed")),
                "reachable": any(is_reachable(r) for r in group_rows),
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
        "SELECT id, application_id, name, role, email, linkedin_url, notes, found_via, source_url, "
        "company, email_source, relationship, shared, phone, links, "
        "added_by, created_at "
        "FROM application_contacts WHERE user_id = ? AND application_id IS NULL AND id >= ? "
        "ORDER BY id ASC LIMIT ?",
        (user_id, after_id, limit),
    )
    return [dict(r) for r in await cur.fetchall()]
