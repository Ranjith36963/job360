"""Personal API tokens — mint / list / revoke (spec 2026-09-03-mcp-server R1, R3).

Session-only on purpose: every route here uses ``require_session_user``, so a
token can never make another token. The secret appears in exactly one response
(the 201 from POST) and nowhere else — not the list, not the logs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from src.api.auth_deps import CurrentUser, require_session_user
from src.core import settings
from src.core.settings import DB_PATH
from src.services.auth import api_tokens

router = APIRouter(prefix="/tokens", tags=["tokens"])


class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class TokenSummary(BaseModel):
    id: int
    name: str
    prefix: str
    created_at: str
    last_used_at: str | None


class TokenCreated(TokenSummary):
    token: str  # shown once; only its hash is stored


class TokenListResponse(BaseModel):
    tokens: list[TokenSummary]


@router.post("", response_model=TokenCreated, status_code=201)
async def create_token(
    body: CreateTokenRequest,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — FastAPI DI idiom
) -> TokenCreated:
    cap = settings.API_TOKENS_PER_USER
    if cap > 0 and await api_tokens.count_active(str(DB_PATH), user.id) >= cap:
        raise HTTPException(
            status_code=409,
            detail=f"You already have {cap} active tokens. Revoke one to create another.",
        )
    made = await api_tokens.mint(str(DB_PATH), user_id=user.id, name=body.name)
    return TokenCreated(**made)


@router.get("", response_model=TokenListResponse)
async def list_tokens(
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> TokenListResponse:
    rows = await api_tokens.list_active(str(DB_PATH), user.id)
    return TokenListResponse(tokens=[TokenSummary(**r) for r in rows])


@router.delete("/{token_id}", status_code=204)
async def revoke_token(
    token_id: int,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> Response:
    if not await api_tokens.revoke(str(DB_PATH), user_id=user.id, token_id=token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return Response(status_code=204)
