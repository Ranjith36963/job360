"""OAuth routes never echo exception text and never log secrets or raw
caller-supplied text (CodeQL alerts #229-#233)."""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.auth import oauth_clients

CLAUDE_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _unauth_client() -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_log_safe_strips_line_breaks_and_caps_length() -> None:
    assert oauth_clients.log_safe("a\r\nb\nc") == "a  b c"
    assert len(oauth_clients.log_safe("x" * 1000)) == 128
    assert oauth_clients.log_safe(42) == "42"


def test_bad_allowlist_entry_is_logged_without_its_text(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "OAUTH_REDIRECT_ALLOWLIST", "https://user:hunter2@evil.example")
    with caplog.at_level(logging.WARNING, logger="job360.oauth.clients"):
        assert oauth_clients._parse_allowlist_entries() == []
    assert "ignoring malformed" in caplog.text
    assert "hunter2" not in caplog.text
    assert "evil.example" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc_type", "expected_code"),
    [
        (oauth_clients.RedirectURIError, "invalid_redirect_uri"),
        (oauth_clients.InvalidClientMetadataError, "invalid_client_metadata"),
    ],
)
async def test_register_error_never_echoes_exception_text(
    authenticated_async_context, monkeypatch, exc_type: type, expected_code: str
) -> None:
    async with authenticated_async_context():
        pass

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise exc_type("INTERNAL-DETAIL traceback line 42")

    monkeypatch.setattr(oauth_clients, "register", _boom)
    async with _unauth_client() as client:
        resp = await client.post("/api/oauth/register", json={"redirect_uris": [CLAUDE_REDIRECT]})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == expected_code
    assert "INTERNAL-DETAIL" not in resp.text


@pytest.mark.asyncio
async def test_token_refusal_audit_has_no_raw_line_breaks(authenticated_async_context, monkeypatch) -> None:
    async with authenticated_async_context():
        pass
    records: list[dict[str, Any]] = []

    class _FakeAudit:
        def info(self, _msg: str, *, extra: dict[str, Any]) -> None:
            records.append(extra)

    import src.api.routes.oauth as oauth_routes

    monkeypatch.setattr(oauth_routes, "get_audit_logger", lambda: _FakeAudit())
    async with _unauth_client() as client:
        resp = await client.post(
            "/api/oauth/token",
            data={"grant_type": "authorization_code", "client_id": "evil\r\nFAKE event=admin_login"},
        )
    assert resp.status_code == 401, resp.text
    refused = [r for r in records if r.get("event") == "oauth_token_refused"]
    assert refused, records
    for r in refused:
        assert "\r" not in r["client_id"] and "\n" not in r["client_id"]
