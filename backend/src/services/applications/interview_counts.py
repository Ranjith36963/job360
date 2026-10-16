"""Feedback loop slice 4 (interview side): the counting.

Three views for ``stats(view=...)`` (``digest``, ``friction``, ``assessments``)
and the ``silent_after_interview`` list for doctor. Pure functions over plain
rows plus one async loader. Job360 only COUNTS what the assistant recorded: no
judging, no wording beyond fixed labels (hard rule M2). Every view leaves out
retracted events (the target of any ``corrects_event_id``) and simulation
applications, and reads only the caller's own rows.

career-ops references: weekly-digest.mjs (digest), process-quality.mjs and
interview-redflag.md (friction), assessment-log.mjs (assessments),
rejection-latency.mjs (silence). Bounds marked NOT IN REFERENCE are ours.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING, Any, Iterable, Optional

from src.core import settings
from src.services.applications import spine
from src.services.applications.stats import _SIMULATED_SQL
from src.services.applications.status import _event_when
from src.utils.logger import get_audit_logger, get_logger, safe_log_value

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase

log = get_logger(__name__)

INTERVIEW_VIEWS = ("digest", "friction", "assessments")

NOT_SET_LABEL = "Company not set"
SILENT_DISCLAIMER = "Elapsed time only — not legal advice or a claim that the employer did anything wrong."
FRICTION_NOTE = (
    "Elapsed counts of what you recorded about the process, never about a named person. Not legal advice."
)
REDFLAG_LABELS = {  # interview-redflag.md:127-131
    "none": "No structural red flags detected",
    "caution": "Enter with eyes open",
    "warning": "Reconsider",
}
SILENT_STATUSES = ("interview_requested", "interview_scheduled", "interview_done")  # rejection-latency.mjs:15-18
TRUNCATED_HINT = "pass since=YYYY-MM-DD to narrow"


# ── small pure helpers ───────────────────────────────────────────────────────


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _company(row: dict[str, Any]) -> tuple[str, str]:
    """(group key, label). A blank or ``unknown`` company is its own ``app:<id>`` group."""
    normalized = str(row.get("normalized_company") or "").strip().lower()
    if not normalized or normalized == "unknown":
        return f"app:{row['application_id']}", NOT_SET_LABEL
    return normalized, str(row.get("job_company") or "").strip() or normalized


def _since_date(since: Optional[str]) -> Optional[date]:
    if not since:
        return None
    try:
        return date.fromisoformat(str(since)[:10])
    except ValueError:
        return None


def _event_date(row: dict[str, Any]) -> str:
    when = _event_when(row)
    return when.date().isoformat() if when else str(row.get("occurred_at") or "")[:10]


def _tag_counts(tags: Iterable[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for tag in tags:
        counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _num(value: Any) -> Optional[float]:
    return None if isinstance(value, bool) or not isinstance(value, (int, float)) else value


# ── the loader ───────────────────────────────────────────────────────────────


async def _load(
    db: JobDatabase,
    user_id: str,
    *,
    event_types: Optional[tuple[str, ...]] = None,
    statuses: Optional[tuple[str, ...]] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """The caller's non-retracted events on non-simulated applications, oldest first,
    with the application's company columns. One more than the cap is read so
    ``truncated`` is a fact. Never raises for an empty history."""
    where = [
        "e.user_id = ?",
        "NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id)",
        "NOT " + _SIMULATED_SQL.format(app="a.id"),
    ]
    params: list[Any] = [user_id, user_id, settings.SIMULATION_EVENT_TYPE]
    if event_types:
        where.append(f"e.event_type IN ({', '.join('?' for _ in event_types)})")
        params.extend(event_types)
    if statuses:
        where.append(f"a.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    limit = settings.INTERVIEW_VIEW_MAX_EVENTS
    sql = (
        "SELECT e.id, e.application_id, e.event_type, e.payload, e.occurred_at, e.recorded_at, "  # noqa: S608
        "e.source_message_id, a.status, a.job_title, a.job_company, j.normalized_company "
        "FROM application_events e "
        "JOIN applications a ON a.id = e.application_id AND a.user_id = e.user_id "
        "LEFT JOIN jobs j ON j.id = a.job_id "
        f"WHERE {' AND '.join(where)} ORDER BY e.id LIMIT ?"
    )
    cur = await db._db.execute(sql, [*params, limit + 1])
    rows = [dict(r) for r in await cur.fetchall()]
    truncated = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        row["payload"] = _payload(row.get("payload"))
        row["id"] = int(row["id"])
        row["application_id"] = int(row["application_id"])
    return rows, truncated


async def _newest_artifact(
    db: JobDatabase, user_id: str, kind: str
) -> list[dict[str, Any]]:
    """Every non-simulated artifact of one kind, newest first (callers take what they need)."""
    cur = await db._db.execute(
        "SELECT art.id, art.application_id, art.version_no, art.created_at, j.normalized_company "  # noqa: S608
        "FROM application_artifacts art "
        "JOIN applications a ON a.id = art.application_id AND a.user_id = art.user_id "
        "LEFT JOIN jobs j ON j.id = a.job_id "
        "WHERE art.user_id = ? AND art.kind = ? AND NOT " + _SIMULATED_SQL.format(app="a.id")
        + " ORDER BY art.created_at DESC, art.id DESC",
        (user_id, kind, user_id, settings.SIMULATION_EVENT_TYPE),
    )
    return [dict(r) for r in await cur.fetchall()]


def _pointer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "application_id": int(row["application_id"]), "artifact_id": int(row["id"]),
        "version_no": int(row["version_no"]), "created_at": row["created_at"],
    }


# ── digest (feature 12) ──────────────────────────────────────────────────────


def _digest(
    rows: list[dict[str, Any]], week_from: date, week_to: date, zone_name: str,
    story_bank: Optional[dict[str, Any]], truncated: bool,
) -> dict[str, Any]:
    sessions = [r for r in rows if r["event_type"] == "interview_session"]
    in_week: dict[int, list[dict[str, Any]]] = {}
    for r in sessions:
        day = str(r["payload"].get("date") or "")
        if week_from.isoformat() <= day <= week_to.isoformat():
            in_week.setdefault(r["application_id"], []).append(r)

    applications: list[dict[str, Any]] = []
    companies: set[str] = set()
    all_tags: list[str] = []
    for app_id, group in in_week.items():
        key, label = _company(group[0])
        companies.add(key)
        rounds = []
        for r in sorted(group, key=lambda r: (str(r["payload"].get("date")), str(r["payload"].get("round")), r["id"])):
            p = r["payload"]
            tags = [t for t in (p.get("competency_tags") or []) if isinstance(t, str)]
            all_tags.extend(tags)
            rounds.append({
                "event_id": r["id"], "round": p.get("round"), "date": p.get("date"),
                "source": p.get("source") or "debrief", "interviewer_role": p.get("interviewer_role"),
                "competency_tags": tags,
            })
        applications.append({
            "application_id": app_id, "company": label, "job_title": group[0].get("job_title") or "",
            "rounds": rounds,
        })
    applications.sort(key=lambda a: (a["company"].casefold(), a["job_title"].casefold(), a["application_id"]))

    counts = _tag_counts(all_tags)
    open_gaps: list[dict[str, Any]] = []
    for app_id in in_week:
        history = sorted(
            (
                r for r in sessions
                if r["application_id"] == app_id and str(r["payload"].get("date") or "") <= week_to.isoformat()
            ),
            key=lambda r: (str(r["payload"].get("date")), r["id"]),
        )
        latest: dict[str, tuple[str, str]] = {}
        for r in history:
            for gap in r["payload"].get("gaps") or []:
                if isinstance(gap, dict) and isinstance(gap.get("topic"), str):
                    latest[gap["topic"].strip().casefold()] = (gap["topic"].strip(), str(gap.get("status")))
        topics = sorted(t for t, status in latest.values() if status == "gap")
        if topics:
            open_gaps.append({
                "application_id": app_id, "company": _company(in_week[app_id][0])[1], "topics": topics,
            })
    open_gaps.sort(key=lambda g: (-len(g["topics"]), g["company"].casefold(), g["application_id"]))

    out: dict[str, Any] = {
        "view": "digest",
        "week": {"from": week_from.isoformat(), "to": week_to.isoformat(), "timezone": zone_name},
        "sessions": sum(len(g) for g in in_week.values()),
        "companies": len(companies),
        "applications": applications,
        "competency_tag_counts": counts,
        "recurring_competencies": [c for c in counts if c["count"] >= settings.DIGEST_RECURRING_MIN],
        "open_gaps": open_gaps,
        "story_bank": story_bank,
        "truncated": truncated,
    }
    if truncated:
        out["truncated_hint"] = TRUNCATED_HINT
    return out


# ── friction + redflags (features 14, 16) ────────────────────────────────────


def _level(points: int) -> str:
    return "warning" if points >= 4 else "caution" if points >= 2 else "none"


def _friction(
    rows: list[dict[str, Any]], since: Optional[date], analyses: dict[str, dict[str, Any]], truncated: bool
) -> dict[str, Any]:
    if since is not None:
        rows = [r for r in rows if _event_date(r) >= since.isoformat()]
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        key, label = _company(r)
        g = groups.setdefault(key, {"company_key": key, "company": label, "apps": {}, "friction": [], "sessions": []})
        per_app = g["apps"].setdefault(r["application_id"], {"done": 0, "session": 0})
        if r["event_type"] == "interview_done":
            per_app["done"] += 1
        elif r["event_type"] == "interview_session":
            per_app["session"] += 1
            g["sessions"].append(r)
        elif r["event_type"] == "process_friction":
            g["friction"].append(r)

    companies: list[dict[str, Any]] = []
    redflags: list[dict[str, Any]] = []
    for key, g in groups.items():
        rounds = sum(max(a["done"], a["session"]) for a in g["apps"].values())
        count = len(g["friction"])
        if rounds >= settings.FRICTION_MIN_ROUNDS or count:
            reasons = [
                str(r["payload"]["reason"])
                for r in sorted(g["friction"], key=lambda r: (str(r["occurred_at"]), r["id"]))
                if r["payload"].get("reason")
            ]
            companies.append({
                "company_key": key, "company": g["company"], "rounds": rounds, "friction_count": count,
                "friction_rate": round(count / rounds, 2) if rounds else None, "reasons": reasons,
            })
        per_signal: dict[str, list[dict[str, Any]]] = {}
        for s in g["sessions"]:
            seen: set[str] = set()
            for item in s["payload"].get("signals") or []:
                name = item.get("signal") if isinstance(item, dict) else None
                if isinstance(name, str) and name not in seen:
                    seen.add(name)
                    per_signal.setdefault(name, []).append({"event_id": s["id"], "quote": item.get("quote")})
        if per_signal:
            points = min(10, sum(1 if len(v) == 1 else 2 for v in per_signal.values()))
            level = _level(points)
            redflags.append({
                "company_key": key, "company": g["company"], "points": points, "level": level,
                "label": REDFLAG_LABELS[level],
                "signals": [
                    {"signal": name, "sessions": len(v), "severity": "single" if len(v) == 1 else "pattern",
                     "quotes": v}
                    for name, v in sorted(per_signal.items())
                ],
                "analysis": analyses.get(key),
            })
    companies.sort(key=lambda c: (
        -c["friction_count"], c["friction_rate"] is None, -(c["friction_rate"] or 0), c["company"].casefold(),
    ))
    redflags.sort(key=lambda c: (-c["points"], c["company"].casefold()))
    out: dict[str, Any] = {
        "view": "friction", "since": since.isoformat() if since else None,
        "companies": companies, "redflags": redflags, "note": FRICTION_NOTE, "truncated": truncated,
    }
    if truncated:
        out["truncated_hint"] = TRUNCATED_HINT
    return out


# ── assessments (feature 15) ─────────────────────────────────────────────────


def _assessments(rows: list[dict[str, Any]], since: Optional[date], truncated: bool) -> dict[str, Any]:
    if since is not None:
        rows = [r for r in rows if _event_date(r) >= since.isoformat()]
    items: list[dict[str, Any]] = []
    by_platform: dict[str, dict[str, Any]] = {}
    quality = {"total": 0, "stale_flagged": 0, "without_score": 0, "without_threshold": 0}
    for r in sorted(rows, key=lambda r: r["id"]):  # oldest first: the first spelling seen names a platform
        p = r["payload"]
        score, threshold = _num(p.get("score_pct")), _num(p.get("threshold_pct"))
        outcome = "unknown" if score is None or threshold is None else ("passed" if score >= threshold else "failed")
        stale = bool(str(p.get("stale_note") or "").strip())
        platform = str(p.get("platform") or "").strip()
        items.append({
            "event_id": r["id"], "application_id": r["application_id"], "company": _company(r)[1],
            "date": _event_date(r), "platform": platform, "subject": p.get("subject"),
            "threshold_pct": threshold, "score_pct": score, "stale_note": p.get("stale_note"), "outcome": outcome,
        })
        group = by_platform.setdefault(platform.casefold(), {
            "platform": platform, "count": 0, "stale_flagged": 0, "passed": 0, "failed": 0, "unknown_outcome": 0,
        })
        group["count"] += 1
        group["stale_flagged"] += int(stale)
        group["passed" if outcome == "passed" else "failed" if outcome == "failed" else "unknown_outcome"] += 1
        quality["total"] += 1
        quality["stale_flagged"] += int(stale)
        quality["without_score"] += int(score is None)
        quality["without_threshold"] += int(threshold is None)
    items.sort(key=lambda i: (i["date"], i["event_id"]), reverse=True)
    out: dict[str, Any] = {
        "view": "assessments", "since": since.isoformat() if since else None,
        "assessments": items, "by_platform": by_platform, "quality": quality, "truncated": truncated,
    }
    if truncated:
        out["truncated_hint"] = TRUNCATED_HINT
    return out


# ── silence after an interview (feature 7) ───────────────────────────────────


def silent_after_interview(
    apps: Iterable[dict[str, Any]], *, today: date, courtesy_days: int, zone: tzinfo = timezone.utc,
) -> list[dict[str, Any]]:
    """Pure. ``apps`` = ``{application_id, company, job_title, status, events: [...]}`` where
    events are non-retracted rows (``id, event_type, payload, occurred_at, recorded_at, source_message_id``)."""
    # `brought` is when the ad was saved, not news from the employer (as in ghosting.py).
    status_types = set(settings.APPLICATION_STATUS_EVENT_TYPES) - {"brought"}
    items: list[dict[str, Any]] = []
    for app in apps:
        if app.get("status") not in SILENT_STATUSES:
            continue
        events = app.get("events") or []
        done = [e for e in events if e["event_type"] == "interview_done"]
        if not done:
            continue
        last = max(done, key=lambda e: (_event_when(e) or datetime.min.replace(tzinfo=timezone.utc), e["id"]))
        when = _event_when(last)
        if when is None:
            continue
        later_news = False
        for e in events:
            if e["id"] == last["id"]:
                continue
            e_when = _event_when(e)
            if e_when is not None and e_when > when and (e["event_type"] in status_types or e.get("source_message_id")):
                later_news = True
                break
        if later_news:
            continue
        days = (today - when.astimezone(zone).date()).days
        if days <= courtesy_days:  # strict (rejection-latency.mjs:421); also covers a future interview (:420)
            continue
        confirmed = any(
            e["event_type"] == "note" and e["id"] > last["id"]
            and _payload(e.get("payload")).get("silence_confirmed") is True
            for e in events
        )
        items.append({
            "application_id": int(app["application_id"]), "company": app.get("company") or "",
            "job_title": app.get("job_title") or "", "last_interview_event_id": int(last["id"]),
            "last_interview_at": when.isoformat(), "days_silent": days, "confirmed": confirmed,
        })
    items.sort(key=lambda i: (-i["days_silent"], i["application_id"]))
    return items[: settings.INTERVIEW_SILENT_MAX_ITEMS]


async def silent_list(db: JobDatabase, user_id: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """The doctor block: ``{courtesy_days, items, disclaimer}``. Read-only."""
    zone = await spine.user_zone(db, user_id)
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    rows, truncated = await _load(db, user_id, statuses=SILENT_STATUSES)
    apps: dict[int, dict[str, Any]] = {}
    for r in rows:
        app = apps.setdefault(r["application_id"], {
            "application_id": r["application_id"], "company": _company(r)[1],
            "job_title": r.get("job_title") or "", "status": r["status"], "events": [],
        })
        app["events"].append(r)
    items = silent_after_interview(
        apps.values(), today=today, courtesy_days=settings.INTERVIEW_COURTESY_DAYS, zone=zone,
    )
    get_audit_logger().info(
        "silent_after_interview",
        extra={"event": "silent_after_interview", "user_id": safe_log_value(user_id), "items": len(items),
               "unconfirmed": sum(1 for i in items if not i["confirmed"]), "truncated": truncated},
    )
    return {"courtesy_days": settings.INTERVIEW_COURTESY_DAYS, "items": items, "disclaimer": SILENT_DISCLAIMER}


# ── the one entry point ──────────────────────────────────────────────────────


async def build_view(
    db: JobDatabase, user_id: str, view: str, *, since: Optional[str] = None, now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Slice 3's ``stats`` dispatcher calls this for ``digest`` / ``friction`` / ``assessments``."""
    if view not in INTERVIEW_VIEWS:
        raise ValueError(f"unknown interview view: {view!r}")
    since_day = _since_date(since)
    try:
        if view == "digest":
            zone = await spine.user_zone(db, user_id)
            anchor = since_day or (now or datetime.now(timezone.utc)).astimezone(zone).date()
            week_from = anchor - timedelta(days=anchor.weekday())
            rows, truncated = await _load(db, user_id, event_types=("interview_session",))
            artifacts = await _newest_artifact(db, user_id, "story_bank")
            result = _digest(
                rows, week_from, week_from + timedelta(days=6), getattr(zone, "key", str(zone)),
                _pointer(artifacts[0]) if artifacts else None, truncated,
            )
        elif view == "friction":
            rows, truncated = await _load(
                db, user_id, event_types=("interview_done", "interview_session", "process_friction"),
            )
            analyses: dict[str, dict[str, Any]] = {}
            for art in await _newest_artifact(db, user_id, "redflag_analysis"):
                key, _ = _company(
                    {"application_id": art["application_id"], "normalized_company": art["normalized_company"]}
                )
                analyses.setdefault(key, _pointer(art))  # newest first, so the first one seen wins
            result = _friction(rows, since_day, analyses, truncated)
        else:
            rows, truncated = await _load(db, user_id, event_types=("assessment",))
            result = _assessments(rows, since_day, truncated)
    except Exception:
        log.warning(
            "interview_view_failed",
            extra={"event": "interview_view_failed", "user_id": safe_log_value(user_id), "view": view},
            exc_info=True,
        )
        raise
    get_audit_logger().info(
        "interview_view",
        extra={"event": "interview_view", "user_id": safe_log_value(user_id), "view": view,
               "rows": len(rows), "truncated": truncated},
    )
    return result
