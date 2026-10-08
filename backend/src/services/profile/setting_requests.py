"""Waiting requests — the "Waiting for your OK" queue for RISKIER setting changes.

Owner decision 2026-10-08 (S2). An assistant (a token or an OAuth client) may
make a SAFER setting change at once, but a RISKIER one — more freedom for the
assistant, see ``assistant_settings.classify_change`` — is stored here instead
of being applied. Only the signed-in user, on the Job360 website, can confirm
it (``routes/assistant_settings.py``, session-only, NO MCP twin — like Keep).

That is the injection guard: text on a job page, in an email or in a form that
tricks an assistant into "turn on auto-submit" produces a waiting row and
nothing more. The assistant cannot confirm, decline or read its way around it.

A row is WAITING while ``decision IS NULL``; the decision columns are set ONCE
(``UPDATE ... WHERE id = ? AND user_id = ? AND decision IS NULL``), asks-table
style, to ``confirmed | declined | superseded | cleared``. A newer request for
the same path supersedes the older waiting one. A waiting row older than
``ASSISTANT_SETTING_REQUEST_TTL_DAYS`` is expired: it stops showing and cannot
be confirmed. Every query filters ``user_id`` by hand (rule #12).

Logs name the user, the actor, the path and the request id — never the value
of a note or pause reason (the one free-text path never reaches here: a pause
reason is always "safer").
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.repositories import pgsync
from src.services.profile.edits import ProfileEditError
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

DECISIONS = ("confirmed", "declined", "superseded", "cleared")
_SELECT = (
    "SELECT id, path, value, requested_by, requested_at, decision, decided_by, decided_at "
    "FROM assistant_setting_requests "
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expires_at(requested_at: str) -> str:
    try:
        base = datetime.fromisoformat(requested_at)
    except ValueError:
        base = _now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(days=settings.ASSISTANT_SETTING_REQUEST_TTL_DAYS)).isoformat()


def is_expired(requested_at: str, now: Optional[datetime] = None) -> bool:
    """True once a request is older than ``ASSISTANT_SETTING_REQUEST_TTL_DAYS``."""
    return _expires_at(requested_at) <= (now or _now()).isoformat()


def _row(raw: Any) -> dict[str, Any]:
    rid, path, value, requested_by, requested_at, decision, decided_by, decided_at = tuple(raw)
    return {
        "id": int(rid), "path": path,
        "value": None if value is None else json.loads(value),
        "requested_by": requested_by, "requested_at": requested_at,
        "expires_at": _expires_at(requested_at),
        "decision": decision, "decided_by": decided_by, "decided_at": decided_at,
        "status": "waiting" if decision is None else str(decision),
    }


def waiting_out(row: dict[str, Any]) -> dict[str, Any]:
    """The shape the API and ``get_profile`` return for one waiting request."""
    return {k: row[k] for k in ("id", "path", "value", "requested_by", "requested_at", "expires_at", "status")}


def _log(event: str, user_id: str, *, level: str = "info", **fields: Any) -> None:
    log = get_audit_logger()
    getattr(log, level)(
        event, extra={"event": event, "user_id": safe_log_value(user_id), **fields}
    )


def check_capacity(user_id: str, actor: str, new_requests: int) -> None:
    """429 when this user's assistants already made too many requests this hour.

    Counted off the table (per USER, never per IP) and checked BEFORE any write
    of the call, so a refused call changes nothing."""
    if new_requests <= 0 or settings.ASSISTANT_SETTING_REQUESTS_MAX_PER_HOUR <= 0:
        return
    since = (_now() - timedelta(hours=1)).isoformat()
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM assistant_setting_requests WHERE user_id = ? AND requested_at > ?",
            (user_id, since),
        ).fetchone()
    used = int(cnt[0]) if cnt else 0
    if used + new_requests > settings.ASSISTANT_SETTING_REQUESTS_MAX_PER_HOUR:
        _log(
            "assistant_setting_request_rejected", user_id, level="warning",
            actor=safe_log_value(actor), status=429, result="rejected",
        )
        raise ProfileEditError(
            429,
            "too many setting change requests in the last hour "
            f"(ASSISTANT_SETTING_REQUESTS_MAX_PER_HOUR is {settings.ASSISTANT_SETTING_REQUESTS_MAX_PER_HOUR}); "
            "try again later",
        )


def create_requests(user_id: str, actor: str, items: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    """Store one waiting request per ``(path, normalised value)``; an older
    waiting request for the same path is superseded. All in one transaction.
    Call :func:`check_capacity` first."""
    now = _now().isoformat()
    created: list[dict[str, Any]] = []
    superseded: list[tuple[str, int]] = []
    with pgsync.connect(str(settings.DB_PATH)) as conn, conn._raw.transaction():
        for path, value in items:
            old = conn.execute(
                "SELECT id FROM assistant_setting_requests WHERE user_id = ? AND path = ? AND decision IS NULL",
                (user_id, path),
            ).fetchall()
            for (old_id,) in old:
                conn.execute(
                    "UPDATE assistant_setting_requests SET decision = 'superseded', decided_by = ?, decided_at = ? "
                    "WHERE id = ? AND user_id = ? AND decision IS NULL",
                    (actor, now, old_id, user_id),
                )
                superseded.append((path, int(old_id)))
            cur = conn.execute(
                "INSERT INTO assistant_setting_requests (user_id, path, value, requested_by, requested_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, path, json.dumps(value), actor, now),
            )
            created.append(
                _row((int(cur.lastrowid or 0), path, json.dumps(value), actor, now, None, None, None))
            )
    for path, old_id in superseded:
        _log(
            "assistant_setting_superseded", user_id, actor=safe_log_value(actor),
            path=safe_log_value(path), request_id=old_id, result="ok",
        )
    for row in created:
        _log(
            "assistant_setting_requested", user_id, actor=safe_log_value(actor),
            path=safe_log_value(row["path"]), request_id=row["id"], result="ok",
        )
    return created


def get_request(user_id: str, request_id: int) -> Optional[dict[str, Any]]:
    """One request of THIS user, or ``None`` (another user's id reads as absent)."""
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        raw = conn.execute(
            _SELECT + "WHERE id = ? AND user_id = ?",
            (request_id, user_id),
        ).fetchone()
    return _row(raw) if raw else None


def list_waiting(user_id: str, conn: Optional[pgsync.Connection] = None) -> list[dict[str, Any]]:
    """The user's waiting, non-expired requests, newest first. ``[]`` when the
    table is absent (a database that predates migration 0051)."""
    sql = _SELECT + "WHERE user_id = ? AND decision IS NULL ORDER BY id DESC"
    try:
        if conn is not None:
            rows = conn.execute(sql, (user_id,)).fetchall()
        else:
            with pgsync.connect(str(settings.DB_PATH)) as own:
                rows = own.execute(sql, (user_id,)).fetchall()
    except pgsync.OperationalError:
        return []
    now = _now()
    return [waiting_out(r) for r in (_row(x) for x in rows) if not is_expired(r["requested_at"], now)]


def decide(user_id: str, request_id: int, decision: str, decided_by: str) -> bool:
    """Set the decision ONCE. ``True`` when this call won it, ``False`` when the
    request was already decided or is not this user's."""
    if decision not in DECISIONS:  # pragma: no cover — callers pass the constants
        raise ValueError(decision)
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        cur = conn.execute(
            "UPDATE assistant_setting_requests SET decision = ?, decided_by = ?, decided_at = ? "
            "WHERE id = ? AND user_id = ? AND decision IS NULL",
            (decision, decided_by, _now().isoformat(), request_id, user_id),
        )
        return cur.rowcount > 0


def cancel_waiting(user_id: str, decided_by: str) -> int:
    """Mark every waiting request ``cleared`` (a "Clear all" reset). Returns how many."""
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        cur = conn.execute(
            "UPDATE assistant_setting_requests SET decision = 'cleared', decided_by = ?, decided_at = ? "
            "WHERE user_id = ? AND decision IS NULL",
            (decided_by, _now().isoformat(), user_id),
        )
        count = int(cur.rowcount)
    _log("assistant_setting_cleared", user_id, actor=safe_log_value(decided_by), requests=count, result="ok")
    return count


async def export_rows(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """Every request of this user (waiting and decided), oldest first, for
    ``export_history`` — the queue is part of the user's own record."""
    cur = await db._db.execute(
        _SELECT + "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, settings.EXPORT_HISTORY_MAX_PROFILE_EDITS),
    )
    rows = [_row(tuple(dict(r).values())) for r in await cur.fetchall()]
    rows.reverse()
    return rows
