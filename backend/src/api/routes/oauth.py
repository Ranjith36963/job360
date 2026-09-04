"""OAuth 2.1 authorization-server routes for MCP clients.

Contract (docs/plans/2026-09-03-oauth-mcp/spec.md R2-R5, R8, S6):
  * dynamic client registration is unauthenticated, so it is rate-limited and
    the client table is bounded (`oauth_clients.register`);
  * `/authorize` validates in a strict NORMATIVE ORDER — client + redirect_uri
    + state length first (400 JSON, never a redirect), only then everything
    else (302 redirect carrying `error`/`error_description`/`state`);
  * `/authorize/{rid}` (+ `/decision`) are session-only — a bearer token can
    never see or approve someone else's consent screen;
  * `/token` reads `Content-Type` itself (never FastAPI's `Form(...)`, which
    would 422 a non-form body instead of the RFC-shaped 400 this spec wants)
    and resolves the grant BEFORE consulting the failure throttle, the same
    order `auth_deps._current_user_from_bearer` uses for personal tokens;
  * `/revoke` always answers 200, even for a token it can't resolve (R8).
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.datastructures import FormData

from src.api.auth_deps import CurrentUser, _client_ip, require_session_user
from src.core import settings
from src.core.settings import DB_PATH
from src.services.auth import oauth_clients, oauth_flow
from src.services.auth import rate_limit as auth_rate_limit
from src.utils.logger import get_audit_logger

logger = logging.getLogger("job360.api.oauth")

router = APIRouter(prefix="/oauth", tags=["oauth"])

_NO_STORE = {"Cache-Control": "no-store"}
_CORS_NO_STORE = {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"}
_MAX_REGISTER_BODY_BYTES = 16 * 1024
_SCOPE_DESCRIPTIONS = {
    oauth_flow.SUPPORTED_SCOPE: (
        "read your profile, bring jobs, tailor documents and record applications"
    ),
}


def _error_json(
    status_code: int, error: str, description: str, *, headers: Optional[dict[str, str]] = None
) -> JSONResponse:
    body = {"error": error, "error_description": description}
    return JSONResponse(body, status_code=status_code, headers=headers or _NO_STORE)


# ── R2: Dynamic client registration ──────────────────────────────────────────


@router.post("/register", status_code=201)
async def register_client(request: Request) -> Response:
    ip = _client_ip(request)
    per_ip = settings.OAUTH_REGISTER_MAX_PER_HOUR
    if per_ip > 0 and not auth_rate_limit.check_and_record(
        f"oauth_register:{ip}", max_in_window=per_ip, window_seconds=3600
    ):
        return _error_json(429, "invalid_request", "too many registrations from this address", headers=_CORS_NO_STORE)
    global_cap = settings.OAUTH_REGISTER_MAX_PER_HOUR_GLOBAL
    if global_cap > 0 and not auth_rate_limit.check_and_record(
        "oauth_register:*", max_in_window=global_cap, window_seconds=3600
    ):
        return _error_json(429, "invalid_request", "registration is temporarily busy", headers=_CORS_NO_STORE)

    # Refuse on the declared length BEFORE reading: a lying/absent header still
    # hits the post-read check below, but an honest oversize body is never buffered.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_REGISTER_BODY_BYTES:
        return _error_json(400, "invalid_client_metadata", "request body too large", headers=_CORS_NO_STORE)
    raw_body = await request.body()
    if len(raw_body) > _MAX_REGISTER_BODY_BYTES:
        return _error_json(400, "invalid_client_metadata", "request body too large", headers=_CORS_NO_STORE)
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return _error_json(400, "invalid_client_metadata", "malformed JSON body", headers=_CORS_NO_STORE)
    if not isinstance(payload, dict):
        return _error_json(400, "invalid_client_metadata", "body must be a JSON object", headers=_CORS_NO_STORE)

    raw_redirect_uris = payload.get("redirect_uris")
    if not isinstance(raw_redirect_uris, list) or not raw_redirect_uris:
        return _error_json(
            400, "invalid_client_metadata", "redirect_uris must be a non-empty list of strings", headers=_CORS_NO_STORE
        )
    redirect_uris: list[str] = []
    for item in raw_redirect_uris:
        if not isinstance(item, str):
            return _error_json(
                400, "invalid_client_metadata", "redirect_uris must be a list of strings", headers=_CORS_NO_STORE
            )
        redirect_uris.append(item)

    try:
        registered = await oauth_clients.register(
            str(DB_PATH),
            redirect_uris=redirect_uris,
            client_name=payload.get("client_name"),
            token_endpoint_auth_method=payload.get("token_endpoint_auth_method"),
            grant_types=payload.get("grant_types"),
            response_types=payload.get("response_types"),
        )
    except oauth_clients.RedirectURIError as exc:
        return _error_json(400, str(exc), "one or more redirect_uris failed validation", headers=_CORS_NO_STORE)
    except oauth_clients.InvalidClientMetadataError as exc:
        return _error_json(400, str(exc), "client metadata is invalid", headers=_CORS_NO_STORE)
    except oauth_clients.ClientCapacityError:
        return _error_json(503, "temporarily_unavailable", "registration is temporarily full", headers=_CORS_NO_STORE)

    issued_at = int(datetime.fromisoformat(registered.created_at).timestamp())
    return JSONResponse(
        {
            "client_id": registered.id,
            "client_id_issued_at": issued_at,
            "client_name": registered.client_name,
            "redirect_uris": registered.redirect_uris,
            "token_endpoint_auth_method": registered.token_endpoint_auth_method,
            "grant_types": registered.grant_types,
            "response_types": registered.response_types,
        },
        status_code=201,
        headers=_CORS_NO_STORE,
    )


# ── R3: Authorize ─────────────────────────────────────────────────────────────


def _step2_redirect(redirect_uri: str, error: str, description: str, state: Optional[str]) -> RedirectResponse:
    params = {"error": error, "error_description": description, "state": state}
    location = oauth_flow.build_redirect_url(redirect_uri, params)
    return RedirectResponse(location, status_code=302, headers=_NO_STORE)


@router.get("/authorize")
async def authorize(request: Request) -> Response:
    ip = _client_ip(request)
    limit = settings.OAUTH_AUTHORIZE_MAX_PER_MIN
    if limit > 0 and not auth_rate_limit.check_and_record(
        f"oauth_authorize:{ip}", max_in_window=limit, window_seconds=60
    ):
        return _error_json(429, "invalid_request", "too many authorize requests")

    q = request.query_params
    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")
    state = q.get("state")
    response_type = q.get("response_type", "")
    code_challenge = q.get("code_challenge", "")
    code_challenge_method = q.get("code_challenge_method", "")
    scope = q.get("scope") or oauth_flow.SUPPORTED_SCOPE
    resource = q.get("resource") or oauth_flow.canonical_resource()

    # Step 1 — client + exact redirect_uri + state length. 400 JSON, NEVER a
    # redirect (RFC 6749 4.1.2.1): the client hasn't been verified yet, so a
    # redirect here could be sent anywhere.
    if state is not None and len(state) > 512:
        return _error_json(400, "invalid_request", "state exceeds 512 characters")
    client = await oauth_clients.load(str(DB_PATH), client_id)
    if client is None:
        return _error_json(400, "invalid_request", "unknown client_id")
    normalized_redirect = oauth_clients.normalize_redirect_uri(redirect_uri) if redirect_uri else ""
    if not redirect_uri or normalized_redirect not in client.redirect_uris:
        return _error_json(400, "invalid_request", "redirect_uri is not registered for this client")

    # Step 2 — everything else. Client + redirect are now trusted, so errors
    # go back to the app as a redirect, with `state` echoed.
    if response_type != "code":
        return _step2_redirect(
            normalized_redirect, "unsupported_response_type", "only response_type=code is supported", state
        )
    if not oauth_flow.is_valid_code_challenge_format(code_challenge):
        return _step2_redirect(normalized_redirect, "invalid_request", "code_challenge is missing or malformed", state)
    if code_challenge_method != "S256":
        return _step2_redirect(normalized_redirect, "invalid_request", "code_challenge_method must be S256", state)
    if scope != oauth_flow.SUPPORTED_SCOPE:
        return _step2_redirect(normalized_redirect, "invalid_scope", f"unsupported scope: {scope}", state)
    if not oauth_flow.resource_matches_canonical(resource):
        return _step2_redirect(normalized_redirect, "invalid_target", "resource must be the Job360 MCP URL", state)

    rid = await oauth_flow.create_authorization_request(
        str(DB_PATH),
        client_id=client_id,
        redirect_uri=normalized_redirect,
        scope=scope,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        resource=oauth_flow.canonical_resource(),
    )
    await oauth_clients.touch_last_used(str(DB_PATH), client_id)
    location = f"{settings.SITE_BASE_URL}/oauth/consent/{rid}"
    return RedirectResponse(location, status_code=302, headers=_NO_STORE)


# ── R4: Consent ───────────────────────────────────────────────────────────────


class ConsentDecisionRequest(BaseModel):
    approve: bool


# The three routes the BROWSER consumes carry `response_model` so the generated
# `api-types.ts` (and its drift gate) sees a real shape. The RFC-shaped
# endpoints (/register, /authorize, /token, /revoke) stay bare `Response` —
# their bodies vary by error branch.
class ConsentRequestResponse(BaseModel):
    client_name: str
    redirect_uri: str
    scope: str
    scope_description: str
    user_email: str
    expires_at: str


class ConsentDecisionResponse(BaseModel):
    redirect_to: str


class OAuthGrantOut(BaseModel):
    id: int
    client_name: str
    redirect_uri: str
    scope: str
    created_at: str
    last_used_at: Optional[str]  # always present; null until first use


class GrantListResponse(BaseModel):
    grants: list[OAuthGrantOut]


@router.get("/authorize/{rid}", response_model=ConsentRequestResponse)
async def get_consent_request(
    rid: str,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — FastAPI DI idiom
) -> Response:
    req = await oauth_flow.get_pending_authorization_request(str(DB_PATH), rid)
    if req is None:
        raise HTTPException(status_code=404, detail="request not found, already decided, or expired")
    return JSONResponse(
        {
            "client_name": req.client_name,
            "redirect_uri": req.redirect_uri,
            "scope": req.scope,
            "scope_description": _SCOPE_DESCRIPTIONS.get(req.scope, req.scope),
            "user_email": user.email,
            "expires_at": req.expires_at,
        },
        headers=_NO_STORE,
    )


@router.post("/authorize/{rid}/decision", response_model=ConsentDecisionResponse)
async def decide_consent(
    rid: str,
    body: ConsentDecisionRequest,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — FastAPI DI idiom
) -> Response:
    try:
        redirect_to = await oauth_flow.decide_authorization_request(
            str(DB_PATH), rid=rid, user_id=user.id, approve=body.approve
        )
    except oauth_flow.AuthorizationRequestGoneError:
        raise HTTPException(status_code=404, detail="request not found, already decided, or expired") from None
    return JSONResponse({"redirect_to": redirect_to}, headers=_NO_STORE)


# ── R5: Token ─────────────────────────────────────────────────────────────────


def _token_fail_keys(ip: str, client_id: str) -> tuple[str, Optional[str]]:
    return f"oauth_token_fail:{ip}", (f"oauth_token_fail:{ip}:{client_id}" if client_id else None)


def _form_str(form: FormData, key: str) -> Optional[str]:
    value = form.get(key)
    return str(value) if value is not None else None


async def _dispatch_grant(*, grant_type: str, client_id: str, form: FormData) -> dict[str, Any]:
    if not client_id:
        raise oauth_flow.TokenError(400, "invalid_request", "client_id is required")
    client = await oauth_clients.load(str(DB_PATH), client_id)
    if client is None:
        raise oauth_flow.TokenError(401, "invalid_client", "unknown client_id")

    if grant_type == "authorization_code":
        return await oauth_flow.exchange_authorization_code(
            str(DB_PATH),
            code=_form_str(form, "code") or "",
            client_id=client_id,
            redirect_uri=_form_str(form, "redirect_uri") or "",
            code_verifier=_form_str(form, "code_verifier") or "",
            resource=_form_str(form, "resource"),
        )
    if grant_type == "refresh_token":
        return await oauth_flow.refresh_access_token(
            str(DB_PATH), refresh_token=_form_str(form, "refresh_token") or "", client_id=client_id,
        )
    raise oauth_flow.TokenError(400, "unsupported_grant_type", "grant_type must be authorization_code or refresh_token")


@router.post("/token")
async def token(request: Request, background_tasks: BackgroundTasks) -> Response:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        return _error_json(
            400, "invalid_request", "Content-Type must be application/x-www-form-urlencoded", headers=_CORS_NO_STORE
        )

    form = await request.form()
    grant_type = _form_str(form, "grant_type") or ""
    client_id = _form_str(form, "client_id") or ""
    ip = _client_ip(request)

    try:
        result = await _dispatch_grant(grant_type=grant_type, client_id=client_id, form=form)
    except oauth_flow.TokenError as exc:
        limit = settings.OAUTH_TOKEN_FAIL_MAX_PER_MIN
        if limit > 0:
            fail_key, fail_key_client = _token_fail_keys(ip, client_id)
            locked = auth_rate_limit.is_locked(fail_key, max_failures=limit, window_seconds=60) or (
                fail_key_client is not None
                and auth_rate_limit.is_locked(fail_key_client, max_failures=limit, window_seconds=60)
            )
            if locked:
                get_audit_logger().info(
                    "oauth_token_refused",
                    extra={"event": "oauth_token_refused", "client_id": client_id, "reason": "rate_limited"},
                )
                return _error_json(429, "invalid_request", "too many failed token requests", headers=_CORS_NO_STORE)
            auth_rate_limit.record_failure(fail_key)
            if fail_key_client is not None:
                auth_rate_limit.record_failure(fail_key_client)
        get_audit_logger().info(
            "oauth_token_refused",
            extra={"event": "oauth_token_refused", "client_id": client_id, "reason": exc.error},
        )
        return _error_json(exc.status_code, exc.error, exc.description, headers=_CORS_NO_STORE)

    if settings.OAUTH_PRUNE_SAMPLE > 0 and secrets.randbelow(settings.OAUTH_PRUNE_SAMPLE) == 0:
        background_tasks.add_task(oauth_clients.prune, str(DB_PATH))

    return JSONResponse(
        result,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache", "Access-Control-Allow-Origin": "*"},
    )


# ── R8: Revocation + web "Connected apps" ────────────────────────────────────


@router.post("/revoke")
async def revoke(request: Request) -> Response:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        token_value = _form_str(form, "token") or ""
        client_id = _form_str(form, "client_id")
        await oauth_flow.revoke_token(str(DB_PATH), token=token_value, client_id=client_id)
    # RFC 7009: always 200, even for an unknown/malformed request — the
    # endpoint never reveals whether a token existed.
    return Response(status_code=200, headers=_CORS_NO_STORE)


@router.get("/grants", response_model=GrantListResponse)
async def list_grants(
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — FastAPI DI idiom
) -> Response:
    grants = await oauth_flow.list_grants_for_user(str(DB_PATH), user.id)
    return JSONResponse({"grants": grants}, headers=_NO_STORE)


@router.delete("/grants/{grant_id}", status_code=204)
async def revoke_grant(
    grant_id: int,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — FastAPI DI idiom
) -> Response:
    if not await oauth_flow.revoke_grant_for_user(str(DB_PATH), user_id=user.id, grant_id=grant_id):
        raise HTTPException(status_code=404, detail="grant not found")
    return Response(status_code=204)
