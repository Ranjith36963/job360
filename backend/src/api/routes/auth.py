"""Auth endpoints — register, login, logout, me, account management."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.api.auth_deps import (
    AUTH_FIRST,
    SESSION_COOKIE_NAME,
    CurrentUser,
    _secret,
    require_session_user,
    require_user,
)
from src.api.client_ip import UNKNOWN, client_ip, forwarding_probe, throttle_client_ip
from src.api.dependencies import get_request_db
from src.api.middleware import _is_production
from src.core import settings
from src.core.settings import DB_PATH, LOGIN_LOCKOUT_WINDOW_SECONDS, LOGIN_MAX_ATTEMPTS, USER_TIMEZONE_MAX_CHARS
from src.repositories import pg
from src.repositories.database import JobDatabase
from src.repositories.db_retry import open_db
from src.services.auth import email_change_undo as auth_email_change_undo
from src.services.auth import email_verification as auth_email_verification
from src.services.auth import magic_link as auth_magic_link
from src.services.auth import password_reset as auth_password_reset
from src.services.auth import rate_limit as auth_rate_limit
from src.services.auth import sessions as auth_sessions
from src.services.auth.passwords import hash_password, verify_password
from src.services.auth.security_notices import send_security_notice
from src.utils.logger import get_audit_logger, mask_email, safe_log_value

logger = logging.getLogger("job360.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# A fixed argon2 hash to verify against when a login email doesn't exist, so the
# response takes the same ~argon2 time whether or not the account is registered —
# no timing side-channel to enumerate accounts (docs/fable/01). Computed once at
# import (a single argon2 hash).
_DUMMY_PW_HASH = hash_password("timing-equalizer-not-a-real-password")


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
    # Owner decision, 2026-09-25 — the IANA zone name from `users.timezone`
    # (migration 0012, default 'UTC'). Always present; the account settings
    # page prefills the browser's own zone when this still reads the
    # untouched default (rule #29 — only a real Save ever writes it).
    timezone: str = "UTC"
    # Gap roadmap slice 4 — True only when the user turned on "Help improve
    # Job360" (`users.analytics_consent_at`, migration 0067). The web app gates
    # Sentry session replay on it; the backend gates PostHog capture on the
    # column itself. Off by default.
    analytics_consent: bool = False


class MeResponse(UserResponse):
    # Migration 0071 — False for an account only a magic link has ever signed
    # in (no password the user knows). The account page then deletes without a
    # password prompt, from a fresh email-link sign-in (``delete_account``).
    has_password: bool = True


class RegisterResponse(BaseModel):
    # M2 — deliberately generic and IDENTICAL whether or not the email was already
    # registered, so /register cannot be used to enumerate accounts. Carries no
    # id/email (returning the real ones for a new user but not a duplicate would
    # itself leak existence).
    status: str = "ok"
    message: str = "Account request received. Please sign in to continue."


def _client_meta(request: Request) -> dict[str, Any]:
    """Extract safe client metadata for audit log extra fields.

    The address comes from ``src.api.client_ip`` (the one reader of
    X-Forwarded-For); ``None`` when there is no address at all.
    """
    ip = client_ip(request)
    return {
        "client_ip": None if ip == UNKNOWN else ip,
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


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    response: Response,
    request: Request,
    background_tasks: BackgroundTasks,
) -> RegisterResponse:
    # M3 — per-IP registration throttle. Without this, register had ZERO rate
    # limit, so a single host could mass-create accounts. The address comes from
    # src.api.client_ip, the same as login (X-Forwarded-For only from trusted
    # proxy hops, never the caller-written leftmost entry). Do NOT leak whether
    # the email exists — this fires before any DB lookup.
    if not auth_rate_limit.check_and_record(
        f"register:{throttle_client_ip(request)}", max_in_window=10, window_seconds=3600
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts, please try again later.",
        )
    user_id = uuid.uuid4().hex
    pw_hash = hash_password(req.password)
    created = False
    async with open_db(str(DB_PATH)) as db:
        try:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                # Store the canonical lowercase form so the UNIQUE constraint actually
                # prevents case-variant duplicate accounts (see LOWER() reads).
                (user_id, req.email.lower(), pw_hash),
            )
            await db.commit()
            created = True
        except pg.IntegrityError:
            # M2 — the email already exists. Do NOT reveal that and do NOT touch the
            # existing account; fall through to the SAME response a new signup gets.
            created = False

    # M2 — /register no longer auto-logs-in and no longer 409s on a taken email, so
    # it can't be used to enumerate accounts. A session cookie for a new user but
    # none for a duplicate would itself leak existence, so NEITHER path sets one —
    # the user signs in next (sign up → sign in). Response body + status are
    # identical whether the account was just created or already existed.
    if created:
        get_audit_logger().info(
            "auth",
            extra={"event": "register", "user_id": user_id, "status": "ok", **_client_meta(request)},
        )
        # Schedule the verification email as a FastAPI BackgroundTask so register
        # isn't blocked on SMTP I/O, and the _safe_ wrapper keeps a DB error in the
        # verification path from 500-ing after the user row was committed.
        background_tasks.add_task(
            _safe_request_email_verification,
            db_path=str(DB_PATH),
            user_id=user_id,
            email=str(req.email),
            frontend_origin=_frontend_origin(),
        )
    else:
        # A duplicate — audited server-side (never surfaced to the caller). The real
        # owner already has an account and can sign in / reset; we send no signal
        # that would confirm the address is registered.
        get_audit_logger().info(
            "auth",
            extra={"event": "register_duplicate", "status": "ok", **_client_meta(request)},
        )
    return RegisterResponse()


@router.post("/login", response_model=UserResponse)
async def login(req: LoginRequest, response: Response, request: Request) -> UserResponse:
    # Brute-force lockout (LAUNCH_PLAN Phase 2 #10). Key on the email so an
    # attacker guessing one account's password gets locked out after
    # LOGIN_MAX_ATTEMPTS failures within the window — checked BEFORE we touch
    # the DB or verify the hash.
    # Lock on email+IP, not email alone (docs/fable/01): an email-only lockout lets
    # an attacker lock a VICTIM out of their own account by spamming failures for
    # their email. Scoping to the source IP means the attacker only locks their own
    # (email, IP) bucket; the real user, on a different IP, can still sign in.
    # The address is src.api.client_ip's (IPv6 counted per /64): a forged
    # X-Forwarded-For entry never changes it, so rotating forged values does not
    # buy fresh buckets.
    email_lc = str(req.email).lower()
    throttle_key = f"login:{email_lc}:{throttle_client_ip(request)}"
    # Gap-audit fix #6 — plus an OPT-IN per-email cap across ALL addresses
    # (settings.LOGIN_EMAIL_MAX_ATTEMPTS, 0 = off by default: when on it refuses
    # even the right password, so a distributed attacker could lock the owner
    # out). Only attempts the email+IP bucket ADMITS are counted toward it. Its count is never cleared on success, only
    # the successful attempt's own pre-count is refunded: one sign-in by the
    # owner must not hand a stuffing run a fresh budget.
    email_key = f"login_email:{email_lc}"
    # Owner, 2026-09-28 — logs keep first letter + domain, never the full address.
    log_email = safe_log_value(mask_email(str(req.email)))
    # Check AND count in one atomic step, BEFORE the first await (security
    # review P1): checking here and recording after the DB read let a concurrent
    # burst from one address pass the check many times over. The attempt is
    # pre-counted as a failure; a successful login refunds it below.
    admission = auth_rate_limit.admit_login_attempt(
        throttle_key,
        email_key,
        ip_max=LOGIN_MAX_ATTEMPTS,
        email_max=settings.LOGIN_EMAIL_MAX_ATTEMPTS,
        window_seconds=LOGIN_LOCKOUT_WINDOW_SECONDS,
    )
    if not admission.admitted:
        get_audit_logger().warning(
            "auth",
            extra={
                "event": "login", "status": "locked", "email": log_email,
                "lock": admission.lock, **_client_meta(request),
            },
        )
        # One body for both locks: which bucket refused is never revealed.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed login attempts; try again later",
            headers={"Retry-After": str(LOGIN_LOCKOUT_WINDOW_SECONDS)},
        )
    async with open_db(str(DB_PATH)) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT id, email, password_hash FROM users "
            "WHERE LOWER(email) = LOWER(?) AND deleted_at IS NULL",
            (req.email,),
        )
        row = await cur.fetchone()
    # Always run one argon2 verify — against the real hash, or a fixed dummy when
    # the email is unknown — so login timing can't reveal whether an account exists.
    stored_hash = row["password_hash"] if row is not None else _DUMMY_PW_HASH
    pw_ok = verify_password(stored_hash, req.password)
    if row is None or not pw_ok:
        # Already counted by admit_login_attempt above.
        get_audit_logger().warning(
            "auth",
            extra={
                "event": "login", "status": "fail", "email": log_email, **_client_meta(request),
                # TEMPORARY probe (src.api.client_ip.forwarding_probe): test addresses only.
                **({"forwarding_probe": forwarding_probe(request)}
                   if email_lc.endswith("@example.com") else {}),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    # Success — clear the email+IP failure counter so earlier typos don't
    # accumulate (this also drops this attempt's pre-count), and refund this
    # attempt's pre-count from the per-email bucket, leaving the rest.
    auth_rate_limit.clear_failures(throttle_key)
    auth_rate_limit.refund_login_attempt(email_key, admission)
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
    # Capture WHO is logging out. This route deliberately takes the raw cookie
    # rather than `require_user` (an expired or forged cookie must still clear
    # the browser's, not 401), so the user is not otherwise in scope — and the
    # audit line was being written without one. `revoke_session` now returns the
    # owner it just signed out, which is the only place that identity exists.
    user_id = None
    if job360_session:
        user_id = await auth_sessions.revoke_session(
            str(DB_PATH), job360_session, secret=_secret()
        )
    get_audit_logger().info(
        "auth",
        extra={
            "event": "logout",
            "status": "ok",
            # None when the cookie was already invalid — genuinely unknown, and
            # honestly recorded as such rather than silently omitted.
            "user_id": user_id,
            **_client_meta(request),
        },
    )
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
async def me(
    user: CurrentUser = Depends(require_user),  # noqa: B008
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> MeResponse:
    cur = await db._db.execute(
        "SELECT timezone, has_password, analytics_consent_at FROM users WHERE id = ?", (user.id,)
    )
    row = await cur.fetchone()
    data = dict(row) if row else {}
    tz = data.get("timezone") or "UTC"
    return MeResponse(
        id=user.id, email=user.email, timezone=tz,
        analytics_consent=bool(data.get("analytics_consent_at")),
        has_password=data.get("has_password") is not False,
    )


class TimezoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(..., max_length=USER_TIMEZONE_MAX_CHARS)


class TimezoneResponse(BaseModel):
    timezone: str


@router.put("/me/timezone", response_model=TimezoneResponse, dependencies=AUTH_FIRST)
async def set_timezone(
    body: TimezoneRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> dict[str, Any]:
    """Web session only (security review 2026-10-18): the user's timezone sets their day, week and
    ``apply_hours``, so an assistant's token must not move it.

    Owner decision, 2026-09-25 — the ONE write door for `users.timezone`
    (browser-prefilled on the account settings page, but only ever SAVED
    here, on an explicit press — rule #29). A name `ZoneInfo` cannot resolve
    is refused with 422 naming the bad value; a valid one is stored and used
    by `spine.user_today` for every "what's due today" read from then on."""
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail=f"unknown timezone: {body.timezone!r}") from None
    await db._db.execute("UPDATE users SET timezone = ? WHERE id = ?", (body.timezone, user.id))
    await db._db.commit()
    return {"timezone": body.timezone}


class AnalyticsConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent: bool


class AnalyticsConsentResponse(BaseModel):
    analytics_consent: bool


@router.put("/me/analytics-consent", response_model=AnalyticsConsentResponse, dependencies=AUTH_FIRST)
async def set_analytics_consent(
    body: AnalyticsConsentRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> dict[str, Any]:
    """Web session only: the user's own "Help improve Job360" switch (gap roadmap slice 4).

    The ONE write door for `users.analytics_consent_at`. ``true`` stamps when they
    agreed; ``false`` clears it, which withdraws consent at once (server PostHog
    capture and Sentry session replay both read it). Consent is the person's own
    choice, so an assistant's token is refused (403 ``session_required``)."""
    stamp = datetime.now(timezone.utc).isoformat() if body.consent else None
    await db._db.execute("UPDATE users SET analytics_consent_at = ? WHERE id = ?", (stamp, user.id))
    await db._db.commit()
    get_audit_logger().info(
        "auth",
        extra={
            "event": "analytics_consent_set", "user_id": safe_log_value(user.id),
            "consent": bool(body.consent), "status": "ok",
        },
    )
    return {"analytics_consent": body.consent}


# ── GDPR Article 20 — data portability (docs/fable/05 C7) ────────────────────


@router.get("/users/me/export", dependencies=AUTH_FIRST)
async def export_my_data(
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — a human web action (session only)
) -> Response:
    """Download everything we hold on the caller as one JSON file (Article 20).

    Web session only (gap roadmap slice 3): a leaked, never-expiring agent token
    must not be able to download the whole account. An assistant reads history
    through ``export_history``, which leaves out the account tables.

    GET is correct here: it is a pure read, no state changes. Scoped to the
    session's own ``user.id`` (rule #12) — the caller can never name another user.
    Secrets (password hash, encrypted channel creds) are redacted and the
    security-token tables are omitted entirely; see ``export_user_data``.
    """
    # Per-user cap (feature 4): a full export reads every table, so bound repeats.
    # 0 or less disables the cap, matching the other *_MAX_PER_HOUR settings.
    if settings.USER_EXPORT_MAX_PER_HOUR > 0 and not auth_rate_limit.check_and_record(
        f"user_export:{user.id}", max_in_window=settings.USER_EXPORT_MAX_PER_HOUR, window_seconds=3600
    ):
        raise HTTPException(
            status_code=429,
            detail=f"too many exports this hour (USER_EXPORT_MAX_PER_HOUR = {settings.USER_EXPORT_MAX_PER_HOUR})",
        )
    data = await db.export_user_data(user.id)
    # Audit line: ids and counts only, never the exported content.
    get_audit_logger().info(
        "auth",
        extra={
            "event": "user_data_exported",
            "user_id": user.id,
            "tables": len([k for k in data if not k.startswith("_") and k != "user"]),
            "incomplete": bool(data.get("_incomplete_tables")),
        },
    )
    # Flat file: ``exported_at`` beside the tables (``user``, ``api_tokens``, …),
    # no wrapper object — one level to read for a human opening the download.
    body = json.dumps(
        {"exported_at": datetime.now(timezone.utc).isoformat(), **data},
        indent=2,
        default=str,
    )
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="job360-my-data.json"'},
    )


# ── B-11: Soft-delete (GDPR Article 17) ──────────────────────────────────────


class AccountDeleteRequest(BaseModel):
    # Optional only for an account with no password (``users.has_password`` is
    # FALSE); an account WITH one must still send it (hard rule #26).
    current_password: Optional[str] = None


def _fresh_link_required() -> str:
    """The 403 detail for a passwordless delete from a stale session: what to do next."""
    minutes = max(1, settings.ACCOUNT_DELETE_FRESH_SESSION_SECONDS // 60)
    return (
        "This account has no password. To delete it, sign out, sign in again with a fresh "
        f"email link, then delete your account within {minutes} minutes."
    )


async def _session_is_fresh(cookie: Optional[str]) -> bool:
    """True when the caller's session was created inside the fresh-sign-in window.

    The sessions table does not record HOW a session was made, so this reads its
    ``created_at`` alone. For an account with no password that is enough: the
    only way such an account gets a session is consuming a magic link.
    """
    if not cookie:
        return False
    created = await auth_sessions.session_created_at(str(DB_PATH), cookie, secret=_secret())
    if created is None:
        return False
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return 0 <= age <= settings.ACCOUNT_DELETE_FRESH_SESSION_SECONDS


@router.delete("/users/me", status_code=204, dependencies=AUTH_FIRST)
async def delete_account(
    req: AccountDeleteRequest,
    response: Response,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — a human web action (session only)
    job360_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Response:
    """ERASE the caller's account (GDPR Article 17), then clear the session cookie.

    Web session only: an agent token can never erase the account. An account
    with a password must send it (hard rule #26). An account with NO password
    (``has_password`` FALSE: only a magic link ever signed it in) cannot, so it
    may delete only from a session created by a fresh email link, inside
    ``ACCOUNT_DELETE_FRESH_SESSION_SECONDS``; otherwise 403 says how.

    Uses hard_delete_user (docs/fable/05) — actually erases all per-user data
    (CV, profile, channels, feed, actions, …), not a soft `deleted_at` flag that
    left the data in place and could be resurrected on a later magic-link consume."""
    async with open_db(str(DB_PATH)) as adb:
        adb.row_factory = pg.Row
        cursor = await adb.execute(
            "SELECT password_hash, has_password FROM users WHERE id = ? AND deleted_at IS NULL",
            (user.id,),
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="current password is incorrect")
    if row["has_password"] is False:
        if not await _session_is_fresh(job360_session):
            get_audit_logger().info(
                "auth", extra={"event": "account_delete", "user_id": user.id, "status": "stale_session"}
            )
            raise HTTPException(status_code=403, detail=_fresh_link_required())
        verified_by = "fresh_link"
    else:
        if not req.current_password or not verify_password(row["password_hash"], req.current_password):
            raise HTTPException(status_code=401, detail="current password is incorrect")
        verified_by = "password"
    await db.hard_delete_user(user.id)
    get_audit_logger().info(
        "auth", extra={"event": "account_delete", "user_id": user.id, "status": "ok", "verified_by": verified_by}
    )
    # Mutate + return the injected response so the cookie clear reaches the client
    # (returning a fresh Response drops the Set-Cookie header).
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ── B-12: Password change ─────────────────────────────────────────────────────


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


@router.patch("/users/me/password", status_code=204, dependencies=AUTH_FIRST)
async def change_password(
    req: PasswordChangeRequest,
    response: Response,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — a human web action (session only)
) -> Response:
    """Authenticated password change. Requires current password verification,
    then invalidates the session cookie to force re-login (hard rule #26 —
    matches the email-change path)."""
    async with open_db(str(DB_PATH)) as adb:
        adb.row_factory = pg.Row
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
    await send_security_notice(str(DB_PATH), user.id, "password_changed")
    # Mutate + return the injected response (returning a fresh Response would
    # drop the Set-Cookie, the latent bug this fix also corrects for email/delete).
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ── B-13: Email change ────────────────────────────────────────────────────────


class EmailChangeRequest(BaseModel):
    current_password: str
    new_email: EmailStr


async def _safe_send_email_change_mails(
    *,
    db_path: str,
    user_id: str,
    old_email: str,
    new_email: str,
    undo_token: str,
    frontend_origin: str,
) -> None:
    """Background task: verify the NEW address, tell the OLD one (slice 35).

    Each send is attempted on its own and a failure is only logged, never
    raised: the change already happened and the user already has their 204
    (same stance as ``_safe_request_email_verification``). Never logs an
    address in full.
    """
    try:
        await auth_email_verification.request_email_verification(
            db_path=db_path, user_id=user_id, email=new_email, frontend_origin=frontend_origin
        )
    except Exception as exc:  # noqa: BLE001 — intentionally broad
        logger.warning(
            "email change: verification mail to new address failed user=%s err=%s",
            safe_log_value(user_id), type(exc).__name__,
        )
    try:
        sent = await auth_email_change_undo.send_change_notice(
            old_email=old_email, new_email=new_email, raw_token=undo_token, frontend_origin=frontend_origin
        )
        if not sent:
            logger.warning("email change: notice to old address not sent user=%s", safe_log_value(user_id))
    except Exception as exc:  # noqa: BLE001 — intentionally broad
        logger.warning(
            "email change: notice to old address failed user=%s err=%s", safe_log_value(user_id), type(exc).__name__
        )


@router.patch("/users/me/email", status_code=204, dependencies=AUTH_FIRST)
async def change_email(
    req: EmailChangeRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: JobDatabase = Depends(get_request_db),
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — a human web action (session only)
) -> Response:
    """Change email. Requires current password. Invalidates session → re-login required.

    The new address is unproven, so ``email_verified_at`` is cleared and the
    verification mail goes to it. The OLD address gets a notice with a 24 h
    one-time link that undoes the change (``/email-change/undo``). Both mails
    are background work: a failed send never breaks the request.
    """
    async with open_db(str(DB_PATH)) as adb:
        adb.row_factory = pg.Row
        cursor = await adb.execute(
            "SELECT password_hash, email, email_verified_at FROM users WHERE id = ? AND deleted_at IS NULL",
            (user.id,),
        )
        row = await cursor.fetchone()
    if not row or not verify_password(row["password_hash"], req.current_password):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    # Check new email is not already taken by another user
    async with open_db(str(DB_PATH)) as adb:
        adb.row_factory = pg.Row
        cursor = await adb.execute(
            "SELECT id FROM users WHERE LOWER(email) = LOWER(?) AND id != ?",
            (str(req.new_email), user.id),
        )
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="email already in use")
    # The undo token is stored BEFORE the change: if storing it fails nothing
    # has changed yet, and an orphan row (change failed) was never mailed.
    undo_token = await auth_email_change_undo.create_undo(
        db_path=str(DB_PATH),
        user_id=user.id,
        previous_email=row["email"],
        previous_verified_at=row["email_verified_at"],
    )
    await db.update_user_email(user.id, str(req.new_email))
    get_audit_logger().info(
        "auth",
        extra={"event": "email_changed", "user_id": safe_log_value(user.id), **_client_meta(request)},
    )
    background_tasks.add_task(
        _safe_send_email_change_mails,
        db_path=str(DB_PATH),
        user_id=user.id,
        old_email=row["email"],
        new_email=str(req.new_email),
        undo_token=undo_token,
        frontend_origin=_frontend_origin(),
    )
    # Terminate all of the user's sessions server-side (rule #26 — same as
    # password change), not just clear this browser's cookie.
    await auth_sessions.revoke_all_for_user(str(DB_PATH), user.id)
    # Mutate + return the injected response so the cookie clear reaches the client
    # (returning a fresh Response drops the Set-Cookie header).
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def _frontend_origin() -> str:
    """First entry from FRONTEND_ORIGIN (CORS allowlist) — used to build reset links."""
    raw = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
    return raw.split(",")[0].strip()


class EmailChangeUndoRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.post("/email-change/undo", status_code=status.HTTP_204_NO_CONTENT)
async def email_change_undo(
    req: EmailChangeUndoRequest,
    request: Request,
    response: Response,
) -> Response:
    """Undo an email change from the link mailed to the OLD address.

    Public, authenticated by its one-time token (like password-reset/confirm).
    On success: 204, the previous email and its previous verified state are
    back, and every session, agent token and OAuth grant is revoked.
    Unknown, used, expired or deleted-user token: generic 400. The previous
    address now belonging to another account: 409, nothing changed.

    Rate-limited per IP (429) like the password-reset request.
    """
    client_ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()[:16]
    if not auth_rate_limit.check_and_record(f"email-change-undo:{ip_hash}", max_in_window=10, window_seconds=60):
        logger.info("email-change undo rate-limited ip_hash=%s", ip_hash)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many attempts; try again in a minute",
        )
    outcome, user_id = await auth_email_change_undo.consume_undo(db_path=str(DB_PATH), raw_token=req.token)
    if outcome == auth_email_change_undo.CONFLICT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the previous email address is now used by another account",
        )
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired undo token",
        )
    get_audit_logger().info(
        "auth",
        extra={"event": "email_change_undo", "user_id": safe_log_value(user_id), **_client_meta(request)},
    )
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ── Phase −2-A: Password reset (forgot-password flow) ────────────────────────


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=256)


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
    ip_hash = hashlib.sha256(throttle_client_ip(request).encode("utf-8")).hexdigest()[:16]
    key = f"password-reset:{ip_hash}"
    # Two dimensions, both must pass. The IP throttle alone lets an attacker who
    # rotates IPs email-bomb ONE address (each fresh IP is a fresh bucket). The
    # per-email throttle caps total reset emails to a single address regardless
    # of source IP. Evaluate both eagerly so both counters record this attempt.
    email_hash = hashlib.sha256(str(req.email).lower().encode("utf-8")).hexdigest()[:16]
    email_key = f"password-reset-email:{email_hash}"
    ip_ok = auth_rate_limit.check_and_record(key, max_in_window=1, window_seconds=60)
    email_ok = auth_rate_limit.check_and_record(
        email_key, max_in_window=3, window_seconds=3600
    )
    # If EITHER limit is exceeded, behave exactly as the existing over-limit
    # path: return 204 silently (no token, no email) so the throttle stays
    # indistinguishable from an unknown-email response (no enumeration).
    if not (ip_ok and email_ok):
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

    On success: 204, and every existing session, agent token and OAuth grant
    for that user is revoked (forces re-login on all devices and reconnection
    of every assistant — security best practice after reset).
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
    await send_security_notice(str(DB_PATH), user_id, "password_changed")
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
    # Optional post-login redirect PATH (spec 2026-09-03-oauth-mcp R9) — lets
    # `/oauth/consent/{rid}` survive a login bounce. Validated server-side by
    # `magic_link.safe_next`; anything else is silently ignored, never a 400.
    next: str | None = Field(default=None, max_length=512)


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
        # CodeQL py/log-injection — `req.email` is masked for privacy, but
        # sanitize before masking too (defense in depth against a control
        # character reaching the masked local-part or being echoed by a
        # loosely-validated address).
        logger.info("magic-link rate-limited email=%s", mask_email(safe_log_value(str(req.email))))
        get_audit_logger().warning(
            "auth",
            extra={
                "event": "magic_link_request",
                "status": "rate_limited",
                "email": safe_log_value(mask_email(str(req.email))),
                **_client_meta(request),
            },
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await auth_magic_link.request_magic_link(
        db_path=str(DB_PATH),
        email=str(req.email),
        frontend_origin=_frontend_origin(),
        next_path=req.next,
    )
    get_audit_logger().info(
        "auth",
        extra={
            "event": "magic_link_request", "status": "ok",
            "email": safe_log_value(mask_email(str(req.email))), **_client_meta(request),
        },
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
) -> dict[str, bool]:
    """Cheap read for the dashboard to render a 'verify email' banner."""
    verified = await auth_email_verification.is_email_verified(
        db_path=str(DB_PATH), user_id=user.id
    )
    return {"email_verified": verified}
