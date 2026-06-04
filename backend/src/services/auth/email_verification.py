"""Email verification flow — Phase −2 item B.

Three operations:

1. ``request_email_verification(user_id, email)`` — mint a token, store
   its hash, email the raw token. Called from the register route after
   user creation, and from a "resend verification" endpoint.

2. ``confirm_email_verification(raw_token)`` — hash the token, look up
   the row, validate (not expired, not used, user not deleted). On
   success, set ``users.email_verified_at`` to NOW and mark the token
   used. Returns the user_id on success or None on any failure.

3. ``is_email_verified(db_path, user_id)`` — convenience reader used by
   gates (e.g. "block channel creation if email_verified_at IS NULL").

Tokens are SHA256-hashed at rest (shared ``tokens.py`` helpers). 24-hour
expiry — longer than password reset because there's no user-facing
"forgot" loop; if the email goes to spam, a 24-hour link is more
forgiving while still bounding the attack window.

The token row records the email at issue time. If a user's email is
changed via ``PATCH /api/auth/users/me/email``, any old tokens still
point at the *old* address — they validate the *previous* email, not the
current one. The confirm path treats that as a stale token (no-op,
caller resends).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from src.services.auth import tokens
from src.services.auth.email_sender import send_system_email

logger = logging.getLogger("job360.auth.email_verification")

# 24 hours — long enough that a slow email arrives in time; short enough
# that a leaked link doesn't haunt the account forever. Test override:
# monkeypatch this value.
VERIFICATION_TOKEN_TTL_HOURS = 24


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_verification_email(
    *, to_email: str, raw_token: str, frontend_origin: str
) -> tuple[str, str, str]:
    link = f"{frontend_origin}/verify-email?token={raw_token}"
    subject = "Verify your Job360 email address"
    text = (
        f"Welcome to Job360!\n\n"
        f"Please confirm that {to_email} is your email address by opening "
        f"this link in the next 24 hours:\n\n"
        f"{link}\n\n"
        f"If you didn't sign up for Job360, ignore this email — the "
        f"account won't be activated without confirmation.\n\n"
        f"— Job360"
    )
    html = (
        f"<p>Welcome to Job360!</p>"
        f"<p>Please confirm that <strong>{to_email}</strong> is your "
        f"email address by opening this link in the next 24 hours:</p>"
        f"<p><a href=\"{link}\">{link}</a></p>"
        f"<p>If you didn't sign up for Job360, ignore this email — the "
        f"account won't be activated without confirmation.</p>"
        f"<p>— Job360</p>"
    )
    return subject, text, html


async def request_email_verification(
    *,
    db_path: str,
    user_id: str,
    email: str,
    frontend_origin: str,
) -> bool:
    """Issue + send a verification email for ``user_id``.

    Returns True if the email was actually sent (SMTP succeeded), False
    otherwise. Caller (register / resend route) typically returns 204
    regardless so a flaky email backend never breaks registration.
    """
    raw, h = tokens.generate_token()
    expires = (
        datetime.now(timezone.utc) + timedelta(hours=VERIFICATION_TOKEN_TTL_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO email_verifications(user_id, token_hash, email, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, h, email, expires),
        )
        await db.commit()

    subject, text, html = _build_verification_email(
        to_email=email, raw_token=raw, frontend_origin=frontend_origin
    )
    return await send_system_email(
        to_email=email, subject=subject, body_text=text, body_html=html
    )


async def confirm_email_verification(
    *,
    db_path: str,
    raw_token: str,
) -> Optional[str]:
    """Validate the token, mark verified, return user_id (or None on failure)."""
    h = tokens.hash_token(raw_token)
    now = _now_iso()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT ev.id        AS ver_id,
                   ev.user_id   AS user_id,
                   ev.email     AS token_email,
                   ev.expires_at AS expires_at,
                   ev.used_at    AS used_at,
                   u.email       AS current_email,
                   u.deleted_at  AS deleted_at
            FROM email_verifications ev
            INNER JOIN users u ON u.id = ev.user_id
            WHERE ev.token_hash = ?
            """,
            (h,),
        )
        row = await cur.fetchone()
        if row is None:
            logger.info("email verification confirm: unknown token")
            return None
        if row["used_at"] is not None:
            logger.info(
                "email verification confirm: token already used user=%s", row["user_id"]
            )
            return None
        if row["expires_at"] <= now:
            logger.info(
                "email verification confirm: token expired user=%s", row["user_id"]
            )
            return None
        if row["deleted_at"] is not None:
            logger.info(
                "email verification confirm: user soft-deleted user=%s", row["user_id"]
            )
            return None
        # Stale-token guard: the user changed their email between issue
        # and confirm. The token verifies an address that's no longer
        # theirs — treat as no-op.
        if row["token_email"] != row["current_email"]:
            logger.info(
                "email verification confirm: token email mismatch user=%s", row["user_id"]
            )
            return None

        await db.execute(
            "UPDATE email_verifications SET used_at = ? WHERE id = ?",
            (now, row["ver_id"]),
        )
        await db.execute(
            "UPDATE users SET email_verified_at = ? WHERE id = ?",
            (now, row["user_id"]),
        )
        await db.commit()
        logger.info("email verification confirm: ok user=%s", row["user_id"])
        return row["user_id"]


async def is_email_verified(*, db_path: str, user_id: str) -> bool:
    """True iff the user has ever completed email verification."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT email_verified_at FROM users WHERE id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row is not None and row["email_verified_at"] is not None
