"""Auth endpoints — register, login, logout, me."""
from __future__ import annotations

import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field

from src.api.auth_deps import (
    CurrentUser,
    SESSION_COOKIE_NAME,
    _secret,
    require_user,
)
from src.core.settings import DB_PATH
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
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie,
        httponly=True,
        samesite="lax",
        secure=False,  # flip to True behind TLS terminator in prod
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
    cookie = await auth_sessions.create_session(
        str(DB_PATH), user_id=user_id, secret=_secret()
    )
    _set_session_cookie(response, cookie)
    return UserResponse(id=user_id, email=req.email)


@router.post("/login", response_model=UserResponse)
async def login(req: LoginRequest, response: Response) -> UserResponse:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, email, password_hash FROM users "
            "WHERE email = ? AND deleted_at IS NULL",
            (req.email,),
        )
        row = await cur.fetchone()
    if row is None or not verify_password(row["password_hash"], req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    cookie = await auth_sessions.create_session(
        str(DB_PATH), user_id=row["id"], secret=_secret()
    )
    _set_session_cookie(response, cookie)
    return UserResponse(id=row["id"], email=row["email"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    job360_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Response:
    if job360_session:
        await auth_sessions.revoke_session(
            str(DB_PATH), job360_session, secret=_secret()
        )
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser = Depends(require_user)) -> UserResponse:
    return UserResponse(id=user.id, email=user.email)
