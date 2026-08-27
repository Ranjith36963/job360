"""One-click unsubscribe tokens (wiring.md W-23).

The digest had no unsubscribe link at all. The only way to stop the emails was to log
in and delete the channel — which is a deliverability problem before it is a legal one:
a recipient who cannot find an unsubscribe link presses "spam" instead, and that is the
single worst signal a sending domain can collect.

DESIGN — stateless, derived, no table:

    token = "{user_id}.{hmac_sha256(secret, user_id)[:32]}"

* No storage, so no migration, no cleanup job, and no row that can drift out of sync
  with the account it refers to.
* Not guessable: forging one requires SESSION_SECRET.
* Not an account takeover if leaked. The token authorises exactly ONE irreversible-in-
  the-safe-direction action — turning notifications OFF. The worst a leaked token buys
  an attacker is silence, and the user can turn them back on by logging in.
* Revocable in bulk by rotating SESSION_SECRET, same as sessions.

Deliberately NOT time-limited. An unsubscribe link must work in a year-old email — a
recipient discovering an ancient email and being told "this link has expired, please log
in" is exactly the dead end this exists to remove.
"""

from __future__ import annotations

import hashlib
import hmac
import os

# 128 bits of the digest. Enough that forging is infeasible, short enough that the
# whole link stays readable in a plain-text email.
_SIG_LENGTH = 32


def _secret() -> bytes:
    """The signing key. Falls back to a constant ONLY when unset (dev/test).

    A missing SESSION_SECRET already breaks sessions long before it reaches here, so
    this fallback is never the thing standing between a real deployment and safety —
    it just keeps the module importable in a bare test environment.
    """
    return (os.environ.get("SESSION_SECRET") or "job360-dev-unsubscribe").encode()


def make_token(user_id: str) -> str:
    """Build the unsubscribe token for ``user_id``."""
    sig = hmac.new(_secret(), user_id.encode(), hashlib.sha256).hexdigest()[:_SIG_LENGTH]
    return f"{user_id}.{sig}"


def verify_token(token: str | None) -> str | None:
    """Return the user id a token authorises, or None if it is not ours.

    Uses ``compare_digest`` so a wrong signature cannot be discovered one character at
    a time by timing the response.
    """
    if not token or "." not in token:
        return None
    user_id, _, sig = token.rpartition(".")
    if not user_id or not sig:
        return None
    expected = hmac.new(_secret(), user_id.encode(), hashlib.sha256).hexdigest()[:_SIG_LENGTH]
    if not hmac.compare_digest(sig, expected):
        return None
    return user_id


def unsubscribe_line(site_base_url: str, user_id: str) -> str:
    """The line appended to every outbound digest.

    Says what it does in plain words. "Manage" or "preferences" hides the action the
    recipient is looking for, and a recipient who cannot find it presses spam.
    """
    base = site_base_url.rstrip("/")
    return f"Stop these emails: {base}/unsubscribe?token={make_token(user_id)}"
