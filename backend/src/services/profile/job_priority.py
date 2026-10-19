"""JOB PRIORITY (owner decision 2026-10-19) - the user's own order of search effort.

Each ``user_info.right_to_work`` record may carry ``remote_priority`` and
``onsite_priority`` (``PRIORITY_LEVELS``). Job360 never ranks a job (VISION
rule 4): this only restates the user's choice per PLACE, so the assistant knows
where to spend its hunt, and the user can filter their own applications by it.

A place = (record, kind): ``remote`` or ``onsite`` for one country, or for ``ANY``
(every other country). A job belongs to the record of its own country, else to
``ANY``. A level the user never chose is ``None`` - rule #29, never a default.

Pure: no I/O.
"""
from __future__ import annotations

from typing import Any, Optional

from src.services.profile.user_info import ANY_COUNTRY, PRIORITY_LEVELS

REMOTE, ONSITE = "remote", "onsite"
PRIORITY_KEYS = {REMOTE: "remote_priority", ONSITE: "onsite_priority"}
OFF = "off"
NOT_SET = "not_set"
# Effort weights for the hunt split. A place with no level chosen gets the middle
# weight ("even share") so it is neither favoured nor dropped; "off" is never searched.
_WEIGHT = {"highest": 4, "high": 3, "medium": 2, "low": 1, OFF: 0}
_UNSET_WEIGHT = 2
FILTER_VALUES = (*PRIORITY_LEVELS, NOT_SET)


def _records(right_to_work: Any) -> list[dict[str, Any]]:
    recs = right_to_work.get("countries") if isinstance(right_to_work, dict) else None
    return [r for r in (recs or []) if isinstance(r, dict) and isinstance(r.get("country"), str)]


def level_of(record: dict[str, Any], kind: str) -> Optional[str]:
    """The user's level for this place, or ``None`` when not chosen."""
    value = record.get(PRIORITY_KEYS[kind])
    return value if value in PRIORITY_LEVELS else None


def record_for(right_to_work: Any, country: Optional[str]) -> Optional[dict[str, Any]]:
    """The record a job in ``country`` belongs to: its own country's, else ``ANY``."""
    recs = _records(right_to_work)
    code = (country or "").strip().upper()
    for r in recs:
        if code and r["country"] == code:
            return r
    return next((r for r in recs if r["country"] == ANY_COUNTRY), None)


def place_priority(right_to_work: Any, job_country: Optional[str], job_remote: Optional[bool]) -> Optional[str]:
    """The level of the place a job sits in, or ``None`` (remote unknown, no record, or not chosen)."""
    if not isinstance(job_remote, bool):
        return None
    rec = record_for(right_to_work, job_country)
    return level_of(rec, REMOTE if job_remote else ONSITE) if rec else None


def effort_split(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``effort_pct`` to each place (whole numbers summing to 100, ``off`` = 0) and
    return them sorted most-wanted first. ``places`` items carry ``priority`` (level or None)."""
    weights = [(_UNSET_WEIGHT if p["priority"] is None else _WEIGHT[p["priority"]]) for p in places]
    total = sum(weights)
    for p, w in zip(places, weights):
        p["effort_pct"] = round(100 * w / total) if total else 0
    # Most effort first; at equal effort a chosen level before an unchosen one. Stable otherwise.
    return sorted(places, key=lambda p: (-p["effort_pct"], p["priority"] is None))


def filter_sql(right_to_work: Any, level: str) -> tuple[str, list[Any]]:
    """A SQL condition over ``applications`` (``job_country``, ``job_remote``) that keeps the
    rows whose place has ``level`` (or no level: ``not_set``). Values go in params only."""
    recs = _records(right_to_work)
    own = [r["country"] for r in recs if r["country"] != ANY_COUNTRY]

    def place_cond(rec: dict[str, Any], kind: str) -> tuple[str, list[Any]]:
        remote = "job_remote = TRUE" if kind == REMOTE else "job_remote = FALSE"
        if rec["country"] != ANY_COUNTRY:
            return f"({remote} AND UPPER(job_country) = ?)", [rec["country"]]
        if not own:
            return f"({remote})", []
        marks = ",".join("?" for _ in own)
        return f"({remote} AND (job_country IS NULL OR UPPER(job_country) NOT IN ({marks})))", list(own)

    parts: list[str] = []
    params: list[Any] = []
    for rec in recs:
        for kind in (REMOTE, ONSITE):
            lvl = level_of(rec, kind)
            if (lvl is None) if level == NOT_SET else (lvl == level):
                sql, p = place_cond(rec, kind)
                parts.append(sql)
                params.extend(p)
    if level != NOT_SET:
        return ("(" + " OR ".join(parts) + ")", params) if parts else ("1 = 0", [])
    # not_set = a job whose place has no level: remote unknown, no record, or the record has none.
    covered = [r for r in recs if r["country"] != ANY_COUNTRY]
    any_rec = next((r for r in recs if r["country"] == ANY_COUNTRY), None)
    extra = ["job_remote IS NULL"]
    if any_rec is None:
        if covered:
            marks = ",".join("?" for _ in covered)
            extra.append(f"(job_country IS NULL OR UPPER(job_country) NOT IN ({marks}))")
            params.extend(r["country"] for r in covered)
        else:
            extra.append("1 = 1")
    return "(" + " OR ".join([*parts, *extra]) + ")", params
