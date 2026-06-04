"""Auth endpoints — register, login, logout, me, account management."""

from __future__ import annotations

import os
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field

from src.api.auth_deps import (
    SESSION_COOKIE_NAME,
    CurrentUser,
    _secret,
    require_user,
)
from src.api.dependencies import get_db
from src.core.settings import DB_PATH
from src.repositories.database import JobDatabase
from src.services.auth import email_verification as auth_email_verification
from src.services.auth import password_reset as auth_password_reset
from src.services.auth import sessions as auth_sessions
from src.services.auth.passwords import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    email: str


def _set_session_cookie(response: Response, cookie: str) -> None:
    # Secure flag gates on JOB360_ENV so prod deploys don't serve bare cookies
    # by accident. Any value other than "prod" falls back to dev-friendly.
    secure = os.environ.get("JOB360_ENV") == "prod"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=60 * 60 * 24 * 30,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, response: Response) -> UserResponse:
    user_id = uuid.uuid4().hex
    pw_hash = hash_password(req.password)
    async with aiosqlite.connect(str(DB_PATH)) as db:
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
    # Phase −2 item B: fire-and-forget verification email. SMTP failure
    # never blocks registration (send_system_email returns False on error
    # and we ignore the bool); the user can request another via
    # POST /api/auth/verify-email/request when they're ready.
    await auth_email_verification.request_email_verification(
        db_path=str(DB_PATH),
        user_id=user_id,
        email=str(req.email),
        frontend_origin=_frontend_origin(),
    )
    return UserResponse(id=user_id, email=req.email)


@router.post("/login", response_model=UserResponse)
async def login(req: LoginRequest, response: Response) -> UserResponse:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, email, password_hash FROM users " "WHERE email = ? AND deleted_at IS NULL",
            (req.email,),
        )
        row = await cur.fetchone()
    if row is None or not verify_password(row["password_hash"], req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    cookie = await auth_sessions.create_session(str(DB_PATH), user_id=row["id"], secret=_secret())
    _set_session_cookie(response, cookie)
    return UserResponse(id=row["id"], email=row["email"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    job360_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Response:
    if job360_session:
        await auth_sessions.revoke_session(str(DB_PATH), job360_session, secret=_secret())
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser = Depends(require_user)) -> UserResponse:
    return UserResponse(id=user.id, email=user.email)


# ── B-11: Soft-delete (GDPR Article 17) ──────────────────────────────────────


@router.delete("/users/me", status_code=204)
async def delete_account(
    response: Response,
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
) -> Response:
    """Soft-delete the caller's account (GDPR Article 17). Sets deleted_at. Clears session cookie."""
    await db.soft_delete_user(user.id)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return Response(status_code=204)


# ── B-12: Password change ─────────────────────────────────────────────────────


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


@router.patch("/users/me/password", status_code=204)
async def change_password(
    req: PasswordChangeRequest,
    db: JobDatabase = Depends(get_db),
    user: CurrentUser = Depends(require_user),
) -> Response:
    """Authenticated password change. Requires current password verification."""
    async with aiosqlite.connect(str(DB_PATH)) as adb:
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
    return Response(status_code=204)


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
    async with aiosqlite.connect(str(DB_PATH)) as adb:
        adb.row_factory = aiosqlite.Row
        cursor = await adb.execute(
            "SELECT password_hash FROM users WHERE id = ? AND deleted_at IS NULL",
            (user.id,),
        )
        row = await cursor.fetchone()
    if not row or not verify_password(row["password_hash"], req.current_password):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    # Check new email is not already taken by another user
    async with aiosqlite.connect(str(DB_PATH)) as adb:
        adb.row_factory = aiosqlite.Row
        cursor = await adb.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?",
            (str(req.new_email), user.id),
        )
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="email already in use")
    await db.update_user_email(user.id, str(req.new_email))
    response.delete_cookie(SESSION_COOKIE_NAME)
    return Response(status_code=204)


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
async def password_reset_request(req: PasswordResetRequest) -> Response:
    """Initiate a password-reset email.

    Always returns 204 regardless of whether the email is registered —
    no-enumeration contract (per OWASP). The service layer logs the
    distinction internally for ops.
    """
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
    """
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


@router.get("/me/email-verified")
async def get_email_verified(
    user: CurrentUser = Depends(require_user),
) -> dict:
    """Cheap read for the dashboard to render a 'verify email' banner."""
    verified = await auth_email_verification.is_email_verified(
        db_path=str(DB_PATH), user_id=user.id
    )
    return {"email_verified": verified}
