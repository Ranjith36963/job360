"""Security notices — one transactional email when something that grants or
changes access to the account happens (gap roadmap slice 36, G400).

Three kinds, all sent to the account's CURRENT address:

- ``oauth_grant``      an assistant was connected (``client_name``)
- ``token_created``    a personal ``j360_`` token was minted (``token_name``)
- ``password_changed`` the password was changed or reset

Each says when (UTC), what happened, and how to cut every assistant off: a
password reset revokes all tokens and grants (``sessions.revoke_agent_access``).
A notice NEVER carries a token value, a client secret or an IP address.

Sending never breaks or slows the request beyond the one HTTP/SMTP call the
other system emails already make: :func:`send_security_notice` catches
everything, logs ``security_notice_failed`` (ids only) and returns False.
``client_name`` / ``token_name`` are untrusted text (an OAuth client names
itself at registration), so they are stripped of control characters and capped
before they reach a subject line (header injection) or a body.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any

from src.core import settings
from src.repositories import pg
from src.repositories.db_retry import open_db
from src.services.auth.email_sender import send_system_email
from src.utils.logger import safe_log_value

logger = logging.getLogger("job360.auth.security_notices")

KINDS = ("oauth_grant", "token_created", "password_changed")
MAX_NAME_CHARS = 80
# C0/C1 controls (CR, LF, NUL, DEL, NEL ...) plus the Unicode line/paragraph separators.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")


def sanitize_name(value: Any, fallback: str) -> str:
    """Make untrusted text safe for a subject line and a body: control
    characters and newlines collapse to a space, then 80 characters at most."""
    text = _CONTROL_RE.sub(" ", str(value or "")).strip()
    text = " ".join(text.split())[:MAX_NAME_CHARS].strip()
    return text or fallback


def _build(kind: str, **facts: Any) -> tuple[str, str, str]:
    """``(subject, text, html)`` for one notice. Raises ``ValueError`` on an unknown kind."""
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if kind == "oauth_grant":
        name = sanitize_name(facts.get("client_name"), "An assistant")
        subject = f"{name} was connected to your Job360 account"
        what = f"{name} was connected to your Job360 account. It can now read and write your job-hunt records."
    elif kind == "token_created":
        name = sanitize_name(facts.get("token_name"), "unnamed")
        subject = f"A new assistant token '{name}' was created"
        what = f"A new assistant token '{name}' was created on your Job360 account."
    elif kind == "password_changed":
        subject = "Your Job360 password was changed"
        what = "Your Job360 password was changed."
    else:
        raise ValueError(f"unknown security notice kind: {kind!r}")
    link = f"{settings.SITE_BASE_URL}/forgot-password"
    warn = f"If this wasn't you, reset your password at {link} — that also disconnects every assistant."
    text = f"Hi,\n\n{what}\n\nWhen: {when}\n\n{warn}\n\n— Job360"
    safe_link = html.escape(link, quote=True)
    html_body = (
        f"<p>Hi,</p>"
        f"<p>{html.escape(what)}</p>"
        f"<p>When: {html.escape(when)}</p>"
        f"<p>If this wasn't you, reset your password at <a href=\"{safe_link}\">{safe_link}</a>"
        f" — that also disconnects every assistant.</p>"
        f"<p>— Job360</p>"
    )
    return subject, text, html_body


async def send_security_notice(db_path: str, user_id: str, kind: str, **facts: Any) -> bool:
    """Email the account owner about ``kind``. True only when the mail went out.

    Never raises: a missing SMTP config, an outage or a bad template must not
    change the status of the request that triggered it. Logs ids only.
    """
    # Ids are logged only as read back from the users row: a caller's user_id can
    # come off the password-reset path, which CodeQL rightly treats as sensitive.
    logged_id: str | None = None
    try:
        subject, text, html_body = _build(kind, **facts)
        async with open_db(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT id, email FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
            )
            row = await cur.fetchone()
        if row is None:
            logger.warning("security_notice_failed: kind=%s reason=no_account", safe_log_value(kind))
            return False
        logged_id = safe_log_value(row["id"])
        sent = await send_system_email(
            to_email=row["email"], subject=subject, body_text=text, body_html=html_body
        )
    except Exception as exc:  # noqa: BLE001 — a notice must never break the request
        logger.warning(
            "security_notice_failed: user=%s kind=%s err=%s", logged_id, safe_log_value(kind), type(exc).__name__,
        )
        return False
    if not sent:
        logger.warning("security_notice_failed: user=%s kind=%s reason=not_sent", logged_id, safe_log_value(kind))
        return False
    logger.info("security_notice_sent: user=%s kind=%s", logged_id, safe_log_value(kind))
    return True
