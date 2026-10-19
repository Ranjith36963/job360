"""Assistant settings — the website's door for the user's own click.

Owner decision 2026-10-08 (S2). An assistant can change a setting through
``update_profile`` only when the change is SAFER; a RISKIER one is stored as a
waiting request (``services/profile/setting_requests.py``). THIS module is the
only place a waiting request is confirmed or declined, and every write here is
SESSION-ONLY (``require_session_user``): a personal token or an OAuth grant is
an assistant, and an assistant must never confirm its own request. There is
deliberately NO MCP tool for any of these writes (pinned by
``tests/test_assistant_settings.py``; same stance as Keep / Take back).

Every route scopes by ``user.id`` (rules #12/#25) — a request id that belongs to
another user reads as 404.

Logs: ``assistant_settings_read``, ``assistant_setting_confirmed|declined|
taken_back`` — the user id, the actor, the path and the request id; never a
pause reason or notes text.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_deps import CurrentUser, require_session_user, require_user
from src.api.models import (
    AssistantSettingsView,
    ProfileEditHistoryResponse,
    ProfileEditHistoryRow,
    TakeBackRequest,
)
from src.api.routes.profile import settings_view
from src.core import settings
from src.services.applications.authorship import actor_for
from src.services.profile import assistant_settings as rules
from src.services.profile import edits as profile_edits
from src.services.profile import setting_requests
from src.services.profile.models import UserProfile
from src.services.profile.storage import load_profile
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["assistant-settings"])


def _view_for(user_id: str) -> dict[str, Any]:
    """The read model for one user. A user with no profile row yet reads the
    defaults (nothing chosen, nothing waiting)."""
    return settings_view(load_profile(user_id) or UserProfile(), user_id)


def _log(event: str, user: CurrentUser, **fields: Any) -> None:
    get_audit_logger().info(
        event,
        extra={
            "event": event, "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
            "result": "ok", **fields,
        },
    )


def _setting_path_or_422(path: str) -> str:
    if path not in rules.SETTING_PATHS:
        raise HTTPException(
            status_code=422,
            detail=f"{path!r} is not an assistant setting. Settings: {', '.join(rules.SETTING_PATHS)}",
        )
    return path


def _open_request_or_error(user: CurrentUser, request_id: int) -> dict[str, Any]:
    """The caller's own WAITING, unexpired request — else 404 (not theirs / not
    there) or 409 (already decided, or older than the time limit)."""
    row = setting_requests.get_request(user.id, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such request")
    if row["decision"] is not None:
        raise HTTPException(status_code=409, detail=f"This request was already {row['decision']}")
    if setting_requests.is_expired(row["requested_at"]):
        raise HTTPException(
            status_code=409,
            detail=(
                f"This request is older than {settings.ASSISTANT_SETTING_REQUEST_TTL_DAYS} days and has expired; "
                "ask your assistant to send it again"
            ),
        )
    return row


@router.get("/assistant-settings", response_model=AssistantSettingsView)
async def get_assistant_settings(
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI DI idiom
) -> AssistantSettingsView:
    """The caller's settings (each raw + effective), the practice-run state and
    the requests waiting for the user's OK. Read-only; any signed-in caller."""
    view = _view_for(user.id)
    _log("assistant_settings_read", user, surface="web", waiting=len(view["waiting"]))
    return AssistantSettingsView(**view)


@router.post("/assistant-settings/requests/{request_id}/confirm", response_model=AssistantSettingsView)
async def confirm_request(
    request_id: int,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — a human web action (session only)
) -> AssistantSettingsView:
    """The user confirms a waiting request: the change is applied now as a WEB
    row (so history says the human did it). 404 when the id is not the caller's;
    409 when it was already decided or has expired; 422 when the stored value no
    longer passes validation (for example a pause time that has since passed)."""
    row = _open_request_or_error(user, request_id)
    path, value = row["path"], row["value"]
    try:
        profile_edits.validate_edit(path, value)
    except profile_edits.ProfileEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    # Claim the decision FIRST (set once): a double click or two tabs cannot
    # apply the same request twice.
    if not setting_requests.decide(user.id, request_id, "confirmed", actor_for(user)):
        raise HTTPException(status_code=409, detail="This request was already decided")
    profile_edits.record_edits(user.id, profile_edits.WEB_ACTOR, [(path, value)], enforce_rate_limit=False)
    _log("assistant_setting_confirmed", user, path=safe_log_value(path), request_id=request_id)
    return AssistantSettingsView(**_view_for(user.id))


@router.post("/assistant-settings/requests/{request_id}/decline", response_model=AssistantSettingsView)
async def decline_request(
    request_id: int,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — a human web action (session only)
) -> AssistantSettingsView:
    """The user declines a waiting request ("Don't change"): nothing is applied
    and the request is closed. Same 404 / 409 rules as confirm."""
    row = _open_request_or_error(user, request_id)
    if not setting_requests.decide(user.id, request_id, "declined", actor_for(user)):
        raise HTTPException(status_code=409, detail="This request was already decided")
    _log("assistant_setting_declined", user, path=safe_log_value(row["path"]), request_id=request_id)
    return AssistantSettingsView(**_view_for(user.id))


@router.post("/assistant-settings/take-back", response_model=AssistantSettingsView)
async def take_back_setting(
    body: TakeBackRequest,
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 — a human web action (session only)
) -> AssistantSettingsView:
    """Put one setting back to what it held BEFORE its newest change.

    Append-only: a new ``web`` row carries the value of the row before the
    newest one (``null`` — the safe default — when there was none). Not
    rate-limited (the human's own action). 404 when the setting never changed."""
    path = _setting_path_or_422(body.path)
    history = profile_edits.path_history(user.id, path, 2)
    if not history:
        raise HTTPException(status_code=404, detail=f"No change to take back on {path}")
    previous = history[1]["value"] if len(history) > 1 else None
    profile_edits.record_edits(user.id, profile_edits.WEB_ACTOR, [(path, previous)], enforce_rate_limit=False)
    _log("assistant_setting_taken_back", user, path=safe_log_value(path))
    return AssistantSettingsView(**_view_for(user.id))


@router.get("/assistant-settings/history", response_model=ProfileEditHistoryResponse)
async def setting_history(
    path: str = Query(..., max_length=100),
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI DI idiom
) -> ProfileEditHistoryResponse:
    """Every change to ONE setting, newest first, both sides (the user's web
    rows and the assistant's). At most ``PROFILE_EDIT_HISTORY_MAX`` rows."""
    _setting_path_or_422(path)
    rows = profile_edits.path_history(user.id, path, max(1, settings.PROFILE_EDIT_HISTORY_MAX))
    return ProfileEditHistoryResponse(path=path, rows=[ProfileEditHistoryRow(**r) for r in rows])
