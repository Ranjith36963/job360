"""Stats — counts over the event log, nothing else (spec
docs/plans/2026-09-05-contacts-stats/spec.md R5-R7, S1, S7, S10).

Everything here is a hand count: ``brought``/``applied``/``replied``/
``interview``/``offer``/``rejected`` as ``COUNT(DISTINCT application_id)``
having >=1 event of that type, plus two groupings (by the CV variant label
named on the latest receipt, and by role). No inference, no keyword lists —
grouping keys are ``lower(trim(...))`` of data the caller already wrote.

**One query, and it is bounded.** This module issues ONE query, not three.
It returns one row per application in scope (whether each status event type
fired, plus the CV label/profile version off that application's LATEST
receipt via a correlated subquery), and computes the overall totals AND both
groupings from that single result set in Python — no per-application loop
that hits the database.

The fetch itself is capped at ``settings.STATS_MAX_APPLICATIONS`` (S6): the
query reads the NEWEST N applications for the user, and the response says
``applications_truncated: true`` when there were more. Group count is capped
separately at ``settings.STATS_MAX_GROUPS``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications.spine import SpineError
from src.services.auth import rate_limit

if TYPE_CHECKING:  # pragma: no cover — type-only, same reasoning as spine.py
    from src.repositories.database import JobDatabase

def _parse_since(raw: Optional[str]) -> Optional[str]:
    """R7 — optional ISO date/datetime, NORMALISED to a UTC isoformat string.

    ``applications.created_at`` is stored as ``datetime.now(timezone.utc).
    isoformat()`` — always ``+00:00``-suffixed text — and the comparison is a
    TEXT comparison in SQL. So the caller's spelling has to be converted to
    that same spelling before it can be compared: ``...T10:00:00Z`` sorts
    AFTER ``...T10:00:00+00:00`` lexically (``Z`` > ``+``), which silently
    excluded rows the caller meant to include, and an offset spelling
    (``12:00:00+02:00``) compared as though it were noon UTC.

    Returns ``.astimezone(timezone.utc).isoformat()``; a naive datetime is
    read as UTC (the only interpretation consistent with what is stored).
    Anything unparseable is a 422.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise SpineError(422, "since must be an ISO-8601 date or datetime") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _norm(raw: Optional[str]) -> Optional[str]:
    """``lower(trim(...))`` — the ONLY normalisation (memory: no hand-typed
    vocabularies). Empty/whitespace-only or missing collapses to the null
    group."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped.lower() if stripped else None


def _rates(applied: int, replied: int, interview: int, offer: int) -> dict[str, Optional[float]]:
    """R5 — null when ``applied = 0`` (rule #29: an empty shelf stays silent,
    never a computed 0%)."""
    if applied == 0:
        return {"reply_rate": None, "interview_rate": None, "offer_rate": None}
    return {
        "reply_rate": round(replied / applied, 3),
        "interview_rate": round(interview / applied, 3),
        "offer_rate": round(offer / applied, 3),
    }


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "brought": len(rows),
        "applied": sum(r["applied"] for r in rows),
        "replied": sum(r["replied"] for r in rows),
        "interview": sum(r["interview"] for r in rows),
        "offer": sum(r["offer"] for r in rows),
        "rejected": sum(r["rejected"] for r in rows),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _counts(rows)
    return {**counts, **_rates(counts["applied"], counts["replied"], counts["interview"], counts["offer"])}


def _group(
    rows: list[dict[str, Any]],
    *,
    key_field: str,
    label_key: str,
    with_profile_versions: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Group ``rows`` by ``lower(trim(row[key_field]))``; the display value
    (``label_key`` in the output) is the RAW ``key_field`` value on the
    group's earliest application (lowest ``application_id`` — spec R6),
    never the normalised key. Each group also carries the normalised ``key``
    itself, which is what the tie-break below orders on.

    Ordered ``applied DESC, brought DESC, key ASC``, capped at
    ``STATS_MAX_GROUPS``. THE THIRD TERM IS NOT DECORATION: with only the two
    count terms, tied groups came back in Python dict-insertion order — which
    is application order, so which groups survived the cap depended on the
    order the user happened to bring their jobs in, and two identical calls
    could disagree after any new row. The null group sorts last among ties;
    it is the "we could not name this" bucket, never a labelled variant's
    equal.
    """
    buckets: dict[Optional[str], list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(_norm(row.get(key_field)), []).append(row)

    groups: list[dict[str, Any]] = []
    for key, members in buckets.items():
        earliest = min(members, key=lambda m: m["application_id"])
        entry: dict[str, Any] = {
            "key": key,
            label_key: None if key is None else earliest.get(key_field),
        }
        entry.update(_summarize(members))
        if with_profile_versions:
            versions = sorted({m["cv_profile_version"] for m in members if m.get("cv_profile_version") is not None})
            entry["profile_versions"] = versions
        groups.append(entry)

    groups.sort(key=lambda g: (-g["applied"], -g["brought"], g["key"] is None, g["key"] or ""))
    truncated = len(groups) > settings.STATS_MAX_GROUPS
    return groups[: settings.STATS_MAX_GROUPS], truncated


async def compute_stats(db: JobDatabase, user_id: str, since: Optional[str] = None) -> dict[str, Any]:
    """R5-R7 — overall counts + rates, ``by_cv_version``, ``by_role``, all
    scoped to the caller's own applications (S1: ``user_id`` filtered on
    every table in the join) and rate-limited per user (S7).

    ``since`` is parsed BEFORE the budget is consulted: a malformed value is
    a 422 the caller must fix, and making them pay an hourly slot for each
    attempt would lock them out for an hour over a typo.

    Every event-type name is BOUND AS A PARAMETER from ``src.core.settings``
    (S4) — this module never re-types one as a SQL literal, so the vocabulary
    has exactly one home and a rename there cannot leave a stale string here.
    """
    since_val = _parse_since(since)

    key = f"stats:{user_id}"
    if not rate_limit.check_and_record(key, max_in_window=settings.STATS_MAX_PER_HOUR, window_seconds=3600):
        raise SpineError(429, "stats rate limit exceeded; try again in an hour")

    interview_types = settings.STATS_INTERVIEW_EVENT_TYPES
    interview_placeholders = ", ".join("?" for _ in interview_types)

    where = ["a.user_id = ?"]
    where_params: list[Any] = [user_id]
    if since_val:
        where.append("a.created_at >= ?")
        where_params.append(since_val)
    where_sql = " AND ".join(where)

    # S6 — read one more than the cap so "there were more" is a fact, not a
    # guess: N+1 rows back means the tail was cut.
    limit = settings.STATS_MAX_APPLICATIONS

    sql = f"""
        SELECT
            a.id AS application_id,
            a.job_title AS job_title,
            MAX(CASE WHEN e.event_type = ? THEN 1 ELSE 0 END) AS applied,
            MAX(CASE WHEN e.event_type = ? THEN 1 ELSE 0 END) AS replied,
            MAX(CASE WHEN e.event_type IN ({interview_placeholders}) THEN 1 ELSE 0 END) AS interview,
            MAX(CASE WHEN e.event_type = ? THEN 1 ELSE 0 END) AS offer,
            MAX(CASE WHEN e.event_type = ? THEN 1 ELSE 0 END) AS rejected,
            (
                SELECT art.label FROM application_receipts r
                LEFT JOIN application_artifacts art
                    ON art.id = r.cv_artifact_id AND art.user_id = a.user_id
                WHERE r.application_id = a.id AND r.user_id = a.user_id
                ORDER BY r.id DESC LIMIT 1
            ) AS cv_label,
            (
                SELECT art.profile_version FROM application_receipts r
                LEFT JOIN application_artifacts art
                    ON art.id = r.cv_artifact_id AND art.user_id = a.user_id
                WHERE r.application_id = a.id AND r.user_id = a.user_id
                ORDER BY r.id DESC LIMIT 1
            ) AS cv_profile_version
        FROM applications a
        LEFT JOIN application_events e ON e.application_id = a.id AND e.user_id = a.user_id
        WHERE {where_sql}
        GROUP BY a.id, a.job_title, a.user_id
        ORDER BY a.id DESC
        LIMIT ?
    """  # noqa: S608 — where_sql/interview_placeholders are built from constants, never user input

    params: list[Any] = [
        settings.STATS_APPLIED_EVENT_TYPE,
        settings.STATS_REPLIED_EVENT_TYPE,
        *interview_types,
        settings.STATS_OFFER_EVENT_TYPE,
        settings.STATS_REJECTED_EVENT_TYPE,
        *where_params,
        limit + 1,
    ]
    cur = await db._db.execute(sql, params)
    rows = [dict(r) for r in await cur.fetchall()]
    applications_truncated = len(rows) > limit
    rows = rows[:limit]

    overall = _summarize(rows)

    by_cv_version, cv_truncated = _group(
        rows, key_field="cv_label", label_key="label", with_profile_versions=True
    )
    by_role, role_truncated = _group(
        rows, key_field="job_title", label_key="role", with_profile_versions=False
    )

    return {
        "since": since_val,
        "overall": overall,
        "by_cv_version": by_cv_version,
        "by_role": by_role,
        "groups_truncated": cv_truncated or role_truncated,
        "applications_truncated": applications_truncated,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
