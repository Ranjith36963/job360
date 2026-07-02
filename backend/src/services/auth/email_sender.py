"""Transactional email sender for system messages.

Distinct from ``services/channels/dispatcher.py`` which delivers to per-user
configured channels via Apprise. Here we send to *one* email address — the
user's own — using the system SMTP credentials (``SMTP_EMAIL`` /
``SMTP_PASSWORD``). Use cases: password reset link, email verification link,
account notices.

In production this should be wired to Amazon SES (Phase 1 of LAUNCH_PLAN.md
— same Apprise URL shape, different SMTP host). For dev / Phase −2 the
existing Gmail App Password setup is enough to verify the flow.

Failure model:
- If ``SMTP_EMAIL`` / ``SMTP_PASSWORD`` are unset, ``send_system_email``
  logs at WARNING and *returns False* — never raises. Callers can either
  treat that as a soft failure (don't 5xx the password-reset request — see
  Phase −2 plan note about no-enumeration) or surface it in dev console.
- Network or auth failures during send are caught, logged, and produce
  False. The route layer translates this into a 204 (no enumeration)
  rather than a 500.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("job360.auth.email_sender")


def _smtp_config() -> Optional[tuple[str, str]]:
    """Return ``(smtp_email, smtp_password)`` if both env vars set, else None."""
    email = os.environ.get("SMTP_EMAIL")
    password = os.environ.get("SMTP_PASSWORD")
    if not email or not password:
        return None
    return email, password


async def send_system_email(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
) -> bool:
    """Send a single transactional email. Returns True on success, False on failure.

    Never raises — the auth flows that call this must remain robust to SMTP
    outages. A False return surfaces in logs at WARNING with the underlying
    cause so an operator can diagnose post-hoc.
    """
    from_addr = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_EMAIL") or "onboarding@resend.dev"

    # Prefer Resend's HTTP API (port 443). Railway (like most hosts) BLOCKS
    # outbound SMTP ports (25/465/587), so smtplib just times out in prod. Worse,
    # this coroutine previously called blocking smtplib INLINE, so that 60s stall
    # froze the whole event loop (login and every request hung behind it). An
    # async HTTP POST fixes both: 443 is open, and it never blocks the loop.
    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key and os.environ.get("SMTP_PASSWORD", "").startswith("re_"):
        resend_key = os.environ["SMTP_PASSWORD"]
    if resend_key:
        try:
            import httpx

            payload: dict = {"from": from_addr, "to": [to_email], "subject": subject, "text": body_text}
            if body_html:
                payload["html"] = body_html
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}"},
                    json=payload,
                )
            if resp.status_code >= 400:
                logger.warning(
                    "send_system_email (resend) failed: to=%s subject=%s status=%s body=%s",
                    to_email, subject, resp.status_code, resp.text[:300],
                )
                return False
            logger.info("send_system_email (resend) ok: to=%s subject=%s", to_email, subject)
            return True
        except Exception as exc:  # noqa: BLE001 — never raise to the auth flow
            logger.warning("send_system_email (resend) error: to=%s subject=%s err=%s", to_email, subject, exc)
            return False

    # Fallback: real SMTP. Run BLOCKING smtplib in a worker thread so a slow or
    # blocked SMTP server can never stall the async event loop again.
    cfg = _smtp_config()
    if cfg is None:
        logger.warning(
            "send_system_email skipped: no RESEND_API_KEY and SMTP_EMAIL/SMTP_PASSWORD not set. to=%s subject=%s",
            to_email, subject,
        )
        return False
    smtp_email, smtp_password = cfg

    def _send_blocking() -> None:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("SMTP_FROM") or smtp_email
        msg["To"] = to_email
        msg.set_content(body_text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")
        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(os.environ.get("SMTP_USER") or smtp_email, smtp_password)
            smtp.send_message(msg)

    try:
        import asyncio

        await asyncio.to_thread(_send_blocking)
    except Exception as exc:  # noqa: BLE001 — intentionally broad
        logger.warning("send_system_email failed: to=%s subject=%s err=%s", to_email, subject, exc)
        return False
    logger.info("send_system_email ok: to=%s subject=%s", to_email, subject)
    return True
