"""User-level templates (outreach pack #13): reusable wording the user's own
assistant fills in. Job360 never fills, renders or sends a template."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase
from src.services.applications import templates as templates_service
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError

router = APIRouter(tags=["templates"])

TEMPLATE_NOT_TIED = (
    "an outreach_template belongs to the user, not to a job or a person: "
    "leave application_id and contact_id out"
)
TEMPLATE_NO_CHANNEL = "channel is only for an outreach message to a contact"


class SaveTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    label: str = Field(..., max_length=200)
    text: str
    model: Optional[str] = Field(None, max_length=200)


class TemplateOut(BaseModel):
    template_id: int
    kind: str
    label: str
    version_no: int
    text: str
    chars: int
    made_by: str
    model: Optional[str] = None
    created_at: str


class ListTemplatesResponse(BaseModel):
    templates: list[TemplateOut]


@router.post("/templates", status_code=201, response_model=TemplateOut, dependencies=AUTH_FIRST)
async def save_template(
    body: SaveTemplateRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return await templates_service.save_template(
            db, user.id, kind=body.kind, label=body.label, text=body.text, made_by=actor_for(user),
            model=body.model,
        )
    except SpineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.get("/templates", response_model=ListTemplatesResponse, dependencies=AUTH_FIRST)
async def list_templates(
    kind: str = Query("outreach_template", max_length=40),  # noqa: B008
    label: Optional[str] = Query(None, max_length=100),  # noqa: B008
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    try:
        return {"templates": await templates_service.list_templates(db, user.id, kind=kind, label=label)}
    except SpineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
