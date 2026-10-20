"""Send starts my assistant — the website's routes (services/browser_agent/trigger.py).

Every route is a browser session only (``require_session_user``): a token — an assistant —
can neither read where Send points, nor change it, nor fire it. The token itself is never
returned. No MCP tool exposes these (rule M5 is about tools that exist; none should).
"""
from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user
from src.api.dependencies import get_request_db
from src.core import settings
from src.services.browser_agent import trigger as trigger_service
from src.services.browser_agent.sign_ins import SignInKeyMissingError
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["assistant-trigger"])


class TriggerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2048)
    token: str = Field("", max_length=4096)


def _db() -> contextlib.AbstractAsyncContextManager[Any]:
    return contextlib.asynccontextmanager(get_request_db)()


def _audit(user: CurrentUser, action: str, result: str) -> None:
    get_audit_logger().info(
        "assistant_trigger",
        extra={"event": "assistant_trigger", "user_id": safe_log_value(user.id), "action": action,
               "result": safe_log_value(result)},
    )


@router.get("/assistant-trigger", dependencies=AUTH_FIRST)
async def get_trigger(user: CurrentUser = Depends(require_session_user)) -> dict[str, Any]:  # noqa: B008
    """Is Send set to start the user's assistant? Host and last result only; never the token."""
    async with _db() as db:
        return trigger_service.public_view(await trigger_service.load(db, user.id))


@router.put("/assistant-trigger", dependencies=AUTH_FIRST)
async def put_trigger(
    body: TriggerIn, user: CurrentUser = Depends(require_session_user)  # noqa: B008
) -> dict[str, Any]:
    """Save (or replace) the start address and its token. The token is encrypted at rest."""
    try:
        async with _db() as db:
            await trigger_service.save(db, user.id, body.url, body.token)
            view = trigger_service.public_view(await trigger_service.load(db, user.id))
    except trigger_service.TriggerError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from None
    except SignInKeyMissingError:
        raise HTTPException(status_code=503, detail="This server cannot keep tokens yet (no SESSION_SECRET).") from None
    _audit(user, "save", "ok")
    return view


@router.delete("/assistant-trigger", dependencies=AUTH_FIRST)
async def delete_trigger(user: CurrentUser = Depends(require_session_user)) -> dict[str, Any]:  # noqa: B008
    async with _db() as db:
        deleted = await trigger_service.delete(db, user.id)
    _audit(user, "delete", "ok" if deleted else "none")
    return {"deleted": deleted}


@router.post("/assistant-trigger/fire", dependencies=AUTH_FIRST)
async def fire_trigger(user: CurrentUser = Depends(require_session_user)) -> dict[str, Any]:  # noqa: B008
    """Called by the website right after Send / Send all: start the user's assistant once for every
    application whose newest send decision is their yes. Never fails the Send itself: a problem comes
    back as ``fired: false`` with a reason the page shows."""
    from src.services.fetch.fetcher import DEFAULT_RESOLVE  # noqa: PLC0415

    async with _db() as db:
        result = await trigger_service.fire(db, user.id, site=settings.SITE_BASE_URL, resolve=DEFAULT_RESOLVE)
    _audit(user, "fire", "started" if result.get("fired") else str(result.get("reason")))
    return result
