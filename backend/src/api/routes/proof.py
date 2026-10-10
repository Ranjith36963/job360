"""Proof of application (S7, owner decision 2026-10-10): screenshots + single-use upload links.

``POST /api/proof/{token}`` has NO login: an assistant's file tool posts the image
there, and the token IS the credential (single use, 5 minutes, only its hash is
stored). Minting the link is per-user and scoped by ``user.id``. Pasted confirmation text is a
plain ``proof_text`` event through ``record_event``. See ``services/applications/proof.py``.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.routes.files import _NO_STORE, _ip_hash
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import proof
from src.services.applications.spine import SpineError

router = APIRouter(tags=["proof"])
_short_db = asynccontextmanager(get_request_db)  # a connection held for one query batch, never across the body read
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


async def _read_body(request: Request) -> bytes:
    """The upload body, bounded by the size cap and ``PROOF_UPLOAD_READ_SECONDS``; run only once the token is live."""
    try:
        try:
            return await asyncio.wait_for(_read_file(request), settings.PROOF_UPLOAD_READ_SECONDS)
        except asyncio.TimeoutError:
            raise SpineError(408, "upload took too long") from None
    except SpineError as exc:
        _raise(exc, _NO_STORE)


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
async def upload_proof_via_link(token: str, request: Request, response: Response) -> dict[str, Any]:
    """PUBLIC: the token is the credential. Errors: 404 unknown, 410 used/expired, 413, 415, 409, 429, 408.
    Token first (short connection), then the body read holding none, then a second short one to store."""
    response.headers.update(_NO_STORE)
    now = datetime.now(timezone.utc)
    try:
        async with _short_db() as db:
            link = await proof.find_link(db, token, _ip_hash(request), now)
    except SpineError as exc:
        _raise(exc, _NO_STORE)
    data = await _read_body(request)
    try:
        async with _short_db() as db:
            meta = await proof.store_via_link(db, link, data, datetime.now(timezone.utc))
    except SpineError as exc:
        _raise(exc, _NO_STORE)
    return {
        "screenshot_id": meta["id"], "application_id": link["application_id"], "mime": meta["mime"],
        "size": meta["size"],
    }
