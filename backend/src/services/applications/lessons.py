"""Slice 9 (#516) — "flag for next time": the read side of ``lesson`` events.

docs/plans/2026-09-11-lessons/spec.md R1. A lesson is written through the
one door for history (``record_event``, type ``lesson`` — rule M3); this
module only reads them back, across every application, newest first, so the
profile page can list them and ``get_profile`` can hand them to the agent
before it tailors the next CV. Sync (pgsync), the same connection style as
the profile read it rides alongside.
"""
from __future__ import annotations

from typing import Any

from src.core.settings import DB_PATH
from src.repositories import pgsync

_COLS = (
    "e.id, e.application_id, a.job_title, a.job_company, e.detail, e.occurred_at, e.recorded_by"
)

# A lesson some later event `corrects_event_id`-points at is superseded — the
# same rule `spine.list_events_for_display` applies on the timeline.
_NOT_SUPERSEDED = "NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id)"

# Both statements are assembled from the constants above only — no caller
# input ever reaches the SQL text; every value goes through a `?` parameter.
_SELECT_PAGE = (
    f"SELECT {_COLS} FROM application_events e "  # noqa: S608
    "JOIN applications a ON a.id = e.application_id AND a.user_id = e.user_id "
    f"WHERE e.user_id = ? AND e.event_type = 'lesson' AND {_NOT_SUPERSEDED} "
    "ORDER BY e.occurred_at DESC, e.id DESC LIMIT ? OFFSET ?"
)
_SELECT_TOTAL = (
    "SELECT COUNT(*) FROM application_events e "  # noqa: S608
    f"WHERE e.user_id = ? AND e.event_type = 'lesson' AND {_NOT_SUPERSEDED}"
)


def list_lessons(user_id: str, *, limit: int, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """``(rows, total)`` — the caller's lessons, newest ``occurred_at`` first.

    ``total`` counts every non-superseded lesson the user has, regardless of
    ``limit``/``offset``, so a capped page can still say how many there are.
    Every query is scoped by ``user_id`` (rule #12); the join to
    ``applications`` is on the same user so a foreign application can never
    lend its title.
    """
    with pgsync.connect(str(DB_PATH)) as conn:
        cur = conn.execute(_SELECT_PAGE, (user_id, limit, offset))
        rows = cur.fetchall()
        cur = conn.execute(_SELECT_TOTAL, (user_id,))
        total_row = cur.fetchone()
    total = int(total_row[0]) if total_row else 0
    return [
        {
            "event_id": int(r[0]),
            "application_id": int(r[1]),
            "job_title": r[2] or "",
            "job_company": r[3] or "",
            "detail": r[4] or "",
            "occurred_at": r[5],
            "recorded_by": r[6],
        }
        for r in rows
    ], total
