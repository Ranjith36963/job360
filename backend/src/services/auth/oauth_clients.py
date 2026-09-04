"""OAuth client registration, loading, the redirect allow-list, and pruning.

Contract (docs/plans/2026-09-03-oauth-mcp/spec.md R2, S3, S7, R10):
  * a client registers itself with no credential (DCR, RFC 7591) — no secret
    is ever issued, only a public `client_id`;
  * every redirect URI is checked against a host-anchored allow-list before
    it is ever stored (S3) — a look-alike host or a path-traversal candidate
    is refused at registration, not at authorize time;
  * the client table is bounded (S7): at the ceiling, registration first
    prunes the oldest clients that never got a grant, and only refuses a
    real client if that freed nothing;
  * housekeeping (R10) deletes clients, requests, codes and tokens that are
    old and dead, sampled and off the hot path.

Pure helpers (`check_redirect_uri`, `normalize_redirect_uri`) do no I/O so
they are table-driven-testable without a DB.
"""
from __future__ import annotations

import json
import logging
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import SplitResult, urlsplit, urlunsplit

from src.core import settings
from src.repositories import pg
from src.repositories.db_retry import open_db
from src.utils.logger import get_audit_logger

logger = logging.getLogger("job360.oauth.clients")

CLIENT_ID_PREFIX = "j360c_"  # noqa: S105 — a public identifier, not a secret
MAX_REDIRECT_URIS = 10
MAX_CLIENT_NAME_CHARS = 100
DEFAULT_CLIENT_NAME = "Unnamed client"
_ALLOWED_GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})
_ALLOWED_RESPONSE_TYPES = frozenset({"code"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# Unicode categories stripped from client_name at registration: control (Cc),
# format incl. bidi overrides (Cf), surrogate (Cs), private-use (Co), and
# unassigned (Cn) — everything that isn't a printable/spacing character.
_STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def _now() -> datetime:
    """Module-level clock so tests can monkeypatch a single function."""
    return datetime.now(timezone.utc)


class RedirectURIError(ValueError):
    """Raised with an RFC 7591 error code as its message (e.g. "invalid_redirect_uri")."""


class InvalidClientMetadataError(ValueError):
    """Raised with an RFC 7591 error code as its message (e.g. "invalid_client_metadata")."""


class ClientCapacityError(RuntimeError):
    """The client table is at its ceiling and pruning freed nothing (S7)."""


@dataclass(frozen=True)
class RegisteredClient:
    id: str
    client_name: str
    redirect_uris: list[str]
    token_endpoint_auth_method: str
    grant_types: list[str]
    response_types: list[str]
    created_at: str


@dataclass(frozen=True)
class LoadedClient:
    id: str
    client_name: str
    redirect_uris: list[str]


# ── Pure helpers (no DB) ─────────────────────────────────────────────────────


def _default_port(scheme: str) -> int:
    return {"http": 80, "https": 443}.get(scheme, -1)


def _effective_port(parts: SplitResult) -> int:
    if parts.port is not None:
        return parts.port
    return _default_port(parts.scheme.lower())


def _is_loopback_host(host: str) -> bool:
    # No trailing-dot normalisation: "localhost." is a different name to a resolver.
    return host.lower() in _LOOPBACK_HOSTS


def _validate_shape(uri: str) -> SplitResult:
    """Structural checks shared by every candidate, before scheme/host logic.

    Refuses: >2048 chars, a backslash anywhere, no scheme/host, a fragment,
    userinfo, a `//` run in the path, a `%2e`/`%2E` escape, or a `.`/`..`
    path segment (dot-segment traversal).
    """
    if len(uri) > 2048:
        raise RedirectURIError("invalid_redirect_uri")
    if "\\" in uri:
        raise RedirectURIError("invalid_redirect_uri")
    parts = urlsplit(uri)
    if not parts.scheme or not parts.hostname:
        raise RedirectURIError("invalid_redirect_uri")
    if parts.fragment:
        raise RedirectURIError("invalid_redirect_uri")
    if parts.username is not None or parts.password is not None:
        raise RedirectURIError("invalid_redirect_uri")
    path = parts.path or ""
    if "//" in path:
        raise RedirectURIError("invalid_redirect_uri")
    if "%2e" in path.lower():
        raise RedirectURIError("invalid_redirect_uri")
    if any(seg in (".", "..") for seg in path.split("/")):
        raise RedirectURIError("invalid_redirect_uri")
    return parts


def _parse_allowlist_entries() -> list[tuple[str, str, int, str]]:
    """Parse `settings.OAUTH_REDIRECT_ALLOWLIST` into (scheme, host, port, path).

    An entry with an empty path is refused at load time (logged, ignored) —
    S3: `https://grok.x.ai` (no path) can never match `grok.x.ai.evil.com`,
    but also can never match anything else, so it is simply dropped.
    """
    entries: list[tuple[str, str, int, str]] = []
    raw = settings.OAUTH_REDIRECT_ALLOWLIST or ""
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = urlsplit(item)
        if not parts.scheme or not parts.hostname or not parts.path:
            logger.warning("oauth: ignoring malformed/empty-path allow-list entry: %s", item)
            continue
        entries.append((parts.scheme.lower(), parts.hostname.lower(), _effective_port(parts), parts.path))
    return entries


def _redirect_matches_allowlist(uri: str) -> bool:
    parts = urlsplit(uri)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = _effective_port(parts)
    path = parts.path or ""
    for e_scheme, e_host, e_port, e_path in _parse_allowlist_entries():
        if scheme != e_scheme or host != e_host or port != e_port:
            continue
        if path == e_path:
            return True
        if e_path.endswith("/") and path.startswith(e_path):
            return True
    return False


def check_redirect_uri(uri: str) -> None:
    """Raise :class:`RedirectURIError` unless `uri` passes S3 in full.

    Loopback candidates (host exactly `127.0.0.1` / `::1` / `localhost`,
    case-insensitive, scheme `http`) bypass the allow-list per RFC 8252, but
    only when `OAUTH_ALLOW_LOOPBACK_REDIRECTS` is on — the default is off,
    matching "nothing in production needs it" (intent.md). Every other
    candidate must be `https` and must match an allow-list entry exactly.
    """
    parts = _validate_shape(uri)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if _is_loopback_host(host) and scheme == "http":
        if not settings.OAUTH_ALLOW_LOOPBACK_REDIRECTS:
            raise RedirectURIError("invalid_redirect_uri")
        return
    if scheme != "https":
        raise RedirectURIError("invalid_redirect_uri")
    if not _redirect_matches_allowlist(uri):
        raise RedirectURIError("invalid_redirect_uri")


def normalize_redirect_uri(uri: str) -> str:
    """Lower-case scheme+host, drop a default port; keep path/query byte-for-byte.

    Used both when storing a client's redirect URIs and when comparing a
    presented `redirect_uri` against them (R3/R5 exact match).
    """
    parts = urlsplit(uri)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    default = _default_port(scheme)
    netloc = host if (port is None or port == default) else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "", parts.query, ""))


def sanitize_client_name(name: Optional[str]) -> str:
    """Strip bidi/format/control/non-printable chars; default + truncate.

    The result is stored and later rendered as untrusted text on the consent
    page (R9) — this only prevents a name from *looking* like something it
    isn't (a right-to-left override hiding a fake suffix, e.g.), it is not a
    trust signal.
    """
    if not name:
        return DEFAULT_CLIENT_NAME
    cleaned = "".join(ch for ch in name if unicodedata.category(ch) not in _STRIPPED_CATEGORIES)
    cleaned = cleaned.strip()
    if not cleaned:
        return DEFAULT_CLIENT_NAME
    return cleaned[:MAX_CLIENT_NAME_CHARS]


# ── DB-backed operations ─────────────────────────────────────────────────────


async def _count_clients(db: Any) -> int:
    cur = await db.execute("SELECT COUNT(*) FROM oauth_clients")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _prune_grantless_clients(db: Any, *, limit: int, older_than: Optional[str]) -> int:
    """Delete up to `limit` of the oldest clients with no `oauth_grants` row
    at all (revoked included). Postgres has no `DELETE ... LIMIT`, so the
    limit is applied via a `ctid` subquery (R10)."""
    cond = "NOT EXISTS (SELECT 1 FROM oauth_grants g WHERE g.client_id = oauth_clients.id)"
    params: tuple[Any, ...]
    if older_than is not None:
        cond += " AND created_at < ?"
        params = (older_than, limit)
    else:
        params = (limit,)
    cur = await db.execute(
        "DELETE FROM oauth_clients WHERE ctid IN ("  # noqa: S608 — no user input, static SQL
        f"SELECT ctid FROM oauth_clients WHERE {cond} "
        "ORDER BY created_at ASC LIMIT ?)",
        params,
    )
    return int(cur.rowcount or 0)


async def _prune_and_check_ceiling(db: Any) -> None:
    cap = settings.OAUTH_MAX_CLIENTS
    if cap <= 0:
        return
    if await _count_clients(db) < cap:
        return
    freed = await _prune_grantless_clients(db, limit=100, older_than=None)
    if freed == 0:
        raise ClientCapacityError("oauth client table is full")


async def register(
    db_path: str,
    *,
    redirect_uris: list[str],
    client_name: Optional[str],
    token_endpoint_auth_method: Optional[str],
    grant_types: Optional[list[str]],
    response_types: Optional[list[str]],
) -> RegisteredClient:
    """Register a new public OAuth client (RFC 7591).

    Raises :class:`RedirectURIError` (S3) or :class:`InvalidClientMetadataError`
    (other bad metadata) with an RFC 7591 error code as the message; the route
    maps either to a 400. Raises :class:`ClientCapacityError` (S7) which the
    route maps to a 503.
    """
    if not redirect_uris or len(redirect_uris) > MAX_REDIRECT_URIS:
        raise InvalidClientMetadataError("invalid_client_metadata")
    for uri in redirect_uris:
        check_redirect_uri(uri)
    normalized = [normalize_redirect_uri(u) for u in redirect_uris]

    if token_endpoint_auth_method not in (None, "none"):
        raise InvalidClientMetadataError("invalid_client_metadata")

    effective_grant_types = grant_types if grant_types is not None else ["authorization_code", "refresh_token"]
    if not effective_grant_types or not set(effective_grant_types) <= _ALLOWED_GRANT_TYPES:
        raise InvalidClientMetadataError("invalid_client_metadata")

    effective_response_types = response_types if response_types is not None else ["code"]
    if not effective_response_types or not set(effective_response_types) <= _ALLOWED_RESPONSE_TYPES:
        raise InvalidClientMetadataError("invalid_client_metadata")

    if client_name is not None and len(client_name) > MAX_CLIENT_NAME_CHARS:
        raise InvalidClientMetadataError("invalid_client_metadata")
    name = sanitize_client_name(client_name)

    client_id = CLIENT_ID_PREFIX + secrets.token_urlsafe(24)
    created_at = _now().isoformat()

    async with open_db(db_path) as db:
        await _prune_and_check_ceiling(db)
        await db.execute(
            "INSERT INTO oauth_clients(id, client_name, redirect_uris, "
            "token_endpoint_auth_method, created_at) VALUES (?, ?, ?, ?, ?)",
            (client_id, name, json.dumps(normalized), "none", created_at),
        )
        await db.commit()

    get_audit_logger().info(
        "oauth_client_registered",
        extra={
            "event": "oauth_client_registered",
            "client_id": client_id,
            "redirect_hosts": sorted({urlsplit(u).hostname or "" for u in normalized}),
        },
    )
    return RegisteredClient(
        id=client_id,
        client_name=name,
        redirect_uris=normalized,
        token_endpoint_auth_method="none",  # noqa: S106 — the RFC 7591 auth-method literal, not a secret
        grant_types=list(effective_grant_types),
        response_types=list(effective_response_types),
        created_at=created_at,
    )


async def load(db_path: str, client_id: str) -> Optional[LoadedClient]:
    """Return the client, or None when `client_id` is unknown."""
    if not client_id:
        return None
    async with open_db(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT id, client_name, redirect_uris FROM oauth_clients WHERE id = ?",
            (client_id,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    return LoadedClient(id=row["id"], client_name=row["client_name"], redirect_uris=json.loads(row["redirect_uris"]))


async def touch_last_used(db_path: str, client_id: str) -> None:
    """Best-effort `last_used_at` bump — never raises."""
    try:
        async with open_db(db_path) as db:
            await db.execute(
                "UPDATE oauth_clients SET last_used_at = ? WHERE id = ?",
                (_now().isoformat(), client_id),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — a display hint, never worth failing a request
        logger.warning("oauth: failed to touch client last_used_at: %s", exc)


async def prune(db_path: str) -> None:
    """R10 housekeeping — sampled at the call site, own transaction, never raises.

    Deletes: clients with no `oauth_grants` row at all, older than
    `OAUTH_CLIENT_PRUNE_DAYS`; authorization requests, codes and tokens that
    are already dead (consumed/expired/revoked) and older than 1 day.
    """
    try:
        now = _now()
        now_iso = now.isoformat()
        client_cutoff = (now - timedelta(days=settings.OAUTH_CLIENT_PRUNE_DAYS)).isoformat()
        row_cutoff = (now - timedelta(days=1)).isoformat()
        async with open_db(db_path) as db:
            await _prune_grantless_clients(db, limit=100, older_than=client_cutoff)
            await db.execute(
                "DELETE FROM oauth_authorization_requests WHERE ctid IN ("
                "SELECT ctid FROM oauth_authorization_requests WHERE "
                "(consumed_at IS NOT NULL OR expires_at < ?) AND created_at < ? LIMIT 100)",
                (now_iso, row_cutoff),
            )
            await db.execute(
                "DELETE FROM oauth_authorization_codes WHERE ctid IN ("
                "SELECT ctid FROM oauth_authorization_codes WHERE "
                "(used_at IS NOT NULL OR expires_at < ?) AND created_at < ? LIMIT 100)",
                (now_iso, row_cutoff),
            )
            await db.execute(
                "DELETE FROM oauth_tokens WHERE ctid IN ("
                "SELECT ctid FROM oauth_tokens WHERE "
                "(revoked_at IS NOT NULL OR expires_at < ?) AND created_at < ? LIMIT 100)",
                (now_iso, row_cutoff),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — housekeeping must never break a real request
        logger.warning("oauth: prune failed: %s", exc)
