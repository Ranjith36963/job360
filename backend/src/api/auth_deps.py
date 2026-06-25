"""FastAPI auth dependencies — cookie-based session resolution.

Usage::

    @router.get("/me")
    async def me(user: CurrentUser = Depends(require_user)):
        return {"id": user.id, "email": user.email}

When the session cookie is missing / tampered / expired, raises HTTP 401.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import aiosqlite
from fastapi import Cookie, Depends, HTTPException, Request, status

from src.core.settings import DB_PATH
from src.services.auth import sessions as auth_sessions

SESSION_COOKIE_NAME = "job360_session"


def _secret() -> str:
    """Return the HMAC secret for session cookies.

    Fail-closed: raises if ``SESSION_SECRET`` is unset. A committed default
    would silently sign production cookies with a value visible in git log,
    so we refuse to serve traffic without an explicit key. Tests must set
    the env var (the fixtures in ``test_auth_routes.py`` /
    ``test_channels_routes.py`` do this via ``monkeypatch.setenv``;
    ``test_auth_sessions.py`` passes ``secret=`` explicitly).
    """
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET env var is required. Generate with: "
            "python -c 'import secrets; print(secrets.token_urlsafe(64))'"
        )
    return secret


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    email_verified: bool = False


async def _current_user_from_cookie(
    cookie: Optional[str],
) -> Optional[CurrentUser]:
    if not cookie:
        return None
    user_id = await auth_sessions.resolve_session(
        str(DB_PATH), cookie, secret=_secret()
    )
    if user_id is None:
        return None
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, email, email_verified_at FROM users "
            "WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    return CurrentUser(
        id=row["id"],
        email=row["email"],
        email_verified=row["email_verified_at"] is not None,
    )


async def require_user(
    request: Request,
    job360_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> CurrentUser:
    user = await _current_user_from_cookie(job360_session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    # Stash user_id on request.state so the access-log middleware can record WHO
    # made the request (request.state is shared across the middleware boundary).
    request.state.user_id = user.id
    return user


async def optional_user(
    request: Request,
    job360_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Optional[CurrentUser]:
    user = await _current_user_from_cookie(job360_session)
    if user is not None:
        request.state.user_id = user.id
    return user


async def require_verified_user(
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI DI idiom
) -> CurrentUser:
    """Like ``require_user`` but also requires a confirmed email (Finding #15).

    Gate app/data routes with this so unverified users can't use the product
    until they confirm their email. Auth / verify-email / account / logout /
    /me routes must keep using ``require_user`` so an unverified user can still
    verify, manage their account, and sign out. Returns HTTP 403 with detail
    ``email_not_verified`` (distinct from the 401 'authentication required').
    """
    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="email_not_verified",
        )
    return user
