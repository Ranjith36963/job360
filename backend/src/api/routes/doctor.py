"""The agent doctor and report_missing_tool (feature 7).

``GET /api/doctor`` is ONE read of the account's state for a connected
assistant (or the user): how it is signed in, what the profile still lacks,
open asks, waiting setting changes, the pause, today's outreach and ONE
closed ``next_step`` code. It writes nothing but an audit line. Every field
is built defensively - a brand-new account with no profile gets an answer,
never a 404 or a 500.

``POST /api/missing-tools`` only STORES "a tool I needed does not exist" for
the owner to read with a DB query (no web UI). It does nothing else.

Both ``Depends(require_user)`` and scope by ``user.id`` (rules #12/#25); the
MCP tools ``doctor`` and ``report_missing_tool`` call these route functions.
Logs carry ids and closed codes only - never ``why`` (free text an assistant
typed), never an email address.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.models import DoctorResponse, MissingToolRequest, MissingToolResponse
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import asks as asks_service
from src.services.applications import kit as kit_service
from src.services.applications.authorship import actor_for
from src.services.profile import assistant_settings as settings_rules
from src.services.profile import setting_requests
from src.services.profile.models import UserProfile
from src.services.profile.search_brief import build_search_brief
from src.services.profile.storage import load_profile_with_overlay
from src.utils.logger import get_audit_logger, get_logger, safe_log_value

router = APIRouter(tags=["doctor"])
log = get_logger(__name__)

MISSING_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
WHATS_NEW_HINT = "Call whats_new(since=<your last check>) to see what changed; Job360 keeps no read cursor."
# The closed next_step codes, in the order they are checked ("setup:<round>" carries a round name).
NEXT_STEPS: tuple[str, ...] = ("verify_email", "upload_cv", "setup:<round>", "answer_asks", "confirm_settings", "ok")


def _verification_required() -> bool:
    """The same escape hatch ``require_verified_user`` reads."""
    return os.getenv("REQUIRE_EMAIL_VERIFICATION", "true").strip().lower() not in ("false", "0", "no", "off")


def _missing(profile: UserProfile, overlay: list[dict[str, Any]], has_profile: bool) -> list[dict[str, str]]:
    """What the profile lacks, from the SAME code the kit and the search brief
    use (never a copy): no CV, no target titles, the kit's ``contact.*`` gaps
    (job-country rows dropped - doctor has no job) and the search-brief gaps."""
    out: list[dict[str, str]] = []
    if not has_profile or not (profile.cv_data.raw_text or "").strip():
        out.append({"key": "cv", "why": "no CV uploaded yet"})
    if not profile.preferences.target_job_titles:
        out.append({"key": "preferences.target_job_titles", "why": "no target job titles saved"})
    try:
        _answers, kit_missing = kit_service.compute_answers(profile, overlay, None)
        out.extend({"key": m["key"], "why": m["why"]} for m in kit_missing if m["key"].startswith("contact."))
    except Exception:  # noqa: BLE001 - doctor never 500s on a partial profile
        log.warning("doctor_missing_kit_failed", extra={"event": "doctor_missing_kit_failed"})
    try:
        info = profile.user_info
        brief = build_search_brief(info.right_to_work, (info.contact or {}).get("residence_country"), info.answers)
        out.extend({"key": str(g.get("path") or ""), "why": str(g.get("ask") or "")} for g in brief["gaps"])
    except Exception:  # noqa: BLE001
        log.warning("doctor_missing_brief_failed", extra={"event": "doctor_missing_brief_failed"})
    return out


async def _latest_event_at(db: JobDatabase, user_id: str) -> Optional[str]:
    cur = await db._db.execute("SELECT MAX(recorded_at) FROM application_events WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    return str(row[0]) if row and row[0] else None


async def _blocked_today(db: JobDatabase, user_id: str, now: datetime) -> int:
    """Today's ``outreach_check`` audit rows answered ``stop`` (the user's own day)."""
    try:
        return await settings_rules.count_on_user_today(
            db, user_id, now,
            "SELECT occurred_at AS recorded_at FROM audit_log "
            "WHERE user_id = ? AND event = 'outreach_check' AND detail LIKE ? AND occurred_at >= ?",
            (user_id, '%"decision": "stop"%'),
        )
    except Exception:  # noqa: BLE001 - a schema without audit_log reads as 0
        return 0


def _next_step(
    *, user: CurrentUser, has_cv: bool, setup_next: str, open_asks: int, waiting: int
) -> str:
    if not user.email_verified and _verification_required():
        return "verify_email"
    if not has_cv:
        return "upload_cv"
    if setup_next:
        return f"setup:{setup_next}"
    if open_asks:
        return "answer_asks"
    if waiting:
        return "confirm_settings"
    return "ok"


@router.get("/doctor", response_model=DoctorResponse, dependencies=AUTH_FIRST)
async def doctor(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
    client_tools_version: Annotated[str, Query(max_length=64)] = "",
    client_tools_rev: Annotated[Optional[int], Query(ge=0, le=1_000_000)] = None,
) -> dict[str, Any]:
    """The account's state in one read, plus ONE closed ``next_step``:
    ``verify_email | upload_cv | setup:<round> | answer_asks | confirm_settings | ok``.
    Read-only; email verification is NOT required (an unverified user is told
    to verify). No 404 when there is no profile (``profile_exists: false``).

    Also the MCP tool list as served now (``tools``, ``tools_version``) so an
    assistant can tell its client cached an old one: pass the version it saw
    last as ``client_tools_version`` and ``tools_stale`` says whether it moved
    (null when none was passed)."""
    now = datetime.now(timezone.utc)
    ghosted_now = await _sweep_ghosted(db, user.id, now)
    loaded, overlay = load_profile_with_overlay(user.id)
    profile = loaded or UserProfile()
    has_cv = bool((profile.cv_data.raw_text or "").strip())
    setup = settings_rules.setup_progress_view(profile.assistant_settings.setup_progress)
    eff = settings_rules.effective(profile.assistant_settings)
    paused = settings_rules.is_paused(eff.paused_until, now)
    open_asks = await asks_service.count_open_asks(db, user.id)
    waiting = len(setting_requests.list_waiting(user.id))
    counts = await settings_rules.load_outreach_counts(db, user.id, now)
    next_step = _next_step(
        user=user, has_cv=has_cv, setup_next=setup["next"], open_asks=open_asks, waiting=waiting,
    )
    get_audit_logger().info(
        "doctor_read",
        extra={"event": "doctor_read", "user_id": safe_log_value(user.id), "next_step": next_step},
    )
    return {
        "auth": {"kind": user.auth_via, "actor": actor_for(user)},
        "email_verified": bool(user.email_verified),
        "profile_exists": loaded is not None,
        "setup": {"done": setup["done"], "total": setup["total"], "next": setup["next"]},
        "missing": _missing(profile, overlay, loaded is not None),
        "paused": paused,
        "paused_until": eff.paused_until if paused else "",
        "open_asks": open_asks,
        "waiting_settings": waiting,
        "latest_event_recorded_at": await _latest_event_at(db, user.id),
        "whats_new_hint": WHATS_NEW_HINT,
        "outreach": {
            "sent_today": counts.sent_today,
            "daily_max": settings.OUTREACH_AUTO_SENDS_MAX_PER_DAY,
            "blocked_today": await _blocked_today(db, user.id, now),
        },
        "next_step": next_step,
        "ghosted_now": ghosted_now,
        **_tools_block(client_tools_version, client_tools_rev),
    }


async def _sweep_ghosted(db: JobDatabase, user_id: str, now: datetime) -> list[int]:
    """Auto-ghost silent applications (services/applications/ghosting.py); a failure logs and reads as none."""
    try:
        from src.services.applications.ghosting import sweep_ghosted  # noqa: PLC0415

        return await sweep_ghosted(db, user_id, now)
    except Exception:  # noqa: BLE001
        log.warning(
            "doctor_ghost_sweep_failed",
            extra={"event": "doctor_ghost_sweep_failed", "user_id": safe_log_value(user_id)},
            exc_info=True,
        )
        return []


def _tools_block(client_tools_version: str, client_tools_rev: Optional[int] = None) -> dict[str, Any]:
    """The MCP tool-list block (``mcp_server.tools_report``), lazily imported (rule #16).
    Defensive like every doctor field: a failure reads as an empty list, never a 500."""
    try:
        from src.api.mcp_server import tools_report  # noqa: PLC0415

        return tools_report(client_tools_version, client_tools_rev)
    except Exception:  # noqa: BLE001
        log.warning("doctor_tools_report_failed", extra={"event": "doctor_tools_report_failed"})
        return {"tools_version": "", "tools_rev": 0, "tools": {}, "tools_stale": None, "stale_hint": ""}


@router.post("/missing-tools", status_code=201, response_model=MissingToolResponse, dependencies=AUTH_FIRST)
async def report_missing_tool(
    body: MissingToolRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Store "a tool I needed does not exist" for the owner. It only stores.
    422 when ``name`` is not ``^[a-z][a-z0-9_]{1,59}$`` or ``why`` is over 280
    characters (the message names the rule, never the value); 429 past
    ``MISSING_TOOL_REPORTS_MAX_PER_DAY`` reports in the last 24 hours."""
    name = body.name.strip()
    why = body.why.strip()
    if not MISSING_TOOL_NAME_RE.match(name):
        raise HTTPException(status_code=422, detail="name must match ^[a-z][a-z0-9_]{1,59}$ (a tool-style name)")
    if len(why) > settings.MISSING_TOOL_WHY_MAX_CHARS:
        raise HTTPException(
            status_code=422, detail=f"why must be at most {settings.MISSING_TOOL_WHY_MAX_CHARS} characters"
        )
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=1)).isoformat()
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM missing_tool_reports WHERE user_id = ? AND created_at >= ?", (user.id, since)
    )
    row = await cur.fetchone()
    if row and int(row[0]) >= settings.MISSING_TOOL_REPORTS_MAX_PER_DAY:
        get_audit_logger().info(
            "missing_tool_refused",
            extra={"event": "missing_tool_refused", "user_id": safe_log_value(user.id), "status": "refused"},
        )
        raise HTTPException(
            status_code=429,
            detail=f"too many reports today (MISSING_TOOL_REPORTS_MAX_PER_DAY = "
                   f"{settings.MISSING_TOOL_REPORTS_MAX_PER_DAY})",
        )
    cur = await db._db.execute(
        "INSERT INTO missing_tool_reports (user_id, name, why, reported_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (user.id, name, why, actor_for(user), now.isoformat()),
    )
    report_id = int(cur.lastrowid or 0)
    await db._db.commit()
    get_audit_logger().info(
        "missing_tool_reported",
        extra={
            "event": "missing_tool_reported", "user_id": safe_log_value(user.id),
            "report_id": report_id, "tool_name": safe_log_value(name),  # `name` is a reserved LogRecord attribute
        },
    )
    return {"id": report_id}
