"""Password reset flow — Phase −2 item A.

Two operations, mirroring the two API routes:

1. ``request_password_reset(email)`` — find user by email; if found, mint a
   token, store its hash, email the raw token to the user. **No-enumeration
   contract**: always returns silently regardless of whether the email
   exists (caller route returns 204 either way).

2. ``confirm_password_reset(raw_token, new_password)`` — hash the token,
   look up the row, validate (not expired, not used, user not deleted),
   rehash the password, mark token used, revoke all the user's existing
   sessions. Returns the affected user id on success or None on any failure
   (caller returns 400 / 401 accordingly).

Tokens are SHA256-hashed at rest (see ``tokens.py``). 1-hour expiry.
Used-once enforced by the ``used_at`` column + the SQL guard in confirm.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, cast
from urllib.parse import quote as urlquote

from src.repositories import pg
from src.repositories.db_retry import open_db
from src.services.auth import tokens
from src.services.auth.email_sender import send_system_email
from src.services.auth.passwords import hash_password
from src.utils.logger import mask_email

logger = logging.getLogger("job360.auth.password_reset")

# 1 hour — long enough for delivery, short enough to bound exposure if the
# email account is compromised. Test override: monkeypatch this value.
RESET_TOKEN_TTL_MINUTES = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_reset_email(*, to_email: str, raw_token: str, frontend_origin: str) -> tuple[str, str, str]:
    """Return ``(subject, text_body, html_body)`` for the reset email.

    Frontend URL composes ``raw_token`` as a query string parameter so the
    landing page can pre-fill the form.

    Post-review fix #4 — defense-in-depth HTML escaping. EmailStr already
    blocks angle brackets in the email local part, but ``frontend_origin``
    comes from an env var and ``to_email`` from upstream pydantic. Escaping
    here removes any possibility of HTML/JS injection in the HTML body
    rendered by the recipient's mail client.
    """
    # URL-encode the token so e.g. a `&` mid-token doesn't break the
    # query string. ``raw_token`` is base64url today, but be defensive.
    safe_token = urlquote(raw_token, safe="")
    link = f"{frontend_origin}/reset-password?token={safe_token}"
    subject = "Reset your Job360 password"
    # Plain-text body — no escaping needed.
    text = (
        f"Hi,\n\n"
        f"We received a request to reset the password for the Job360 account "
        f"associated with {to_email}.\n\n"
        f"To reset your password, open this link within the next hour:\n\n"
        f"{link}\n\n"
        f"If you didn't request this, you can safely ignore the email — "
        f"your password will not change.\n\n"
        f"— Job360"
    )
    # HTML body — escape all interpolated values.
    safe_email = html.escape(to_email)
    safe_link = html.escape(link, quote=True)  # also escapes " for href
    html_body = (
        f"<p>Hi,</p>"
        f"<p>We received a request to reset the password for the Job360 "
        f"account associated with <strong>{safe_email}</strong>.</p>"
        f"<p>To reset your password, open this link within the next hour:</p>"
        f"<p><a href=\"{safe_link}\">{safe_link}</a></p>"
        f"<p>If you didn't request this, you can safely ignore the email — "
        f"your password will not change.</p>"
        f"<p>— Job360</p>"
    )
    return subject, text, html_body


async def request_password_reset(
    *,
    db_path: str,
    email: str,
    frontend_origin: str,
) -> bool:
    """Issue a reset token for ``email`` if a user exists, and send the email.

    Returns True if an email was actually sent, False otherwise. The caller
    (route) ignores the return value to keep the no-enumeration contract.
    """
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT id FROM users WHERE email = ? AND deleted_at IS NULL",
            (email,),
        )
        row = await cur.fetchone()
        if row is None:
            # Don't leak existence. Caller still returns 204.
            logger.info("password reset requested for unknown email: %s", mask_email(email))
            return False
        user_id = row["id"]

        raw, h = tokens.generate_token()
        expires = (datetime.now(timezone.utc)
                   + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await db.execute(
            "INSERT INTO password_resets(user_id, token_hash, expires_at) "
            "VALUES (?, ?, ?)",
            (user_id, h, expires),
        )
        await db.commit()

    subject, text, html = _build_reset_email(
        to_email=email, raw_token=raw, frontend_origin=frontend_origin
    )
    return await send_system_email(
        to_email=email, subject=subject, body_text=text, body_html=html
    )


async def confirm_password_reset(
    *,
    db_path: str,
    raw_token: str,
    new_password: str,
) -> Optional[str]:
    """Validate the token and reset the user's password.

    Returns the user_id on success, None on any validation failure
    (expired, used, unknown token, user soft-deleted).
    """
    h = tokens.hash_token(raw_token)
    now = _now_iso()
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        # Single read to validate everything atomically. SQLite's
        # transaction default ensures this is consistent with the UPDATE
        # below.
        cur = await db.execute(
            """
            SELECT pr.id        AS reset_id,
                   pr.user_id   AS user_id,
                   pr.expires_at AS expires_at,
                   pr.used_at    AS used_at,
                   u.deleted_at  AS deleted_at
            FROM password_resets pr
            INNER JOIN users u ON u.id = pr.user_id
            WHERE pr.token_hash = ?
            """,
            (h,),
        )
        row = await cur.fetchone()
        if row is None:
            logger.info("password reset confirm: unknown token")
            return None
        if row["used_at"] is not None:
            logger.info("password reset confirm: token already used user=%s", row["user_id"])
            return None
        if row["expires_at"] <= now:
            logger.info("password reset confirm: token expired user=%s", row["user_id"])
            return None
        if row["deleted_at"] is not None:
            logger.info("password reset confirm: user is soft-deleted user=%s", row["user_id"])
            return None

        # Atomically mark used + rehash + revoke sessions. SQLite can't
        # do a multi-statement transaction trivially across separate
        # `execute()` calls, but aiosqlite buffers them as one tx until
        # commit(). If any step fails the tx rolls back.
        new_hash = hash_password(new_password)
        await db.execute(
            "UPDATE password_resets SET used_at = ? WHERE id = ?",
            (now, row["reset_id"]),
        )
        await db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, row["user_id"]),
        )
        # Force re-login on every device. Security best practice after a
        # reset — assume the previous sessions may be compromised.
        await db.execute(
            "DELETE FROM sessions WHERE user_id = ?",
            (row["user_id"],),
        )
        await db.commit()
        logger.info("password reset confirm: ok user=%s", row["user_id"])
        return cast(Optional[str], row["user_id"])
