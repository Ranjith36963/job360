"""Per-user channel configuration endpoints.

Scoped to the authenticated user — every query filters by
``user_id = current_user.id``. Cross-tenant reads are impossible via this
router because the user id never appears in the URL; it is always the
cookie-resolved user.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, require_user
from src.core.settings import DB_PATH
from src.repositories import pg
from src.repositories.db_retry import open_db
from src.services.channels import crypto, dispatcher
from src.services.channels.email_url import EMAIL_RE as _EMAIL_RE
from src.services.channels.email_url import build_email_apprise_url
from src.services.channels.ssrf_guard import assert_public_http_url
from src.utils.logger import get_audit_logger

router = APIRouter(prefix="/settings/channels", tags=["channels"])

# Delivery is EMAIL + WEBHOOK only (2026-08-24). The per-user Slack, Discord
# and Telegram channels were removed: they were never configured in production,
# no user ever connected one, and they carried ~450 lines of OAuth for zero
# delivered notifications. Rationale and evidence:
# docs/plans/2026-08-24-email-webhook-only-delivery.md
#
# `email` is the supported product surface. `webhook` is an unsupported raw-JSON
# escape hatch for a technical user's own tooling.
#
# There is no separate _VALID_TYPES set: the ONE definition of "which channel
# types exist" is the ChannelIn.channel_type pattern below. The old set was dead
# code — defined, never referenced — which is exactly how a second source of
# truth starts.

# _EMAIL_RE is imported from services/channels/email_url.py — ONE definition,
# shared with the signup seeder, so the route and the seeder can never drift
# apart on what counts as a valid address. The ReDoS-safety rationale
# (CodeQL py/polynomial-redos) lives with the pattern.


class ChannelIn(BaseModel):
    channel_type: str = Field(pattern="^(email|webhook)$")
    display_name: str = Field(min_length=1, max_length=120)
    # max_length caps the regex/parse work per request — defence in depth behind
    # the ReDoS-safe _EMAIL_RE above. Webhook URLs and bot tokens fit well under 512.
    credential: str = Field(min_length=1, max_length=512)  # interpreted per channel_type


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

    Two channel types exist, and ``ChannelIn.channel_type``'s pattern is the
    only place that fact is written down. Anything else is a 422 before this
    body runs.

    * ``webhook``: ``credential`` must be an http(s) URL; backend converts it
      to the Apprise ``json[s]://`` URL scheme.
    * ``email``: ``credential`` must be a valid email address; the backend
      builds the Apprise URL from the platform's own mail credentials
      (``resend://`` where a Resend key is configured, ``mailtos://`` only as
      the local-SMTP fallback — see ``services/channels/email_url.py``).
    """
    ct_type = body.channel_type

    if ct_type == "webhook":
        cred = body.credential
        if not (cred.startswith("http://") or cred.startswith("https://")):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="webhook must be an http(s) URL",
            )
        # SSRF guard: reject URLs whose host resolves to a private/internal IP
        # (loopback, link-local incl. 169.254.169.254 metadata, RFC1918, …).
        # Re-checked at send time in the dispatcher (DNS-rebinding defence).
        try:
            assert_public_http_url(cred)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
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
        # #318 — the URL shape moved to services/channels/email_url.py so this
        # route and the signup seeder can never disagree about it.
        #
        # It also stopped being `mailtos://`. Railway blocks outbound SMTP
        # ports (25/465/587) — that is why `auth/email_sender.py` was rewritten
        # off smtplib onto Resend's HTTPS API in the first place. So every
        # email channel created here was built on a transport this deployment
        # cannot use: Apprise would have timed out, the dispatcher would have
        # recorded ok=False, and the user would have seen silence. The builder
        # now prefers `resend://` (HTTPS:443, already proven to deliver in
        # prod) and keeps `mailtos://` only for local/self-hosted SMTP.
        built = build_email_apprise_url(dest)
        if built is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="email delivery is not configured",
            )
        apprise_url = built
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


@router.delete("/{channel_id:int}", status_code=status.HTTP_204_NO_CONTENT)
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


@router.post("/{channel_id:int}/test", response_model=TestSendResult)
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

