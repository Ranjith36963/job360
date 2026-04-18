"""Per-user channel configuration endpoints.

Scoped to the authenticated user — every query filters by
``user_id = current_user.id``. Cross-tenant reads are impossible via this
router because the user id never appears in the URL; it is always the
cookie-resolved user.
"""
from __future__ import annotations

from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.auth_deps import CurrentUser, require_user
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


class TestSendResult(BaseModel):
    ok: bool
    error: Optional[str] = None


@router.get("", response_model=list[ChannelOut])
async def list_channels(user: CurrentUser = Depends(require_user)) -> list[ChannelOut]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, channel_type, display_name, enabled FROM user_channels "
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
