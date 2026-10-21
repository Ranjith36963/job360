"""Bulk-apply Q1 / Q2 / M1: the batch queue's preflight and summary.

Everything here is DERIVED from facts Job360 already stores (the profile, artifacts, events,
asks, receipts). No table, no ranking: rows keep first-in-first-out order and nothing scores a
job (product rule 4). ``list_applications queued=true`` calls :func:`preflight_rows` for the
rows on the page and :func:`queue_summary` for the user's whole open queue. Always scoped by
``user_id`` (rule #12).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import blocked as blocked_service
from src.services.applications import kit, ready, spine

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

# M1: why a row left the queue. Optional on `unqueued`; a missing reason counts as `other`.
UNQUEUE_REASONS = (
    "blocked", "duplicate", "avoided_company", "cap_reached", "closed_posting", "user_removed", "other",
)


def check_unqueue_payload(payload: dict[str, Any]) -> list[str]:
    """Problems with an ``unqueued`` payload: ``{}`` or ``{"reason": <one of UNQUEUE_REASONS>}``."""
    problems: list[str] = []
    if set(payload) - {"reason"}:
        problems.append("unqueued takes only an optional reason")
    elif "reason" in payload and payload["reason"] not in UNQUEUE_REASONS:
        problems.append(f"unqueued reason must be one of {', '.join(UNQUEUE_REASONS)}")
    return problems


def _marks(ids: list[int]) -> str:
    return ",".join("?" for _ in ids)


async def _blocked_counts(db: JobDatabase, user_id: str, ids: list[int]) -> dict[int, int]:
    """``blocked`` events per application that no later event retracts."""
    if not ids:
        return {}
    cur = await db._db.execute(
        f"SELECT e.application_id, COUNT(*) FROM application_events e "  # noqa: S608 - placeholders only
        f"WHERE e.user_id = ? AND e.event_type = 'blocked' AND e.application_id IN ({_marks(ids)}) "
        f"AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        f"GROUP BY e.application_id",
        [user_id, *ids],
    )
    return {int(r[0]): int(r[1]) for r in await cur.fetchall()}


async def _with_kind(db: JobDatabase, user_id: str, ids: list[int], kind: str) -> set[int]:
    if not ids:
        return set()
    cur = await db._db.execute(
        f"SELECT DISTINCT application_id FROM application_artifacts "  # noqa: S608 - placeholders only
        f"WHERE kind = ? AND user_id = ? AND application_id IN ({_marks(ids)})",
        [kind, user_id, *ids],
    )
    return {int(r[0]) for r in await cur.fetchall()}


async def _cleared(db: JobDatabase, user_id: str, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    cur = await db._db.execute(
        f"SELECT DISTINCT application_id FROM application_events "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND event_type = 'duplicate_cleared' AND application_id IN ({_marks(ids)})",
        [user_id, *ids],
    )
    return {int(r[0]) for r in await cur.fetchall()}


async def _open_ask_apps(db: JobDatabase, user_id: str, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    cur = await db._db.execute(
        f"SELECT DISTINCT application_id FROM application_asks "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND answered_at IS NULL AND withdrawn_at IS NULL AND application_id IN ({_marks(ids)})",
        [user_id, *ids],
    )
    return {int(r[0]) for r in await cur.fetchall()}


def _days_since(iso: Optional[str], now: datetime) -> Optional[int]:
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return max(0, (now - at).days)


async def preflight_rows(
    db: JobDatabase, user_id: str, rows: list[dict[str, Any]], queued_at: dict[int, str],
    holds: dict[int, Any], cfg: Any, profile: Any, overlay: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """``{application_id: preflight}`` for queued ``rows`` (applications table rows). Per row:
    ``kit_missing`` (the job-country keys memory lacks, same functions the Needs-you card uses),
    ``has_cv``, ``duplicate_flag`` ('' / same_job / same_role / same_company), ``company_left``
    (null = no company limit), ``avoided`` (the user's own never-apply list), ``blocked_count``,
    ``queued_days`` and ``hold`` (another session's live hold, else null). ``profile`` / ``overlay``
    are the caller's one ``load_profile_with_overlay`` read (never re-read here, never on the loop)."""
    from src.services.profile import assistant_settings as settings_rules  # noqa: PLC0415 - profile <-> spine cycle
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return {}
    now = datetime.now(timezone.utc)
    blocked_n = await _blocked_counts(db, user_id, ids)
    cv_apps = await _with_kind(db, user_id, ids, "cv")
    cleared = await _cleared(db, user_id, ids)
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        app_id = int(r["id"])
        country = (str(r.get("job_country") or "").strip().upper()) or None
        _blocks, missing = kit.compute_answers(profile, overlay, country)
        flags = ready._missing_flags(profile, missing, country)
        app_row = {"id": app_id, "job_id": r.get("job_id"), "job_url": r.get("job_url"),
                   "job_company": r.get("job_company")}
        dup = await kit.duplicate_facts(db, user_id, app_row, now)
        flag = str(dup["flag"] or "")
        if flag == "same_job" and app_id in cleared:
            flag = ""
        left: Optional[int] = None
        if cfg.company_cap is not None:
            applied = await settings_rules.company_applied_count(db, user_id, app_id, now, cfg.company_cap_days)
            left = settings_rules.company_left(cfg, applied)
        out[app_id] = {
            "kit_missing": [str(f["key"]) for f in flags if f.get("key")],
            "has_cv": app_id in cv_apps,
            "duplicate_flag": flag,
            "company_left": left,
            "avoided": kit.company_avoided(user_id, app_row, profile.preferences.avoid_companies),
            "blocked_count": blocked_n.get(app_id, 0),
            "queued_days": _days_since(queued_at.get(app_id), now),
            "hold": holds[app_id][0] if app_id in holds else None,
        }
    return out


async def _removed_today(db: JobDatabase, user_id: str, now: datetime) -> dict[str, int]:
    """``unqueued`` events recorded on the user's own today, counted by reason (M1)."""
    from datetime import timedelta  # noqa: PLC0415

    zone = await spine.user_zone(db, user_id)
    today = now.astimezone(zone).date()
    cur = await db._db.execute(
        "SELECT e.recorded_at, e.payload FROM application_events e "
        "WHERE e.user_id = ? AND e.event_type = 'unqueued' AND e.recorded_at >= ? "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id)",
        (user_id, (now - timedelta(hours=48)).isoformat()),
    )
    counts: dict[str, int] = {}
    for r in await cur.fetchall():
        d = dict(r)
        try:
            at = datetime.fromisoformat(str(d["recorded_at"]))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at.astimezone(zone).date() != today:
            continue
        raw = d.get("payload")
        try:
            p = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            p = {}
        reason = p.get("reason") if isinstance(p, dict) else None
        reason = reason if reason in UNQUEUE_REASONS else "other"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


async def queue_summary(db: JobDatabase, user_id: str, queued_ids: list[int], cfg: Any) -> dict[str, Any]:
    """The whole open queue at a glance (Q2): ``queued``; ``ready_to_send`` (form filled, waiting
    for the user's yes); ``asked`` (an open Needs-you ask, not a block); ``blocked`` (an open
    ``blocked``); ``submitted_today`` / ``daily_cap`` / ``daily_left`` (null = no cap);
    ``removed_today`` = ``unqueued`` events today by closed reason (M1)."""
    from src.services.profile import assistant_settings as settings_rules  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    counts = await settings_rules.load_submit_counts(db, user_id, now)
    in_queue = set(queued_ids)
    ready_ids = {
        int(r["application_id"])
        for r in await ready.ready_rows(db, user_id, limit=settings.READY_TO_SEND_MAX)
    } & in_queue
    blocked_ids = {a for a in in_queue if await blocked_service.open_block(db, user_id, a) is not None}
    asked_ids = (await _open_ask_apps(db, user_id, sorted(in_queue))) - blocked_ids
    return {
        "queued": len(in_queue),
        "ready_to_send": len(ready_ids),
        "asked": len(asked_ids),
        "blocked": len(blocked_ids),
        "submitted_today": counts.submitted_today,
        "daily_cap": cfg.daily_cap,
        "daily_left": settings_rules.daily_left(cfg, counts),
        "removed_today": await _removed_today(db, user_id, now),
    }
