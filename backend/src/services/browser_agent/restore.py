"""The held form survives a deploy (owner, 2026-10-26).

A deploy restarts the process and wipes the in-process browser pool, so a form held 30 minutes for a
CAPTCHA (``agent.hold_for_user``) is lost. On boot, once this process is serving (``deploy_hold``), one
background task reopens the forms the old process was holding: it replays each job's stored fill script
(``api/routes/browser.replay_and_hold``, the same path as the user's "Refill the form": never a submit,
never a CAPTCHA, nothing new typed) and holds the page for the time LEFT of the old hold, so "Finish it
here" keeps working with no click.

Which forms: the open block is a CAPTCHA / bot check that Job360's own browser wrote (``by: browser``,
which a caller can never write), the job has a fill script, and nothing after the block released it
(applied, withdrawn, rejected, hold_released). The hold's start is the newest of the block, the newest
``form_filled`` and the newest fill step: each is a moment the existing code (re)starts a hold (the hand-off,
a refill, a fill while the CAPTCHA waits). The hold ends ``HANDOFF_HOLD_SECONDS`` after it; nothing is
persisted for this. A paused assistant's job is never touched. One at a time, at most
``RESTORE_MAX_PER_BOOT`` (and one pool slot always stays free). Each failure is logged and swallowed;
the audit line carries ids, the result and step counts, never a value.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

logger = logging.getLogger(__name__)

RESTORE_MAX_PER_BOOT = 5
# Less than this left on the old hold: not worth reopening a page.
MIN_LEFT_S = 60
# The server is up and answering before the first DB read (the lifespan has just yielded).
START_DELAY_S = 5.0
WAIT_POLL_S = 5.0
SYSTEM_ACTOR = "system"  # the same actor name ghosting.py writes
_REASONS = ("captcha", "bot_check")  # blocked.FINISH_HERE_REASONS: the blocks the user finishes here

_CANDIDATES_SQL = (
    "SELECT e.user_id, e.application_id, e.payload, e.recorded_at, "
    "(SELECT MAX(f.recorded_at) FROM application_events f WHERE f.user_id = e.user_id "
    " AND f.application_id = e.application_id AND f.event_type = 'form_filled') AS filled_at, "
    "(SELECT MAX(s.recorded_at) FROM application_fill_steps s WHERE s.user_id = e.user_id "
    " AND s.application_id = e.application_id) AS step_at "
    "FROM application_events e WHERE e.event_type = 'blocked' "
    "AND e.id = (SELECT MAX(y.id) FROM application_events y WHERE y.user_id = e.user_id "
    " AND y.application_id = e.application_id AND y.event_type IN ('blocked', 'unblocked')) "
    "AND NOT EXISTS (SELECT 1 FROM application_events r WHERE r.user_id = e.user_id "
    " AND r.application_id = e.application_id AND r.id > e.id "
    " AND r.event_type IN ('applied', 'withdrawn', 'rejected', 'hold_released')) "
    "AND e.application_id IN ("
    " SELECT application_id FROM application_fill_steps WHERE recorded_at >= ? "
    " UNION SELECT application_id FROM application_events WHERE recorded_at >= ? "
    " AND event_type IN ('blocked', 'form_filled')) "
    "AND EXISTS (SELECT 1 FROM application_fill_steps z WHERE z.user_id = e.user_id "
    " AND z.application_id = e.application_id) "
    "ORDER BY e.id DESC LIMIT 100"
)


def _when(raw: Any) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


async def candidates(db: JobDatabase, now: datetime) -> list[dict[str, Any]]:
    """The forms whose hold would still be running at ``now``, most time left first:
    ``{user_id, application_id, ends_at}``. Read-only, every user (this runs once per boot)."""
    from src.services.browser_agent.agent import HANDOFF_HOLD_SECONDS  # noqa: PLC0415

    hold = timedelta(seconds=HANDOFF_HOLD_SECONDS)
    cutoff = (now - hold).isoformat()
    cur = await db._db.execute(_CANDIDATES_SQL, (cutoff, cutoff))
    out = []
    for row in await cur.fetchall():
        raw = row["payload"]
        try:
            p = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        if not isinstance(p, dict) or p.get("by") != "browser" or p.get("reason") not in _REASONS:
            continue
        stamps = [t for t in (_when(row["recorded_at"]), _when(row["filled_at"]), _when(row["step_at"])) if t]
        if not stamps:
            continue
        ends_at = max(stamps) + hold
        if (ends_at - now).total_seconds() >= MIN_LEFT_S:
            out.append({"user_id": str(row["user_id"]), "application_id": int(row["application_id"]),
                        "ends_at": ends_at})
    return sorted(out, key=lambda c: c["ends_at"], reverse=True)


def _paused(user_id: str, now: datetime) -> bool:
    """The user's assistant settings say paused. Unreadable settings count as paused: never act blind."""
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    try:
        cfg = rules.effective((load_profile(user_id) or UserProfile()).assistant_settings)
        return rules.is_paused(cfg.paused_until, now)
    except Exception:  # noqa: BLE001
        logger.warning("browser restore: settings unreadable", exc_info=True)
        return True


async def _restore_one(c: dict[str, Any]) -> dict[str, Any]:
    from src.api.auth_deps import CurrentUser  # noqa: PLC0415
    from src.api.routes import browser as browser_route  # noqa: PLC0415 - the route's own replay + hold
    from src.services.browser_agent import agent  # noqa: PLC0415

    user_id, app_id = c["user_id"], c["application_id"]
    if _paused(user_id, datetime.now(timezone.utc)):
        return {"result": "paused"}
    if agent.held_seconds(agent.get_pool(), user_id, app_id) is not None:
        return {"result": "already_held"}
    user = CurrentUser(id=user_id, email="", auth_via="session")
    out = await browser_route.replay_and_hold(user, app_id, actor=SYSTEM_ACTOR, by="restore", ends_at=c["ends_at"])
    if out["status"] != "ok":
        return {"result": str(out.get("reason") or out["status"]), "steps_ok": 0, "steps_failed": 0}
    return {"result": "ok" if out.get("held_seconds") else "not_held",
            "steps_ok": int(out["steps_ok"]), "steps_failed": len(out["steps_failed"])}


async def restore_held_forms() -> list[dict[str, Any]]:
    """Reopen and re-hold every form the old process held (see the module doc). One at a time; never raises.
    Returns one ``{user_id, application_id, result, steps_ok, steps_failed}`` per form looked at."""
    from src.api.routes.browser import _db  # noqa: PLC0415
    from src.core import settings  # noqa: PLC0415 - live read, house style
    from src.services.browser_agent import agent  # noqa: PLC0415

    if not settings.BROWSER_AGENT_ENABLED:
        return []
    try:
        async with _db() as db:
            found = await candidates(db, datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        logger.warning("browser restore: candidates unreadable", exc_info=True)
        return []
    # Never every slot: a new user's browser must still start while restored forms are held.
    room = min(RESTORE_MAX_PER_BOOT, max(0, agent.get_pool().max_sessions - 1))
    results: list[dict[str, Any]] = []
    tried = 0
    for c in found:
        if tried >= room:
            break
        try:
            got = await _restore_one(c)
        except Exception as exc:  # noqa: BLE001 - one form's failure never stops the others
            logger.warning("browser restore failed", exc_info=True)
            got = {"result": "error", "reason": type(exc).__name__}
        if got["result"] not in ("paused", "already_held"):
            tried += 1
        row = {"user_id": c["user_id"], "application_id": c["application_id"], "steps_ok": 0, "steps_failed": 0,
               **got}
        results.append(row)
        get_audit_logger().info(
            "browser_restore",
            extra={"event": "browser_restore", "user_id": safe_log_value(row["user_id"]),
                   "application_id": int(row["application_id"]), "result": safe_log_value(row["result"]),
                   "steps_ok": int(row["steps_ok"]), "steps_failed": int(row["steps_failed"])},
        )
    return results


async def restore_after_boot() -> None:
    """The lifespan's task: wait until this process is serving (its deploy hold answered ready, which latches,
    so the restored browsers can never hold its own healthcheck), then restore. Never raises."""
    from src.services.browser_agent import deploy_hold  # noqa: PLC0415

    try:
        await asyncio.sleep(START_DELAY_S)
        while await deploy_hold.hold_reason() is not None:
            await asyncio.sleep(WAIT_POLL_S)
        await restore_held_forms()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("browser restore after boot failed", exc_info=True)
