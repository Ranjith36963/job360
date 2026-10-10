"""Ready to send - the applications whose form the assistant filled and the
user has not yet answered (S5b).

An application is READY when the user owns it, it is still ``considering``, it
has no receipt, its newest ``form_filled`` event is F, and no ``submit_approved``
or ``submit_declined`` event came after F (a decline then a NEW fill is ready
again), and it is not blocked (S6: its newest ``blocked`` / ``unblocked`` event
is not ``blocked``). One SQL, no per-row query. Always scoped by ``user_id`` (rule #12).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core import settings
from src.services.applications.kit import _json

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

_READY_SQL = (
    "SELECT a.id AS application_id, a.job_title, a.job_company, f.id AS form_filled_event_id, "
    "f.recorded_at AS filled_at, f.recorded_by AS filled_by, f.payload "
    "FROM applications a JOIN application_events f "
    "ON f.application_id = a.id AND f.user_id = a.user_id AND f.event_type = 'form_filled' "
    "AND f.id = (SELECT MAX(x.id) FROM application_events x WHERE x.application_id = a.id "
    "AND x.user_id = a.user_id AND x.event_type = 'form_filled') "
    "WHERE a.user_id = ? AND a.status = 'considering' "
    "AND NOT EXISTS (SELECT 1 FROM application_receipts r WHERE r.application_id = a.id AND r.user_id = a.user_id) "
    "AND NOT EXISTS (SELECT 1 FROM application_events d WHERE d.application_id = a.id AND d.user_id = a.user_id "
    "AND d.event_type IN ('submit_approved', 'submit_declined') AND d.id > f.id) "
    # S6: an open block (newest blocked/unblocked is `blocked`) is never ready to send.
    "AND NOT EXISTS (SELECT 1 FROM application_events b WHERE b.application_id = a.id AND b.user_id = a.user_id "
    "AND b.event_type = 'blocked' AND b.id = (SELECT MAX(y.id) FROM application_events y "
    "WHERE y.application_id = a.id AND y.user_id = a.user_id AND y.event_type IN ('blocked', 'unblocked'))) "
    "ORDER BY f.id DESC LIMIT ?"
)


async def ready_rows(db: JobDatabase, user_id: str, *, limit: int) -> list[dict[str, Any]]:
    """The caller's ready-to-send applications, newest fill first, at most
    ``min(limit, READY_TO_SEND_MAX)``. Each row: ``application_id``,
    ``form_filled_event_id``, ``filled_at``, ``filled_by``, ``payload`` (parsed),
    plus ``job_title`` / ``job_company`` for display."""
    cap = max(0, min(int(limit), settings.READY_TO_SEND_MAX))
    if cap == 0:
        return []
    cur = await db._db.execute(_READY_SQL, (user_id, cap))
    out: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        d = dict(r)
        d["payload"] = _json(d.get("payload"))
        out.append(d)
    return out
