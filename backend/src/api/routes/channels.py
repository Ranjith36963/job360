"""Per-user channel configuration endpoints.

Scoped to the authenticated user — every query filters by
``user_id = current_user.id``. Cross-tenant reads are impossible via this
router because the user id never appears in the URL; it is always the
cookie-resolved user.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, require_user
from src.core import settings as _settings
from src.core.settings import DB_PATH
from src.repositories import pg
from src.repositories.db_retry import open_db
from src.services.channels import crypto, dispatcher
from src.utils.logger import get_audit_logger

router = APIRouter(prefix="/settings/channels", tags=["channels"])

_VALID_TYPES = {"email", "slack", "discord", "telegram", "webhook"}

# Chat channel types that must use the Connect flow (not the paste path).
_CONNECT_ONLY_TYPES = {"slack", "discord", "telegram"}

# Simple email regex — intentionally lenient; catches obvious non-emails.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ChannelIn(BaseModel):
    channel_type: str = Field(pattern="^(email|slack|discord|telegram|webhook)$")
    display_name: str = Field(min_length=1, max_length=120)
    credential: str = Field(min_length=1)  # interpreted per channel_type


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
    """Which OAuth/connect buttons the frontend should display."""

    slack: bool
    discord: bool
    telegram: bool


class TelegramConnectOut(BaseModel):
    """Response from GET /connect/telegram."""

    deep_link: str
    state: str


class TelegramPollOut(BaseModel):
    """Response from GET /connect/telegram/poll."""

    connected: bool
    target_label: Optional[str] = None


@router.get("", response_model=list[ChannelOut])
async def list_channels(user: CurrentUser = Depends(require_user)) -> list[ChannelOut]:
    async with open_db(str(DB_PATH)) as db:
        db.row_factory = pg.Row
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
    """Create a channel via direct credential input.

    * ``slack``, ``discord``, ``telegram`` must use the Connect flow → 400.
    * ``webhook``: ``credential`` must be an http(s) URL; backend converts it
      to the Apprise ``json[s]://`` URL scheme.
    * ``email``: ``credential`` must be a valid email address; backend builds
      the ``mailtos://`` Apprise URL from the platform SMTP creds.
    """
    ct_type = body.channel_type

    if ct_type in _CONNECT_ONLY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="use the Connect flow for this channel type",
        )

    if ct_type == "webhook":
        cred = body.credential
        if not (cred.startswith("http://") or cred.startswith("https://")):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="webhook must be an http(s) URL",
            )
        # Convert to Apprise JSON webhook scheme:
        # https://example.com/hook → jsons://example.com/hook
        # http://example.com/hook  → json://example.com/hook
        if cred.startswith("https://"):
            apprise_url = "jsons://" + cred[len("https://"):]
        else:
            apprise_url = "json://" + cred[len("http://"):]
        encrypted = crypto.encrypt(apprise_url)

    elif ct_type == "email":
        dest = body.credential
        if not _EMAIL_RE.match(dest):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="enter a valid email address",
            )
        smtp_user = _settings.SMTP_EMAIL
        smtp_pass = _settings.SMTP_PASSWORD
        if not smtp_user or not smtp_pass:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="email delivery is not configured",
            )
        # Build Apprise mailtos:// URL.
        # mailtos://{user}:{pass}@{smtp_domain}?to={dest}
        smtp_domain = smtp_user.split("@", 1)[1] if "@" in smtp_user else smtp_user
        apprise_url = (
            f"mailtos://{quote(smtp_user, safe='')}:{quote(smtp_pass, safe='')}"
            f"@{smtp_domain}?to={quote(dest, safe='')}"
        )
        encrypted = crypto.encrypt(apprise_url)

    else:
        # Unknown type — should not reach here because ChannelIn.pattern guards it.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unsupported channel type: {ct_type}",
        )

    async with open_db(str(DB_PATH)) as db:
        cur = await db.execute(
            """
            INSERT INTO user_channels(user_id, channel_type, display_name,
                                      credential_encrypted, enabled)
            VALUES(?, ?, ?, ?, 1)
            """,
            (user.id, body.channel_type, body.display_name, encrypted),
        )
        await db.commit()
        channel_id = cur.lastrowid
    get_audit_logger().info(
        "channel_created",
        extra={
            "event": "channel_created",
            "user_id": user.id,
            "channel_type": body.channel_type,
            "channel_id": channel_id,
        },
    )
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
    async with open_db(str(DB_PATH)) as db:
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
    get_audit_logger().info(
        "channel_deleted",
        extra={"event": "channel_deleted", "user_id": user.id, "channel_id": channel_id},
    )


@router.post("/{channel_id}/test", response_model=TestSendResult)
async def test_send_channel(
    channel_id: int, user: CurrentUser = Depends(require_user)
) -> TestSendResult:
    # Two-layer ownership check: HTTP SELECT here AND dispatcher filters
    # internally on user_id. Either layer rejects a cross-user attempt.
    async with open_db(str(DB_PATH)) as db:
        db.row_factory = pg.Row
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
    get_audit_logger().info(
        "channel_test_send",
        extra={
            "event": "channel_test_send",
            "user_id": user.id,
            "channel_id": channel_id,
            "ok": result.ok,
            "reason": result.error or "",
        },
    )
    return TestSendResult(
        ok=result.ok,
        error=result.error or None,
    )


@router.get("/providers", response_model=ProvidersOut)
async def get_providers(user: CurrentUser = Depends(require_user)) -> ProvidersOut:
    """Return which Connect buttons are configured on this deployment."""
    return ProvidersOut(
        slack=_slack_oauth_enabled(),
        discord=_discord_oauth_enabled(),
        telegram=_telegram_enabled(),
    )


# ---------------------------------------------------------------------------
# Shared OAuth-state helper
# ---------------------------------------------------------------------------

_STATE_TTL_MINUTES = 10


async def _consume_oauth_state(
    db: pg.Connection,
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

    async with open_db(str(DB_PATH)) as db:
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
    async with open_db(str(DB_PATH)) as db:
        db.row_factory = pg.Row

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
        f"{_settings.OAUTH_REDIRECT_BASE}/channels?connected=slack"
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

    async with open_db(str(DB_PATH)) as db:
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
    async with open_db(str(DB_PATH)) as db:
        db.row_factory = pg.Row

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
        f"{_settings.OAUTH_REDIRECT_BASE}/channels?connected=discord"
    )
    return RedirectResponse(settings_url, status_code=302)


# ---------------------------------------------------------------------------
# Telegram Bot deep-link + poll connect
# ---------------------------------------------------------------------------


def _telegram_enabled() -> bool:
    """Return True only when both Telegram bot env vars are set."""
    return bool(_settings.TELEGRAM_BOT_TOKEN and _settings.TELEGRAM_BOT_USERNAME)


async def _telegram_get_updates() -> list[dict]:
    """GET /getUpdates from the Telegram Bot API and return the result list.

    This is the ONLY function that makes live HTTP to Telegram — isolated here
    so tests can monkeypatch it without touching httpx internals.
    """
    token = _settings.TELEGRAM_BOT_TOKEN
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])


@router.get("/connect/telegram", response_model=TelegramConnectOut)
async def connect_telegram(
    user: CurrentUser = Depends(require_user),
) -> TelegramConnectOut:
    """Return a Telegram deep-link the user must tap to start the bot.

    The frontend polls ``GET /connect/telegram/poll?state=<state>`` until
    the user's ``/start <state>`` message arrives in the bot's update queue.

    Returns 404 when TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME are not set.
    """
    if not _telegram_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Telegram connect is not configured",
        )

    state = secrets.token_urlsafe(32)
    now_iso = datetime.now(timezone.utc).isoformat()

    async with open_db(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO oauth_states(state, user_id, channel_type, created_at) "
            "VALUES(?, ?, 'telegram', ?)",
            (state, user.id, now_iso),
        )
        await db.commit()

    deep_link = f"https://t.me/{_settings.TELEGRAM_BOT_USERNAME}?start={state}"
    return TelegramConnectOut(deep_link=deep_link, state=state)


@router.get("/connect/telegram/poll", response_model=TelegramPollOut)
async def poll_telegram(
    state: str,
    user: CurrentUser = Depends(require_user),
) -> TelegramPollOut:
    """Poll whether the user has tapped the Telegram deep-link.

    The frontend calls this repeatedly until ``connected=true`` or the state
    expires.  The state row is NOT consumed until a matching Telegram message
    is found (so callers can safely retry while the user is still tapping).

    Security checks (without consuming the state):
    - State must exist in ``oauth_states``.
    - ``user_id`` must match the requesting user (IDOR guard).
    - ``channel_type`` must be ``'telegram'``.
    - Age must be ≤ ``_STATE_TTL_MINUTES`` (row is deleted on expiry).

    If Telegram has received ``/start <state>`` from the user's chat, the
    channel row is created and the state is consumed; returns
    ``{connected: true, target_label: ...}``.  Otherwise returns
    ``{connected: false}`` with the state still alive.
    """
    async with open_db(str(DB_PATH)) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT user_id, channel_type, created_at FROM oauth_states WHERE state = ?",
            (state,),
        )
        row = await cur.fetchone()

        if row is None:
            # State never existed or was already consumed — permanent 400.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid or expired state",
            )

        if row["user_id"] != user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid or expired state",
            )

        if row["channel_type"] != "telegram":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid or expired state",
            )

        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created_at
        if age > timedelta(minutes=_STATE_TTL_MINUTES):
            # Expired — clean up and reject.
            await db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid or expired state",
            )

        # State is valid — check Telegram for the matching /start message.
        updates = await _telegram_get_updates()
        expected_text = f"/start {state}"

        for update in updates:
            message = update.get("message", {})
            if message.get("text") == expected_text:
                chat = message["chat"]
                chat_id = chat["id"]
                label = (
                    chat.get("title")
                    or chat.get("username")
                    or chat.get("first_name")
                    or str(chat_id)
                )
                token = _settings.TELEGRAM_BOT_TOKEN
                apprise_url = f"tgram://{token}/{chat_id}"
                encrypted = crypto.encrypt(apprise_url)

                await db.execute(
                    """
                    INSERT INTO user_channels(
                        user_id, channel_type, display_name,
                        credential_encrypted, connection_status, target_label, enabled
                    ) VALUES(?, 'telegram', ?, ?, 'connected', ?, 1)
                    """,
                    (user.id, f"Telegram: {label}", encrypted, label),
                )
                # Consume the state (one-time use).
                await db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
                await db.commit()
                return TelegramPollOut(connected=True, target_label=label)

    # No matching update yet — return not-connected; state row kept alive.
    return TelegramPollOut(connected=False)
