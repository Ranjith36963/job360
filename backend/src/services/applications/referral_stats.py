"""``stats(view="referrals")``: applications with a recorded referral against the rest.

Counts only: an application is "referred" when it has a live (not corrected)
``referral_given`` event, or a receipt naming ``referred_by_contact_id``. Both
groups are summarised over the same rows ``compute_stats`` counted (since-scoped,
simulations out, newest ``STATS_MAX_APPLICATIONS``; replied / interview / offer
gated on applied). ``referral_asked`` is the number of applications with a live
``referral_asked`` event. Never ranks; logs the user and counts only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core import settings

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

NOTE = (
    "Facts only: counts of what you recorded, not proof that referrals work. "
    "Never rank or skip a job, company or person on these numbers; small n says little."
)

_LIVE = "NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id)"


async def build(db: JobDatabase, user_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``rows`` are the scoped application rows of ``compute_stats`` (``application_id``, ...)."""
    from src.services.applications.stats import _summarize  # noqa: PLC0415
    from src.utils.logger import get_audit_logger, safe_log_value  # noqa: PLC0415

    ids = [int(r["application_id"]) for r in rows]
    referred: set[int] = set()
    asked: set[int] = set()
    if ids:
        marks = ",".join("?" for _ in ids)
        given_type = settings.CONTACT_LEDGER_EVENT_TYPES["referral_given"]
        asked_type = settings.CONTACT_LEDGER_EVENT_TYPES["referral_asked"]
        cur = await db._db.execute(
            "SELECT e.application_id FROM application_events e WHERE e.user_id = ? AND e.event_type = ? "  # noqa: S608
            f"AND e.application_id IN ({marks}) AND {_LIVE} "
            "UNION "
            "SELECT r.application_id FROM application_receipts r WHERE r.user_id = ? "
            f"AND r.referred_by_contact_id IS NOT NULL AND r.application_id IN ({marks})",
            (user_id, given_type, *ids, user_id, *ids),
        )
        referred = {int(dict(r)["application_id"]) for r in await cur.fetchall()}
        cur = await db._db.execute(
            "SELECT e.application_id FROM application_events e WHERE e.user_id = ? AND e.event_type = ? "  # noqa: S608
            f"AND e.application_id IN ({marks}) AND {_LIVE}",
            (user_id, asked_type, *ids),
        )
        asked = {int(dict(r)["application_id"]) for r in await cur.fetchall()}

    yes = [r for r in rows if int(r["application_id"]) in referred]
    no = [r for r in rows if int(r["application_id"]) not in referred]
    get_audit_logger().info(
        "referral_stats",
        extra={
            "event": "referral_stats", "user_id": safe_log_value(user_id), "n": len(rows),
            "referred": len(yes), "result": "ok",
        },
    )
    return {
        "n": len(rows), "referred": _summarize(yes), "not_referred": _summarize(no),
        "referral_asked": len(asked), "note": NOTE,
    }
