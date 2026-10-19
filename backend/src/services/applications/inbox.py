"""The Gmail check's two stored facts (owner decision 2026-10-15, Gmail check slice).

The user's own assistant reads Gmail (VISION rule 5: Job360 reads no email). Job360
only keeps what makes the next read correct:

* ``email_senders`` - the domains this application's ATS sends from, so the
  assistant's search also catches mail that never names the company (an Ashby
  confirmation signed "TALENT TEAM"). Derived from the stored apply link; same
  look-alike-safe host match as job_checks (pattern from MadsLorentzen/ai-job-search
  ``/gmail-sync``, which ORs ATS sender domains into its Gmail query).
* ``inbox_checked_at`` - the time up to which this application's mail was read: a
  cache SLOT on ``applications`` (migration 0061), never an event, so a check every
  few hours cannot flood the timeline or make a quiet job look active.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications.job_checks import _is_or_under, apply_host
from src.services.applications.spine import SpineError, parse_occurred_at

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase


def email_senders_for(url: str) -> list[str]:
    """The ATS sender domains for an apply link; ``[]`` when the host is not a known ATS
    (a company's own careers site: its domain is unknown to us, so we stay silent)."""
    host = apply_host(url or "")
    for apex in settings.OFFICIAL_ATS_HOSTS:
        if host and _is_or_under(host, apex):
            return [apex, *settings.ATS_EXTRA_EMAIL_SENDERS.get(apex, ())]
    return []


def _checked_at(raw: Optional[str]) -> str:
    """ISO-8601 with a timezone; now when empty. Never later than now: occurred_at's
    clock-skew allowance would let the cursor skip mail from the next few minutes."""
    now = datetime.now(timezone.utc)
    if not raw:
        return now.isoformat()
    parsed = parse_occurred_at(raw)
    return min(datetime.fromisoformat(parsed), now).isoformat()


async def mark_checked(
    db: JobDatabase, user_id: str, application_ids: list[int], checked_at: Optional[str] = None
) -> dict[str, Any]:
    """Set ``inbox_checked_at`` on each of THIS user's applications. All or nothing: an id
    that is not the caller's is a 404 and nothing is written (never a partial cursor)."""
    ids = sorted({int(i) for i in application_ids})
    if not ids:
        raise SpineError(422, "application_ids must name at least one application")
    cap = settings.INBOX_CHECK_MAX_APPLICATIONS
    if len(ids) > cap:
        raise SpineError(422, f"at most {cap} application_ids per call (INBOX_CHECK_MAX_APPLICATIONS)")
    when = _checked_at(checked_at)
    marks = ",".join("?" for _ in ids)
    cur = await db._db.execute(
        f"SELECT id FROM applications WHERE user_id = ? AND id IN ({marks})",  # noqa: S608 - placeholders only
        [user_id, *ids],
    )
    owned = {int(r[0]) for r in await cur.fetchall()}
    missing = [i for i in ids if i not in owned]
    if missing:
        raise SpineError(404, f"application not found: {missing}")
    # The cursor only moves forward: an older checked_at never rewinds a newer one.
    await db._db.execute(
        f"UPDATE applications SET inbox_checked_at = ? WHERE user_id = ? AND id IN ({marks}) "  # noqa: S608
        "AND (inbox_checked_at IS NULL OR inbox_checked_at < ?)",
        [when, user_id, *ids, when],
    )
    await db._db.commit()
    return {"application_ids": ids, "inbox_checked_at": when}
