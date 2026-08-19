"""Build the Apprise URL that delivers email for a user channel.

Why this is its own module: the URL shape is a **production transport
decision**, and it was previously inlined in the ``POST /settings/channels``
route where nothing else could reuse it — or audit it.

The decision it encodes
-----------------------
Railway (like most hosts) **blocks outbound SMTP ports 25/465/587**. That is
not a guess: ``services/auth/email_sender.py`` was rewritten off ``smtplib``
for exactly this reason and says so in its own comments — magic-link email only
started arriving once it moved to Resend's HTTPS API on 443.

Apprise's ``mailtos://`` scheme is plain SMTP. So an email channel built as
``mailtos://`` cannot deliver on Railway; it times out, the dispatcher records
``ok=False``, and the user sees silence. Prefer ``resend://`` (HTTPS:443 — the
transport already proven to work in this deployment) and keep ``mailtos://``
only as the local/self-hosted fallback where SMTP really is reachable.

Credential precedence deliberately mirrors ``email_sender._smtp_config`` /
``send_system_email`` so the alert path and the auth path can never disagree
about which mailbox this platform sends from.
"""
from __future__ import annotations

import os
import re
from typing import Optional

# Lenient e-mail check, shared with the channels route.
#
# ReDoS-safe (CodeQL py/polynomial-redos): the original
# ``^[^@\s]+@[^@\s]+\.[^@\s]+$`` let ``.`` match inside BOTH domain classes, so
# the engine had O(n) ways to split the domain → quadratic backtracking on a
# non-matching tail (8 000 chars took 3.4 s and pinned an API worker). Excluding
# ``.`` from the label class makes the split unambiguous → linear.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

# Apprise's resend plugin restricts the API key to this alphabet
# (``NotifyResend.template_tokens['apikey']['regex']``). A key outside it makes
# ``Apprise.add()`` silently return False, which would look exactly like the
# silent-failure bug this whole change exists to kill — so reject it up front.
_RESEND_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Last-resort sender. Resend accepts this shared domain without verification,
# so a misconfigured deploy degrades to "delivers from an odd address" rather
# than "delivers nothing". Same constant email_sender.py falls back to.
_FALLBACK_FROM = "onboarding@resend.dev"


def _resend_api_key() -> Optional[str]:
    """Return the Resend API key, or None.

    Mirrors ``send_system_email``: some deploys put the Resend key in
    ``SMTP_PASSWORD`` (it is recognisable by its ``re_`` prefix) rather than in
    ``RESEND_API_KEY``, and the auth path already honours both.
    """
    key = os.environ.get("RESEND_API_KEY")
    if not key and os.environ.get("SMTP_PASSWORD", "").startswith("re_"):
        key = os.environ["SMTP_PASSWORD"]
    return key or None


def _from_address() -> str:
    """Return the address this platform sends alerts from."""
    return os.environ.get("SMTP_FROM") or os.environ.get("SMTP_EMAIL") or _FALLBACK_FROM


def build_email_apprise_url(dest: str) -> Optional[str]:
    """Return an Apprise URL delivering to ``dest``, or None if unconfigured.

    Prefers ``resend://{apikey}:{from_addr}/{to}/`` (HTTPS:443, works on
    Railway) and falls back to ``mailtos://`` only when no Resend key is
    present but real SMTP credentials are.

    Returns None when neither transport is configured — callers decide whether
    that is a 503 (an explicit user request) or a silent skip (auto-seeding).

    :param dest: the recipient address.
    :raises ValueError: if ``dest`` is not a syntactically valid address.
    """
    if not EMAIL_RE.match(dest):
        raise ValueError("enter a valid email address")

    api_key = _resend_api_key()
    if api_key and _RESEND_KEY_RE.match(api_key):
        # NOT url-quoted on purpose: Apprise's resend parser rejects a
        # percent-encoded from/to pair outright (`Apprise.add()` returns False).
        # EMAIL_RE has already excluded whitespace and multi-@ inputs, and the
        # key alphabet is checked above, so there is nothing left to escape.
        return f"resend://{api_key}:{_from_address()}/{dest}/"

    smtp_user = os.environ.get("SMTP_EMAIL", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    if smtp_user and smtp_pass:
        from urllib.parse import quote

        smtp_domain = smtp_user.split("@", 1)[1] if "@" in smtp_user else smtp_user
        return (
            f"mailtos://{quote(smtp_user, safe='')}:{quote(smtp_pass, safe='')}"
            f"@{smtp_domain}?to={quote(dest, safe='')}"
        )

    return None
