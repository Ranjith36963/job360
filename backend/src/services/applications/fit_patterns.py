"""The ``patterns`` and ``skill_gaps`` views of ``stats`` (feedback slice 5).

COUNTING ONLY. Job360 counts what the assistant saved with ``save_fit`` and
what the user's applications did; it never ranks or recommends a job or a
company (rule M1) and states no cause. Slice 3 owns the ``stats(view=...)``
dispatcher; ``stats.compute_stats`` calls ``load`` once and the two functions
below.

References (career-ops, opened 2026-10-16):
* ``analyze-patterns.mjs`` - ``MIN_THRESHOLD = 5`` (:96, gate :1382-1388),
  ``classifyOutcome`` (:113-127), ``MIN_DECIDED_FOR_RECOMMENDATION = 2``
  (:161-164: employer silence is not evidence either way), ``withOutcomeRates``
  (:181-191), ``breakdownBy`` (:194-206), ``bestDecidedSegment`` (:214-219),
  ``worstDecidedSegment`` (:226-230), ``SUBMITTED_STATUSES`` (:278),
  ``ADVANCED_STATUSES`` (:283), the +10 points lead (:1569, :1585).
* ``upskill.mjs`` - ``LOW_FIT_SCORE`` (:98), gate (:483-490), known skills
  skipped (:349-352), tiers (:385-388), order (:395).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from src.core import settings
from src.services.applications.fit_record import fit_view
from src.services.applications.stats import _SIMULATED_SQL, NOT_SET_LABEL, _norm

# Outcome of an application from its CURRENT status (analyze-patterns.mjs:113-127,
# ADVANCED_STATUSES :283). A rejection is the only negative; `ghosted` is silence
# - ADAPTED: career-ops has no ghosted state; per :161-164 silence is not
# evidence, so it is submitted but never decided and never called a rejection.
_POSITIVE = frozenset({
    "replied", "interview_requested", "interview_scheduled", "interview_done",
    "offer", "hired", "offer_declined",
})
_NEGATIVE = frozenset({"rejected"})
_NO_ANSWER = frozenset({"ghosted"})

_NOTE = (
    "Counts from your own applications, not causes. Group similar reasons yourself. "
    "Job360 never ranks or recommends a job. Ghosted is no answer here; the calibration "
    "view counts it as a negative outcome."
)
_SKILL_NOTE = "Gaps you recorded in save_fit, counted. Never add a skill to a CV the user does not have."
_TRUNCATED_HINT = "pass since=YYYY-MM-DD"


class Rows(list):  # type: ignore[type-arg]
    """The applications read for the two views; ``truncated`` = only the newest
    STATS_MAX_APPLICATIONS were read."""

    truncated: bool = False


async def load(db: Any, user_id: str, since_val: Optional[str]) -> tuple[Rows, list[dict[str, Any]]]:
    """TWO queries, ``user_id = ?`` on every table (rule 12).

    1. the user's applications - the same scope as ``compute_stats`` (``since``
       on created_at, simulations excluded, newest STATS_MAX_APPLICATIONS);
    2. the payloads of their non-retracted ``rejected`` events (slice-1 key
       ``reasons``, ``spine.validate_outcome_payload``).
    Every event type is a bound parameter."""
    where = ["a.user_id = ?"]
    params: list[Any] = [user_id]
    if since_val:
        where.append("a.created_at >= ?")
        params.append(since_val)
    where.append("NOT " + _SIMULATED_SQL.format(app="a.id"))
    params.extend([user_id, settings.SIMULATION_EVENT_TYPE])
    where_sql = " AND ".join(where)
    limit = settings.STATS_MAX_APPLICATIONS
    sql = f"""
        SELECT a.id, a.status, a.job_remote, a.fit_recorded_at, a.fit_score, a.fit_gaps,
               a.fit_gaps_detail, a.fit_archetype, a.fit_remote_policy, a.fit_team_size, a.fit_via,
               CASE WHEN EXISTS (
                   SELECT 1 FROM application_events e
                   WHERE e.application_id = a.id AND e.user_id = a.user_id AND e.event_type = ?
                     AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id)
               ) THEN 1 ELSE 0 END AS applied
        FROM applications a
        WHERE {where_sql}
        ORDER BY a.id DESC
        LIMIT ?
    """  # noqa: S608 - where_sql is built from constants, never user input
    cur = await db._db.execute(sql, [settings.STATS_APPLIED_EVENT_TYPE, *params, limit + 1])
    fetched = [dict(r) for r in await cur.fetchall()]
    rows = Rows(fetched[:limit])
    rows.truncated = len(fetched) > limit
    ids = {int(r["id"]) for r in rows}
    reasons_sql = (
        "SELECT e.application_id, e.payload FROM application_events e "  # noqa: S608 - constants only
        "JOIN applications a ON a.id = e.application_id AND a.user_id = e.user_id "
        f"WHERE e.user_id = ? AND e.event_type = ? AND {where_sql} "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        "ORDER BY e.id"
    )
    cur = await db._db.execute(reasons_sql, [user_id, settings.STATS_REJECTED_EVENT_TYPE, *params])
    reasons = [dict(r) for r in await cur.fetchall() if int(r["application_id"]) in ids]
    return rows, reasons


# ── patterns ────────────────────────────────────────────────────────────────


def _is_submitted(row: dict[str, Any]) -> bool:
    return bool(row.get("applied")) and row.get("status") != "withdrawn"


def _outcome(status: Optional[str]) -> str:
    if status in _POSITIVE:
        return "positive"
    if status in _NEGATIVE:
        return "negative"
    if status in _NO_ANSWER:
        return "no_answer"
    return "awaiting"


def _outcome_row(key: Optional[str], label: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"positive": 0, "negative": 0, "awaiting": 0, "no_answer": 0}
    for m in members:
        counts[_outcome(m.get("status"))] += 1
    submitted = len(members)
    decided = counts["positive"] + counts["negative"]
    return {
        "key": key, "label": label, "submitted": submitted, **counts, "decided": decided,
        "conversion_rate": round(counts["positive"] / submitted, 3) if submitted else None,
        "decided_rate": round(counts["positive"] / decided, 3) if decided else None,
        "small_sample": submitted < settings.STATS_SMALL_SAMPLE_N,
    }


def _team_bucket(size: Any) -> Optional[str]:
    if size is None:
        return None
    low, high = settings.FIT_TEAM_SIZE_BUCKETS
    n = int(size)
    if n <= low:
        return f"1-{low}"
    if n <= high:
        return f"{low + 1}-{high}"
    return f"{high + 1}+"


def _breakdown(
    submitted: list[dict[str, Any]], key_of: Any, label_of: Any
) -> tuple[list[dict[str, Any]], bool]:
    """analyze-patterns.mjs:194-206. Order: count DESC, key ASC, null last; capped."""
    buckets: dict[Optional[str], list[dict[str, Any]]] = {}
    for row in sorted(submitted, key=lambda r: int(r["id"])):  # earliest application first
        buckets.setdefault(key_of(row), []).append(row)
    groups = []
    for key, members in buckets.items():
        label = NOT_SET_LABEL if key is None else label_of(key, members[0])
        groups.append(_outcome_row(key, label, members))
    groups.sort(key=lambda g: (-g["submitted"], g["key"] is None, g["key"] or ""))
    return groups[: settings.STATS_MAX_GROUPS], len(groups) > settings.STATS_MAX_GROUPS


def _remote_key(row: dict[str, Any]) -> Optional[str]:
    policy = row.get("fit_remote_policy")
    if policy:
        return str(policy)
    # A stored fact (job_remote), not a guess; anything else stays "Not set".
    return "remote" if row.get("job_remote") else None


def _pct(rate: float) -> int:
    return int(round(rate * 100))


def _signals(dimension: str, groups: list[dict[str, Any]], overall: dict[str, Any]) -> list[dict[str, Any]]:
    """<= 2 per dimension, never from a null group (:214-230)."""
    named = [g for g in groups if g["key"] is not None]
    out: list[dict[str, Any]] = []
    best_pool = [
        g for g in named if g["decided"] >= settings.PATTERNS_MIN_DECIDED and (g["decided_rate"] or 0) > 0
    ]
    best_pool.sort(key=lambda g: (-g["decided_rate"], -g["decided"], g["key"]))
    if best_pool:
        out.append(_signal(dimension, "best", best_pool[0], overall))
    worst_pool = [
        g for g in named if g["positive"] == 0 and g["decided"] >= settings.PATTERNS_MIN_DECIDED
    ]
    worst_pool.sort(key=lambda g: (-g["decided"], g["key"]))
    if worst_pool:
        out.append(_signal(dimension, "no_advance", worst_pool[0], overall))
    return out


def _signal(dimension: str, kind: str, g: dict[str, Any], overall: dict[str, Any]) -> dict[str, Any]:
    points: Optional[int] = None
    if g["decided"] and overall["decided"]:
        points = int(round((g["positive"] / g["decided"] - overall["positive"] / overall["decided"]) * 100))
    if kind == "best":
        text = (
            f'"{g["label"]}": {g["positive"]} of {g["decided"]} decided applications advanced '
            f'({_pct(g["decided_rate"])}%); overall {_pct(overall["decided_rate"])}%.'
            if overall["decided_rate"] is not None
            else f'"{g["label"]}": {g["positive"]} of {g["decided"]} decided applications advanced.'
        )
    else:
        text = f'"{g["label"]}": none of {g["decided"]} decided applications advanced ({g["submitted"]} sent).'
    return {
        "dimension": dimension, "kind": kind, "key": g["key"], "label": g["label"],
        "submitted": g["submitted"], "decided": g["decided"], "positive": g["positive"],
        "decided_rate": g["decided_rate"], "points_vs_overall": points,
        "clear_lead": kind == "best" and points is not None and points >= settings.PATTERNS_LEAD_POINTS,
        "text": text,
    }


def _reason_list(
    reasons: list[dict[str, Any]], submitted_ids: set[int]
) -> tuple[list[dict[str, Any]], int]:
    """Exact words only - no fixed list, no keyword buckets (decision #3). A
    reason key is ``_norm(reason)``, counted once per application."""
    by_key: dict[str, dict[str, Any]] = {}
    with_reason: set[int] = set()
    for ev in reasons:
        app_id = int(ev["application_id"])
        if app_id not in submitted_ids:
            continue
        try:
            payload = json.loads(ev.get("payload") or "{}")
        except (TypeError, ValueError):
            continue
        items = payload.get("reasons") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        for raw in items:
            if not isinstance(raw, str) or _norm(raw) is None:
                continue
            with_reason.add(app_id)
            slot = by_key.setdefault(str(_norm(raw)), {"reason": raw.strip(), "ids": set()})
            slot["ids"].add(app_id)
    listed = [
        {"reason": v["reason"], "count": len(v["ids"]), "application_ids": sorted(v["ids"])[:10]}
        for v in by_key.values()
    ]
    listed.sort(key=lambda r: (-r["count"], r["reason"]))
    return listed[: settings.PATTERNS_REASONS_MAX], len(with_reason)


def patterns(rows: list[dict[str, Any]], reasons: list[dict[str, Any]]) -> dict[str, Any]:
    submitted = [r for r in rows if _is_submitted(r)]
    n = len(submitted)
    threshold = settings.PATTERNS_MIN_SUBMITTED
    if n < threshold:
        return {
            "available": False, "submitted": n, "threshold": threshold,
            "text": f"Not enough data yet: {n}/{threshold} applications sent. "
                    "Keep applying and come back when you have more outcomes.",
            "withdrawn_excluded": None, "overall": None, "by_archetype": None, "by_remote_policy": None,
            "by_team_size": None, "by_via": None, "signals": None, "rejection_reasons": None,
            "reason_base": None, "groups_truncated": None, "note": None,
        }
    withdrawn = sum(1 for r in rows if r.get("applied") and r.get("status") == "withdrawn")
    overall = _outcome_row(None, "All sent applications", submitted)
    raw_label = lambda _k, first: str(first.get("fit_archetype") or "").strip()  # noqa: E731
    by_archetype, t1 = _breakdown(submitted, lambda r: _norm(r.get("fit_archetype")), raw_label)
    by_remote, t2 = _breakdown(submitted, _remote_key, lambda k, _f: k)
    by_team, t3 = _breakdown(submitted, lambda r: _team_bucket(r.get("fit_team_size")), lambda k, _f: k)
    by_via, t4 = _breakdown(submitted, lambda r: r.get("fit_via") or None, lambda k, _f: k)
    signals: list[dict[str, Any]] = []
    for dim, groups in (
        ("archetype", by_archetype), ("remote_policy", by_remote), ("team_size", by_team), ("via", by_via),
    ):
        signals.extend(_signals(dim, groups, overall))
    rejection_reasons, reason_base = _reason_list(reasons, {int(r["id"]) for r in submitted})
    truncated = t1 or t2 or t3 or t4
    out: dict[str, Any] = {
        "available": True, "submitted": n, "withdrawn_excluded": withdrawn, "threshold": threshold,
        "overall": overall, "by_archetype": by_archetype, "by_remote_policy": by_remote,
        "by_team_size": by_team, "by_via": by_via, "signals": signals,
        "rejection_reasons": rejection_reasons, "reason_base": reason_base,
        "groups_truncated": truncated, "note": _NOTE,
    }
    if truncated:
        out["truncated_hint"] = _TRUNCATED_HINT
    return out


# ── skill_gaps ──────────────────────────────────────────────────────────────


def _tier(low_fit: int, total_low: int) -> str:
    share = low_fit / total_low if total_low else 0.0
    if share >= 0.5 and low_fit >= 3:
        return "Critical"
    if share >= 0.3 and low_fit >= 2:
        return "High"
    if low_fit >= 2:
        return "Medium"
    return "Low"


def skill_gaps(rows: list[dict[str, Any]], known_skills: list[str]) -> dict[str, Any]:
    fits = [r for r in rows if r.get("fit_recorded_at")]
    scored = [r for r in fits if r.get("fit_score") is not None]
    threshold = settings.SKILL_GAPS_MIN_SCORED
    if len(scored) < threshold:
        return {
            "available": False, "scored": len(scored), "threshold": threshold,
            "text": f"Not enough data: {len(scored)}/{threshold} scored fits. Judge more jobs and come back.",
            "fits_read": None, "total_low_fit": None, "low_fit_score": None, "gaps": None,
            "excluded_as_known": None, "truncated": None, "note": None,
        }
    line = settings.SKILL_GAPS_LOW_FIT_SCORE
    known = {k for k in (_norm(s) for s in known_skills) if k}
    total_low = sum(1 for r in scored if r["fit_score"] < line)
    kind_rank = {"hard": 2, "soft": 1, None: 0}
    buckets: dict[str, dict[str, Any]] = {}
    excluded: set[str] = set()
    for row in sorted(fits, key=lambda r: int(r["id"])):  # earliest application first
        fit = fit_view(row) or {}
        per_app: dict[str, dict[str, Any]] = {}
        for item in fit.get("gaps_detail", []):
            key = _norm(item.get("text"))
            if key is None:
                continue
            if key in known:
                excluded.add(key)
                continue
            cur_item = per_app.get(key)
            if cur_item is None or kind_rank[item.get("kind")] > kind_rank[cur_item.get("kind")]:
                per_app[key] = item
        score = row.get("fit_score")
        for key, item in per_app.items():
            slot = buckets.setdefault(key, {
                "key": key, "gap": str(item["text"]).strip(), "applications": 0, "low_fit": 0,
                "hard": 0, "soft": 0, "unmarked": 0, "weighted": 0.0, "application_ids": [],
            })
            slot["applications"] += 1
            if score is not None and score < line:
                slot["low_fit"] += 1
            slot[item.get("kind") or "unmarked"] += 1
            slot["weighted"] += (100 - score) / 20 if score is not None else 1.0
            if len(slot["application_ids"]) < 20:
                slot["application_ids"].append(int(row["id"]))
    gaps = []
    for slot in buckets.values():
        slot["weighted"] = round(slot["weighted"], 2)
        slot["tier"] = _tier(slot["low_fit"], total_low)
        gaps.append(slot)
    gaps.sort(key=lambda g: (-g["weighted"], -g["applications"], g["key"]))
    truncated = len(gaps) > settings.SKILL_GAPS_MAX
    out: dict[str, Any] = {
        "available": True, "fits_read": len(fits), "scored": len(scored), "total_low_fit": total_low,
        "low_fit_score": line, "gaps": gaps[: settings.SKILL_GAPS_MAX],
        "excluded_as_known": len(excluded), "truncated": truncated, "note": _SKILL_NOTE,
    }
    if truncated:
        out["truncated_hint"] = _TRUNCATED_HINT
    return out
