"""Per-user channel configuration endpoints.

Scoped to the authenticated user — every query filters by
``user_id = current_user.id``. Cross-tenant reads are impossible via this
router because the user id never appears in the URL; it is always the
cookie-resolved user.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, require_user
from src.core import settings as _settings
from src.core.settings import DB_PATH
from src.services.channels import crypto, dispatcher

router = APIRouter(prefix="/settings/channels", tags=["channels"])

_VALID_TYPES = {"email", "slack", "discord", "telegram", "webhook"}


class ChannelIn(BaseModel):
    channel_type: str = Field(pattern="^(email|slack|discord|telegram|webhook)$")
    display_name: str = Field(min_length=1, max_length=120)
    credential: str = Field(min_length=1)  # the Apprise URL


class ChannelOut(BaseModel):
    id: int
    channel_type: str
    display_name: str
    enabled: bool
    connection_status: str = "connected"
    target_label: Optional[str] = None


class TestSendResult(BaseModel):
    ok: bool
    error: Optional[str] = None


class ProvidersOut(BaseModel):
    """Which OAuth connect buttons the frontend should display."""

    slack: bool
    discord: bool
    telegram: bool


@router.get("", response_model=list[ChannelOut])
async def list_channels(user: CurrentUser = Depends(require_user)) -> list[ChannelOut]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, channel_type, display_name, enabled, "
            "connection_status, target_label FROM user_channels "
            "WHERE user_id = ? ORDER BY id",
            (user.id,),
        )
        rows = await cur.fetchall()
    return [
        ChannelOut(
            id=r["id"],
            channel_type=r["channel_type"],
            display_name=r["display_name"],
            enabled=bool(r["enabled"]),
            connection_status=r["connection_status"] or "connected",
            target_label=r["target_label"],
        )
        for r in rows
    ]


@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelIn, user: CurrentUser = Depends(require_user)
) -> ChannelOut:
    ct = crypto.encrypt(body.credential)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cur = await db.execute(
            """
            INSERT INTO user_channels(user_id, channel_type, display_name,
                                      credential_encrypted, enabled)
            VALUES(?, ?, ?, ?, 1)
            """,
            (user.id, body.channel_type, body.display_name, ct),
        )
        await db.commit()
        channel_id = cur.lastrowid
    return ChannelOut(
        id=int(channel_id or 0),
        channel_type=body.channel_type,
        display_name=body.display_name,
        enabled=True,
    )


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: int, user: CurrentUser = Depends(require_user)
) -> None:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cur = await db.execute(
            "DELETE FROM user_channels WHERE id = ? AND user_id = ?",
            (channel_id, user.id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="channel not found",
            )


@router.post("/{channel_id}/test", response_model=TestSendResult)
async def test_send_channel(
    channel_id: int, user: CurrentUser = Depends(require_user)
) -> TestSendResult:
    # Two-layer ownership check: HTTP SELECT here AND dispatcher filters
    # internally on user_id. Either layer rejects a cross-user attempt.
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id FROM user_channels WHERE id = ? AND user_id = ?",
            (channel_id, user.id),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="channel not found",
            )
        result = await dispatcher.test_send(db, channel_id, user_id=user.id)
    return TestSendResult(
        ok=result.ok,
        error=result.error or None,
    )


@router.get("/providers", response_model=ProvidersOut)
async def get_providers(user: CurrentUser = Depends(require_user)) -> ProvidersOut:
    """Return which OAuth Connect buttons are configured on this deployment."""
    return ProvidersOut(
        slack=_slack_oauth_enabled(),
        discord=_discord_oauth_enabled(),
        telegram=False,  # Telegram OAuth slice not yet implemented.
    )


# ---------------------------------------------------------------------------
# Shared OAuth-state helper
# ---------------------------------------------------------------------------

_STATE_TTL_MINUTES = 10


async def _consume_oauth_state(
    db: aiosqlite.Connection,
    state: str,
    user_id: str,
    channel_type: str,
) -> None:
    """Validate and consume a one-time OAuth state nonce.

    Given an already-open aiosqlite connection (with row_factory set), this
    function:
    1. Looks up the state row — raises 400 if missing.
    2. Checks ``row["user_id"] == user_id`` — raises 400 on mismatch (IDOR).
    3. Checks ``row["channel_type"] == channel_type`` — raises 400 if a state
       from a different provider is replayed here.
    4. Checks age <= _STATE_TTL_MINUTES — deletes the row and raises 400 if
       expired.
    5. Deletes the row (one-time use) on success.

    All failure branches raise ``HTTPException(400, "invalid or expired state")``.
    """
    cur = await db.execute(
        "SELECT user_id, channel_type, created_at FROM oauth_states WHERE state = ?",
        (state,),
    )
    row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired state",
        )

    if row["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired state",
        )

    if row["channel_type"] != channel_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired state",
        )

    created_at = datetime.fromisoformat(row["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created_at
    if age > timedelta(minutes=_STATE_TTL_MINUTES):
        await db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired state",
        )

    # Consume (one-time use).
    await db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    await db.commit()


# ---------------------------------------------------------------------------
# Slack OAuth one-click connect
# ---------------------------------------------------------------------------

_SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
_SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"


def _slack_oauth_enabled() -> bool:
    """Return True only when all three Slack OAuth env vars are set."""
    return bool(
        _settings.SLACK_CLIENT_ID
        and _settings.SLACK_CLIENT_SECRET
        and _settings.OAUTH_REDIRECT_BASE
    )


async def _exchange_slack_code(code: str) -> dict:
    """POST the authorization code to Slack and return the parsed JSON body.

    This is the ONLY function that makes live HTTP to Slack — isolated here
    so tests can monkeypatch it without touching httpx internals.
    """
    redirect_uri = (
        f"{_settings.OAUTH_REDIRECT_BASE}/api/settings/channels/callback/slack"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _SLACK_TOKEN_URL,
            data={
                "client_id": _settings.SLACK_CLIENT_ID,
                "client_secret": _settings.SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        return resp.json()


@router.get("/connect/slack", include_in_schema=False)
async def connect_slack(user: CurrentUser = Depends(require_user)) -> RedirectResponse:
    """Redirect the browser to Slack's OAuth authorization page.

    Generates a one-time CSRF state token, persists it to ``oauth_states``,
    then returns a 302 to Slack.  The callback validates the state and
    completes the connection.
    """
    if not _slack_oauth_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Slack connect is not configured",
        )

    state = secrets.token_urlsafe(32)
    now_iso = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO oauth_states(state, user_id, channel_type, created_at) "
            "VALUES(?, ?, 'slack', ?)",
            (state, user.id, now_iso),
        )
        await db.commit()

    redirect_uri = (
        f"{_settings.OAUTH_REDIRECT_BASE}/api/settings/channels/callback/slack"
    )
    params = urlencode(
        {
            "client_id": _settings.SLACK_CLIENT_ID,
            "scope": "incoming-webhook",
            "state": state,
            "redirect_uri": redirect_uri,
        }
    )
    authorize_url = f"{_SLACK_AUTHORIZE_URL}?{params}"
    return RedirectResponse(authorize_url, status_code=302)


@router.get("/callback/slack", include_in_schema=False)
async def callback_slack(
    code: str = "",
    state: str = "",
    user: CurrentUser = Depends(require_user),
) -> RedirectResponse:
    """Handle the redirect back from Slack after user authorizes the app.

    Security checks are delegated to ``_consume_oauth_state`` which validates:
    1. State exists in ``oauth_states``.
    2. State belongs to the requesting user (IDOR guard).
    3. State channel_type matches 'slack' (cross-provider replay guard).
    4. State is not older than ``_STATE_TTL_MINUTES``.
    State is always consumed (deleted) on first use past validation.
    """
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row

        await _consume_oauth_state(db, state, user.id, "slack")

        # Exchange authorization code for tokens.
        data = await _exchange_slack_code(code)

        if not data.get("ok") or "incoming_webhook" not in data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slack authorization failed",
            )

        webhook = data["incoming_webhook"]
        webhook_url: str = webhook["url"]
        channel: str = webhook.get("channel", "")

        # Convert the HTTPS webhook URL to Apprise's slack:// scheme.
        # Slack URL format: https://hooks.slack.com/services/T111/B222/zzz333
        # Apprise format:   slack://T111/B222/zzz333
        try:
            path = webhook_url.split("/services/", 1)[1]
            parts = path.rstrip("/").split("/")
            if len(parts) < 3:
                raise ValueError("unexpected webhook URL structure")
            slack_apprise_url = f"slack://{parts[0]}/{parts[1]}/{parts[2]}"
        except (IndexError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slack authorization failed",
            ) from exc

        ct = crypto.encrypt(slack_apprise_url)
        await db.execute(
            """
            INSERT INTO user_channels(
                user_id, channel_type, display_name,
                credential_encrypted, connection_status, target_label, enabled
            ) VALUES(?, 'slack', ?, ?, 'connected', ?, 1)
            """,
            (user.id, f"Slack: {channel}", ct, channel),
        )
        await db.commit()

    settings_url = (
        f"{_settings.OAUTH_REDIRECT_BASE}/settings/channels?connected=slack"
    )
    return RedirectResponse(settings_url, status_code=302)


# ---------------------------------------------------------------------------
# Discord OAuth one-click connect
# ---------------------------------------------------------------------------

_DISCORD_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
_DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"


def _discord_oauth_enabled() -> bool:
    """Return True only when all three Discord OAuth env vars are set."""
    return bool(
        _settings.DISCORD_CLIENT_ID
        and _settings.DISCORD_CLIENT_SECRET
        and _settings.OAUTH_REDIRECT_BASE
    )


async def _exchange_discord_code(code: str) -> dict:
    """POST the authorization code to Discord and return the parsed JSON body.

    This is the ONLY function that makes live HTTP to Discord — isolated here
    so tests can monkeypatch it without touching httpx internals.

    Discord returns JSON containing a ``webhook`` object with ``url``,
    ``name``, and ``channel_id`` keys.
    """
    redirect_uri = (
        f"{_settings.OAUTH_REDIRECT_BASE}/api/settings/channels/callback/discord"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _DISCORD_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": _settings.DISCORD_CLIENT_ID,
                "client_secret": _settings.DISCORD_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        return resp.json()


@router.get("/connect/discord", include_in_schema=False)
async def connect_discord(user: CurrentUser = Depends(require_user)) -> RedirectResponse:
    """Redirect the browser to Discord's OAuth authorization page.

    Generates a one-time CSRF state token, persists it to ``oauth_states``
    with ``channel_type='discord'``, then returns a 302 to Discord.
    The callback validates the state and completes the connection.
    """
    if not _discord_oauth_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discord connect is not configured",
        )

    state = secrets.token_urlsafe(32)
    now_iso = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO oauth_states(state, user_id, channel_type, created_at) "
            "VALUES(?, ?, 'discord', ?)",
            (state, user.id, now_iso),
        )
        await db.commit()

    redirect_uri = (
        f"{_settings.OAUTH_REDIRECT_BASE}/api/settings/channels/callback/discord"
    )
    params = urlencode(
        {
            "client_id": _settings.DISCORD_CLIENT_ID,
            "scope": "webhook.incoming",
            "state": state,
            "redirect_uri": redirect_uri,
            "response_type": "code",
        }
    )
    authorize_url = f"{_DISCORD_AUTHORIZE_URL}?{params}"
    return RedirectResponse(authorize_url, status_code=302)


@router.get("/callback/discord", include_in_schema=False)
async def callback_discord(
    code: str = "",
    state: str = "",
    user: CurrentUser = Depends(require_user),
) -> RedirectResponse:
    """Handle the redirect back from Discord after user authorizes the app.

    Security checks are delegated to ``_consume_oauth_state`` which validates:
    1. State exists in ``oauth_states``.
    2. State belongs to the requesting user (IDOR guard).
    3. State channel_type matches 'discord' (cross-provider replay guard).
    4. State is not older than ``_STATE_TTL_MINUTES``.
    State is always consumed (deleted) on first use past validation.
    """
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row

        await _consume_oauth_state(db, state, user.id, "discord")

        # Exchange authorization code for tokens.
        data = await _exchange_discord_code(code)

        if "webhook" not in data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="discord authorization failed",
            )

        webhook = data["webhook"]
        webhook_url: str = webhook.get("url", "")
        label: str = webhook.get("name") or webhook.get("channel_id") or "Discord"

        # Convert Discord webhook URL to Apprise's discord:// scheme.
        # Discord URL format: https://discord.com/api/webhooks/{id}/{token}
        # Apprise format:     discord://{id}/{token}
        try:
            after_webhooks = webhook_url.split("/webhooks/", 1)[1]
            parts = after_webhooks.rstrip("/").split("/")
            if len(parts) < 2:
                raise ValueError("unexpected webhook URL structure")
            discord_apprise_url = f"discord://{parts[0]}/{parts[1]}"
        except (IndexError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="discord authorization failed",
            ) from exc

        ct = crypto.encrypt(discord_apprise_url)
        await db.execute(
            """
            INSERT INTO user_channels(
                user_id, channel_type, display_name,
                credential_encrypted, connection_status, target_label, enabled
            ) VALUES(?, 'discord', ?, ?, 'connected', ?, 1)
            """,
            (user.id, f"Discord: {label}", ct, label),
        )
        await db.commit()

    settings_url = (
        f"{_settings.OAUTH_REDIRECT_BASE}/settings/channels?connected=discord"
    )
    return RedirectResponse(settings_url, status_code=302)
