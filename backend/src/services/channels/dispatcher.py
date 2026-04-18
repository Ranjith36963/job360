"""Apprise-backed channel dispatcher.

Responsibilities:
- Look up a user's enabled channels.
- Decrypt each credential (Fernet).
- Ask Apprise to ``notify()`` with a per-channel formatted payload.
- Return a per-channel result dict (ok + error).

Idempotency / retry / ledger writes are the CALLER's job (the ARQ task).
This module only knows "send a message now, synchronously from the caller's
event loop, and tell me how it went."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import aiosqlite

from src.services.channels.crypto import decrypt

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


async def dispatch(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    title: str,
    body: str,
) -> list[ChannelSendResult]:
    """Send ``(title, body)`` to every enabled channel for ``user_id``.

    Returns one result per channel attempted.
    """
    apprise = _get_apprise_cls()
    channels = await load_user_channels(db, user_id)
    results: list[ChannelSendResult] = []
    for ch in channels:
        url = decrypt(ch["credential_encrypted"])
        ap = apprise.Apprise()
        ap.add(url)
        t, b = format_payload(ch["channel_type"], title, body)
        try:
            ok = await _notify_async(ap, title=t, body=b)
            results.append(
                ChannelSendResult(
                    channel_id=ch["id"],
                    channel_type=ch["channel_type"],
                    ok=bool(ok),
                    error="" if ok else "apprise returned False",
                )
            )
        except Exception as e:  # noqa: BLE001 — caller gets the string
            results.append(
                ChannelSendResult(
                    channel_id=ch["id"],
                    channel_type=ch["channel_type"],
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


async def test_send(
    db: aiosqlite.Connection, channel_id: int, *, user_id: Optional[str] = None
) -> ChannelSendResult:
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
        cur = await db.execute(
            "SELECT * FROM user_channels WHERE id = ?", (channel_id,)
        )
    else:
        cur = await db.execute(
            "SELECT * FROM user_channels WHERE id = ? AND user_id = ?",
            (channel_id, user_id),
        )
    row = await cur.fetchone()
    if row is None:
        return ChannelSendResult(
            channel_id=channel_id, channel_type="", ok=False, error="channel not found"
        )
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
