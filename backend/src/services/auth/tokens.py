"""Token helpers for password reset + email verification.

Single-use cryptographic tokens used by Phase −2 features A (password reset)
and B (email verification). The pattern is shared:

  1. ``generate_token()`` returns ``(raw, sha256_hash)``. Raw is sent in the
     email body / URL; hash is stored at rest. A DB leak alone never yields
     usable links.
  2. The DB row also records ``user_id``, ``expires_at``, and ``used_at``.
     Consume is atomic: ``used_at`` is set in the same UPDATE that the
     caller reads back, so concurrent confirms can't both succeed.
  3. ``verify_token(raw, stored_hash)`` is constant-time via ``secrets``.

The token alphabet is ``secrets.token_urlsafe`` (~43 chars of base64url at
32 raw bytes = 256 bits of entropy — well past brute-force threshold).
"""
from __future__ import annotations

import hashlib
import secrets


# 32 bytes of entropy → 256 bits → ~43 base64url chars.
# Past any brute-force horizon; small enough for URL query strings.
_TOKEN_NBYTES = 32


def generate_token() -> tuple[str, str]:
    """Return ``(raw_token, sha256_hash)``.

    Caller emails the raw to the user and stores the hash in the DB.
    """
    raw = secrets.token_urlsafe(_TOKEN_NBYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    """Return the lowercase hex SHA256 of ``raw``.

    Used at both ends: at issue time to store the hash, and at confirm time
    to look up the row by hashing the URL-supplied token.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    """``secrets.compare_digest`` wrapper for symmetry with ``verify_password``.

    Used when comparing two hashes (both already SHA256'd). Constant-time so
    timing side channels can't peek at the prefix.
    """
    return secrets.compare_digest(a, b)
