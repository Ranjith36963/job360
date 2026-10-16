"""Repost among the jobs THIS user brought (feedback slice 5, feature 18).

ADAPTED from career-ops ``detect-reposts.mjs``: the same title at the same
company under a different link, first seen 1..90 days ago, is a probable
repost of one opening. Mirrors :268-281 (title identity key), :443-449 (the
same link is the same posting, not a repost), :457-458 (the day window).
Facts only - Job360 never skips, ranks or recommends on it (rule M1).

Scope is the caller's own ``applications`` rows (rule 12). Never
``jobs.first_seen_at``: that column is catalog-wide and would leak another
user's bring.
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

from src.core import settings
from src.repositories.database import JobDatabase
from src.utils.logger import safe_log_value

log = logging.getLogger("job360.reposts")

LABEL = "among jobs you brought"


def title_identity_key(title: str) -> str:
    """detect-reposts.mjs:268-281 - NFD, drop combining marks, lower, every
    non-alphanumeric char to a space, split, de-duplicate, sort, join. No words
    -> the stripped lower-case title. A comparison key only; ``Job.normalized_key``
    (hard rule 1) is not touched."""
    raw = title or ""
    decomposed = unicodedata.normalize("NFD", raw)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").lower()
    spaced = "".join(ch if ch.isalnum() else " " for ch in stripped)
    words = sorted(set(spaced.split()))
    if not words:
        return raw.strip().lower()
    return " ".join(words)


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def repost_of(
    db: JobDatabase,
    user_id: str,
    *,
    company: str,
    title: str,
    apply_url: str,
    exclude_application_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """The earliest of this user's own applications that is the same title at
    the same company under ANOTHER job link, 1..REPOST_WINDOW_DAYS days ago;
    else None. Never raises: a failure is logged and returns None (a bring
    must never fail on it)."""
    try:
        from src.services.applications import spine  # noqa: PLC0415
        from src.services.applications.job_checks import canonical_url, is_job_specific_url  # noqa: PLC0415
        from src.services.applications.kit import normalized_company_key  # noqa: PLC0415

        wanted = canonical_url(apply_url or "")
        if not wanted or not is_job_specific_url(apply_url or ""):
            return None
        want_company = normalized_company_key(company or "")
        want_title = title_identity_key(title or "")
        if not want_company or not want_title:
            return None
        cur = await db._db.execute(
            "SELECT a.id, a.job_company, a.job_title, a.job_url, a.created_at, j.apply_url AS catalog_url "
            "FROM applications a LEFT JOIN jobs j ON j.id = a.job_id WHERE a.user_id = ? ORDER BY a.id",
            (user_id,),
        )
        zone = await spine.user_zone(db, user_id)
        today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
        best: Optional[tuple[datetime, dict[str, Any]]] = None
        for r in await cur.fetchall():
            row = dict(r)
            if exclude_application_id is not None and int(row["id"]) == exclude_application_id:
                continue
            if normalized_company_key(str(row["job_company"] or "")) != want_company:
                continue
            if title_identity_key(str(row["job_title"] or "")) != want_title:
                continue
            links = {
                canonical_url(str(row["job_url"] or "")), canonical_url(str(row["catalog_url"] or "")),
            } - {""}
            # Both links non-empty and job-specific; the same link is the same posting.
            if not links or wanted in links:
                continue
            if not any(is_job_specific_url(str(u or "")) for u in (row["job_url"], row["catalog_url"])):
                continue
            created = _parse(row["created_at"])
            if created is None:
                continue
            first_seen = created.astimezone(zone).date()
            days = (today - first_seen).days
            if not settings.REPOST_MIN_SPAN_DAYS <= days <= settings.REPOST_WINDOW_DAYS:
                continue
            if best is None or created < best[0]:
                best = (created, {
                    "label": LABEL, "first_seen": first_seen.isoformat(), "days_since": days,
                    "application_id": int(row["id"]), "window_days": settings.REPOST_WINDOW_DAYS,
                })
        return best[1] if best else None
    except Exception:  # noqa: BLE001 - a bring must never fail on a repost check
        log.warning("repost_check_failed", extra={"user_id": safe_log_value(user_id)}, exc_info=True)
        return None
