"""Sign a test user in through the magic-link flow (the only way in).

``sign_in`` (sync ``TestClient``) and ``async_sign_in`` (``httpx.AsyncClient``)
mint a ``magic_link_tokens`` row and consume it through
``POST /api/auth/magic-link/consume``, the same route a real emailed link
reaches. Consuming finds-or-creates the user by lower-cased email, marks the
address verified and sets the session cookie on the client.

Both return ``(cookie, user_id)``. Nothing here holds a database path at import
time (a stale path breaks per-test schema isolation); it is read per call.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

_CONSUME = "/api/auth/magic-link/consume"


def _mint_token(email: str) -> str:
    from src.core import settings
    from src.repositories import pgsync
    from src.services.auth import tokens

    raw, token_hash = tokens.generate_token()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute(
            "INSERT INTO magic_link_tokens (email, token_hash, expires_at) VALUES (?, ?, ?)",
            (email, token_hash, expires),
        )
        conn.commit()
    finally:
        conn.close()
    return raw


def _result(resp: Any) -> tuple[str, str]:
    assert resp.status_code == 200, resp.text
    cookie = resp.cookies.get("job360_session")
    assert cookie, "magic-link consume set no session cookie"
    return str(cookie), str(resp.json()["id"])


def sign_in(client: Any, email: str) -> tuple[str, str]:
    """Sync: sign ``email`` in on a ``TestClient``; its cookie jar is now signed in."""
    return _result(client.post(_CONSUME, json={"token": _mint_token(email)}))


async def async_sign_in(client: Any, email: str) -> tuple[str, str]:
    """Async: the same, for an ``httpx.AsyncClient``."""
    return _result(await client.post(_CONSUME, json={"token": _mint_token(email)}))
