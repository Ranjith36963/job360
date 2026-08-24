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

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Optional, cast

from src.repositories import pg
from src.services.channels.crypto import decrypt
from src.services.channels.ssrf_guard import assert_public_http_url
from src.services.notifications.defaults import DEFAULT_SCORE_THRESHOLD
from src.utils.logger import get_logger

# DEFAULT_SCORE_THRESHOLD is imported, not defined here, so the seeder, this
# dispatcher and the repository layer all read ONE number. Three separate
# defaults (60 / 60 / 30) is how the old unreachable-threshold bug survived as
# long as it did. Full rationale at the use site in dispatch().

logger = get_logger("channels.dispatcher")  # job360.channels.dispatcher → data/logs/

# Deferred import so tests for crypto alone don't pay for Apprise.
_apprise = None


def _get_apprise_cls() -> Any:
    global _apprise
    if _apprise is None:
        import apprise

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
    db: pg.Connection, user_id: str, *, enabled_only: bool = True
) -> list[dict[str, Any]]:
    db.row_factory = pg.Row
    query = "SELECT * FROM user_channels WHERE user_id = ?"
    params: list[Any] = [user_id]
    if enabled_only:
        query += " AND enabled = 1"
    cur = await db.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


def _assert_webhook_url_safe(channel_type: str, apprise_url: str) -> None:
    """Re-resolve a webhook host at SEND time and reject private targets.

    The stored credential for a webhook channel is an Apprise ``json[s]://``
    URL. We map it back to ``http[s]://`` and run the same SSRF guard used at
    create time — this catches a host that was public when the channel was
    created but has since been re-pointed to a private IP (DNS rebinding).

    No-op for ``email`` — its Apprise URL points at our own provider, not at a
    user-supplied host. Raises ``ValueError`` when unsafe.
    """
    if channel_type != "webhook":
        return
    if apprise_url.startswith("jsons://"):
        http_url = "https://" + apprise_url[len("jsons://"):]
    elif apprise_url.startswith("json://"):
        http_url = "http://" + apprise_url[len("json://"):]
    else:
        return
    assert_public_http_url(http_url)


def format_payload(channel_type: str, title: str, body: str) -> tuple[str, str]:
    """Return (title, body) formatted for ``channel_type``.

    Today this is pass-through for both live channels. It is kept as a seam,
    not as dead code: it is the ONE place per-channel templating belongs, so
    the HTML email upgrade lands here instead of being sprayed through the
    worker.

    The Slack ``*bold*`` / Discord ``**bold**`` / Telegram MarkdownV2 branches
    were deleted with those channels on 2026-08-24. `webhook` must stay raw —
    it carries JSON to a user's own tooling, and markup would corrupt it.

    Guard: ``tests/test_channels_dispatcher.py::test_format_payload_does_not_resurrect_chat_markup``
    """
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
        # mypy cannot match a starred ``map[int]`` against time()'s overloads;
        # the runtime call is unchanged.
        start = time(*map(int, quiet_start_str.split(":")))  # type: ignore[arg-type]
        end = time(*map(int, quiet_end_str.split(":")))  # type: ignore[arg-type]
        if start <= end:
            return start <= now_local < end
        # Wraparound: e.g. 23:00–07:00
        return now_local >= start or now_local < end
    except Exception:  # noqa: BLE001 — defensive; default to allow dispatch
        return False


async def _load_notification_rule(db: pg.Connection, user_id: str) -> Optional[dict[str, Any]]:
    """Return the single notification_rules row for user_id or None."""
    try:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT * FROM notification_rules WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001 — table may be missing on legacy DB
        return None


async def _load_user_timezone(db: pg.Connection, user_id: str) -> str:
    """Return the user's timezone string (IANA) from the users table, defaulting to UTC."""
    try:
        db.row_factory = pg.Row
        cur = await db.execute("SELECT timezone FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        if row and row["timezone"]:
            return cast(str, row["timezone"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not load timezone for user %s: %s", user_id, exc)
    return "UTC"


async def _queue_digest(db: pg.Connection, user_id: str, channel: str, job_id: int | None) -> None:
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


def _log_dispatch(user_id: str, job_id: int | None, results: list[ChannelSendResult]) -> None:
    """Gap I — one structured line per channel: did it send / queue / skip, and why."""
    for r in results:
        if r.queued_digest:
            outcome = "queued"
        elif r.skipped:
            outcome = "skipped"
        elif r.ok:
            outcome = "sent"
        else:
            outcome = "failed"
        logger.info(
            "notification_dispatch",
            extra={
                "event": "notification_dispatch",
                "user_id": user_id,
                "job_id": job_id,
                "channel": r.channel_type,
                "channel_id": r.channel_id,
                "outcome": outcome,
                "reason": r.error or "",
            },
        )


async def dispatch(
    db: pg.Connection,
    *,
    user_id: str,
    title: str,
    body: str,
    job_id: int | None = None,
    match_score: int | None = None,
    force: bool = False,
) -> list[ChannelSendResult]:
    """Send ``(title, body)`` to every enabled channel for ``user_id``.

    Channels & Notifications overhaul: one rule per user (not per channel).
    If no rule exists the channel receives the notification immediately
    (backwards-compatible default).

    Rule evaluation order:
      1. rule.enabled=0 → skip all channels
      2. match_score < score_threshold → skip all channels
      3. notify_mode in ('daily', 'every_n_hours') or quiet_hours active
         → queue digest (unless force=True)
      4. Otherwise dispatch immediately via Apprise

    ``job_id`` and ``match_score`` are optional; when absent rule gates that
    inspect them are bypassed (keeps the function usable for non-job
    notifications like test-sends).

    ``force=True`` bypasses mode/quiet-hours gate — used by send_bundle to
    deliver already-queued digests regardless of current mode/time.

    Returns one result per channel attempted.
    """
    apprise = _get_apprise_cls()
    channels = await load_user_channels(db, user_id)
    user_tz = await _load_user_timezone(db, user_id)
    results: list[ChannelSendResult] = []

    # ── Rule consultation ──────────────────────────────────────────────────
    rule = await _load_notification_rule(db, user_id)
    # Compute once for all channels
    rule_enabled = rule is None or bool(rule.get("enabled", 1))
    # 60 USED to be unreachable, and that claim is now STALE — do not repeat it.
    # Re-measured against live production 2026-08-19 over 24,597 feed rows:
    # max 95, avg 15.3, and >=30: 3,016 · >=50: 1,215 · >=60: 534 · >=70: 111.
    # So 60 is reachable today (it was not on 2026-08-03, when the ceiling was
    # 69 and only 6 of 9,429 rows cleared it) — it is merely tight.
    #
    # 30 stays the default because it is not a guess: it is MATCHER_THRESHOLD,
    # the bar this system already uses for "worth spending an LLM call on". A
    # job good enough to judge is good enough to tell someone about.
    #
    # Env-overridable (NOTIFY_SCORE_THRESHOLD) so the number can move with the
    # score distribution rather than rotting again, and product_assertions
    # alarms if whatever it is set to stops being reachable.
    threshold = int(rule.get("score_threshold", DEFAULT_SCORE_THRESHOLD)) if rule else 0
    notify_mode = rule.get("notify_mode", "instant") if rule else "instant"
    in_quiet = False
    if rule:
        qs = rule.get("quiet_hours_start")
        qe = rule.get("quiet_hours_end")
        if qs and qe:
            in_quiet = _is_in_quiet_window(qs, qe, user_tz)

    # Gate 1: rule disabled
    if not rule_enabled:
        results = [
            ChannelSendResult(
                channel_id=ch["id"],
                channel_type=ch["channel_type"],
                ok=True,
                skipped=True,
                error="rule disabled",
            )
            for ch in channels
        ]
        _log_dispatch(user_id, job_id, results)
        return results

    # Gate 2: score threshold
    if match_score is not None and match_score < threshold:
        results = [
            ChannelSendResult(
                channel_id=ch["id"],
                channel_type=ch["channel_type"],
                ok=True,
                skipped=True,
                error=f"score {match_score} < threshold {threshold}",
            )
            for ch in channels
        ]
        _log_dispatch(user_id, job_id, results)
        return results

    for ch in channels:
        ch_type = ch["channel_type"]

        # Gate 3+4: bundle mode or quiet hours (unless force=True)
        if not force and (notify_mode in ("daily", "every_n_hours") or in_quiet):
            await _queue_digest(db, user_id, ch_type, job_id)
            results.append(
                ChannelSendResult(
                    channel_id=ch["id"],
                    channel_type=ch_type,
                    ok=True,
                    queued_digest=True,
                    error="queued for bundle",
                )
            )
            continue

        # Immediate dispatch via Apprise
        url = decrypt(ch["credential_encrypted"])
        # SSRF re-check: block a webhook re-pointed to a private IP after create.
        try:
            _assert_webhook_url_safe(ch_type, url)
        except ValueError as e:
            results.append(
                ChannelSendResult(
                    channel_id=ch["id"],
                    channel_type=ch_type,
                    ok=False,
                    error=str(e)[:500],
                )
            )
            continue
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
                    error="" if ok else "delivery failed - check the channel URL and credentials",
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
    _log_dispatch(user_id, job_id, results)
    return results


async def _notify_async(ap: Any, *, title: str, body: str) -> bool:
    """Run apprise.notify — prefer async if present, else call sync.

    Apprise has async_notify in recent versions; fallback keeps us
    forward-compatible without forcing a version pin beyond what works.
    """
    if hasattr(ap, "async_notify"):
        return bool(await ap.async_notify(title=title, body=body))
    # Sync call is fine — Apprise uses threads internally for fan-out.
    return bool(ap.notify(title=title, body=body))


async def test_send(db: pg.Connection, channel_id: int, *, user_id: Optional[str] = None) -> ChannelSendResult:
    """Send a test notification to a single channel.

    Ownership check: when ``user_id`` is supplied the SELECT filters by
    both ``id`` and ``user_id`` so the service boundary itself refuses to
    dispatch to a channel the caller does not own. Defense-in-depth — the
    HTTP layer already does the check, but future callers (ARQ tasks,
    admin routes, digest path) that forget it won't leak here either.
    """
    apprise = _get_apprise_cls()
    db.row_factory = pg.Row
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
    # SSRF re-check: block a webhook re-pointed to a private IP after create.
    try:
        _assert_webhook_url_safe(row["channel_type"], url)
    except ValueError as e:
        return ChannelSendResult(
            channel_id=row["id"],
            channel_type=row["channel_type"],
            ok=False,
            error=str(e)[:500],
        )
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
            error="" if ok else "delivery failed - check the channel URL and credentials",
        )
    except Exception as e:  # noqa: BLE001
        return ChannelSendResult(
            channel_id=row["id"],
            channel_type=row["channel_type"],
            ok=False,
            error=str(e)[:500],
        )
