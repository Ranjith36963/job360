"""OAuth 2.1 authorization server for MCP clients (docs/plans/2026-09-03-oauth-mcp).

Frozen per spec.md's own list (§Frozen tests) — once written these are not
edited to make the implementation pass; a test believed wrong is left as-is
and called out in the build report instead.

Pure helpers (allow-list match, PKCE, code-challenge format) are exercised
without a DB. Everything else drives the real routes through
``authenticated_async_context`` (same fixture ``test_api_tokens.py`` uses) so
it runs against the same Postgres the rest of the suite does.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.auth import magic_link as auth_magic_link
from src.services.auth import oauth_clients, oauth_flow

pytest.importorskip("mcp")

CLAUDE_REDIRECT = "https://claude.ai/api/mcp/auth_callback"
CHATGPT_PREFIX_REDIRECT = "https://chatgpt.com/connector/oauth/abc123"


# ── Shared helpers ────────────────────────────────────────────────────────────


def _unauth_client() -> AsyncClient:
    """A client with no cookie and no bearer — the public routes (register,
    authorize step-1/2, token, revoke, discovery)."""
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _mcp_client(token: str):
    """Official MCP client wired straight into the FastAPI app (in-process),
    mirroring ``test_mcp_server.py``."""
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)[:64]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _rid_from_location(location: str) -> str:
    return location.rstrip("/").rsplit("/", 1)[-1]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _set_token_field(field: str, value, *, token: str, kind: str = "access") -> None:
    """Direct-DB edit (mirrors ``test_mcp_gate_parity.py``'s ``_unverify``) —
    the only way to produce states the public API can't (an expired row, a
    wrong audience) without sleeping or a second authorization flow."""
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(
        f"UPDATE oauth_tokens SET {field} = ? WHERE token_hash = ? AND kind = ?",  # noqa: S608 — fixed column set, test-only
        (value, _hash_token(token), kind),
    )
    conn.commit()
    conn.close()


async def _register_client(redirect_uris: list[str], **kwargs) -> dict:
    async with _unauth_client() as client:
        resp = await client.post("/api/oauth/register", json={"redirect_uris": redirect_uris, **kwargs})
        assert resp.status_code == 201, resp.text
        return resp.json()


async def _authorize(
    session_client: AsyncClient,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: Optional[str],
    state: Optional[str] = "state-xyz",
    scope: Optional[str] = None,
    resource: Optional[str] = None,
    response_type: str = "code",
    code_challenge_method: Optional[str] = "S256",
):
    params: dict[str, str] = {"response_type": response_type, "client_id": client_id, "redirect_uri": redirect_uri}
    if code_challenge is not None:
        params["code_challenge"] = code_challenge
    if code_challenge_method is not None:
        params["code_challenge_method"] = code_challenge_method
    if state is not None:
        params["state"] = state
    if scope is not None:
        params["scope"] = scope
    if resource is not None:
        params["resource"] = resource
    return await session_client.get("/api/oauth/authorize", params=params, follow_redirects=False)


async def _authorize_and_approve(
    authenticated_async_context, *, client_id: str, redirect_uri: str, code_challenge: str, state: str = "s1"
) -> str:
    """Drive authorize -> consent GET -> approve. Returns the code."""
    async with authenticated_async_context() as session_client:
        resp = await _authorize(
            session_client, client_id=client_id, redirect_uri=redirect_uri, code_challenge=code_challenge, state=state
        )
        assert resp.status_code == 302, resp.text
        rid = _rid_from_location(resp.headers["location"])

        got = await session_client.get(f"/api/oauth/authorize/{rid}")
        assert got.status_code == 200, got.text
        assert got.json()["redirect_uri"] == redirect_uri

        decided = await session_client.post(f"/api/oauth/authorize/{rid}/decision", json={"approve": True})
        assert decided.status_code == 200, decided.text
    redirect_to = decided.json()["redirect_to"]
    qs = parse_qs(urlsplit(redirect_to).query)
    assert qs.get("state", [None])[0] == state
    return qs["code"][0]


async def _full_code_exchange(
    authenticated_async_context, *, redirect_uri: str = CLAUDE_REDIRECT, token_headers: Optional[dict] = None
) -> dict:
    """Register a client and drive it end to end to a token pair. Returns the
    token JSON plus bookkeeping the caller needs (registered client, verifier)."""
    registered = await _register_client([redirect_uri])
    verifier, challenge = _pkce_pair()
    code = await _authorize_and_approve(
        authenticated_async_context, client_id=registered["client_id"], redirect_uri=redirect_uri, code_challenge=challenge
    )
    async with _unauth_client() as client:
        resp = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": registered["client_id"],
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            headers=token_headers or {},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {"registered": registered, "verifier": verifier, "code": code, "redirect_uri": redirect_uri, **body}


class _Clock:
    """Injectable clock for `oauth_flow._now` — advance instead of sleeping."""

    def __init__(self, start: datetime):
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.fixture
def oauth_clock(monkeypatch):
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(oauth_flow, "_now", clock)
    return clock


# ═══════════════════════════════════════════════════════════════════════════
# Pure helpers — no DB (table-driven)
# ═══════════════════════════════════════════════════════════════════════════


class TestRedirectAllowlist:
    """S3 — host-anchored allow-list, every evasion case named in the spec."""

    @pytest.mark.parametrize(
        "uri",
        [
            "https://claude.ai/api/mcp/auth_callback/../../evil",  # dot-segment traversal
            "https://claude.ai/api/mcp/auth_callback%2e%2e",  # %2e escape
            "http://localhost.evil.com/cb",  # look-alike host, not loopback
            "http://claude.ai/api/mcp/auth_callback",  # http, non-loopback host
            "https://user@claude.ai/api/mcp/auth_callback",  # userinfo
            "https://claude.ai/api/mcp/auth_callback#frag",  # fragment
            "https://claude.ai:9999/api/mcp/auth_callback",  # wrong port
            "https://claude.ai/api/mcp/auth_callback_evil",  # not the registered path
        ],
        ids=[
            "dot-segment", "percent-2e", "localhost-lookalike", "http-non-loopback",
            "userinfo", "fragment", "wrong-port", "path-not-registered",
        ],
    )
    def test_rejected(self, uri):
        with pytest.raises(oauth_clients.RedirectURIError):
            oauth_clients.check_redirect_uri(uri)

    def test_host_only_allowlist_entry_never_matches_evil_suffix(self, monkeypatch):
        """`https://grok.x.ai` (no path) must be dropped at load time, so
        `grok.x.ai.evil.com` can never match it — and neither can the real host."""
        monkeypatch.setattr(settings, "OAUTH_REDIRECT_ALLOWLIST", "https://grok.x.ai")
        with pytest.raises(oauth_clients.RedirectURIError):
            oauth_clients.check_redirect_uri("https://grok.x.ai.evil.com/cb")
        with pytest.raises(oauth_clients.RedirectURIError):
            oauth_clients.check_redirect_uri("https://grok.x.ai/cb")

    def test_exact_entry_accepted(self):
        oauth_clients.check_redirect_uri(CLAUDE_REDIRECT)  # must not raise

    def test_prefix_entry_accepted(self):
        oauth_clients.check_redirect_uri(CHATGPT_PREFIX_REDIRECT)  # must not raise

    def test_loopback_refused_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "OAUTH_ALLOW_LOOPBACK_REDIRECTS", False)
        with pytest.raises(oauth_clients.RedirectURIError):
            oauth_clients.check_redirect_uri("http://127.0.0.1:5555/cb")

    @pytest.mark.parametrize("host", ["127.0.0.1", "[::1]", "localhost"])
    def test_loopback_accepted_with_flag(self, monkeypatch, host):
        monkeypatch.setattr(settings, "OAUTH_ALLOW_LOOPBACK_REDIRECTS", True)
        oauth_clients.check_redirect_uri(f"http://{host}:5555/cb")  # must not raise

    def test_normalize_drops_default_port_and_lowercases(self):
        assert (
            oauth_clients.normalize_redirect_uri("HTTPS://Claude.AI:443/api/mcp/auth_callback")
            == "https://claude.ai/api/mcp/auth_callback"
        )


class TestPKCE:
    def test_valid_pair_verifies(self):
        verifier, challenge = _pkce_pair()
        assert oauth_flow.verify_pkce(verifier, challenge)

    def test_wrong_verifier_fails(self):
        _, challenge = _pkce_pair()
        assert not oauth_flow.verify_pkce("x" * 50, challenge)

    @pytest.mark.parametrize("verifier", ["", "short", "x" * 129, "not-base64url-chars-!!!!!!!!!!!!!!!!!!!!!!!"])
    def test_malformed_verifier_never_verifies(self, verifier):
        assert not oauth_flow.verify_pkce(verifier, "a" * 43)

    @pytest.mark.parametrize("challenge", ["", "a" * 42, "a" * 44, "not valid chars here !!!!!!!!!!!!!!!!!!!!!"])
    def test_invalid_challenge_format_rejected(self, challenge):
        assert not oauth_flow.is_valid_code_challenge_format(challenge)

    def test_valid_challenge_format_accepted(self):
        _, challenge = _pkce_pair()
        assert oauth_flow.is_valid_code_challenge_format(challenge)


class TestSafeNext:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, None),
            ("", None),
            ("/dashboard", "/dashboard"),
            ("/oauth/consent/abc123", "/oauth/consent/abc123"),
            ("//evil.com", None),
            ("http://evil.com", None),
            ("javascript:alert(1)", None),
            ("evil.com/path", None),
            ("/" + "x" * 600, None),
        ],
    )
    def test_safe_next(self, value, expected):
        assert auth_magic_link.safe_next(value) == expected


class TestResourceHelpers:
    def test_matches_canonical_ignores_trailing_slash_and_case(self, monkeypatch):
        monkeypatch.setattr(settings, "SITE_BASE_URL", "https://job360.uk")
        assert oauth_flow.resource_matches_canonical("https://JOB360.uk/api/mcp/")
        assert oauth_flow.resource_matches_canonical("https://job360.uk/api/mcp")

    def test_does_not_match_a_different_path(self, monkeypatch):
        monkeypatch.setattr(settings, "SITE_BASE_URL", "https://job360.uk")
        assert not oauth_flow.resource_matches_canonical("https://job360.uk/api/other")


# ═══════════════════════════════════════════════════════════════════════════
# Discovery (R1)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_well_known_metadata_shapes_and_cors(authenticated_async_context):
    async with authenticated_async_context():
        pass
    async with _unauth_client() as client:
        as_resp = await client.get("/.well-known/oauth-authorization-server")
        assert as_resp.status_code == 200
        assert as_resp.headers["access-control-allow-origin"] == "*"
        body = as_resp.json()
        site = settings.SITE_BASE_URL
        assert body["issuer"] == site
        assert body["authorization_endpoint"] == f"{site}/api/oauth/authorize"
        assert body["token_endpoint"] == f"{site}/api/oauth/token"
        assert body["registration_endpoint"] == f"{site}/api/oauth/register"
        assert body["revocation_endpoint"] == f"{site}/api/oauth/revoke"
        assert body["code_challenge_methods_supported"] == ["S256"]
        assert body["token_endpoint_auth_methods_supported"] == ["none"]
        assert body["scopes_supported"] == ["job360"]

        pr_root = await client.get("/.well-known/oauth-protected-resource")
        assert pr_root.status_code == 200
        assert pr_root.headers["access-control-allow-origin"] == "*"
        pr_body = pr_root.json()
        assert pr_body["resource"] == f"{site}/api/mcp"
        assert pr_body["authorization_servers"] == [site]
        assert pr_body["bearer_methods_supported"] == ["header"]

        pr_mcp = await client.get("/.well-known/oauth-protected-resource/api/mcp")
        assert pr_mcp.status_code == 200
        assert pr_mcp.json() == pr_body


# ═══════════════════════════════════════════════════════════════════════════
# Dynamic client registration (R2)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_register_happy_path(authenticated_async_context):
    async with authenticated_async_context():
        pass
    body = await _register_client([CLAUDE_REDIRECT], client_name="Claude")
    assert body["client_id"].startswith("j360c_")
    assert "client_secret" not in body
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == [CLAUDE_REDIRECT]
    assert body["client_name"] == "Claude"
    assert isinstance(body["client_id_issued_at"], int)


@pytest.mark.asyncio
async def test_register_rejects_redirect_outside_allowlist(authenticated_async_context):
    async with authenticated_async_context():
        pass
    async with _unauth_client() as client:
        resp = await client.post("/api/oauth/register", json={"redirect_uris": ["https://evil.example/cb"]})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_redirect_uri"


@pytest.mark.asyncio
async def test_register_rejects_non_none_auth_method(authenticated_async_context):
    async with authenticated_async_context():
        pass
    async with _unauth_client() as client:
        resp = await client.post(
            "/api/oauth/register",
            json={"redirect_uris": [CLAUDE_REDIRECT], "token_endpoint_auth_method": "client_secret_basic"},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_client_metadata"


@pytest.mark.asyncio
async def test_register_global_budget_429(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REGISTER_MAX_PER_HOUR", 1000)
    monkeypatch.setattr(settings, "OAUTH_REGISTER_MAX_PER_HOUR_GLOBAL", 2)
    async with authenticated_async_context():
        pass
    await _register_client([CLAUDE_REDIRECT])
    await _register_client([CLAUDE_REDIRECT])
    async with _unauth_client() as client:
        resp = await client.post("/api/oauth/register", json={"redirect_uris": [CLAUDE_REDIRECT]})
        assert resp.status_code == 429, resp.text


@pytest.mark.asyncio
async def test_ceiling_prune_frees_room(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_MAX_CLIENTS", 2)
    monkeypatch.setattr(settings, "OAUTH_REGISTER_MAX_PER_HOUR", 1000)
    monkeypatch.setattr(settings, "OAUTH_REGISTER_MAX_PER_HOUR_GLOBAL", 1000)
    async with authenticated_async_context():
        pass
    await _register_client([CLAUDE_REDIRECT])  # #1, no grant -> prune-eligible
    await _register_client([CLAUDE_REDIRECT])  # #2, table now AT the ceiling
    third = await _register_client([CLAUDE_REDIRECT])  # must succeed: prune frees room first
    assert third["client_id"].startswith("j360c_")


# ═══════════════════════════════════════════════════════════════════════════
# Authorize (R3)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_authorize_step1_errors_are_json_never_redirect(authenticated_async_context):
    async with authenticated_async_context():
        pass
    registered = await _register_client([CLAUDE_REDIRECT])
    _, challenge = _pkce_pair()
    async with _unauth_client() as client:
        unknown_client = await _authorize(
            client, client_id="j360c_doesnotexist", redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge
        )
        assert unknown_client.status_code == 400, unknown_client.text
        assert "location" not in unknown_client.headers

        bad_redirect = await _authorize(
            client, client_id=registered["client_id"], redirect_uri="https://not-registered.example/cb",
            code_challenge=challenge,
        )
        assert bad_redirect.status_code == 400, bad_redirect.text
        assert "location" not in bad_redirect.headers

        long_state = await _authorize(
            client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge,
            state="x" * 600,
        )
        assert long_state.status_code == 400, long_state.text
        assert "location" not in long_state.headers


@pytest.mark.asyncio
async def test_authorize_missing_pkce_redirects_with_error_and_state(authenticated_async_context):
    async with authenticated_async_context():
        pass
    registered = await _register_client([CLAUDE_REDIRECT])
    async with _unauth_client() as client:
        resp = await _authorize(
            client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=None,
            code_challenge_method=None, state="abc",
        )
        assert resp.status_code == 302, resp.text
        loc = resp.headers["location"]
        assert loc.startswith(CLAUDE_REDIRECT)
        qs = parse_qs(urlsplit(loc).query)
        assert qs["error"][0] == "invalid_request"
        assert qs["state"][0] == "abc"


@pytest.mark.asyncio
async def test_authorize_unsupported_response_type_redirects(authenticated_async_context):
    async with authenticated_async_context():
        pass
    registered = await _register_client([CLAUDE_REDIRECT])
    _, challenge = _pkce_pair()
    async with _unauth_client() as client:
        resp = await _authorize(
            client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge,
            response_type="token", state="s2",
        )
        assert resp.status_code == 302
        qs = parse_qs(urlsplit(resp.headers["location"]).query)
        assert qs["error"][0] == "unsupported_response_type"


@pytest.mark.asyncio
async def test_authorize_invalid_scope_and_invalid_target_redirect(authenticated_async_context):
    async with authenticated_async_context():
        pass
    registered = await _register_client([CLAUDE_REDIRECT])
    _, challenge = _pkce_pair()
    async with _unauth_client() as client:
        bad_scope = await _authorize(
            client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge,
            scope="admin", state="s3",
        )
        assert parse_qs(urlsplit(bad_scope.headers["location"]).query)["error"][0] == "invalid_scope"

        bad_resource = await _authorize(
            client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge,
            resource="https://not-mcp.example/", state="s4",
        )
        assert parse_qs(urlsplit(bad_resource.headers["location"]).query)["error"][0] == "invalid_target"


@pytest.mark.asyncio
async def test_authorize_happy_redirects_to_consent(authenticated_async_context):
    async with authenticated_async_context():
        pass
    registered = await _register_client([CLAUDE_REDIRECT])
    _, challenge = _pkce_pair()
    async with _unauth_client() as client:
        resp = await _authorize(
            client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge,
        )
        assert resp.status_code == 302
        assert "/oauth/consent/" in resp.headers["location"]
        assert resp.headers["cache-control"] == "no-store"


# ═══════════════════════════════════════════════════════════════════════════
# Consent (R4)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_consent_get_returns_full_redirect_uri_and_user_email(authenticated_async_context):
    registered = await _register_client([CLAUDE_REDIRECT], client_name="Test Agent")
    _, challenge = _pkce_pair()
    async with authenticated_async_context() as session_client:
        resp = await _authorize(
            session_client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge,
        )
        rid = _rid_from_location(resp.headers["location"])
        me = await session_client.get("/api/auth/me")
        email = me.json()["email"]

        got = await session_client.get(f"/api/oauth/authorize/{rid}")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["redirect_uri"] == CLAUDE_REDIRECT
        assert body["client_name"] == "Test Agent"
        assert body["user_email"] == email
        assert body["scope"] == "job360"
        assert body["scope_description"]


@pytest.mark.asyncio
async def test_consent_requires_session_not_a_bearer(authenticated_async_context):
    """An OAuth access token resolves a `CurrentUser` fine, but consent is
    gated by `require_session_user`, which refuses anything but
    `auth_via == "session"` with 403 `session_required` (S10) — never a 401,
    which would (wrongly) suggest the bearer itself was bad."""
    result = await _full_code_exchange(authenticated_async_context)
    registered = await _register_client([CLAUDE_REDIRECT])
    _, challenge = _pkce_pair()
    async with _unauth_client() as client:
        resp = await _authorize(client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge)
        rid = _rid_from_location(resp.headers["location"])
    async with _bearer_client(result["access_token"]) as agent:
        got = await agent.get(f"/api/oauth/authorize/{rid}")
        assert got.status_code == 403, got.text
        assert got.json()["detail"] == "session_required"


@pytest.mark.asyncio
async def test_consumed_or_expired_request_is_404(authenticated_async_context):
    registered = await _register_client([CLAUDE_REDIRECT])
    _, challenge = _pkce_pair()
    async with authenticated_async_context() as session_client:
        resp = await _authorize(session_client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge)
        rid = _rid_from_location(resp.headers["location"])
        decided = await session_client.post(f"/api/oauth/authorize/{rid}/decision", json={"approve": True})
        assert decided.status_code == 200

        again = await session_client.get(f"/api/oauth/authorize/{rid}")
        assert again.status_code == 404

        redecide = await session_client.post(f"/api/oauth/authorize/{rid}/decision", json={"approve": True})
        assert redecide.status_code == 404


@pytest.mark.asyncio
async def test_deny_redirects_with_access_denied(authenticated_async_context):
    registered = await _register_client([CLAUDE_REDIRECT])
    _, challenge = _pkce_pair()
    async with authenticated_async_context() as session_client:
        resp = await _authorize(
            session_client, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge,
            state="deny-state",
        )
        rid = _rid_from_location(resp.headers["location"])
        decided = await session_client.post(f"/api/oauth/authorize/{rid}/decision", json={"approve": False})
        assert decided.status_code == 200, decided.text
    qs = parse_qs(urlsplit(decided.json()["redirect_to"]).query)
    assert qs["error"][0] == "access_denied"
    assert qs["state"][0] == "deny-state"


# ═══════════════════════════════════════════════════════════════════════════
# Token exchange (R5)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_token_exchange_happy_path(authenticated_async_context):
    result = await _full_code_exchange(authenticated_async_context)
    assert result["access_token"].startswith("j360a_")
    assert result["refresh_token"].startswith("j360r_")
    assert result["token_type"] == "Bearer"
    assert result["scope"] == "job360"
    assert result["expires_in"] == settings.OAUTH_ACCESS_TOKEN_TTL_SECONDS


@pytest.mark.asyncio
async def test_token_json_body_is_400_not_422(authenticated_async_context):
    async with authenticated_async_context():
        pass
    async with _unauth_client() as client:
        resp = await client.post("/api/oauth/token", json={"grant_type": "authorization_code"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_request"


@pytest.mark.asyncio
async def test_wrong_verifier_is_invalid_grant(authenticated_async_context):
    registered = await _register_client([CLAUDE_REDIRECT])
    _, challenge = _pkce_pair()
    code = await _authorize_and_approve(
        authenticated_async_context, client_id=registered["client_id"], redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge
    )
    async with _unauth_client() as client:
        resp = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code", "code": code, "client_id": registered["client_id"],
                "redirect_uri": CLAUDE_REDIRECT, "code_verifier": "x" * 50,
            },
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_code_reuse_inside_grace_is_invalid_grant_and_grant_stays_alive(authenticated_async_context):
    result = await _full_code_exchange(authenticated_async_context)
    registered, verifier, code, redirect_uri = result["registered"], result["verifier"], result["code"], result["redirect_uri"]

    async with _unauth_client() as client:
        replay = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code", "code": code, "client_id": registered["client_id"],
                "redirect_uri": redirect_uri, "code_verifier": verifier,
            },
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

    # The grant from the FIRST (successful) exchange is still alive.
    async with _bearer_client(result["access_token"]) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_code_reuse_after_grace_revokes_grant_and_kills_prior_access_token(authenticated_async_context, oauth_clock):
    result = await _full_code_exchange(authenticated_async_context)
    registered, verifier, code, redirect_uri = result["registered"], result["verifier"], result["code"], result["redirect_uri"]

    oauth_clock.advance(settings.OAUTH_REUSE_GRACE_SECONDS + 5)
    async with _unauth_client() as client:
        replay = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code", "code": code, "client_id": registered["client_id"],
                "redirect_uri": redirect_uri, "code_verifier": verifier,
            },
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

    async with _bearer_client(result["access_token"]) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotation_issues_a_new_pair(authenticated_async_context):
    result = await _full_code_exchange(authenticated_async_context)
    async with _unauth_client() as client:
        refreshed = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "refresh_token", "refresh_token": result["refresh_token"],
                "client_id": result["registered"]["client_id"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        new_body = refreshed.json()
        assert new_body["access_token"] != result["access_token"]
        assert new_body["refresh_token"] != result["refresh_token"]

    # Old access token is now dead (R5: old access tokens of the grant are revoked).
    async with _bearer_client(result["access_token"]) as old_agent:
        assert (await old_agent.get("/api/auth/me")).status_code == 401
    # New access token works.
    async with _bearer_client(new_body["access_token"]) as new_agent:
        assert (await new_agent.get("/api/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_rotated_refresh_reuse_after_grace_revokes_grant(authenticated_async_context, oauth_clock):
    result = await _full_code_exchange(authenticated_async_context)
    async with _unauth_client() as client:
        refreshed = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "refresh_token", "refresh_token": result["refresh_token"],
                "client_id": result["registered"]["client_id"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        new_access = refreshed.json()["access_token"]

        oauth_clock.advance(settings.OAUTH_REUSE_GRACE_SECONDS + 5)
        replay = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "refresh_token", "refresh_token": result["refresh_token"],
                "client_id": result["registered"]["client_id"],
            },
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

    async with _bearer_client(new_access) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_refresh_expiry_is_absolute_not_sliding(authenticated_async_context, oauth_clock, monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REFRESH_TOKEN_TTL_SECONDS", 100)
    result = await _full_code_exchange(authenticated_async_context)

    oauth_clock.advance(101)
    async with _unauth_client() as client:
        refreshed = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "refresh_token", "refresh_token": result["refresh_token"],
                "client_id": result["registered"]["client_id"],
            },
        )
        assert refreshed.status_code == 400
        assert refreshed.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_token_endpoint_ignores_foreign_origin(authenticated_async_context):
    result = await _full_code_exchange(authenticated_async_context, token_headers={"Origin": "https://evil.example"})
    assert result["access_token"].startswith("j360a_")


# ═══════════════════════════════════════════════════════════════════════════
# Bearer resolution + MCP (R6, R7)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_access_token_works_on_me_and_mcp_tools_list(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    result = await _full_code_exchange(authenticated_async_context)
    access = result["access_token"]

    async with _bearer_client(access) as agent:
        me = await agent.get("/api/auth/me")
        assert me.status_code == 200, me.text

    async with mcp_runtime():
        async with _mcp_client(access) as mcp:
            listed = await mcp.list_tools()
            assert {t.name for t in listed.tools} >= {"get_profile", "bring_job"}


@pytest.mark.asyncio
async def test_wrong_audience_401_at_mcp_only(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    result = await _full_code_exchange(authenticated_async_context)
    access = result["access_token"]
    _set_token_field("audience", "https://other.example/mcp", token=access)

    async with _bearer_client(access) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 200

    async with mcp_runtime():
        async with _bearer_client(access) as agent:
            resp = await agent.post(
                "/api/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert resp.status_code == 401, resp.text
            challenge = resp.headers.get("www-authenticate", "")
            assert "resource_metadata" in challenge
            assert 'scope="job360"' in challenge


@pytest.mark.asyncio
async def test_mcp_401_carries_resource_metadata_and_scope_on_missing_bearer(authenticated_async_context):
    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context():
        pass
    async with mcp_runtime():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            resp = await anon.post(
                "/api/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert resp.status_code == 401
            challenge = resp.headers.get("www-authenticate", "")
            assert challenge.startswith("Bearer")
            assert "resource_metadata=" in challenge
            assert 'scope="job360"' in challenge


@pytest.mark.asyncio
async def test_expired_access_token_401_without_counting_toward_throttle(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_BEARER_FAIL_MAX_PER_MIN", 1)
    result = await _full_code_exchange(authenticated_async_context)
    access = result["access_token"]
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _set_token_field("expires_at", past, token=access)

    async with _bearer_client(access) as expired:
        assert (await expired.get("/api/auth/me")).status_code == 401
        # A second expired attempt is STILL a plain 401 (limit=1 would have
        # tripped 429 on the second call if expiry counted as a failure).
        assert (await expired.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_unknown_oauth_bearer_has_its_own_throttle_key(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_BEARER_FAIL_MAX_PER_MIN", 2)
    async with authenticated_async_context():
        pass
    junk = "j360a_" + "x" * 43
    async with _bearer_client(junk) as attacker:
        assert (await attacker.get("/api/auth/me")).status_code == 401
        assert (await attacker.get("/api/auth/me")).status_code == 401
        assert (await attacker.get("/api/auth/me")).status_code == 429

    # A personal-token guess from the SAME client IP is unaffected — different bucket.
    async with _bearer_client("j360_" + "y" * 43) as other:
        assert (await other.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_bearer_is_401(authenticated_async_context):
    result = await _full_code_exchange(authenticated_async_context)
    async with _bearer_client(result["refresh_token"]) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_personal_token_still_works_alongside_oauth(authenticated_async_context):
    await _full_code_exchange(authenticated_async_context)
    async with authenticated_async_context() as client:
        minted = await client.post("/api/tokens", json={"name": "cli"})
        assert minted.status_code == 201, minted.text
        personal_token = minted.json()["token"]
    async with _bearer_client(personal_token) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_oauth_access_token_cannot_manage_credentials(authenticated_async_context):
    result = await _full_code_exchange(authenticated_async_context)
    async with _bearer_client(result["access_token"]) as agent:
        assert (await agent.get("/api/tokens")).status_code == 403
        assert (await agent.post("/api/tokens", json={"name": "x"})).status_code == 403
        assert (await agent.get("/api/oauth/grants")).status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Revocation + Connected apps (R8)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delete_grant_revokes_immediately(authenticated_async_context):
    result = await _full_code_exchange(authenticated_async_context)
    access = result["access_token"]

    async with authenticated_async_context() as session_client:
        grants = (await session_client.get("/api/oauth/grants")).json()["grants"]
        assert grants, "expected one active grant"
        grant_id = grants[0]["id"]
        assert grants[0]["client_name"] == result["registered"]["client_name"]
        deleted = await session_client.delete(f"/api/oauth/grants/{grant_id}")
        assert deleted.status_code == 204, deleted.text
        assert (await session_client.delete(f"/api/oauth/grants/{grant_id}")).status_code == 404

    async with _bearer_client(access) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_revoke_access_kills_refresh_too_and_always_200(authenticated_async_context):
    result = await _full_code_exchange(authenticated_async_context)
    access, refresh = result["access_token"], result["refresh_token"]
    client_id = result["registered"]["client_id"]

    async with _unauth_client() as client:
        resp = await client.post("/api/oauth/revoke", data={"token": access, "client_id": client_id})
        assert resp.status_code == 200
        assert resp.content in (b"", b"null") or resp.content == b""

        # Unknown token: always 200 too (never reveals existence).
        unknown = await client.post("/api/oauth/revoke", data={"token": "j360a_" + "z" * 43})
        assert unknown.status_code == 200

        refreshed = await client.post(
            "/api/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id},
        )
        assert refreshed.status_code == 400
        assert refreshed.json()["error"] == "invalid_grant"

    async with _bearer_client(access) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Migration 0036 (R10 §Design)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_migration_0036_up_down_up(migrated_db_path):
    """`migrated_db_path` already ran `init_db()` + every migration — this
    only exercises down()/up() on it, matching the runner's real "reverse the
    LAST migration" contract.

    ``down()`` only ever reverses the NEWEST applied migration, so everything
    above 0036 has to come off first. Which migrations those are is READ FROM
    THE DIRECTORY, never named: a test that hardcodes "the newest migration is
    0037" turns main red the moment 0038 lands, which is exactly what happened
    (memory: tests must not encode the merge queue). The names below are the
    ones this test is actually ABOUT — 0036 itself and the tables it creates.
    """
    from migrations import runner
    from src.repositories import pg as _pg

    db_path = migrated_db_path
    all_stems = runner._discover_pairs(runner.MIGRATIONS_DIR)
    newer = [s for s in all_stems if s > "0036_oauth"]
    for expected in reversed(newer):
        assert await runner.down(db_path) == expected

    oauth_tables = (
        "oauth_clients", "oauth_authorization_requests", "oauth_grants",
        "oauth_authorization_codes", "oauth_tokens",
    )

    async def _has_table(name: str) -> bool:
        async with _pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)
            )
            return (await cur.fetchone()) is not None

    for table in oauth_tables:
        assert await _has_table(table), f"{table} missing after the fixture's up()"

    reverted = await runner.down(db_path)
    assert reverted == "0036_oauth"
    for table in oauth_tables:
        assert not await _has_table(table), f"{table} still present after down()"

    # `up()` with no target applies EVERY pending migration — everything
    # reverted above comes back along with 0036, in order.
    reapplied = await runner.up(db_path)
    assert reapplied == ["0036_oauth", *newer]
    for table in oauth_tables:
        assert await _has_table(table), f"{table} missing after re-up()"
