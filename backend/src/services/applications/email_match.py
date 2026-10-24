"""match_email (feature 8, G088) - which of the user's applications an email may belong to,
from facts Job360 already stores.

VISION rule 5 / hard rule M2: the user's assistant reads the mail and decides. Job360 reads no
email and gives NO verdict: it lists each application a stored fact links to the email, naming
the fact. No score, no ranking - newest application first. Read-only: nothing is written.
"""
from __future__ import annotations

import re
from email.utils import parseaddr
from typing import TYPE_CHECKING, Any

from src.core import settings
from src.services.applications.contacts import _contacts_by_current_email
from src.services.applications.inbox import email_senders_for
from src.services.applications.job_checks import _is_or_under
from src.services.applications.spine import SpineError, _has_control_chars

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase

# The closed set of reasons, in the order they appear on a candidate.
MATCH_EMAIL_REASONS = ("same_thread", "contact_email", "sender_domain_in_email_senders", "company_in_subject")
_NO_COMPANY = frozenset({"", "unknown"})  # models.Job._clean_company stores "Unknown" for a blank company
_MIN_COMPANY_CHARS = 2


def _clean(name: str, value: str, cap: int, setting_name: str) -> str:
    value = (value or "").strip()
    if _has_control_chars(value):
        raise SpineError(422, f"{name} must not contain control characters")
    if len(value) > cap:
        raise SpineError(422, f"{name} exceeds {setting_name} ({cap} chars)")
    return value


def _named_in(company_key: str, subject_folded: str) -> bool:
    """True when the company name appears in the subject as a whole word."""
    if company_key in _NO_COMPANY or len(company_key) < _MIN_COMPANY_CHARS:
        return False
    return re.search(rf"(?<!\w){re.escape(company_key)}(?!\w)", subject_folded) is not None


async def match_email(
    db: JobDatabase, user_id: str, *, sender: str = "", thread_id: str = "", subject: str = ""
) -> dict[str, Any]:
    sender = _clean(
        "sender",
        sender,
        settings.APPLICATION_EVENT_SOURCE_SENDER_MAX_CHARS,
        "APPLICATION_EVENT_SOURCE_SENDER_MAX_CHARS",
    )
    thread_id = _clean(
        "thread_id",
        thread_id,
        settings.APPLICATION_EVENT_SOURCE_THREAD_ID_MAX_CHARS,
        "APPLICATION_EVENT_SOURCE_THREAD_ID_MAX_CHARS",
    )
    subject = _clean(
        "subject",
        subject,
        settings.APPLICATION_EVENT_SOURCE_SUBJECT_MAX_CHARS,
        "APPLICATION_EVENT_SOURCE_SUBJECT_MAX_CHARS",
    )
    if not (sender or thread_id or subject):
        raise SpineError(422, "give at least one of sender, thread_id or subject")

    address = parseaddr(sender)[1].strip().lower()
    if "@" not in address:
        address = ""
    domain = address.rsplit("@", 1)[1] if address else ""

    thread_events: dict[int, list[int]] = {}
    if thread_id:
        cur = await db._db.execute(
            "SELECT id, application_id FROM application_events "
            "WHERE user_id = ? AND source_thread_id = ? ORDER BY id ASC",
            (user_id, thread_id),
        )
        for r in await cur.fetchall():
            thread_events.setdefault(int(r["application_id"]), []).append(int(r["id"]))

    contact_ids: dict[int, list[int]] = {}
    if address:
        for c in await _contacts_by_current_email(db, user_id, address):
            if c.get("application_id") is not None:
                contact_ids.setdefault(int(c["application_id"]), []).append(int(c["id"]))

    cur = await db._db.execute(
        "SELECT a.id, a.job_title, a.job_company, a.job_url, a.status, "
        "COALESCE(j.normalized_company, '') AS normalized_company "
        "FROM applications a LEFT JOIN jobs j ON j.id = a.job_id "
        "WHERE a.user_id = ? ORDER BY a.id DESC",
        (user_id,),
    )
    subject_folded = subject.casefold()
    candidates: list[dict[str, Any]] = []
    for a in await cur.fetchall():
        app_id = int(a["id"])
        senders = email_senders_for(a["job_url"] or "")
        reasons: list[str] = []
        if app_id in thread_events:
            reasons.append("same_thread")
        if app_id in contact_ids:
            reasons.append("contact_email")
        if domain and any(_is_or_under(domain, s) for s in senders):
            reasons.append("sender_domain_in_email_senders")
        company = (a["normalized_company"] or a["job_company"] or "").strip().casefold()
        if subject and _named_in(company, subject_folded):
            reasons.append("company_in_subject")
        if not reasons:
            continue
        candidates.append(
            {
                "application_id": app_id,
                "job_title": a["job_title"],
                "job_company": a["job_company"],
                "status": a["status"],
                "reasons": reasons,
                "thread_event_ids": sorted(thread_events.get(app_id, [])),
                "contact_ids": sorted(contact_ids.get(app_id, [])),
                "email_senders": senders,
            }
        )
    cap = settings.MATCH_EMAIL_MAX_CANDIDATES
    return {"candidates": candidates[:cap], "truncated": len(candidates) > cap}
