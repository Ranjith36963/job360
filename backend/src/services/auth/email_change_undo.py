"""Undo link for an email change (gap roadmap slice 35, migration 0074).

``PATCH /api/auth/users/me/email`` moves the account to a new, unproven
address. Whoever held a stolen session plus the password could do that, and the
real owner would only find out when the next sign-in failed. So a change now:

1. ``create_undo`` — stores a hashed one-time token (24 h) that remembers the
   PREVIOUS email and its previous ``email_verified_at``;
2. ``send_change_notice`` — mails the OLD address a short notice with the undo
   link. The notice carries the NEW address only masked (``mask_email``).

``consume_undo`` is the public route's worker: it validates the token, puts the
old address and its old verified state back, and signs the account out
everywhere (sessions, agent tokens, OAuth grants). Same token mechanics as
``password_reset`` (``tokens.py`` hashes, single-use via ``used_at``).
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote as urlquote

from src.repositories import pg
from src.repositories.db_retry import open_db
from src.services.auth import tokens
from src.services.auth.email_sender import send_system_email
from src.services.auth.sessions import revoke_agent_access
from src.utils.logger import get_audit_logger, mask_email

logger = logging.getLogger("job360.auth.email_change_undo")

# 24 hours — the window the notice promises. Test override: monkeypatch.
UNDO_TOKEN_TTL_HOURS = 24

# consume_undo outcomes. Only OK carries a user id.
OK = "ok"
INVALID = "invalid"  # unknown, used, expired or deleted-user: one generic answer
CONFLICT = "conflict"  # the previous address now belongs to another account


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_undo(
    *,
    db_path: str,
    user_id: str,
    previous_email: str,
    previous_verified_at: Optional[str],
) -> str:
    """Store a hashed undo token for this change and return the RAW token.

    Older tokens are left valid on purpose: an attacker who changes the address
    a second time must not be able to void the owner's first link. Consuming a
    token voids only the tokens issued AFTER it (see ``consume_undo``).
    """
    raw, h = tokens.generate_token()
    expires = (datetime.now(timezone.utc) + timedelta(hours=UNDO_TOKEN_TTL_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with open_db(db_path) as db:
        await db.execute(
            "INSERT INTO email_change_undos(user_id, token_hash, previous_email, previous_verified_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, h, previous_email, previous_verified_at, expires),
        )
        await db.commit()
    logger.info("email change undo token issued user=%s", user_id)
    return raw


def build_change_notice(
    *, new_email: str, raw_token: str, frontend_origin: str, changed_at: str
) -> tuple[str, str, str]:
    """Return ``(subject, text_body, html_body)`` for the old-address notice.

    Only ``mask_email(new_email)`` ever appears in the body. Values are
    HTML-escaped in the HTML part (same defence as ``password_reset``).
    """
    link = f"{frontend_origin}/undo-email-change?token={urlquote(raw_token, safe='')}"
    masked = mask_email(new_email)
    subject = "Your Job360 email address was changed"
    text = (
        f"The email on your Job360 account was changed to {masked} at {changed_at}.\n\n"
        f"If this wasn't you, use this link within 24 hours to undo it and sign out everywhere:\n\n"
        f"{link}\n\n"
        f"If it was you, you can ignore this email.\n\n"
        f"— Job360"
    )
    safe_link = html.escape(link, quote=True)
    html_body = (
        f"<p>The email on your Job360 account was changed to <strong>{html.escape(masked)}</strong> "
        f"at {html.escape(changed_at)}.</p>"
        f"<p>If this wasn't you, use this link within 24 hours to undo it and sign out everywhere:</p>"
        f'<p><a href="{safe_link}">{safe_link}</a></p>'
        f"<p>If it was you, you can ignore this email.</p>"
        f"<p>— Job360</p>"
    )
    return subject, text, html_body


async def send_change_notice(
    *, old_email: str, new_email: str, raw_token: str, frontend_origin: str
) -> bool:
    """Mail the notice to the OLD address. False on a failed send; never raises."""
    changed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject, text, html_body = build_change_notice(
        new_email=new_email, raw_token=raw_token, frontend_origin=frontend_origin, changed_at=changed_at
    )
    return await send_system_email(to_email=old_email, subject=subject, body_text=text, body_html=html_body)


async def consume_undo(*, db_path: str, raw_token: str) -> tuple[str, Optional[str]]:
    """Validate the token and undo the change. Returns ``(outcome, user_id)``.

    ``OK`` restores the previous email and its previous ``email_verified_at``,
    marks the token used, and revokes every session, agent token and OAuth grant
    in the SAME transaction (a failure leaves the token usable for a retry).
    ``CONFLICT`` (another account now owns the previous address) changes
    nothing and leaves the token unused. Anything else is ``INVALID``.
    """
    h = tokens.hash_token(raw_token)
    now = _now_iso()
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            """
            SELECT eu.id AS undo_id, eu.user_id AS user_id, eu.previous_email AS previous_email,
                   eu.previous_verified_at AS previous_verified_at, eu.expires_at AS expires_at,
                   eu.used_at AS used_at, u.deleted_at AS deleted_at
            FROM email_change_undos eu
            INNER JOIN users u ON u.id = eu.user_id
            WHERE eu.token_hash = ?
            """,
            (h,),
        )
        row = await cur.fetchone()
        if row is None:
            logger.info("email change undo: unknown token")
            return INVALID, None
        user_id = str(row["user_id"])
        if row["used_at"] is not None:
            logger.info("email change undo: token already used user=%s", user_id)
            return INVALID, None
        if row["expires_at"] <= now:
            logger.info("email change undo: token expired user=%s", user_id)
            return INVALID, None
        if row["deleted_at"] is not None:
            logger.info("email change undo: user is soft-deleted user=%s", user_id)
            return INVALID, None

        taken = await db.execute(
            "SELECT id FROM users WHERE LOWER(email) = LOWER(?) AND id != ?",
            (row["previous_email"], user_id),
        )
        if await taken.fetchone():
            logger.warning("email change undo: previous address now taken user=%s", user_id)
            return CONFLICT, None

        # Single-use: the guard in the UPDATE makes a concurrent second consume
        # match zero rows.
        claim = await db.execute(
            "UPDATE email_change_undos SET used_at = ? WHERE id = ? AND used_at IS NULL",
            (now, row["undo_id"]),
        )
        if not claim.rowcount:
            logger.info("email change undo: lost the race for the token user=%s", user_id)
            return INVALID, None
        # Undoing this change also undoes every LATER change, so their links die
        # with it: an attacker who changed A->B->C must not take the account back
        # to B with the B-link after the owner restored A. Earlier links stay.
        await db.execute(
            "UPDATE email_change_undos SET used_at = ? WHERE user_id = ? AND id > ? AND used_at IS NULL",
            (now, user_id, row["undo_id"]),
        )
        await db.execute(
            "UPDATE users SET email = ?, email_verified_at = ? WHERE id = ?",
            (row["previous_email"], row["previous_verified_at"], user_id),
        )
        sessions = await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        agents = await revoke_agent_access(db, user_id)
        await db.commit()

    get_audit_logger().info(
        "email_change_undone",
        extra={"event": "email_change_undone", "user_id": user_id, "sessions": sessions.rowcount, **agents},
    )
    logger.info("email change undo: ok user=%s", user_id)
    return OK, user_id
