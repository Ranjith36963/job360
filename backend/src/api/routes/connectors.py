"""The connector check (owner, 2026-10-25) - the doors. Rules: ``services/profile/connectors.py``.

``POST /api/capabilities`` - the assistant reports which helper tools it has (MCP ``report_capabilities``
calls this function). ``GET /api/connectors`` - what the web shows: each assistant's last report, the
catalog's suggestions and the cards; it remembers when a card was first shown. ``POST
/api/connectors/dismiss`` - the user's own "don't show this again" (session only). The user always comes
from the session / token, never from input (rules #12/#25); logs carry closed values only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase
from src.services.applications.authorship import actor_for
from src.services.profile import connectors
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["connectors"])

Assistant = Literal["claude", "chatgpt", "gemini", "other"]
Category = Literal["gmail", "calendar", "job_search", "people_lookup", "company_lookup", "browser", "web_search"]
Status = Literal["ok", "needs_sign_in", "not_on_plan", "failing"]


class CapabilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    via: str = Field(default="", max_length=connectors.VIA_MAX_CHARS)
    status: Status = "ok"


class CapabilitiesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assistant: Assistant
    categories: list[CapabilityIn] = Field(default_factory=list, max_length=len(connectors.CATEGORIES))
    # Categories a run needed but this assistant lacks (e.g. the people hunt found nobody to reach).
    needed: list[Category] = Field(default_factory=list, max_length=len(connectors.CATEGORIES))


class CapabilitiesOut(BaseModel):
    assistant: Assistant
    reported_at: str
    saved: bool
    missing_essential: list[str]
    missing_recommended: list[str]
    failing: list[str]


class DismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=3, max_length=80, pattern=r"^(fix|add):[a-z_]+(:[a-z_]+)?$")


def _log(event: str, user: CurrentUser, **fields: Any) -> None:
    get_audit_logger().info(event, extra={"event": event, "user_id": safe_log_value(user.id),
                                          "actor": safe_log_value(actor_for(user)), "result": "ok", **fields})


@router.post("/capabilities", response_model=CapabilitiesOut, dependencies=AUTH_FIRST)
async def report_capabilities(
    body: CapabilitiesIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> CapabilitiesOut:
    """Store which helper tools this assistant has (append-only; a repeat of the newest report is not
    stored again). Answers what is missing or failing, so the assistant can tell the user in chat."""
    try:
        categories, needed = connectors.clean_report([c.model_dump() for c in body.categories], list(body.needed))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    now = datetime.now(timezone.utc)
    stamp, saved = await connectors.save_report(db, user.id, body.assistant, categories, needed, actor_for(user), now)
    view = connectors.summary(await connectors.latest_reports(db, user.id),
                              has_feed=await connectors.has_calendar_feed(db, user.id))
    mine = f"{body.assistant}:"
    _log("capabilities_reported", user, assistant=body.assistant, saved=saved, categories=len(categories))
    return CapabilitiesOut(
        assistant=body.assistant, reported_at=stamp, saved=saved,
        missing_essential=[m for m in view["missing_essential"] if m.startswith(mine) or m == connectors.CALENDAR_FEED],
        missing_recommended=[m for m in view["missing_recommended"] if m.startswith(mine)],
        failing=[m for m in view["failing"] if m.startswith(mine)],
    )


class EntryOut(BaseModel):
    category: str
    name: str
    tier: str
    free: Optional[bool] = None
    plan_note: Optional[str] = None
    unlocks: str
    connect_url: Optional[str] = None
    verified_on: Optional[str] = None


class CardOut(BaseModel):
    key: str
    kind: Literal["fix", "add"]
    tier: str
    assistant: Optional[str] = None
    category: str
    title: str
    unlocks: str
    link: Optional[str] = None
    suggested: Optional[EntryOut] = None
    alternatives: list[EntryOut] = Field(default_factory=list)


class AssistantViewOut(BaseModel):
    assistant: str
    label: str
    reported_at: str
    connected: list[dict[str, str]]
    missing: dict[str, list[EntryOut]]


class ConnectorsOut(BaseModel):
    assistants: list[AssistantViewOut]
    has_calendar_feed: bool
    cards: list[CardOut]


@router.get("/connectors", response_model=ConnectorsOut, dependencies=AUTH_FIRST)
async def connector_view(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the web view (it records what it showed)
) -> ConnectorsOut:
    """For Settings and Home: each assistant's last report (what is connected, what is missing with the
    catalog's suggestions) and the cards to show now. Records when a card is first shown."""
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    reports = await connectors.latest_reports(db, user.id)
    has_feed = await connectors.has_calendar_feed(db, user.id)
    profile = load_profile(user.id) or UserProfile()
    setup_done = rules.setup_progress_view(profile.assistant_settings.setup_progress)["next"] == ""
    shown = connectors.cards(reports, has_feed=has_feed, state=await connectors.card_state(db, user.id),
                             setup_done=setup_done, now=now)
    await connectors.mark_shown(db, user.id, [c["key"] for c in shown], now)
    views = []
    for assistant, rep in sorted(reports.items()):
        has = {i["category"] for i in rep["categories"]}
        views.append(AssistantViewOut(
            assistant=assistant,
            label=(connectors.catalog()["assistants"].get(assistant) or {}).get("label") or "Your assistant",
            reported_at=rep["reported_at"], connected=rep["categories"],
            missing={c: [EntryOut(**e) for e in connectors.suggestions(assistant, c)]
                     for c in connectors.CATEGORIES if c not in has},
        ))
    return ConnectorsOut(assistants=views, has_calendar_feed=has_feed, cards=[CardOut(**c) for c in shown])


@router.post("/connectors/dismiss", status_code=204, dependencies=AUTH_FIRST)
async def dismiss_card(
    body: DismissIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008 - the user's own choice, web only
) -> None:
    """Never show this card again (remembered per user)."""
    await connectors.dismiss(db, user.id, body.key, datetime.now(timezone.utc))
    _log("connector_card_dismissed", user, card=body.key.split(":")[0])
