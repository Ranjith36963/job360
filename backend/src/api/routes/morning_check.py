"""Morning check - one read for the top of "Needs you" (S5b).

State (running / paused, the two modes, today's count against the daily limit)
plus what happened since the user last looked: sent, blocked, waiting, failed.
A WEB read, not a gate: no MCP twin (the assistant has ``whats_new`` and
``check_submit``). Scoped by ``user.id`` (rule #12). Logs the four counts only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.routes.profile import settings_view
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import ready as ready_service
from src.services.applications.authorship import actor_for
from src.services.applications.kit import _json
from src.services.profile import assistant_settings as rules
from src.services.profile import edits as profile_edits
from src.services.profile.models import UserProfile
from src.services.profile.storage import load_profile
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["morning-check"])

ITEMS_PER_BUCKET = 20
LOOKBACK_DEFAULT = timedelta(hours=24)
LOOKBACK_MAX = timedelta(days=30)
_EVENT_SCAN = 500


class TallyItem(BaseModel):
    application_id: int
    receipt_id: Optional[int] = None
    company: str
    title: str
    at: str


class TallyBucket(BaseModel):
    count: int
    items: list[TallyItem]


class MorningState(BaseModel):
    paused: bool
    paused_by: Optional[str] = None
    paused_at: Optional[str] = None
    apply_mode: str
    submit_mode: str
    daily_cap: Optional[int] = None
    applied_today: int


class MorningTally(BaseModel):
    sent: TallyBucket
    blocked: TallyBucket
    waiting: TallyBucket
    failed: TallyBucket


class MorningCheckOut(BaseModel):
    now: str
    since: str
    state: MorningState
    tally: MorningTally


def resolve_since(raw: Optional[str], now: datetime) -> datetime:
    """Window start: missing / unparseable / future = 24h ago; older than 30 days is clamped."""
    since = now - LOOKBACK_DEFAULT
    if raw:
        try:
            parsed = rules.parse_iso_with_offset(raw)
        except ValueError:
            parsed = now + timedelta(seconds=1)
        if parsed <= now:
            since = parsed
    return max(since, now - LOOKBACK_MAX)


def _item(d: dict[str, Any], at: str, receipt_id: Optional[int] = None) -> TallyItem:
    return TallyItem(
        application_id=d["application_id"], receipt_id=receipt_id, company=d["job_company"] or "",
        title=d["job_title"] or "", at=at,
    )


async def _sent(db: JobDatabase, user_id: str, since: str) -> TallyBucket:
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_receipts WHERE user_id = ? AND sent_at > ?", (user_id, since)
    )
    row = await cur.fetchone()
    cur = await db._db.execute(
        "SELECT id, application_id, job_company, job_title, sent_at FROM application_receipts "
        "WHERE user_id = ? AND sent_at > ? ORDER BY sent_at DESC, id DESC LIMIT ?",
        (user_id, since, ITEMS_PER_BUCKET),
    )
    items = [_item(d, d["sent_at"], d["id"]) for d in (dict(r) for r in await cur.fetchall())]
    return TallyBucket(count=int(row[0]) if row else 0, items=items)


async def _stopped(db: JobDatabase, user_id: str, since: str) -> tuple[TallyBucket, TallyBucket]:
    """(blocked, failed): distinct applications; the newest event names the row."""
    cur = await db._db.execute(
        "SELECT e.application_id, e.event_type, e.payload, e.recorded_at, a.job_company, a.job_title "
        "FROM application_events e JOIN applications a ON a.id = e.application_id AND a.user_id = e.user_id "
        "WHERE e.user_id = ? AND e.event_type IN ('account_needed', 'hold_released') AND e.recorded_at > ? "
        "ORDER BY e.id DESC LIMIT ?",
        (user_id, since, _EVENT_SCAN),
    )
    seen: dict[str, dict[int, TallyItem]] = {"blocked": {}, "failed": {}}
    for d in (dict(r) for r in await cur.fetchall()):
        reason = "blocked" if d["event_type"] == "account_needed" else _json(d["payload"]).get("reason")
        if reason in seen and d["application_id"] not in seen[reason]:
            seen[reason][d["application_id"]] = _item(d, d["recorded_at"])
    pack = [TallyBucket(count=len(b), items=list(b.values())[:ITEMS_PER_BUCKET]) for b in seen.values()]
    return pack[0], pack[1]


async def _waiting(db: JobDatabase, user_id: str, since: str) -> TallyBucket:
    rows = [
        r for r in await ready_service.ready_rows(db, user_id, limit=settings.READY_TO_SEND_MAX)
        if str(r["filled_at"]) > since
    ]
    return TallyBucket(count=len(rows), items=[_item(r, r["filled_at"]) for r in rows[:ITEMS_PER_BUCKET]])


@router.get("/morning-check", response_model=MorningCheckOut, dependencies=AUTH_FIRST)
async def morning_check(
    since: Optional[str] = Query(None, max_length=64),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI DI idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> MorningCheckOut:
    """State + the four-bucket tally since ``since``."""
    now = datetime.now(timezone.utc)
    start = resolve_since(since, now).isoformat()
    view = settings_view(load_profile(user.id) or UserProfile(), user.id)
    paused = bool(view["paused"])
    by = at = None
    if paused:
        history = profile_edits.path_history(user.id, rules.PAUSED_UNTIL_PATH, 1)
        if history:
            by, at = history[0]["set_by"], history[0]["set_at"]
    counts = await rules.load_submit_counts(db, user.id, now)
    blocked, failed = await _stopped(db, user.id, start)
    tally = MorningTally(
        sent=await _sent(db, user.id, start), blocked=blocked,
        waiting=await _waiting(db, user.id, start), failed=failed,
    )
    get_audit_logger().info(
        "morning_check_read",
        extra={
            "event": "morning_check_read", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "result": "ok", "paused": paused,
            **{k: v.count for k, v in tally},
        },
    )
    return MorningCheckOut(
        now=now.isoformat(), since=start,
        state=MorningState(
            paused=paused, paused_by=by, paused_at=at,
            apply_mode=view["apply_mode"]["effective"], submit_mode=view["submit_mode"]["effective"],
            daily_cap=view["daily_cap"]["effective"], applied_today=counts.submitted_today,
        ),
        tally=tally,
    )
