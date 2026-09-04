"""FastAPI auth dependencies — session cookie OR personal bearer token.

Usage::

    @router.get("/me")
    async def me(user: CurrentUser = Depends(require_user)):
        return {"id": user.id, "email": user.email}

Two credentials, one resolution order (spec 2026-09-03-mcp-server, R2):

* ``Authorization: Bearer j360_…`` present → token path ONLY. A bad or revoked
  token is 401 even if a valid cookie rides along — an explicit credential is
  never silently downgraded to the ambient one.
* otherwise → the ``job360_session`` cookie.

``CurrentUser.auth_via`` says which one won (``"session"`` / ``"token"``) so
routes that must stay session-only (token management) can refuse tokens.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from src.core import settings
from src.core.settings import DB_PATH
from src.repositories import pg
from src.services.auth import api_tokens as auth_api_tokens
from src.services.auth import oauth_flow as auth_oauth_flow
from src.services.auth import rate_limit as auth_rate_limit
from src.services.auth import sessions as auth_sessions

SESSION_COOKIE_NAME = "job360_session"
_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


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
    auth_via: Literal["session", "token", "oauth"] = "session"
    # RFC 8707 audience the OAuth access token was issued for (spec
    # 2026-09-03-oauth-mcp R6/S13). None for a session or a personal token —
    # only an OAuth bearer ever carries one, and only `/api/mcp` checks it.
    audience: Optional[str] = None


def _client_ip(request: Request) -> str:
    """Same trust rule as routes/auth._client_meta: X-Forwarded-For only behind our proxy."""
    if os.getenv("JOB360_TRUST_PROXY") == "1":
        xff = request.headers.get("x-forwarded-for", "")
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _bearer_from_header(authorization: Optional[str]) -> Optional[str]:
    """The token part of ``Authorization: Bearer <token>``; None when the header is absent."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip()


async def _current_user_from_bearer(request: Request, token: str) -> CurrentUser:
    """Resolve a presented bearer or raise. Never falls back to the cookie.

    Prefix dispatch (spec 2026-09-03-oauth-mcp, R6): ``j360a_`` -> an OAuth
    access token (its own throttle, below); anything else -> a personal
    ``j360_`` token (unchanged). ``"j360a_".startswith("j360_")`` is False,
    so nothing overlaps.
    """
    if token.startswith(auth_oauth_flow.ACCESS_TOKEN_PREFIX):
        return await _current_user_from_oauth_bearer(request, token)
    return await _current_user_from_personal_bearer(request, token)


async def _current_user_from_personal_bearer(request: Request, token: str) -> CurrentUser:
    """Resolve a ``j360_...`` personal API token or raise.

    Failed attempts are rate-limited per client IP (``API_TOKEN_FAIL_MAX_PER_MIN``):
    a 256-bit token cannot be guessed, but a guesser should be slow and loud.

    The token is resolved FIRST and the throttle consulted only on failure: a
    credential that verifies is never brute force, so it must never be refused
    because of someone else's junk. Behind the Next.js rewrite every agent
    shares the proxy's IP (unless ``JOB360_TRUST_PROXY=1``), so a lock checked
    before the lookup would let one bad client 429 every valid token. The cost
    is one indexed hash lookup per junk attempt — cheap, and bounded by 429.
    """
    owner = await auth_api_tokens.resolve(str(DB_PATH), token)
    if owner is None:
        limit = settings.API_TOKEN_FAIL_MAX_PER_MIN
        if limit > 0:
            fail_key = f"api_token_fail:{_client_ip(request)}"
            # is_locked prunes the window; checking it before recording keeps
            # the bucket bounded (same order the login route uses).
            if auth_rate_limit.is_locked(fail_key, max_failures=limit, window_seconds=60):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="too many failed token attempts",
                    headers=_BEARER_CHALLENGE,
                )
            auth_rate_limit.record_failure(fail_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or revoked token",
            headers=_BEARER_CHALLENGE,
        )
    return CurrentUser(
        id=owner.user_id,
        email=owner.email,
        email_verified=owner.email_verified,
        auth_via="token",
    )


async def _current_user_from_oauth_bearer(request: Request, token: str) -> CurrentUser:
    """Resolve a ``j360a_...`` OAuth access token or raise (spec R6).

    Its own throttle key (``oauth_bearer_fail:{ip}``) — never ``api_token_fail``,
    so a guesser against one credential kind can never spend the other's
    budget. Only a hash that matches NO row counts as a failure; an
    expired/revoked token (``hash_known=True``) is the normal hourly state of
    every connected client, not an attack, so it never touches the counter.
    """
    resolution = await auth_oauth_flow.resolve_access_token(str(DB_PATH), token)
    if resolution.owner is None:
        if not resolution.hash_known:
            limit = settings.OAUTH_BEARER_FAIL_MAX_PER_MIN
            if limit > 0:
                fail_key = f"oauth_bearer_fail:{_client_ip(request)}"
                if auth_rate_limit.is_locked(fail_key, max_failures=limit, window_seconds=60):
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="too many failed token attempts",
                        headers=_BEARER_CHALLENGE,
                    )
                auth_rate_limit.record_failure(fail_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or revoked token",
            headers=_BEARER_CHALLENGE,
        )
    owner = resolution.owner
    return CurrentUser(
        id=owner.user_id,
        email=owner.email,
        email_verified=owner.email_verified,
        auth_via="oauth",
        audience=owner.audience,
    )


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
    async with pg.connect(str(DB_PATH)) as db:
        db.row_factory = pg.Row
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


async def resolve_current_user(
    request: Request,
    cookie: Optional[str],
    authorization: Optional[str],
) -> Optional[CurrentUser]:
    """Header present → bearer only (may raise 401/429); else cookie or None.

    Shared by the FastAPI dependencies below and by the raw-ASGI MCP mount,
    which has no ``Depends`` to lean on.
    """
    # A non-Bearer Authorization header (Basic, Digest…) is not ours → cookie path.
    token = _bearer_from_header(authorization)
    if token is not None:
        return await _current_user_from_bearer(request, token)
    return await _current_user_from_cookie(cookie)


async def require_user(
    request: Request,
    job360_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
) -> CurrentUser:
    user = await resolve_current_user(request, job360_session, authorization)
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
    authorization: Optional[str] = Header(default=None),
) -> Optional[CurrentUser]:
    user = await resolve_current_user(request, job360_session, authorization)
    if user is not None:
        request.state.user_id = user.id
    return user


async def require_session_user(
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI DI idiom
) -> CurrentUser:
    """A browser session only — never a token (spec R3).

    Guards credential management: a leaked token must not be able to mint,
    list, or revoke tokens. 403 ``session_required`` tells a client exactly
    what to do (log in on the web app), distinct from the 401 for no auth.
    """
    if user.auth_via != "session":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session_required",
        )
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
    # Escape hatch for testing: REQUIRE_EMAIL_VERIFICATION=false disables this
    # gate (e.g. while Resend is in sandbox mode and verification emails only
    # reach the account owner). Defaults to ON — set back to true before launch.
    import os

    if os.getenv("REQUIRE_EMAIL_VERIFICATION", "true").strip().lower() in ("false", "0", "no", "off"):
        return user
    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="email_not_verified",
        )
    return user
