"""Apprise-backed channel dispatcher.

Responsibilities:
- Look up a user's enabled channels.
- Decrypt each credential (Fernet).
- Consult notification_rules for per-channel threshold / quiet-hours / mode.
- Ask Apprise to ``notify()`` with a per-channel formatted payload.
- Return a per-channel result dict (ok + error).

Idempotency / retry / ledger writes are the CALLER's job (the ARQ task).
This module only knows "send a message now, synchronously from the caller's
event loop, and tell me how it went."

Step-3 B-03 — rule consultation
---------------------------------
When a ``notification_rules`` row exists for (user_id, channel_type):
  * enabled=0  → skip entirely (ChannelSendResult with ok=True and skipped=True)
  * match_score < score_threshold → skip (score gate)
  * quiet hours active (converted via zoneinfo to user's tz) → skip or queue
    digest depending on notify_mode
  * notify_mode='digest' (and outside quiet hours) → queue for digest, skip now

If NO rule row exists: dispatch immediately (backwards-compat default).

Quiet-hours use ``zoneinfo.ZoneInfo`` (stdlib 3.9+) — no pytz required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Optional

import aiosqlite

from src.services.channels.crypto import decrypt

logger = logging.getLogger(__name__)

# Deferred import so tests for crypto alone don't pay for Apprise.
_apprise = None


def _get_apprise_cls():
    global _apprise
    if _apprise is None:
        import apprise  # type: ignore

        _apprise = apprise
    return _apprise


@dataclass(frozen=True)
class ChannelSendResult:
    channel_id: int
    channel_type: str
    ok: bool
    error: str = ""
    skipped: bool = False  # True when a rule gate prevented dispatch
    queued_digest: bool = False  # True when enqueued for digest instead


async def load_user_channels(
    db: aiosqlite.Connection, user_id: str, *, enabled_only: bool = True
) -> list[dict[str, Any]]:
    db.row_factory = aiosqlite.Row
    query = "SELECT * FROM user_channels WHERE user_id = ?"
    params: list = [user_id]
    if enabled_only:
        query += " AND enabled = 1"
    cur = await db.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


def format_payload(channel_type: str, title: str, body: str) -> tuple[str, str]:
    """Return (title, body) formatted for ``channel_type``.

    Stub — Phase 6 ships a single plain-text shape. Blueprint §1 calls for
    Slack Block Kit / Discord embed / Telegram MarkdownV2 richness; the
    design keeps the channel-specific templating in one place so the
    upgrade is a local change.
    """
    if channel_type == "slack":
        return title, f"*{title}*\n{body}"
    if channel_type == "discord":
        return title, f"**{title}**\n{body}"
    if channel_type == "telegram":
        return title, f"*{title}*\n{body}"
    # email, webhook, default
    return title, body


def _is_in_quiet_window(
    quiet_start_str: str,
    quiet_end_str: str,
    user_tz: str,
) -> bool:
    """Return True when the current UTC moment falls inside the quiet window.

    Both ``quiet_start_str`` and ``quiet_end_str`` are HH:MM strings in the
    user's local timezone (``user_tz``). Conversion uses ``zoneinfo.ZoneInfo``
    (stdlib 3.9+) per CLAUDE.md constraint — no pytz.

    Supports wraparound windows that cross midnight (e.g. 23:00 – 07:00).
    Returns False on any parsing error so the caller defaults to immediate
    dispatch.
    """
    try:
        from zoneinfo import ZoneInfo  # stdlib 3.9+ — no pytz needed

        tz = ZoneInfo(user_tz)
        now_local = datetime.now(tz).time().replace(second=0, microsecond=0)
        start = time(*map(int, quiet_start_str.split(":")))
        end = time(*map(int, quiet_end_str.split(":")))
        if start <= end:
            return start <= now_local < end
        # Wraparound: e.g. 23:00–07:00
        return now_local >= start or now_local < end
    except Exception:  # noqa: BLE001 — defensive; default to allow dispatch
        return False


async def _load_notification_rule(db: aiosqlite.Connection, user_id: str, channel_type: str) -> dict | None:
    """Return the notification_rules row for (user_id, channel_type) or None."""
    try:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM notification_rules WHERE user_id = ? AND channel = ?",
            (user_id, channel_type),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001 — table may be missing on legacy DB
        return None


async def _load_user_timezone(db: aiosqlite.Connection, user_id: str) -> str:
    """Return the user's timezone string (IANA) from the users table, defaulting to UTC."""
    try:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT timezone FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        if row and row["timezone"]:
            return row["timezone"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not load timezone for user %s: %s", user_id, exc)
    return "UTC"


async def _queue_digest(db: aiosqlite.Connection, user_id: str, channel: str, job_id: int | None) -> None:
    """Insert a pending digest row for the given (user_id, channel, job_id)."""
    if job_id is None:
        return
    try:
        await db.execute(
            "INSERT INTO user_notification_digests(user_id, channel, job_id) VALUES(?, ?, ?)",
            (user_id, channel, job_id),
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — table missing on legacy DB
        logger.debug("Could not queue digest for user %s channel %s: %s", user_id, channel, exc)


async def dispatch(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    title: str,
    body: str,
    job_id: int | None = None,
    match_score: int | None = None,
) -> list[ChannelSendResult]:
    """Send ``(title, body)`` to every enabled channel for ``user_id``.

    Step-3 B-03: before dispatching each channel the function consults
    ``notification_rules`` for the matching (user_id, channel_type) row.
    If no rule exists the channel receives the notification immediately
    (backwards-compatible default).

    Rule evaluation order:
      1. enabled=0 → skip (ChannelSendResult ok=True, skipped=True)
      2. match_score < score_threshold → skip
      3. notify_mode='digest' → queue for digest, skip immediate dispatch
      4. quiet_hours active → skip (for instant) or queue digest

    ``job_id`` and ``match_score`` are optional; when absent rule gates that
    inspect them are bypassed (keeps the function usable for non-job
    notifications like test-sends).

    Returns one result per channel attempted.
    """
    apprise = _get_apprise_cls()
    channels = await load_user_channels(db, user_id)
    user_tz = await _load_user_timezone(db, user_id)
    results: list[ChannelSendResult] = []
    for ch in channels:
        ch_type = ch["channel_type"]

        # ── Rule consultation ──────────────────────────────────────────────
        rule = await _load_notification_rule(db, user_id, ch_type)
        if rule is not None:
            # Gate 1: channel-level enable switch
            if not rule.get("enabled", 1):
                results.append(
                    ChannelSendResult(
                        channel_id=ch["id"],
                        channel_type=ch_type,
                        ok=True,
                        skipped=True,
                        error="rule disabled",
                    )
                )
                continue

            # Gate 2: score threshold
            if match_score is not None:
                threshold = int(rule.get("score_threshold", 60))
                if match_score < threshold:
                    results.append(
                        ChannelSendResult(
                            channel_id=ch["id"],
                            channel_type=ch_type,
                            ok=True,
                            skipped=True,
                            error=f"score {match_score} < threshold {threshold}",
                        )
                    )
                    continue

            # Gate 3: digest mode — queue and skip immediate send
            notify_mode = rule.get("notify_mode", "instant")
            if notify_mode == "digest":
                await _queue_digest(db, user_id, ch_type, job_id)
                results.append(
                    ChannelSendResult(
                        channel_id=ch["id"],
                        channel_type=ch_type,
                        ok=True,
                        queued_digest=True,
                        error="queued for digest",
                    )
                )
                continue

            # Gate 4: quiet hours (instant mode only — digest already handled)
            qs = rule.get("quiet_hours_start")
            qe = rule.get("quiet_hours_end")
            if qs and qe:
                if _is_in_quiet_window(qs, qe, user_tz):
                    # Queue for digest if job_id is available, otherwise skip.
                    if job_id is not None:
                        await _queue_digest(db, user_id, ch_type, job_id)
                        results.append(
                            ChannelSendResult(
                                channel_id=ch["id"],
                                channel_type=ch_type,
                                ok=True,
                                queued_digest=True,
                                error="quiet hours — queued for digest",
                            )
                        )
                    else:
                        results.append(
                            ChannelSendResult(
                                channel_id=ch["id"],
                                channel_type=ch_type,
                                ok=True,
                                skipped=True,
                                error="quiet hours — skipped",
                            )
                        )
                    continue
        # ── /Rule consultation ─────────────────────────────────────────────

        url = decrypt(ch["credential_encrypted"])
        ap = apprise.Apprise()
        ap.add(url)
        t, b = format_payload(ch_type, title, body)
        try:
            ok = await _notify_async(ap, title=t, body=b)
            results.append(
                ChannelSendResult(
                    channel_id=ch["id"],
                    channel_type=ch_type,
                    ok=bool(ok),
                    error="" if ok else "apprise returned False",
                )
            )
        except Exception as e:  # noqa: BLE001 — caller gets the string
            results.append(
                ChannelSendResult(
                    channel_id=ch["id"],
                    channel_type=ch_type,
                    ok=False,
                    error=str(e)[:500],
                )
            )
    return results


async def _notify_async(ap, *, title: str, body: str) -> bool:
    """Run apprise.notify — prefer async if present, else call sync.

    Apprise has async_notify in recent versions; fallback keeps us
    forward-compatible without forcing a version pin beyond what works.
    """
    if hasattr(ap, "async_notify"):
        return bool(await ap.async_notify(title=title, body=body))
    # Sync call is fine — Apprise uses threads internally for fan-out.
    return bool(ap.notify(title=title, body=body))


async def test_send(db: aiosqlite.Connection, channel_id: int, *, user_id: Optional[str] = None) -> ChannelSendResult:
    """Send a test notification to a single channel.

    Ownership check: when ``user_id`` is supplied the SELECT filters by
    both ``id`` and ``user_id`` so the service boundary itself refuses to
    dispatch to a channel the caller does not own. Defense-in-depth — the
    HTTP layer already does the check, but future callers (ARQ tasks,
    admin routes, digest path) that forget it won't leak here either.
    """
    apprise = _get_apprise_cls()
    db.row_factory = aiosqlite.Row
    if user_id is None:
        cur = await db.execute("SELECT * FROM user_channels WHERE id = ?", (channel_id,))
    else:
        cur = await db.execute(
            "SELECT * FROM user_channels WHERE id = ? AND user_id = ?",
            (channel_id, user_id),
        )
    row = await cur.fetchone()
    if row is None:
        return ChannelSendResult(channel_id=channel_id, channel_type="", ok=False, error="channel not found")
    url = decrypt(row["credential_encrypted"])
    ap = apprise.Apprise()
    ap.add(url)
    t, b = format_payload(
        row["channel_type"],
        "Job360 test notification",
        "If you see this, your channel is wired up correctly.",
    )
    try:
        ok = await _notify_async(ap, title=t, body=b)
        return ChannelSendResult(
            channel_id=row["id"],
            channel_type=row["channel_type"],
            ok=bool(ok),
            error="" if ok else "apprise returned False",
        )
    except Exception as e:  # noqa: BLE001
        return ChannelSendResult(
            channel_id=row["id"],
            channel_type=row["channel_type"],
            ok=False,
            error=str(e)[:500],
        )
