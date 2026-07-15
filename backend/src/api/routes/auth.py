"""Auth endpoints — register, login, logout, me, account management."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from src.api.auth_deps import (
    SESSION_COOKIE_NAME,
    CurrentUser,
    _secret,
    require_user,
)
from src.api.dependencies import get_db
from src.api.middleware import _is_production
from src.core.settings import DB_PATH, LOGIN_LOCKOUT_WINDOW_SECONDS, LOGIN_MAX_ATTEMPTS
from src.repositories import pg as aiosqlite
from src.repositories.database import JobDatabase
from src.repositories.db_retry import open_db
from src.services.auth import email_verification as auth_email_verification
from src.services.auth import magic_link as auth_magic_link
from src.services.auth import password_reset as auth_password_reset
from src.services.auth import rate_limit as auth_rate_limit
from src.services.auth import sessions as auth_sessions
from src.services.auth.passwords import hash_password, verify_password
from src.utils.logger import get_audit_logger

logger = logging.getLogger("job360.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


async def _safe_request_email_verification(
    *,
    db_path: str,
    user_id: str,
    email: str,
    frontend_origin: str,
) -> None:
    """Background-task wrapper around request_email_verification.

    Phase −2 review fix #1 + #2 — `register` previously awaited the
    verification call inline. That meant:
      (a) the HTTP response was blocked on the synchronous SMTP send
          (300ms–30s — review finding #1);
      (b) any DB exception in the verification call (e.g. fresh DB
          before migration 0016 applied) propagated as HTTP 500 *after*
          the user row had already been committed, orphaning the account
          (review finding #2).

    Scheduled via FastAPI BackgroundTasks (runs after the response
    headers/body have been sent), this wrapper decouples the email work
    from the register response. Any exception is logged but swallowed —
    the user already got their 201, and the worst case is they request
    a resend manually.
    """
    try:
        await auth_email_verification.request_email_verification(
            db_path=db_path,
            user_id=user_id,
            email=email,
            frontend_origin=frontend_origin,
        )
    except Exception as exc:  # noqa: BLE001 — intentionally broad
        logger.warning(
            "background verification email failed: user=%s err=%s", user_id, exc
        )


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    email: str


def _client_meta(request: Request) -> dict:
    """Extract safe client metadata for audit log extra fields.

    X-Forwarded-For is only trusted when ``JOB360_TRUST_PROXY=1`` is set —
    forwarding without this gate lets attackers spoof their source IP.
    """
    xff_ip = None
    if os.getenv("JOB360_TRUST_PROXY") == "1":
        xff = request.headers.get("x-forwarded-for", "")
        xff_ip = xff.split(",")[0].strip() or None
    return {
        "client_ip": xff_ip or (request.client.host if request.client else None),
        "user_agent": request.headers.get("user-agent", "")[:200],
    }


def _set_session_cookie(response: Response, cookie: str) -> None:
    # H5 — Secure flag gates on the SAME prod detection as HSTS / Sentry / CORS
    # (APP_ENV=production OR RAILWAY_ENVIRONMENT set) so a prod deploy never
    # serves a bare session cookie. Reuses middleware._is_production so the
    # signal can't drift out of sync with the other prod-gated behaviour.
    # (Previously gated on JOB360_ENV, never set on Railway — see docs/fable/01.)
    secure = _is_production()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=60 * 60 * 24 * 30,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    response: Response,
    request: Request,
    background_tasks: BackgroundTasks,
) -> UserResponse:
    user_id = uuid.uuid4().hex
    pw_hash = hash_password(req.password)
    async with open_db(str(DB_PATH)) as db:
        try:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                (user_id, req.email, pw_hash),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="email already registered",
            )
    cookie = await auth_sessions.create_session(str(DB_PATH), user_id=user_id, secret=_secret())
    _set_session_cookie(response, cookie)
    get_audit_logger().info(
        "auth",
        extra={"event": "register", "user_id": user_id, "status": "ok", **_client_meta(request)},
    )
    # Phase −2 item B + post-review fix #1 + #2: schedule the verification
    # email as a FastAPI BackgroundTask. The task runs AFTER the response
    # is queued so register isn't blocked on SMTP I/O (300ms–30s), and the
    # _safe_ wrapper guarantees a DB error in the verification path can't
    # propagate as HTTP 500 after the user row was already committed
    # (orphan-account avoidance).
    background_tasks.add_task(
        _safe_request_email_verification,
        db_path=str(DB_PATH),
        user_id=user_id,
        email=str(req.email),
        frontend_origin=_frontend_origin(),
    )
    return UserResponse(id=user_id, email=req.email)


@router.post("/login", response_model=UserResponse)
async def login(req: LoginRequest, response: Response, request: Request) -> UserResponse:
    # Brute-force lockout (LAUNCH_PLAN Phase 2 #10). Key on the email so an
    # attacker guessing one account's password gets locked out after
    # LOGIN_MAX_ATTEMPTS failures within the window — checked BEFORE we touch
    # the DB or verify the hash.
    throttle_key = f"login:{str(req.email).lower()}"
    if auth_rate_limit.is_locked(
        throttle_key,
        max_failures=LOGIN_MAX_ATTEMPTS,
        window_seconds=LOGIN_LOCKOUT_WINDOW_SECONDS,
    ):
        get_audit_logger().warning(
            "auth",
            extra={"event": "login", "status": "locked", "email": req.email, **_client_meta(request)},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed login attempts; try again later",
            headers={"Retry-After": str(LOGIN_LOCKOUT_WINDOW_SECONDS)},
        )
    async with open_db(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, email, password_hash FROM users " "WHERE email = ? AND deleted_at IS NULL",
            (req.email,),
        )
        row = await cur.fetchone()
    if row is None or not verify_password(row["password_hash"], req.password):
        auth_rate_limit.record_failure(throttle_key)
        get_audit_logger().warning(
            "auth",
            extra={"event": "login", "status": "fail", "email": req.email, **_client_meta(request)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    # Success — clear the failure counter so earlier typos don't accumulate.
    auth_rate_limit.clear_failures(throttle_key)
    cookie = await auth_sessions.create_session(str(DB_PATH), user_id=row["id"], secret=_secret())
    _set_session_cookie(response, cookie)
    get_audit_logger().info(
        "auth",
        extra={"event": "login", "user_id": row["id"], "status": "ok", **_client_meta(request)},
    )
    return UserResponse(id=row["id"], email=row["email"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    request: Request,
    job360_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Response:
    if job360_session:
        await auth_sessions.revoke_session(str(DB_PATH), job360_session, secret=_secret())
    get_audit_logger().info("auth", extra={"event": "logout", "status": "ok", **_client_meta(request)})
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser = Depends(require_user)) -> UserResponse:
    return UserResponse(id=user.id, email=user.email)


# ── B-11: Soft-delete (GDPR Article 17) ──────────────────────────────────────


class AccountDeleteRequest(BaseModel):
    current_password: str


@router.delete("/users/me", status_code=204)
async def delete_account(
    req: AccountDeleteRequest,
    response: Response,
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
) -> Response:
    """Soft-delete the caller's account (GDPR Article 17). Requires current-password
    verification (hard rule #26), then clears the session cookie."""
    async with open_db(str(DB_PATH)) as adb:
        adb.row_factory = aiosqlite.Row
        cursor = await adb.execute(
            "SELECT password_hash FROM users WHERE id = ? AND deleted_at IS NULL",
            (user.id,),
        )
        row = await cursor.fetchone()
    if not row or not verify_password(row["password_hash"], req.current_password):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    await db.soft_delete_user(user.id)
    # Mutate + return the injected response so the cookie clear reaches the client
    # (returning a fresh Response drops the Set-Cookie header).
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ── B-12: Password change ─────────────────────────────────────────────────────


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


@router.patch("/users/me/password", status_code=204)
async def change_password(
    req: PasswordChangeRequest,
    response: Response,
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
) -> Response:
    """Authenticated password change. Requires current password verification,
    then invalidates the session cookie to force re-login (hard rule #26 —
    matches the email-change path)."""
    async with open_db(str(DB_PATH)) as adb:
        adb.row_factory = aiosqlite.Row
        cursor = await adb.execute(
            "SELECT password_hash FROM users WHERE id = ? AND deleted_at IS NULL",
            (user.id,),
        )
        row = await cursor.fetchone()
    if not row or not verify_password(row["password_hash"], req.current_password):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    new_hash = hash_password(req.new_password)
    await db.update_user_password(user.id, new_hash)
    # Terminate ALL of the user's sessions server-side (other devices / stolen
    # cookies), not just clear this browser's cookie — full intent of rule #26.
    await auth_sessions.revoke_all_for_user(str(DB_PATH), user.id)
    # Mutate + return the injected response (returning a fresh Response would
    # drop the Set-Cookie, the latent bug this fix also corrects for email/delete).
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ── B-13: Email change ────────────────────────────────────────────────────────


class EmailChangeRequest(BaseModel):
    current_password: str
    new_email: EmailStr


@router.patch("/users/me/email", status_code=204)
async def change_email(
    req: EmailChangeRequest,
    response: Response,
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
) -> Response:
    """Change email. Requires current password. Invalidates session → re-login required."""
    async with open_db(str(DB_PATH)) as adb:
        adb.row_factory = aiosqlite.Row
        cursor = await adb.execute(
            "SELECT password_hash FROM users WHERE id = ? AND deleted_at IS NULL",
            (user.id,),
        )
        row = await cursor.fetchone()
    if not row or not verify_password(row["password_hash"], req.current_password):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    # Check new email is not already taken by another user
    async with open_db(str(DB_PATH)) as adb:
        adb.row_factory = aiosqlite.Row
        cursor = await adb.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?",
            (str(req.new_email), user.id),
        )
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="email already in use")
    await db.update_user_email(user.id, str(req.new_email))
    # Terminate all of the user's sessions server-side (rule #26 — same as
    # password change), not just clear this browser's cookie.
    await auth_sessions.revoke_all_for_user(str(DB_PATH), user.id)
    # Mutate + return the injected response so the cookie clear reaches the client
    # (returning a fresh Response drops the Set-Cookie header).
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ── Phase −2-A: Password reset (forgot-password flow) ────────────────────────


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=256)


def _frontend_origin() -> str:
    """First entry from FRONTEND_ORIGIN (CORS allowlist) — used to build reset links."""
    raw = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
    return raw.split(",")[0].strip()


@router.post("/password-reset/request", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset_request(
    req: PasswordResetRequest,
    request: Request,
) -> Response:
    """Initiate a password-reset email.

    Always returns 204 regardless of whether the email is registered —
    no-enumeration contract (per OWASP). The service layer logs the
    distinction internally for ops.

    **Rate-limited** (Phase −2 review fix #3): 1 request per minute per
    IP. When limited, returns 204 *without* issuing a token or sending
    an email — preserves the no-enumeration contract by being
    indistinguishable from "unknown email" responses. Logged so an
    operator can see the suppression pattern.
    """
    # Hash the IP rather than use it directly — bucket key is opaque
    # in any future log dump or telemetry export.
    client_ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()[:16]
    key = f"password-reset:{ip_hash}"
    if not auth_rate_limit.check_and_record(key, max_in_window=1, window_seconds=60):
        logger.info("password-reset rate-limited ip_hash=%s", ip_hash)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await auth_password_reset.request_password_reset(
        db_path=str(DB_PATH),
        email=str(req.email),
        frontend_origin=_frontend_origin(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset_confirm(
    req: PasswordResetConfirmRequest,
    response: Response,
) -> Response:
    """Validate the token and set the new password.

    On success: 204, and every existing session for that user is revoked
    (forces re-login on all devices — security best practice after reset).
    The caller's session cookie is cleared too so the API response itself
    can't accidentally land the caller into the just-reset account.

    On failure: 400 with a generic message. We deliberately don't
    distinguish unknown vs expired vs used vs deleted-user — that would
    leak which tokens exist.
    """
    user_id = await auth_password_reset.confirm_password_reset(
        db_path=str(DB_PATH),
        raw_token=req.token,
        new_password=req.new_password,
    )
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired reset token",
        )
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ── Phase −2-B: Email verification ───────────────────────────────────────────


class EmailVerificationConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.post("/verify-email/request", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email_request(
    user: CurrentUser = Depends(require_user),
) -> Response:
    """Resend the verification email for the current user.

    Requires an active session — there's no public 'resend by email
    address' endpoint because that would let an attacker spam any
    address. Repeated requests issue independently-valid tokens (any of
    the unused ones works until its own expiry), so the user can't lock
    themselves out by hitting resend twice.

    **Rate-limited** (Phase −2 review fix #3): 1 request per minute per
    user. The session-gate already bounds the threat (only authenticated
    users can call), but a frustrated user clicking resend rapidly would
    burn through SMTP quota. Returns HTTP 429 when limited; UX should
    surface the cooldown.
    """
    key = f"verify-email:{user.id}"
    if not auth_rate_limit.check_and_record(key, max_in_window=1, window_seconds=60):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many resend requests; try again in a minute",
        )
    await auth_email_verification.request_email_verification(
        db_path=str(DB_PATH),
        user_id=user.id,
        email=user.email,
        frontend_origin=_frontend_origin(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify-email/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email_confirm(req: EmailVerificationConfirmRequest) -> Response:
    """Validate token and mark the user's email verified.

    No session required — the link in the email is the authentication
    artefact. Returns 204 on success, 400 on any failure (generic to
    prevent token-existence enumeration).
    """
    user_id = await auth_email_verification.confirm_email_verification(
        db_path=str(DB_PATH),
        raw_token=req.token,
    )
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired verification token",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Passwordless magic-link login ────────────────────────────────────────────


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkConsumeRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.post("/magic-link/request", status_code=status.HTTP_204_NO_CONTENT)
async def magic_link_request(
    req: MagicLinkRequest,
    request: Request,
) -> Response:
    """Email a passwordless sign-in link.

    Always returns 204 regardless of whether the email is registered — the
    account is created lazily on consume, so there's nothing to enumerate.
    Rate-limited to 3 requests per 5 minutes per email so an attacker can't
    spam any address with sign-in emails. When limited we still return 204
    (indistinguishable) but issue no token and send no email.
    """
    key = f"magic-link:{str(req.email).lower()}"
    if not auth_rate_limit.check_and_record(key, max_in_window=3, window_seconds=300):
        logger.info("magic-link rate-limited email=%s", req.email)
        get_audit_logger().warning(
            "auth",
            extra={
                "event": "magic_link_request",
                "status": "rate_limited",
                "email": req.email,
                **_client_meta(request),
            },
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await auth_magic_link.request_magic_link(
        db_path=str(DB_PATH),
        email=str(req.email),
        frontend_origin=_frontend_origin(),
    )
    get_audit_logger().info(
        "auth",
        extra={"event": "magic_link_request", "status": "ok", "email": req.email, **_client_meta(request)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/magic-link/consume", response_model=UserResponse)
async def magic_link_consume(
    req: MagicLinkConsumeRequest,
    response: Response,
    request: Request,
) -> UserResponse:
    """Validate a magic-link token, sign the user in (find-or-create + verify).

    On success sets the session cookie and returns the user. On any failure
    (unknown / expired / already-used token) raises 400 with a generic
    message so token existence can't be probed.
    """
    result = await auth_magic_link.consume_magic_link(
        db_path=str(DB_PATH),
        raw_token=req.token,
        secret=_secret(),
    )
    if result is None:
        get_audit_logger().warning(
            "auth",
            extra={"event": "magic_link_consume", "status": "fail", **_client_meta(request)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired link",
        )
    cookie, user_id, email = result
    _set_session_cookie(response, cookie)
    get_audit_logger().info(
        "auth",
        extra={"event": "magic_link_consume", "user_id": user_id, "status": "ok", **_client_meta(request)},
    )
    return UserResponse(id=user_id, email=email)


@router.get("/me/email-verified")
async def get_email_verified(
    user: CurrentUser = Depends(require_user),
) -> dict:
    """Cheap read for the dashboard to render a 'verify email' banner."""
    verified = await auth_email_verification.is_email_verified(
        db_path=str(DB_PATH), user_id=user.id
    )
    return {"email_verified": verified}
