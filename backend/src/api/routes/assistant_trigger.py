"""Send starts my assistant — the website's routes (services/browser_agent/trigger.py).

Every route is a browser session only (``require_session_user``): a token — an assistant —
can neither read where Send points, nor change it, nor fire it. The token itself is never
returned. No MCP tool exposes these (rule M5 is about tools that exist; none should).
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user
from src.api.dependencies import get_request_db
from src.core import settings
from src.services.browser_agent import duty_trigger as duty_service
from src.services.browser_agent import trigger as trigger_service
from src.services.browser_agent.sign_ins import SignInKeyMissingError
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["assistant-trigger"])
logger = logging.getLogger("job360.api.assistant_trigger")


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
    """Save (or replace) the start address and its token. The token is encrypted at rest.

    An unexpected error must not carry the plaintext token into an error report: the body is emptied of
    it, the error is logged without its traceback or message, and a plain 500 replaces it (from None)."""
    try:
        async with _db() as db:
            await trigger_service.save(db, user.id, body.url, body.token)
            view = trigger_service.public_view(await trigger_service.load(db, user.id))
    except trigger_service.TriggerError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from None
    except SignInKeyMissingError:
        raise HTTPException(status_code=503, detail="This server cannot keep tokens yet (no SESSION_SECRET).") from None
    except Exception:  # noqa: BLE001 - its message and frames can hold the token
        logger.error("assistant trigger not saved for user %s", safe_log_value(user.id))
        raise HTTPException(status_code=500, detail="The start address could not be saved. Try again.") from None
    finally:
        body.token = ""
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


# ── Ignition (owner 2026-10-24): one start link per duty ─────────────────────
# Same rule as above: a browser session only. `batch` (Apply) is the Send link above.


class DutyTriggerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2048)
    token: str = Field("", max_length=4096)


def _duty_or_404(duty: str) -> str:
    if duty not in duty_service.DUTIES:
        raise HTTPException(status_code=404, detail="unknown duty")
    return duty


@router.get("/assistant-duty-triggers", dependencies=AUTH_FIRST)
async def get_duty_triggers(user: CurrentUser = Depends(require_session_user)) -> dict[str, Any]:  # noqa: B008
    """Every duty's start link: host and last result only; never the token."""
    async with _db() as db:
        return {"duties": await duty_service.views(db, user.id)}


@router.put("/assistant-duty-triggers/{duty}", dependencies=AUTH_FIRST)
async def put_duty_trigger(
    duty: str, body: DutyTriggerIn, user: CurrentUser = Depends(require_session_user)  # noqa: B008
) -> dict[str, Any]:
    """Save (or replace) one duty's start link and token (encrypted). Like PUT /assistant-trigger, an
    unexpected error never carries the plaintext token into a report."""
    _duty_or_404(duty)
    try:
        async with _db() as db:
            await duty_service.save(db, user.id, duty, body.url, body.token)
            view = duty_service.public_view(duty, await duty_service.load(db, user.id, duty))
    except (duty_service.DutyError, trigger_service.TriggerError) as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from None
    except SignInKeyMissingError:
        raise HTTPException(status_code=503, detail="This server cannot keep tokens yet (no SESSION_SECRET).") from None
    except Exception:  # noqa: BLE001 - its message and frames can hold the token
        logger.error("duty trigger not saved for user %s", safe_log_value(user.id))
        raise HTTPException(status_code=500, detail="The start link could not be saved. Try again.") from None
    finally:
        body.token = ""
    _audit(user, f"save_{duty}", "ok")
    return view


@router.delete("/assistant-duty-triggers/{duty}", dependencies=AUTH_FIRST)
async def delete_duty_trigger(duty: str, user: CurrentUser = Depends(require_session_user)) -> dict[str, Any]:  # noqa: B008
    _duty_or_404(duty)
    async with _db() as db:
        deleted = await duty_service.delete(db, user.id, duty)
    _audit(user, f"delete_{duty}", "ok" if deleted else "none")
    return {"deleted": deleted}


class DutyRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test: bool = False


@router.post("/assistant-duty-triggers/{duty}/run", dependencies=AUTH_FIRST)
async def run_duty(
    duty: str, body: DutyRunIn | None = None, user: CurrentUser = Depends(require_session_user)  # noqa: B008
) -> dict[str, Any]:
    """Run now (or Test): start the user's routine for ``duty`` once. Never a 500 for a start that did
    not happen: ``fired: false`` and a closed reason the page shows."""
    from src.services.fetch.fetcher import DEFAULT_RESOLVE  # noqa: PLC0415

    _duty_or_404(duty)
    cause = "test" if body is not None and body.test else "run_now"
    async with _db() as db:
        result = await duty_service.fire(db, user.id, duty, cause=cause, resolve=DEFAULT_RESOLVE)
    _audit(user, f"{cause}_{duty}", "started" if result.get("fired") else str(result.get("reason")))
    return result
