"""The OAuth 2.1 authorization-code + PKCE flow itself.

Contract (docs/plans/2026-09-03-oauth-mcp/spec.md R3-R8, S1-S5, S9-S13):
  * PKCE S256 is the only binding between the browser leg and the token leg
    (no client secret exists) — `verify_pkce` is constant-time;
  * a code or token is claimed with one atomic `UPDATE ... RETURNING`, so two
    concurrent exchanges cannot both win, and every check after the claim
    still leaves the credential consumed;
  * reuse of an already-claimed code/refresh-token revokes the whole grant
    UNLESS it happened inside `OAUTH_REUSE_GRACE_SECONDS` (a client
    timeout-and-retry, not an attack);
  * revocation is immediate and total: `resolve_access_token` re-checks the
    live grant/token/user on every call, no cache;
  * a redirect URL is always built with `urlencode`, never string
    concatenation — `state` is attacker-supplied.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from src.core import settings
from src.repositories import pg
from src.repositories.db_retry import open_db
from src.utils.logger import get_audit_logger

ACCESS_TOKEN_PREFIX = "j360a_"  # noqa: S105 — a public display prefix, not a secret
REFRESH_TOKEN_PREFIX = "j360r_"  # noqa: S105
SUPPORTED_SCOPE = "job360"
# `last_used_at` is a hint, not an audit trail — same interval as api_tokens.py.
LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)

# `\Z`, not `$` — `$` also matches before a trailing newline.
_CODE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}\Z")
_CODE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}\Z")


def _now() -> datetime:
    """Module-level clock so tests can monkeypatch a single function."""
    return datetime.now(timezone.utc)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class TokenError(Exception):
    """Carries the HTTP status + RFC 6749 error code/description for the route."""

    def __init__(self, status_code: int, error: str, description: str = ""):
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.description = description


class AuthorizationRequestGoneError(LookupError):
    """The `rid` is unknown, already consumed, or expired — route returns 404."""


# ── Pure helpers (no DB) ─────────────────────────────────────────────────────


def is_valid_code_challenge_format(challenge: str) -> bool:
    """S1: exactly 43 base64url characters (a raw SHA-256 digest, unpadded)."""
    return bool(challenge) and bool(_CODE_CHALLENGE_RE.match(challenge))


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """S1: `base64url(sha256(code_verifier))` (no padding) via constant-time compare.

    The verifier's own charset/length (43-128 of `[A-Za-z0-9._~-]`, RFC 7636)
    is checked first so a malformed verifier fails fast without touching the
    comparison.
    """
    if not code_verifier or not _CODE_VERIFIER_RE.match(code_verifier):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, code_challenge)


def canonical_resource() -> str:
    """The one audience this server ever issues (S13)."""
    return f"{settings.SITE_BASE_URL}/api/mcp"


def normalize_resource(resource: str) -> str:
    """Lower-case scheme+host and strip one trailing slash — nothing else."""
    parts = urlsplit(resource)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def resource_matches_canonical(resource: str) -> bool:
    try:
        return normalize_resource(resource) == normalize_resource(canonical_resource())
    except ValueError:
        return False


def build_redirect_url(redirect_uri: str, params: dict[str, Optional[str]]) -> str:
    """Build a redirect URL with `urlencode` — never string concatenation.

    `state` is attacker-supplied; CR/LF in a hand-built `Location` header is
    an HTTP response-splitting vector (spec R3).
    """
    query = urlencode({k: v for k, v in params.items() if v is not None})
    if not query:
        return redirect_uri
    separator = "&" if urlsplit(redirect_uri).query else "?"
    return f"{redirect_uri}{separator}{query}"


# ── Authorization requests + consent (R3, R4) ────────────────────────────────


@dataclass(frozen=True)
class AuthorizationRequest:
    id: str
    client_id: str
    client_name: str
    redirect_uri: str
    scope: str
    state: Optional[str]
    code_challenge: str
    resource: str
    expires_at: str


async def create_authorization_request(
    db_path: str,
    *,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: Optional[str],
    code_challenge: str,
    code_challenge_method: str,
    resource: str,
) -> str:
    """Store a pending consent request; returns its `rid` (R3, valid path)."""
    rid = secrets.token_urlsafe(32)
    now = _now()
    expires_at = (now + timedelta(seconds=settings.OAUTH_AUTHORIZE_TTL_SECONDS)).isoformat()
    async with open_db(db_path) as db:
        await db.execute(
            "INSERT INTO oauth_authorization_requests(id, client_id, redirect_uri, scope, "
            "state, code_challenge, code_challenge_method, resource, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rid, client_id, redirect_uri, scope, state, code_challenge,
                code_challenge_method, resource, now.isoformat(), expires_at,
            ),
        )
        await db.commit()
    return rid


async def get_pending_authorization_request(db_path: str, rid: str) -> Optional[AuthorizationRequest]:
    """R4 GET: None when the rid is unknown, consumed, or expired (route -> 404)."""
    now_iso = _now().isoformat()
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT r.id, r.client_id, r.redirect_uri, r.scope, r.state, "
            "r.code_challenge, r.resource, r.expires_at, c.client_name "
            "FROM oauth_authorization_requests r "
            "JOIN oauth_clients c ON c.id = r.client_id "
            "WHERE r.id = ? AND r.consumed_at IS NULL AND r.expires_at > ?",
            (rid, now_iso),
        )
        row = await cur.fetchone()
    if not row:
        return None
    return AuthorizationRequest(
        id=row["id"], client_id=row["client_id"], client_name=row["client_name"],
        redirect_uri=row["redirect_uri"], scope=row["scope"], state=row["state"],
        code_challenge=row["code_challenge"], resource=row["resource"], expires_at=row["expires_at"],
    )


async def decide_authorization_request(
    db_path: str, *, rid: str, user_id: str, approve: bool
) -> str:
    """R4 POST decision. Returns `redirect_to`; raises :class:`AuthorizationRequestGoneError`
    for an unknown/consumed/expired rid (route -> 404). The user is always the
    session's (never a body field, rule #25)."""
    req = await get_pending_authorization_request(db_path, rid)
    if req is None:
        raise AuthorizationRequestGoneError(rid)

    async with open_db(db_path) as db:
        # Consume exactly once, even under a concurrent double-click.
        consume_cur = await db.execute(
            "UPDATE oauth_authorization_requests SET consumed_at = ? "
            "WHERE id = ? AND consumed_at IS NULL",
            (_now().isoformat(), rid),
        )
        if not consume_cur.rowcount:
            raise AuthorizationRequestGoneError(rid)

        if not approve:
            await db.commit()
            return build_redirect_url(req.redirect_uri, {"error": "access_denied", "state": req.state})

        db.row_factory = pg.Row
        grant_cur = await db.execute(
            "SELECT id FROM oauth_grants WHERE user_id = ? AND client_id = ? AND revoked_at IS NULL",
            (user_id, req.client_id),
        )
        grant_row = await grant_cur.fetchone()
        if grant_row is not None:
            grant_id = int(grant_row["id"])
        else:
            ins_cur = await db.execute(
                "INSERT INTO oauth_grants(user_id, client_id, scope, created_at) VALUES (?, ?, ?, ?)",
                (user_id, req.client_id, req.scope, _now().isoformat()),
            )
            grant_id = int(ins_cur.lastrowid)
            get_audit_logger().info(
                "oauth_grant_created",
                extra={
                    "event": "oauth_grant_created", "user_id": user_id,
                    "client_id": req.client_id, "grant_id": grant_id,
                },
            )

        code = secrets.token_urlsafe(32)
        now = _now()
        expires_at = (now + timedelta(seconds=settings.OAUTH_CODE_TTL_SECONDS)).isoformat()
        await db.execute(
            "INSERT INTO oauth_authorization_codes(code_hash, grant_id, client_id, redirect_uri, "
            "code_challenge, resource, scope, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _hash(code), grant_id, req.client_id, req.redirect_uri,
                req.code_challenge, req.resource, req.scope, now.isoformat(), expires_at,
            ),
        )
        await db.commit()

    return build_redirect_url(req.redirect_uri, {"code": code, "state": req.state})


# ── Token endpoint (R5) ───────────────────────────────────────────────────────


async def _grant_created_at(db: Any, grant_id: int) -> datetime:
    db.row_factory = pg.Row
    cur = await db.execute("SELECT created_at FROM oauth_grants WHERE id = ?", (grant_id,))
    row = await cur.fetchone()
    ts = _parse_ts(row["created_at"]) if row else None
    return ts or _now()


async def _issue_token_pair(db: Any, *, grant_id: int, audience: str) -> tuple[str, str, int, int, int]:
    """Insert a fresh access+refresh pair. Returns (access, refresh, expires_in, access_id, refresh_id)."""
    now = _now()
    access = ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)
    refresh = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
    access_expires = (now + timedelta(seconds=settings.OAUTH_ACCESS_TOKEN_TTL_SECONDS)).isoformat()
    # Absolute, not sliding (R5): every refresh token in a grant expires at
    # grant.created_at + OAUTH_REFRESH_TOKEN_TTL_SECONDS.
    grant_created = await _grant_created_at(db, grant_id)
    refresh_expires = (grant_created + timedelta(seconds=settings.OAUTH_REFRESH_TOKEN_TTL_SECONDS)).isoformat()

    access_cur = await db.execute(
        "INSERT INTO oauth_tokens(grant_id, kind, token_hash, audience, created_at, expires_at) "
        "VALUES (?, 'access', ?, ?, ?, ?)",
        (grant_id, _hash(access), audience, now.isoformat(), access_expires),
    )
    refresh_cur = await db.execute(
        "INSERT INTO oauth_tokens(grant_id, kind, token_hash, audience, created_at, expires_at) "
        "VALUES (?, 'refresh', ?, ?, ?, ?)",
        (grant_id, _hash(refresh), audience, now.isoformat(), refresh_expires),
    )
    expires_in = settings.OAUTH_ACCESS_TOKEN_TTL_SECONDS
    return access, refresh, expires_in, int(access_cur.lastrowid), int(refresh_cur.lastrowid)


async def _revoke_grant(db: Any, grant_id: int, *, reason: str) -> None:
    await db.execute(
        "UPDATE oauth_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (_now().isoformat(), grant_id),
    )
    await db.commit()
    get_audit_logger().info(
        "oauth_grant_revoked", extra={"event": "oauth_grant_revoked", "grant_id": grant_id, "by": reason},
    )


async def _handle_possible_code_reuse(db: Any, code_hash: str, now: datetime) -> None:
    """S4/R5: a code reused after the grace window revokes its grant."""
    db.row_factory = pg.Row
    cur = await db.execute(
        "SELECT grant_id, used_at FROM oauth_authorization_codes WHERE code_hash = ?",
        (code_hash,),
    )
    row = await cur.fetchone()
    if row is None or row["used_at"] is None:
        return  # genuinely unknown code — nothing to revoke
    used_at = _parse_ts(row["used_at"])
    if used_at is None:
        return
    if (now - used_at).total_seconds() > settings.OAUTH_REUSE_GRACE_SECONDS:
        await _revoke_grant(db, int(row["grant_id"]), reason="reuse")


async def _load_live_grant(db: Any, grant_id: int) -> Optional[Any]:
    db.row_factory = pg.Row
    cur = await db.execute(
        "SELECT g.id FROM oauth_grants g JOIN users u ON u.id = g.user_id "
        "WHERE g.id = ? AND g.revoked_at IS NULL AND u.deleted_at IS NULL",
        (grant_id,),
    )
    return await cur.fetchone()


async def exchange_authorization_code(
    db_path: str,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
    resource: Optional[str],
) -> dict[str, Any]:
    """R5 `grant_type=authorization_code`. Raises :class:`TokenError` on failure.

    The code is claimed ATOMICALLY before any other check (S4): two
    concurrent exchanges cannot both win, and a check failing after the claim
    still leaves the code consumed.
    """
    if not code or not client_id or not redirect_uri or not code_verifier:
        raise TokenError(400, "invalid_request", "missing required parameter")

    code_hash = _hash(code)
    now = _now()
    now_iso = now.isoformat()
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        claim_cur = await db.execute(
            "UPDATE oauth_authorization_codes SET used_at = ? "
            "WHERE code_hash = ? AND used_at IS NULL "
            "RETURNING grant_id, client_id, redirect_uri, code_challenge, resource, scope, expires_at",
            (now_iso, code_hash),
        )
        claimed = await claim_cur.fetchone()
        if claimed is None:
            await _handle_possible_code_reuse(db, code_hash, now)
            raise TokenError(400, "invalid_grant", "authorization code not found or already used")

        if claimed["client_id"] != client_id:
            raise TokenError(400, "invalid_grant", "client_id mismatch")
        if claimed["redirect_uri"] != redirect_uri:
            raise TokenError(400, "invalid_grant", "redirect_uri mismatch")
        if claimed["expires_at"] <= now_iso:
            raise TokenError(400, "invalid_grant", "authorization code expired")
        if resource is not None and normalize_resource(resource) != normalize_resource(claimed["resource"]):
            raise TokenError(400, "invalid_target", "resource mismatch")
        if not verify_pkce(code_verifier, claimed["code_challenge"]):
            raise TokenError(400, "invalid_grant", "PKCE verification failed")

        grant_id = int(claimed["grant_id"])
        if await _load_live_grant(db, grant_id) is None:
            raise TokenError(400, "invalid_grant", "grant is no longer active")

        access, refresh, expires_in, _access_id, _refresh_id = await _issue_token_pair(
            db, grant_id=grant_id, audience=claimed["resource"]
        )
        await db.execute("UPDATE oauth_grants SET last_used_at = ? WHERE id = ?", (now_iso, grant_id))
        await db.commit()

    get_audit_logger().info(
        "oauth_token_issued",
        extra={"event": "oauth_token_issued", "grant_id": grant_id, "grant_type": "authorization_code"},
    )
    return {
        "access_token": access, "token_type": "Bearer", "expires_in": expires_in,
        "refresh_token": refresh, "scope": claimed["scope"],
    }


async def _load_grant_for_client(db: Any, grant_id: int, *, client_id: str) -> Optional[Any]:
    db.row_factory = pg.Row
    cur = await db.execute(
        "SELECT g.id, g.scope FROM oauth_grants g JOIN users u ON u.id = g.user_id "
        "WHERE g.id = ? AND g.client_id = ? AND g.revoked_at IS NULL AND u.deleted_at IS NULL",
        (grant_id, client_id),
    )
    return await cur.fetchone()


async def _token_created_at(db: Any, token_id: int) -> Optional[datetime]:
    db.row_factory = pg.Row
    cur = await db.execute("SELECT created_at FROM oauth_tokens WHERE id = ?", (token_id,))
    row = await cur.fetchone()
    return _parse_ts(row["created_at"]) if row else None


async def _handle_possible_refresh_reuse(db: Any, token_hash: str, now: datetime) -> None:
    """S4/R5: a rotated refresh token presented again after the grace window
    revokes the grant. `replaced_by == 0` is the in-flight claim sentinel of
    a request racing THIS one — contention, not reuse — so it is skipped."""
    db.row_factory = pg.Row
    cur = await db.execute(
        "SELECT grant_id, replaced_by FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'",
        (token_hash,),
    )
    row = await cur.fetchone()
    if row is None or row["replaced_by"] is None or row["replaced_by"] == 0:
        return
    rotated_at = await _token_created_at(db, int(row["replaced_by"]))
    if rotated_at is None:
        return
    if (now - rotated_at).total_seconds() > settings.OAUTH_REUSE_GRACE_SECONDS:
        await _revoke_grant(db, int(row["grant_id"]), reason="reuse")


async def refresh_access_token(db_path: str, *, refresh_token: str, client_id: str) -> dict[str, Any]:
    """R5 `grant_type=refresh_token`. Raises :class:`TokenError` on failure."""
    if not refresh_token or not client_id:
        raise TokenError(400, "invalid_request", "missing required parameter")
    if not refresh_token.startswith(REFRESH_TOKEN_PREFIX):
        raise TokenError(400, "invalid_grant", "not a refresh token")

    token_hash = _hash(refresh_token)
    now = _now()
    now_iso = now.isoformat()
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        claim_cur = await db.execute(
            "UPDATE oauth_tokens SET replaced_by = 0 "
            "WHERE token_hash = ? AND kind = 'refresh' AND replaced_by IS NULL AND revoked_at IS NULL "
            "RETURNING id, grant_id, audience, expires_at",
            (token_hash,),
        )
        claimed = await claim_cur.fetchone()
        if claimed is None:
            await _handle_possible_refresh_reuse(db, token_hash, now)
            raise TokenError(400, "invalid_grant", "refresh token not found or already used")

        # The claim is committed (autocommit driver). A failure below must not
        # leave the row parked on the `replaced_by = 0` sentinel for ever —
        # `_handle_possible_refresh_reuse` skips the sentinel, so a stuck row
        # would never trip reuse detection again.
        if claimed["expires_at"] <= now_iso:
            await db.execute(
                "UPDATE oauth_tokens SET revoked_at = ?, replaced_by = NULL WHERE id = ?",
                (now_iso, int(claimed["id"])),
            )
            await db.commit()
            raise TokenError(400, "invalid_grant", "refresh token expired")

        grant_id = int(claimed["grant_id"])
        grant = await _load_grant_for_client(db, grant_id, client_id=client_id)
        if grant is None:
            # Wrong client, or a dead grant: give the row back untouched so the
            # right client (if any) can still use it and reuse detection holds.
            await db.execute(
                "UPDATE oauth_tokens SET replaced_by = NULL WHERE id = ? AND replaced_by = 0",
                (int(claimed["id"]),),
            )
            await db.commit()
            raise TokenError(400, "invalid_grant", "grant is no longer active for this client")

        access, refresh, expires_in, access_id, refresh_id = await _issue_token_pair(
            db, grant_id=grant_id, audience=claimed["audience"]
        )
        await db.execute(
            "UPDATE oauth_tokens SET replaced_by = ? WHERE id = ?", (refresh_id, int(claimed["id"]))
        )
        # The old access tokens of this grant are revoked (R5).
        await db.execute(
            "UPDATE oauth_tokens SET revoked_at = ? WHERE grant_id = ? AND kind = 'access' "
            "AND id != ? AND revoked_at IS NULL",
            (now_iso, grant_id, access_id),
        )
        await db.execute("UPDATE oauth_grants SET last_used_at = ? WHERE id = ?", (now_iso, grant_id))
        await db.commit()

    get_audit_logger().info(
        "oauth_token_issued",
        extra={"event": "oauth_token_issued", "grant_id": grant_id, "grant_type": "refresh_token"},
    )
    return {
        "access_token": access, "token_type": "Bearer", "expires_in": expires_in,
        "refresh_token": refresh, "scope": grant["scope"],
    }


# ── Bearer resolution (R6) ────────────────────────────────────────────────────


@dataclass(frozen=True)
class OAuthTokenOwner:
    """What a valid `j360a_...` bearer resolves to."""

    user_id: str
    email: str
    email_verified: bool
    audience: str
    # The registering client's display name — untrusted attacker text by
    # construction (slice 1 R2 sanitises it at registration; the application
    # spine re-truncates it — spec 2026-09-04-application-spine S3/C5).
    client_name: str


@dataclass(frozen=True)
class AccessTokenResolution:
    """`owner` is None for anything not usable; `hash_known` says whether a row
    exists at all (True) versus the hash matching nothing (False) — only the
    latter counts toward the bearer-failure throttle (R6)."""

    owner: Optional[OAuthTokenOwner]
    hash_known: bool


async def resolve_access_token(db_path: str, token: str) -> AccessTokenResolution:
    """Map a presented `j360a_...` bearer to its owner (R6/S5).

    Live grant, live token, unexpired, undeleted user — checked on every
    call, no cache, so a revoke or expiry takes effect on the very next
    request. An expired/revoked token is `hash_known=True` (never throttled);
    a hash matching nothing is `hash_known=False` (the real brute-force case).
    """
    if not token or not token.startswith(ACCESS_TOKEN_PREFIX):
        return AccessTokenResolution(owner=None, hash_known=False)

    now = _now()
    now_iso = now.isoformat()
    token_hash = _hash(token)
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT t.audience, t.revoked_at AS token_revoked_at, t.expires_at AS token_expires_at, "
            "g.id AS grant_id, g.revoked_at AS grant_revoked_at, g.last_used_at, "
            "u.id AS user_id, u.email, u.email_verified_at, u.deleted_at AS user_deleted_at, "
            "c.client_name "
            "FROM oauth_tokens t "
            "JOIN oauth_grants g ON g.id = t.grant_id "
            "JOIN users u ON u.id = g.user_id "
            "JOIN oauth_clients c ON c.id = g.client_id "
            "WHERE t.token_hash = ? AND t.kind = 'access'",
            (token_hash,),
        )
        row = await cur.fetchone()
        if row is None:
            return AccessTokenResolution(owner=None, hash_known=False)

        live = (
            row["token_revoked_at"] is None
            and row["token_expires_at"] > now_iso
            and row["grant_revoked_at"] is None
            and row["user_deleted_at"] is None
        )
        if not live:
            return AccessTokenResolution(owner=None, hash_known=True)

        last_used = _parse_ts(row["last_used_at"])
        if last_used is None or now - last_used >= LAST_USED_WRITE_INTERVAL:
            await db.execute(
                "UPDATE oauth_grants SET last_used_at = ? WHERE id = ?", (now_iso, row["grant_id"])
            )
            await db.commit()

    return AccessTokenResolution(
        owner=OAuthTokenOwner(
            user_id=row["user_id"], email=row["email"],
            email_verified=row["email_verified_at"] is not None, audience=row["audience"],
            client_name=row["client_name"],
        ),
        hash_known=True,
    )


# ── Revocation (R8) ───────────────────────────────────────────────────────────


async def revoke_token(db_path: str, *, token: str, client_id: Optional[str]) -> None:
    """RFC 7009: revoke the GRANT `token` belongs to — access or refresh,
    either kills everything under that grant (a deliberate deviation from
    per-token revocation, per spec R8). Silent no-op for anything that
    doesn't resolve; the route always answers 200 regardless."""
    if not token:
        return
    if token.startswith(ACCESS_TOKEN_PREFIX):
        kind = "access"
    elif token.startswith(REFRESH_TOKEN_PREFIX):
        kind = "refresh"
    else:
        return

    token_hash = _hash(token)
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT grant_id FROM oauth_tokens WHERE token_hash = ? AND kind = ?",
            (token_hash, kind),
        )
        row = await cur.fetchone()
        if row is None:
            return
        grant_id = int(row["grant_id"])
        # R8: the grant is revoked only when the token is THIS client's. A
        # missing `client_id` is "no match", never "skip the check".
        if client_id is None:
            return
        client_cur = await db.execute("SELECT client_id FROM oauth_grants WHERE id = ?", (grant_id,))
        client_row = await client_cur.fetchone()
        if client_row is None or client_row["client_id"] != client_id:
            return
        await db.execute(
            "UPDATE oauth_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_now().isoformat(), grant_id),
        )
        await db.commit()

    get_audit_logger().info(
        "oauth_grant_revoked", extra={"event": "oauth_grant_revoked", "grant_id": grant_id, "by": "client"},
    )


# ── Web-facing "Connected apps" (session only, R8) ───────────────────────────


async def list_grants_for_user(db_path: str, user_id: str) -> list[dict[str, Any]]:
    """The user's live grants. `redirect_uri` is the client's first
    registered URI (grants don't store one; a code's URI is pruned after a
    day, so it can't be the source here)."""
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT g.id, c.client_name, c.redirect_uris, g.scope, g.created_at, g.last_used_at "
            "FROM oauth_grants g JOIN oauth_clients c ON c.id = g.client_id "
            "WHERE g.user_id = ? AND g.revoked_at IS NULL "
            "ORDER BY g.created_at DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
    result: list[dict[str, Any]] = []
    for r in rows:
        uris = json.loads(r["redirect_uris"]) if r["redirect_uris"] else []
        result.append({
            "id": int(r["id"]),
            "client_name": r["client_name"],
            "redirect_uri": uris[0] if uris else "",
            "scope": r["scope"],
            "created_at": str(r["created_at"]),
            "last_used_at": str(r["last_used_at"]) if r["last_used_at"] else None,
        })
    return result


async def revoke_grant_for_user(db_path: str, *, user_id: str, grant_id: int) -> bool:
    """False when the grant is not the user's or already revoked (route -> 404)."""
    async with open_db(db_path) as db:
        cur = await db.execute(
            "UPDATE oauth_grants SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (_now().isoformat(), grant_id, user_id),
        )
        changed = cur.rowcount
        await db.commit()
    if changed:
        get_audit_logger().info(
            "oauth_grant_revoked", extra={"event": "oauth_grant_revoked", "grant_id": grant_id, "by": "user"},
        )
    return bool(changed)
