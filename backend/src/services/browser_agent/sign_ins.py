"""Saved sign-ins for the browser agent (slice 2, docs/product/BROWSER_AGENT.md).

The user signs in to a job site THEMSELVES, in the live view on the Job360
website. When they press "Keep me signed in", Job360 keeps what the site
itself chose to remember — its cookies and site storage — encrypted, one row
per (user, site). Never a password: Job360 never reads or stores what was
typed. The next time the user's cloud browser starts, the saved sign-ins are
loaded, so the assistant lands on the site already signed in.

Encryption: Fernet. The key is ``BROWSER_SIGN_IN_KEY`` (a Fernet key) when
set, else derived from ``SESSION_SECRET`` with HKDF — so production needs no
new variable, and rotating ``SESSION_SECRET`` simply signs every user out of
every saved site (they sign in again).
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

_HKDF_INFO = b"job360 browser sign-ins v1"


class SignInKeyMissingError(RuntimeError):
    """Neither BROWSER_SIGN_IN_KEY nor SESSION_SECRET is set."""


def _fernet() -> Any:
    from cryptography.fernet import Fernet  # noqa: PLC0415 - lazy (rule #16)
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: PLC0415

    explicit = os.environ.get("BROWSER_SIGN_IN_KEY", "").strip()
    if explicit:
        return Fernet(explicit.encode())
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if not secret:
        raise SignInKeyMissingError("BROWSER_SIGN_IN_KEY or SESSION_SECRET must be set to keep sign-ins")
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_state(state: dict[str, Any]) -> str:
    return str(_fernet().encrypt(json.dumps(state, separators=(",", ":")).encode()).decode())


def decrypt_state(token: str) -> Optional[dict[str, Any]]:
    """The saved state, or None when it cannot be read (key rotated, row damaged)."""
    from cryptography.fernet import InvalidToken  # noqa: PLC0415

    try:
        data = json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return None
    return data if isinstance(data, dict) else None


def site_host(url_or_host: str) -> str:
    """The canonical host of a URL (or a bare host), ``guard.canonical_host``; "" when there is none or it
    cannot be trusted to name one."""
    from src.services.fetch.guard import canonical_host  # noqa: PLC0415

    text = (url_or_host or "").strip()
    if not text:
        return ""
    return canonical_host(text if "://" in text else "https://" + text.removeprefix("//")) or ""


def _applies_to(cookie_domain: str, host: str) -> bool:
    """A cookie for ``.acme.com`` or ``acme.com`` applies to ``jobs.acme.com``."""
    d = (cookie_domain or "").lower().lstrip(".")
    return bool(d) and (host == d or host.endswith("." + d))


def state_for_host(state: dict[str, Any], host: str) -> dict[str, Any]:
    """Only the part of a browser's storage that belongs to ``host``.

    A sign-in on one site never carries another site's cookies into the row:
    the user said "keep me signed in HERE".
    """
    cookies = [c for c in state.get("cookies") or [] if _applies_to(str(c.get("domain", "")), host)]
    origins = [o for o in state.get("origins") or [] if site_host(str(o.get("origin", ""))) == host]
    return {"cookies": cookies, "origins": origins}


def merge_states(states: list[dict[str, Any]]) -> dict[str, Any]:
    """One storage state from several saved sites (later rows win on the same cookie)."""
    cookies: dict[tuple[str, str, str], dict[str, Any]] = {}
    origins: dict[str, dict[str, Any]] = {}
    for st in states:
        for c in st.get("cookies") or []:
            cookies[(str(c.get("name")), str(c.get("domain")), str(c.get("path", "/")))] = c
        for o in st.get("origins") or []:
            origins[str(o.get("origin"))] = o
    return {"cookies": list(cookies.values()), "origins": list(origins.values())}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def save(db: Any, user_id: str, host: str, state: dict[str, Any]) -> int:
    """Keep (or replace) this user's sign-in for ``host``. Returns the cookie count kept."""
    part = state_for_host(state, host)
    now = _now()
    await db._db.execute(
        "INSERT INTO browser_sign_ins (user_id, site_host, state_enc, cookie_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, site_host) DO UPDATE SET "
        "state_enc = EXCLUDED.state_enc, cookie_count = EXCLUDED.cookie_count, updated_at = EXCLUDED.updated_at",
        (user_id, host, encrypt_state(part), len(part["cookies"]), now, now),
    )
    await db._db.commit()
    return len(part["cookies"])


async def list_sites(db: Any, user_id: str) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT site_host, cookie_count, updated_at FROM browser_sign_ins WHERE user_id = ? ORDER BY site_host",
        (user_id,),
    )
    return [
        {"site_host": r["site_host"], "cookie_count": int(r["cookie_count"]), "updated_at": r["updated_at"]}
        for r in await cur.fetchall()
    ]


async def forget(db: Any, user_id: str, host: str) -> bool:
    cur = await db._db.execute(
        "DELETE FROM browser_sign_ins WHERE user_id = ? AND site_host = ?", (user_id, host)
    )
    await db._db.commit()
    return bool(getattr(cur, "rowcount", 0))


async def load_all(db: Any, user_id: str) -> dict[str, Any]:
    """Every saved sign-in of this user as one storage state (unreadable rows are skipped)."""
    cur = await db._db.execute("SELECT state_enc FROM browser_sign_ins WHERE user_id = ?", (user_id,))
    states = [s for s in (decrypt_state(r["state_enc"]) for r in await cur.fetchall()) if s]
    return merge_states(states)
