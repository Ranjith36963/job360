"""The employer's confirmation email marks an application sent by itself (owner 2026-11-02, reconfirmed 11-03).

"Confirm sent" on Needs you asked the user "did it go out?" whenever the assistant stored an employer's
"we received your application" email as proof (a ``note`` "Submission confirmed: ..." with its email
``source``, ``kit.pending_confirmations``) while the job was not applied. Now, when that note lands,
Job360 itself records ``applied`` (recorded by ``system``, payload ``{confirmed_by: "email",
note_event_id}``, occurred at the email's time) if ALL of these hold:

* the application is ``considering`` with no receipt and no surviving ``applied`` (a confirmation for an
  applied job stays a note: it is proof, never a second ``applied``);
* a ``submit_attempted`` or a ``form_filled`` was recorded on it, no ``submit_declined`` follows the newest
  fill, and no "It was not sent" (``submit_resolved``) follows the newest submit mark;
* the submit itself was cleared on this application: a ``submit_cleared`` / ``submit_cleared_used``
  (check_submit said ``submit``) or the owner's own web ``submit_approved``, none corrected;
* the email has a ``received_at`` no earlier than the job was brought, filled or its submit started;
* the email is not already stored on another application or as another event here (a rejection recorded
  from it, for one), and Job360's own stored facts (``match_email``: thread, contact, sender domain,
  company in subject) tie it to exactly this one application, by more than the company name in the subject.

Why the clearance (review of #1496): every field of the email is the caller's own text, so the email alone
proves nothing. It may only CONFIRM a submit that #1469's gate (or the owner) already let happen; it never
replaces that gate. #1469 itself is untouched: an assistant's own ``applied`` / ``record_application`` still
needs a clearance. This ``applied`` is recorded by ``system``, like the automatic ``ghosted``.

Otherwise nothing changes and the user is asked, as before. A started submit with no confirmation, no
``applied`` and no "It was not sent" asks the user only after ``CONFIRM_SENT_WAIT_DAYS``
(``silent_submits``, read by ``blockers``). The user undoes with ``POST /applications/{id}/auto/undo``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import cv_review, kit, spine
from src.services.applications.email_match import match_email
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

SYSTEM_ACTOR = "system"
CONFIRMED_BY = "email"
APPLIED_DETAIL = "Sent: the employer's confirmation email proves it"
_MARKS = ("form_filled", "submit_attempted", "submit_declined", "submit_resolved")


def is_confirmation(detail: str) -> bool:
    """The note the recipes write for a confirmation email ("Submission confirmed: <subject>")."""
    return detail.strip().casefold().startswith(kit.CONFIRMATION_NOTE_PREFIX)


def _log(user_id: str, application_id: int, name: str, result: str) -> None:
    get_audit_logger().info(name, extra={
        "event": name, "user_id": safe_log_value(user_id), "application_id": int(application_id),
        "result": safe_log_value(result, max_len=40),
    })


async def _submit_state(db: JobDatabase, user_id: str, application_id: int) -> str:
    """"" when a submit was started or a form filled here and nothing since says it was not sent."""
    cur = await db._db.execute(
        "SELECT id, event_type FROM application_events WHERE user_id = ? AND application_id = ? "  # noqa: S608
        f"AND event_type IN ({','.join('?' for _ in _MARKS)}) ORDER BY id ASC",
        (user_id, application_id, *_MARKS),
    )
    newest: dict[str, int] = {}
    for r in await cur.fetchall():
        newest[str(r["event_type"])] = int(r["id"])
    started = max(newest.get("form_filled", 0), newest.get("submit_attempted", 0))
    if not started:
        return "no_submit_or_fill"
    if newest.get("submit_declined", 0) > newest.get("form_filled", 0):
        return "declined"
    if newest.get("submit_resolved", 0) > started:
        return "said_not_sent"
    return ""


async def _only_here(db: JobDatabase, user_id: str, application_id: int, note_id: int, source: dict[str, Any]) -> bool:
    """The email is stored nowhere else, and Job360's facts tie it to this one application alone."""
    cur = await db._db.execute(
        "SELECT 1 FROM application_events WHERE user_id = ? AND source_message_id = ? AND id <> ? LIMIT 1",
        (user_id, source["message_id"], note_id),
    )
    if await cur.fetchone() is not None:
        return False
    found = await match_email(db, user_id, sender=source.get("sender") or "",
                              thread_id=source.get("thread_id") or "", subject=source.get("subject") or "")
    ids: set[int] = set()
    mine: set[str] = set()
    for c in found["candidates"]:
        # The note just stored is not a fact that links the email: a thread holding only it does not count.
        own_thread = set(c["thread_event_ids"]) <= {note_id}
        reasons = {r for r in c["reasons"] if not (r == "same_thread" and own_thread)}
        if reasons:
            ids.add(int(c["application_id"]))
            mine = reasons if int(c["application_id"]) == application_id else mine
    # A company named in the subject alone is no tie (review of #1496): any sender can write that.
    return ids == {application_id} and bool(mine - {"company_in_subject"}) and not found["truncated"]


def _when(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def _not_proven(db: JobDatabase, user_id: str, application_id: int, source: dict[str, Any]) -> str:
    """Review of #1496: every field of the email is the caller's, so the email may only CONFIRM a submit Job360
    already let happen. "" when the submit was cleared here (an unspent or spent ``submit_cleared``, or the
    owner's own web ``submit_approved``) and the email came after the job was brought and after the newest fill
    and started submit; else the reason the owner is asked instead."""
    received = _when(source.get("received_at"))
    if received is None:
        return "no_received_at"
    cur = await db._db.execute(
        "SELECT recorded_at FROM application_events WHERE user_id = ? AND application_id = ? "
        "AND event_type IN ('brought', 'form_filled', 'submit_attempted')",
        (user_id, application_id),
    )
    starts = [w for w in (_when(r["recorded_at"]) for r in await cur.fetchall()) if w is not None]
    if starts and received < max(starts):
        return "email_before_submit"
    cur = await db._db.execute(
        "SELECT 1 FROM application_events e WHERE e.user_id = ? AND e.application_id = ? "
        "AND (e.event_type IN ('submit_cleared', 'submit_cleared_used') "
        "OR (e.event_type = 'submit_approved' AND e.recorded_by = 'web')) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id "
        "AND c.user_id = e.user_id) LIMIT 1",
        (user_id, application_id),
    )
    return "" if await cur.fetchone() is not None else "not_cleared"


async def apply_if_proven(
    db: JobDatabase, user: CurrentUser, application_id: int, note_id: int, source: dict[str, Any],
) -> Optional[str]:
    """After a confirmation note with an email ``source`` is stored: record ``applied`` when every rule in
    the module doc holds. Returns the warning the assistant reads, or None (the user confirms, as before)."""
    async with db._db.transaction():
        cur = await db._db.execute(
            "SELECT status FROM applications WHERE id = ? AND user_id = ? FOR UPDATE", (application_id, user.id)
        )
        row = await cur.fetchone()
        why = ""
        if source.get("kind") != "email":
            why = "not_email"
        elif row is None or row["status"] != "considering":
            why = "not_considering"
        elif await kit.already_applied(db, user.id, application_id):
            why = "applied"
        else:
            why = await _submit_state(db, user.id, application_id) or await _not_proven(
                db, user.id, application_id, source)
        if not why and not await _only_here(db, user.id, application_id, note_id, source):
            why = "not_one_application"
        if why:
            _log(user.id, application_id, "auto_applied_skipped", why)
            return None
        # Like the user's own Mark Applied: what was still open is stamped on the event (the doctor lists it).
        open_at_submit = await kit.open_rule_items(db, user.id, application_id)
        cv_on_review = await cv_review.review_clearance(db, user.id, application_id, SYSTEM_ACTOR)
        await spine.append_event(
            db, user_id=user.id, application_id=application_id, event_type="applied", detail=APPLIED_DETAIL,
            payload={"confirmed_by": CONFIRMED_BY, "note_event_id": int(note_id),
                     **({"open_at_submit": open_at_submit} if open_at_submit else {})},
            occurred_at=source["received_at"], recorded_by=SYSTEM_ACTOR,  # never before the fill (_not_proven)
        )
        await cv_review.record_sent(db, user.id, application_id, cv_on_review, SYSTEM_ACTOR)
    _log(user.id, application_id, "auto_applied", "ok")
    return ("Job360 recorded this application as applied: the employer's confirmation email matches only this "
            "job, where a submit was started or the form filled. The user can undo it on the website.")


async def silent_submits(
    db: JobDatabase, user_id: str, application_ids: list[int], now: datetime,
) -> dict[int, dict[str, Any]]:
    """``{application_id: its newest submit_attempted}`` where that is still the newest submit mark (no surviving
    ``applied``, no ``submit_resolved`` after it: ``kit.submit_unconfirmed``'s rule) and it is older than
    ``CONFIRM_SENT_WAIT_DAYS``. One query whatever the number of ids."""
    ids = sorted({int(i) for i in application_ids})
    if not ids:
        return {}
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.event_type, e.recorded_at, EXISTS (SELECT 1 FROM application_events c "  # noqa: S608
        "WHERE c.corrects_event_id = e.id AND c.user_id = e.user_id) AS retracted FROM application_events e "
        f"WHERE e.user_id = ? AND e.application_id IN ({','.join('?' for _ in ids)}) "
        "AND e.event_type IN ('submit_attempted', 'applied', 'submit_resolved') ORDER BY e.id ASC",
        (user_id, *ids),
    )
    newest: dict[int, dict[str, Any]] = {}
    for r in await cur.fetchall():
        if r["event_type"] == "applied" and r["retracted"]:
            continue
        newest[int(r["application_id"])] = dict(r)
    cutoff = now - timedelta(days=settings.CONFIRM_SENT_WAIT_DAYS)
    out: dict[int, dict[str, Any]] = {}
    for app_id, ev in newest.items():
        at = ev["recorded_at"]
        at = at if isinstance(at, datetime) else datetime.fromisoformat(str(at))
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        if ev["event_type"] == "submit_attempted" and at <= cutoff:
            out[app_id] = ev
    return out
