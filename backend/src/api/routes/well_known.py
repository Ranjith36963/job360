"""OAuth discovery documents — RFC 8414 (authorization server metadata) and
RFC 9728 (protected-resource metadata).

Contract (docs/plans/2026-09-03-oauth-mcp/spec.md R1): three public, GET-only,
unauthenticated, cacheable JSON documents. Mounted at the site ROOT (no
`/api` prefix — `/.well-known/...` must live at the root for a client to find
it), unlike every other router in `main.py`.

Deliberately separate from `routes/oauth.py`: those are all `/api/oauth/*`
and cookie/CSRF-relevant; these three are the opposite (no cookie, `*` CORS,
public forever).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Response

from src.core import settings
from src.services.auth.oauth_flow import SUPPORTED_SCOPE, canonical_resource

router = APIRouter(tags=["well-known"])

_CACHE_HEADERS = {"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"}

@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata() -> Response:
    site = settings.SITE_BASE_URL
    body = {
        "issuer": site,
        "authorization_endpoint": f"{site}/api/oauth/authorize",
        "token_endpoint": f"{site}/api/oauth/token",
        "registration_endpoint": f"{site}/api/oauth/register",
        "revocation_endpoint": f"{site}/api/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [SUPPORTED_SCOPE],
        "service_documentation": f"{site}/settings/connect",
    }
    return _json(body)


def _protected_resource_body() -> dict[str, Any]:
    site = settings.SITE_BASE_URL
    return {
        "resource": canonical_resource(),
        "authorization_servers": [site],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [SUPPORTED_SCOPE],
        "resource_documentation": f"{site}/settings/connect",
    }


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata_root() -> Response:
    return _json(_protected_resource_body())


@router.get("/.well-known/oauth-protected-resource/api/mcp")
async def protected_resource_metadata_mcp() -> Response:
    return _json(_protected_resource_body())


def _json(body: dict[str, Any]) -> Response:
    return Response(content=json.dumps(body), media_type="application/json", headers=_CACHE_HEADERS)
