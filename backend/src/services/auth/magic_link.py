"""Passwordless magic-link login.

Two operations, mirroring the two API routes:

1. ``request_magic_link(email)`` — mint a single-use token, store its hash,
   and email the raw token as a link to the address. **No-enumeration
   contract**: never raises and returns a bool the route ignores, so the
   response is identical whether or not the email belongs to an account.

2. ``consume_magic_link(raw_token)`` — hash the token, look up an unused,
   unexpired row, mark it used, then **find-or-create** the user by email.
   Clicking the emailed link proves the user controls the address, so we
   also set ``email_verified_at`` (auto-verify). Finally create a session
   and return ``(cookie, user_id, email)``. Returns None on any failure.

Tokens are SHA256-hashed at rest (shared ``tokens.py`` helpers). 15-minute
expiry — short because the whole flow (request → click) happens in one
sitting; a leaked link should not stay usable for long.

Unlike ``email_verification`` the token row is keyed on the EMAIL, not a
user_id — at request time the user may not exist yet. The account is created
lazily on consume, with an argon2 hash of a random secret as its
``password_hash`` (the schema requires one; the user can set a real password
later via the reset flow).
"""
from __future__ import annotations

import html
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote as urlquote

from src.repositories import pg
from src.repositories.db_retry import open_db
from src.services.auth import sessions as auth_sessions
from src.services.auth import tokens
from src.services.auth.email_sender import send_system_email
from src.services.auth.passwords import hash_password
from src.services.notifications.defaults import seed_notification_defaults
from src.utils.logger import mask_email

logger = logging.getLogger("job360.auth.magic_link")

# 15 minutes — the request→click loop happens in one sitting. Short expiry
# bounds the window a leaked link stays usable. Test override: monkeypatch.
MAGIC_LINK_TTL_MINUTES = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Cap on ``next``. Long enough for any real route + query string, short enough
# that nobody can pad an email with kilobytes of attacker-chosen text.
MAX_NEXT_LENGTH = 512


def safe_next_path(raw: str | None) -> str | None:
    """Return ``raw`` if it is a safe same-site path, else ``None``.

    This is the SERVER-SIDE half of the open-redirect guard (wiring.md W-01). The
    browser has its own copy in ``frontend/src/lib/safe-next.ts``, but that one is
    bypassed by POSTing this API directly — and the value ends up inside an email
    WE send, so a bad one is an open redirect carrying our own From: address. It
    must be re-validated here.

    Rejected, and why each matters:

    * anything not starting with ``/`` — an absolute URL or a bare scheme
      (``https://evil.com``, ``javascript:``) leaves the site outright.
    * ``//evil.com`` — protocol-relative: the browser supplies the scheme and
      treats the rest as a HOST, so this is off-site despite the leading slash.
    * any backslash — WHATWG URL parsing folds ``\\`` into ``/``, so ``/\\evil.com``
      becomes ``//evil.com`` in the browser while passing a naive ``startswith("//")``
      check. This is the escape the frontend guard originally missed.
    * control characters (CR, LF, NUL, tab) — header and URL injection into the
      outgoing email.
    * anything over :data:`MAX_NEXT_LENGTH`.

    Returns ``None`` rather than raising: a hostile ``next`` must degrade to an
    ordinary sign-in link, never break the user's ability to log in.
    """
    if not raw or not isinstance(raw, str):
        return None
    if len(raw) > MAX_NEXT_LENGTH:
        return None
    # Control chars anywhere (not just at the edges) — \r \n \0 \t and friends.
    if any(ch < " " or ch == "\x7f" for ch in raw):
        return None
    if "\\" in raw:
        return None
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    return raw


def _dev_echo_link(text_body: str) -> None:
    """Log the sign-in link to the server log — LOCAL DEVELOPMENT ONLY.

    Why this exists: the sign-in token is stored HASHED, so the raw link exists in
    exactly one place — the email. With no email backend configured (a fresh
    checkout, a git worktree with no .env) there is no way to click a magic link,
    which makes the login journey untestable in a real browser. That is what
    blocked rung 4 of wiring_verification.md.

    Why it is safe: it requires an EXPLICIT opt-in env var. It cannot fire from a
    missing variable, a bad default, or a deploy that forgot something — someone
    has to set ``MAGIC_LINK_DEV_ECHO=1`` on purpose. It is additionally only
    reached when the email failed to send, which never happens in production
    (Railway has RESEND_API_KEY set).

    NEVER set this in production: a sign-in link in a log file is a login for
    anyone who can read that log.
    """
    if os.environ.get("MAGIC_LINK_DEV_ECHO") != "1":
        return
    link = next(
        (ln.strip() for ln in text_body.splitlines() if ln.strip().startswith("http")),
        "",
    )
    if link:
        logger.warning("DEV ECHO (MAGIC_LINK_DEV_ECHO=1) sign-in link: %s", link)


def _build_magic_link_email(
    *, to_email: str, raw_token: str, frontend_origin: str, next_path: str | None = None
) -> tuple[str, str, str]:
    """Return ``(subject, text_body, html_body)`` for the sign-in email.

    ``next_path`` is where to send the user AFTER sign-in — it is run through
    :func:`safe_next_path` here rather than at the call site, so no caller can
    route around the guard. An unsafe value is dropped and the link degrades to
    a plain sign-in.

    Defense-in-depth HTML escaping mirrors the sibling helpers in
    ``password_reset.py`` / ``email_verification.py``.
    """
    safe_token = urlquote(raw_token, safe="")
    link = f"{frontend_origin}/auth/magic?token={safe_token}"
    checked_next = safe_next_path(next_path)
    if checked_next:
        # safe="" so the path's own slashes are encoded and cannot break out of
        # the query string.
        link = f"{link}&next={urlquote(checked_next, safe='')}"
    subject = "Sign in to Job360"
    text = (
        f"Hi,\n\n"
        f"Click this link within the next 15 minutes to sign in to Job360:\n\n"
        f"{link}\n\n"
        f"If you didn't request this, you can safely ignore the email.\n\n"
        f"— Job360"
    )
    safe_link = html.escape(link, quote=True)
    html_body = (
        f"<p>Hi,</p>"
        f"<p>Click this link within the next 15 minutes to sign in to Job360:</p>"
        f"<p><a href=\"{safe_link}\">{safe_link}</a></p>"
        f"<p>If you didn't request this, you can safely ignore the email.</p>"
        f"<p>— Job360</p>"
    )
    return subject, text, html_body


async def request_magic_link(
    *,
    db_path: str,
    email: str,
    frontend_origin: str,
    next_path: str | None = None,
) -> bool:
    """Issue + email a magic-link sign-in token for ``email``.

    ``next_path`` is where the user was heading before we asked him to sign in —
    it rides along in the emailed link so the round trip lands him back there
    instead of on the dashboard (wiring.md W-01). It is validated by
    :func:`safe_next_path` inside the email builder, so an unsafe value silently
    degrades to a plain sign-in link rather than failing the login.

    Returns True if the email was actually sent, False otherwise. Never
    raises — the route returns 204 regardless (no-enumeration contract).
    """
    # EMAIL-CASE: normalize before storing. users.email UNIQUE is case-
    # SENSITIVE, so a token minted for 'Alice@Example.COM' used to flow raw
    # into consume's INSERT OR IGNORE, sail past the conflict check (different
    # case ≠ conflict), and mint a SECOND account for the same mailbox.
    email = email.strip().lower()
    try:
        raw, h = tokens.generate_token()
        expires = (
            datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_TTL_MINUTES)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with open_db(db_path) as db:
            await db.execute(
                "INSERT INTO magic_link_tokens(email, token_hash, expires_at) "
                "VALUES (?, ?, ?)",
                (email, h, expires),
            )
            await db.commit()

        subject, text, html_body = _build_magic_link_email(
            to_email=email,
            raw_token=raw,
            frontend_origin=frontend_origin,
            next_path=next_path,
        )
        sent = await send_system_email(
            to_email=email, subject=subject, body_text=text, body_html=html_body
        )
        if not sent:
            _dev_echo_link(text)
        return sent
    except Exception as exc:  # noqa: BLE001 — never raise to the auth flow
        logger.warning("request_magic_link failed: email=%s err=%s", mask_email(email), exc)
        return False


async def consume_magic_link(
    *,
    db_path: str,
    raw_token: str,
    secret: str,
) -> Optional[tuple[str, str, str]]:
    """Validate the token, find-or-create the user, create a session.

    Returns ``(cookie, user_id, email)`` on success, or None on any failure
    (unknown / expired / already-used token). On success the emailed link is
    treated as proof of email ownership, so ``email_verified_at`` is stamped
    (created users start verified; existing unverified users become verified).
    """
    h = tokens.hash_token(raw_token)
    now = _now_iso()
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            """
            SELECT id, email, expires_at, used_at
            FROM magic_link_tokens
            WHERE token_hash = ?
            """,
            (h,),
        )
        row = await cur.fetchone()
        if row is None:
            logger.info("magic link consume: unknown token")
            return None
        if row["used_at"] is not None:
            logger.info("magic link consume: token already used")
            return None
        if row["expires_at"] <= now:
            logger.info("magic link consume: token expired")
            return None

        # EMAIL-CASE belt-and-suspenders: tokens minted BEFORE the request-path
        # normalization shipped may still hold a raw-case address — lowercase
        # here too so the find-or-create below can never split one mailbox
        # into two accounts.
        email = (row["email"] or "").strip().lower()
        # Mark the token used first — single-use enforcement.
        await db.execute(
            "UPDATE magic_link_tokens SET used_at = ? WHERE id = ?",
            (now, row["id"]),
        )

        # Find-or-create the user by email, ATOMICALLY. `INSERT OR IGNORE`
        # (shim → `ON CONFLICT DO NOTHING`) creates the row on first sight and is
        # a safe no-op if the email already exists — active OR soft-deleted (the
        # users.email UNIQUE constraint ignores deleted_at). This removes the old
        # SELECT-then-INSERT race that raised a `UniqueViolation` in prod when two
        # consumes hit the same new email, or when a soft-deleted row still owned
        # it. The schema requires a password_hash, so store an argon2 hash of a
        # random secret (unguessable; the user can set a real one later via reset).
        # Clicking the emailed link proves ownership, so a new row starts verified.
        new_id = uuid.uuid4().hex
        random_pw = secrets.token_urlsafe(32)
        await db.execute(
            "INSERT OR IGNORE INTO users(id, email, password_hash, email_verified_at) "
            "VALUES (?, ?, ?, ?)",
            (new_id, email, hash_password(random_pw), now),
        )
        # Read back the real id: the row we just inserted, or the existing owner.
        cur = await db.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(?)", (email,))
        user_id = (await cur.fetchone())["id"]
        # Prove ownership: verify (keep any existing timestamp) AND reactivate a
        # soft-deleted account — magic-link sign-in brings you back in.
        await db.execute(
            "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?), "
            "deleted_at = NULL WHERE id = ?",
            (now, user_id),
        )
        await db.commit()

        # #318 — seed the notification rulebook + the account-email channel.
        #
        # This path, not just `register`, is where Job360 users actually come
        # from: the INSERT OR IGNORE above creates accounts, so seeding only in
        # register would miss every passwordless signup. It also heals the users
        # who already exist without a rulebook — they never pass through
        # register again, so sign-in is the only place that can reach them.
        #
        # The channel is seeded HERE rather than at register because clicking
        # the emailed link is the proof of address ownership that register
        # lacks.
        #
        # Guarded here as well as inside the helper: the session is about to be
        # issued and the user row is already committed, so anything escaping
        # this line would turn a valid sign-in into a 400 "invalid or expired
        # link". Locking users out of the product is never an acceptable price
        # for a notification default.
        try:
            await seed_notification_defaults(db, user_id=user_id, email=email)
        except Exception as exc:  # noqa: BLE001 — sign-in must still succeed
            logger.warning("notification seed failed for user=%s: %s", user_id, exc)

    cookie = await auth_sessions.create_session(db_path, user_id=user_id, secret=secret)
    logger.info("magic link consume: ok user=%s", user_id)
    return cookie, user_id, email
