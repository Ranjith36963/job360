"""Proof of application (S7, owner decision 2026-10-10): screenshots + single-use upload links.

``POST /api/proof/{token}`` has NO login: an assistant's file tool posts the image
there, and the token IS the credential (single use, 5 minutes, only its hash is
stored). Everything else is per-user and scoped by ``user.id``; the web-only routes
(upload, view, delete an image) refuse a bearer token. Pasted confirmation text is a
plain ``proof_text`` event through ``record_event``. See ``services/applications/proof.py``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_session_user, require_user
from src.api.dependencies import get_request_db
from src.api.routes.applications import ProofOut
from src.api.routes.files import _NO_STORE, _ip_hash
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import proof, spine
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError

router = APIRouter(tags=["proof"])
_MULTIPART_SLACK = 64 * 1024  # envelope bytes around the file
_UPLOAD_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}},
}}}}}


class ProofLinkOut(BaseModel):
    application_id: int
    url: str
    expires_at: str
    max_bytes: int
    accepts: list[str]
    single_use: bool = True


class ProofUploadOut(BaseModel):
    screenshot_id: int
    application_id: int
    mime: str
    size: int


class ProofScreenshotOut(BaseModel):
    id: int
    mime: str
    size: int
    sha256: str
    created_by: str
    created_at: str
    deleted_at: Optional[str] = None
    delete_note: str = ""


class ProofStateOut(BaseModel):
    application_id: int
    proof: ProofOut
    screenshots: list[ProofScreenshotOut]


def _raise(exc: SpineError, headers: Optional[dict[str, str]] = None) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers) from None


async def _read_file(request: Request) -> bytes:
    """The multipart field ``file``, never more than the cap in memory: a declared
    length over the cap is refused unread, and the stream is cut off past it."""
    cap = settings.PROOF_SCREENSHOT_MAX_BYTES + _MULTIPART_SLACK
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise SpineError(413, f"file exceeds PROOF_SCREENSHOT_MAX_BYTES ({settings.PROOF_SCREENSHOT_MAX_BYTES} bytes)")

    async def capped() -> Any:
        seen = 0
        async for chunk in request.stream():
            seen += len(chunk)
            if seen > cap:
                raise SpineError(413, "file is too large")
            yield chunk

    try:
        form = await MultiPartParser(request.headers, capped(), max_files=1, max_fields=1).parse()
    except (MultiPartException, KeyError):
        raise SpineError(422, 'send multipart/form-data with one field named "file"') from None
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise SpineError(422, 'send multipart/form-data with one field named "file"')
    return await upload.read(settings.PROOF_SCREENSHOT_MAX_BYTES + 1)


async def upload_bytes(request: Request) -> bytes:
    """The upload body, read BEFORE ``get_request_db`` is borrowed (declare it ahead of ``db``): a client that
    trickles its body then holds no pooled connection. Bounded by the size cap and ``PROOF_UPLOAD_READ_SECONDS``."""
    public = request.url.path.startswith("/api/proof/")
    try:
        try:
            return await asyncio.wait_for(_read_file(request), settings.PROOF_UPLOAD_READ_SECONDS)
        except asyncio.TimeoutError:
            raise SpineError(408, "upload took too long") from None
    except SpineError as exc:
        _raise(exc, _NO_STORE if public else None)


@router.post(
    "/applications/{application_id}/proof/link", status_code=201, response_model=ProofLinkOut,
    dependencies=AUTH_FIRST,
)
async def create_proof_link(
    application_id: int,
    response: Response,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """Mint a one-time upload link for THIS application's proof screenshot (5 minutes)."""
    response.headers["Cache-Control"] = "no-store"
    try:
        link = await proof.mint_link(db, user, application_id, datetime.now(timezone.utc))
    except SpineError as exc:
        _raise(exc)
    return {"application_id": application_id, **link, "single_use": True}


@router.post("/proof/{token}", status_code=201, response_model=ProofUploadOut, openapi_extra=_UPLOAD_BODY)
async def upload_proof_via_link(
    token: str, request: Request, response: Response,
    data: bytes = Depends(upload_bytes),  # noqa: B008 - before db: the body is read without a pooled connection
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> dict[str, Any]:
    """PUBLIC: the token is the credential. Errors: 404 unknown, 410 used/expired, 413, 415, 409, 429.
    The body is read first (size cap + deadline), so a junk token costs a bounded read before its 404."""
    response.headers.update(_NO_STORE)
    try:
        link = await proof.find_link(db, token, _ip_hash(request))
        meta = await proof.store_via_link(db, link, data, datetime.now(timezone.utc))
    except SpineError as exc:
        _raise(exc, _NO_STORE)
    return {
        "screenshot_id": meta["id"], "application_id": link["application_id"], "mime": meta["mime"],
        "size": meta["size"],
    }


@router.get("/applications/{application_id}/proof", response_model=ProofStateOut, dependencies=AUTH_FIRST)
async def get_proof(
    application_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> dict[str, Any]:
    """How well this application is backed up, plus its screenshots (deleted ones as a note)."""
    if await spine.get_owned_application(db, user.id, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    return {
        "application_id": application_id,
        "proof": (await proof.proof_for(db, user.id, [application_id]))[application_id],
        "screenshots": await proof.screenshot_meta(db, user.id, application_id),
    }


@router.post(
    "/applications/{application_id}/proof/screenshots", status_code=201, response_model=ProofScreenshotOut,
    dependencies=AUTH_FIRST, openapi_extra=_UPLOAD_BODY,
)
async def upload_proof_screenshot(
    application_id: int,
    data: bytes = Depends(upload_bytes),  # noqa: B008 - before db: the body is read without a pooled connection
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> dict[str, Any]:
    """The signed-in user adds a screenshot from the website."""
    try:
        return await proof.store_screenshot(
            db, user.id, application_id, data, actor_for(user), "web", datetime.now(timezone.utc)
        )
    except SpineError as exc:
        _raise(exc)


@router.get(
    "/applications/{application_id}/proof/screenshots/{screenshot_id}", dependencies=AUTH_FIRST,
    response_class=Response, responses={200: {"content": {"image/*": {}}}},
)
async def get_proof_screenshot(
    application_id: int, screenshot_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> Response:
    """The image itself (404 not yours, 410 deleted)."""
    try:
        mime, data = await proof.read_screenshot(db, user.id, application_id, screenshot_id)
    except SpineError as exc:
        _raise(exc)
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    return Response(content=data, media_type=mime, headers=headers)


@router.delete(
    "/applications/{application_id}/proof/screenshots/{screenshot_id}", response_model=ProofScreenshotOut,
    dependencies=AUTH_FIRST,
)
async def delete_proof_screenshot(
    application_id: int, screenshot_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_session_user),  # noqa: B008
) -> dict[str, Any]:
    """Erase the image; the row stays with "Deleted by you, <date>"."""
    try:
        return await proof.delete_screenshot(db, user.id, application_id, screenshot_id, datetime.now(timezone.utc))
    except SpineError as exc:
        _raise(exc)
