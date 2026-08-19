"""Seed a new user's notification defaults — the fix for issue #318.

The bug this closes
-------------------
Job360 is a job-**alert** product that had never alerted anyone. Not because
delivery was broken — the machine is healthy — but because **nothing in the
product ever wrote a ``notification_rules`` row**. ``save_user_notification_rule``
had exactly one caller (the PUT route), and the settings page hid that form
until the user had already added a channel. Both delivery tables therefore
stayed empty forever, and every downstream path is a no-op on an empty table:

* ``main._enqueue_notifications`` returns 0 before ``dispatch()`` is reached;
* ``tasks.notification_tick`` selects ``WHERE r.enabled = 1`` → zero rows,
  every five minutes, forever;
* ``dispatcher.dispatch`` loops over an empty channel list — no send, no ledger
  row, no exception. Silence indistinguishable from success.

So the product shipped opt-in-twice, through two separate screens, in a forced
order nothing told the user about. This module makes it on-by-default instead.

Rule #29 ("empty shelves stay SILENT") does **not** forbid this. That rule
governs *match preferences* — an unset preference must never become a penalty,
a per-job zero or a guess. A notification rulebook is not a match preference;
it is the delivery default of a product whose entire promise is delivery. It
is visible, editable and switchable off on ``/settings/notifications``.

Rule #23 (``UNIQUE(user_id)``, one rulebook per user) is honoured by
``ON CONFLICT (user_id) DO NOTHING`` — register is retryable and its
duplicate-email path deliberately does not raise, so an unguarded insert could
``IntegrityError`` a signup that had already committed its user row.

Everything here is a PARAMETER, not a hardcode: the mode, the threshold, the
send time and the master switch all read from the environment, so the owner can
retune or disable seeding without a code deploy.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from src.repositories import pg
from src.services.channels.email_url import build_email_apprise_url

logger = logging.getLogger("job360.notifications.defaults")

# The score a job must clear before it is worth telling someone about.
#
# 30 is not a guess: it is MATCHER_THRESHOLD, the bar this system already uses
# for "worth spending an LLM call on". A job good enough to judge is good
# enough to mention. Env-overridable so the number can move with the score
# distribution rather than rotting the way the old hardcoded 60 did.
DEFAULT_SCORE_THRESHOLD = int(os.getenv("NOTIFY_SCORE_THRESHOLD", "30"))

# 'daily', never 'instant'. This is the safety interlock on the whole change:
# in 'daily' mode dispatch() gate 3 routes matches into
# ``user_notification_digests`` and ``send_bundle`` mails ONE bundle, while
# 'instant' mails one message per job. The nightly ``refresh_catalog`` fan-out
# is ~280 jobs per tick, so seeding 'instant' would mean up to 280 separate
# emails per user per night the moment that path is switched on.
DEFAULT_NOTIFY_MODE = os.getenv("NOTIFY_DEFAULT_MODE", "daily")

DEFAULT_DAILY_SEND_TIME = os.getenv("NOTIFY_DEFAULT_SEND_TIME", "08:00")
DEFAULT_INTERVAL_HOURS = int(os.getenv("NOTIFY_DEFAULT_INTERVAL_HOURS", "6"))

# Shown in the channels list so the user can recognise and remove it.
ACCOUNT_EMAIL_DISPLAY_NAME = "Your account email"


def seeding_enabled() -> bool:
    """Return True unless the owner has switched auto-seeding off.

    Read at call time (not import time) so the switch takes effect on a restart
    without a redeploy, and so tests can flip it.
    """
    return os.getenv("NOTIFY_SEED_DEFAULTS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _insert_default_rule(db: pg.Connection, user_id: str) -> bool:
    """Insert the default rulebook for ``user_id``. Returns True if it created one.

    Idempotent by ``ON CONFLICT (user_id) DO NOTHING`` (rule #23). A False
    return means the user already had a rulebook and we left it untouched —
    never overwrite a choice the user has already made.
    """
    now = _now_iso()
    cur = await db.execute(
        """
        INSERT INTO notification_rules
            (user_id, score_threshold, notify_mode, interval_hours,
             daily_send_time, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (
            user_id,
            DEFAULT_SCORE_THRESHOLD,
            DEFAULT_NOTIFY_MODE,
            DEFAULT_INTERVAL_HOURS,
            DEFAULT_DAILY_SEND_TIME,
            now,
            now,
        ),
    )
    await db.commit()
    return bool(cur.rowcount)


async def _insert_account_email_channel(
    db: pg.Connection, user_id: str, email: str
) -> bool:
    """Insert an account-email delivery channel. Returns True if it created one.

    Returns False (quietly) when no email transport is configured — a deploy
    without a Resend key or SMTP credentials must not fail a sign-in.
    """
    from src.services.channels import crypto  # local: keeps Fernet off the import path

    try:
        url = build_email_apprise_url(email)
    except ValueError:
        logger.warning("account-email channel skipped: unusable address for user=%s", user_id)
        return False
    if url is None:
        logger.warning(
            "account-email channel skipped for user=%s: no RESEND_API_KEY and no "
            "SMTP_EMAIL/SMTP_PASSWORD, so nothing could deliver it",
            user_id,
        )
        return False

    await db.execute(
        """
        INSERT INTO user_channels(user_id, channel_type, display_name,
                                  credential_encrypted, enabled)
        VALUES(?, 'email', ?, ?, 1)
        """,
        (user_id, ACCOUNT_EMAIL_DISPLAY_NAME, crypto.encrypt(url)),
    )
    await db.commit()
    return True


async def seed_notification_defaults(
    db: pg.Connection,
    *,
    user_id: str,
    email: Optional[str] = None,
) -> dict[str, Any]:
    """Give ``user_id`` a working notification setup. Never raises.

    Called from every path that can bring a user into existence or prove their
    address:

    * ``POST /api/auth/register`` — rulebook only. The address is **unproven**
      there, and seeding a delivery channel for an unproven address would let
      anyone register with a victim's email and aim job alerts at a mailbox
      they do not own.
    * ``magic_link.consume_magic_link`` and
      ``email_verification.confirm_email_verification`` — rulebook **and** the
      account-email channel, because clicking the emailed link is the proof of
      ownership that register lacks.

    The channel is seeded **only in the same call that created the rulebook**,
    which happens at most once per user. That is what stops a user who
    deliberately deletes the seeded channel from having it silently resurrected
    on their next sign-in.

    :param db: an open connection; the caller owns its lifetime.
    :param user_id: the user to set up.
    :param email: their address. When None, only the rulebook is seeded.
    :returns: ``{"rule_seeded": bool, "channel_seeded": bool}``.
    """
    result: dict[str, Any] = {"rule_seeded": False, "channel_seeded": False}
    if not seeding_enabled():
        return result

    try:
        result["rule_seeded"] = await _insert_default_rule(db, user_id)
    except Exception as exc:  # noqa: BLE001 — a seed must never break signup
        logger.warning("notification rule seed failed for user=%s: %s", user_id, exc)
        return result

    if email and result["rule_seeded"]:
        try:
            result["channel_seeded"] = await _insert_account_email_channel(
                db, user_id, email
            )
        except Exception as exc:  # noqa: BLE001 — same: never break sign-in
            logger.warning(
                "account-email channel seed failed for user=%s: %s", user_id, exc
            )

    if result["rule_seeded"]:
        logger.info(
            "seeded notification defaults user=%s mode=%s threshold=%s channel=%s",
            user_id,
            DEFAULT_NOTIFY_MODE,
            DEFAULT_SCORE_THRESHOLD,
            result["channel_seeded"],
        )
    return result
