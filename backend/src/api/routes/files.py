"""Public file download for application-kit links (S3, owner decisions 2026-10-08).

``GET /api/files/{token}`` has NO login: the assistant's file tool fetches it, and
the token IS the credential. So the rules are strict:

* a token is 32 random bytes; only its SHA-256 is stored (``artifact_links``);
* hash, look up, then ``hmac.compare_digest`` the stored hash (constant time);
* an unknown token is 404 and counts against a per-IP lockout (consulted only
  for tokens that do not resolve, so junk can never lock out a valid link);
* a per-owner rate limit caps downloads;
* the download counter is ONE atomic ``UPDATE ... WHERE downloads_left > 0 AND
  expires_at > now`` - two parallel requests can never take the last download twice;
* the link is dead (410) when spent, expired, or when the CV was edited since
  (the link names a version, and only the LATEST version of a kind may leave);
* ``Cache-Control: no-store``, ``Referrer-Policy: no-referrer``, ``X-Robots-Tag``;
* the token, its hash and the CV text are NEVER logged (the access log and Sentry
  rewrite the path to ``/api/files/[redacted]``).

A link download is NOT a ``cv_seen``: the assistant fetched the file, not the user.
Each served download writes one ``document_uploaded`` (slice 29: the sha256 of the
exact bytes, host ``None``) so a receipt can name the file; a failed write is logged
and the file is still served.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.dependencies import get_request_db
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications import kit, spine
from src.services.auth import rate_limit as auth_rate_limit
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["files"])

_NO_STORE = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, noarchive",
}
GONE_DETAIL = "link expired - ask your assistant to get the kit again"
CHANGED_DETAIL = "this CV was changed - get the kit again"


def _ip_hash(request: Request) -> str:
    """A hashed client address: the rate-limit key and the log token. The raw IP
    is personal data and never stored. ``X-Forwarded-For`` only when the operator
    set ``JOB360_TRUST_PROXY=1`` (otherwise it is attacker-controlled)."""
    ip = request.client.host if request.client else "unknown"
    if os.getenv("JOB360_TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        ip = forwarded or ip
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


def _refuse(
    request: Request, status: int, reason: str, user_id: Optional[str] = None, detail: Optional[str] = None
) -> HTTPException:
    """Log one refused download (reason is a closed word) and build the error."""
    get_audit_logger().warning(
        "file_download_refused",
        extra={
            "event": "file_download_refused", "status": status, "reason": reason, "result": "refused",
            "user_id": safe_log_value(user_id) if user_id else None, "client": _ip_hash(request),
        },
    )
    details = {404: "not found", 410: GONE_DETAIL, 429: "too many requests"}
    return HTTPException(status_code=status, detail=detail or details.get(status, reason), headers=_NO_STORE)


@router.get("/files/{token}")
async def download_file(
    token: str, request: Request, db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> Response:
    """Serve one kit file. See the module docstring for the rules."""
    ip = _ip_hash(request)
    bad_key = f"files_bad:{ip}"

    def _bad_token(reason: str) -> HTTPException:
        # The lockout is consulted only for a token that does NOT resolve (same
        # order as the personal-bearer throttle in auth_deps): behind the Next
        # rewrite every caller can share the proxy's address, so a lock checked
        # BEFORE the lookup would let one junk client 429 every valid link.
        if auth_rate_limit.is_locked(bad_key, max_failures=settings.FILE_BAD_TOKEN_MAX_PER_MIN, window_seconds=60):
            return _refuse(request, 429, "locked_out")
        auth_rate_limit.record_failure(bad_key)
        return _refuse(request, 404, reason)

    if not kit.TOKEN_RE.match(token):
        raise _bad_token("bad_shape")

    computed = kit.hash_token(token)
    cur = await db._db.execute(
        "SELECT id, token_hash, user_id, application_id, artifact_id, version_no, fmt, created_by "
        "FROM artifact_links WHERE token_hash = ?",
        (computed,),
    )
    found = await cur.fetchone()
    row: Optional[dict[str, Any]] = dict(found) if found else None
    if row is None or not hmac.compare_digest(str(row["token_hash"]), computed):
        raise _bad_token("unknown_token")
    # Download cap keyed by the link's OWNER, not the address: junk never spends
    # it, and one user's downloads never 429 another's behind a shared proxy IP.
    if not auth_rate_limit.check_and_record(
        f"files:{row['user_id']}", max_in_window=settings.FILE_DOWNLOADS_MAX_PER_MIN, window_seconds=60
    ):
        raise _refuse(request, 429, "rate_limited", str(row["user_id"]))

    user_id = str(row["user_id"])
    now_iso = datetime.now(timezone.utc).isoformat()
    # ONE atomic statement spends a download; zero rows = spent or expired.
    spent = await db._db.execute(
        "UPDATE artifact_links SET downloads_left = downloads_left - 1 "
        "WHERE id = ? AND downloads_left > 0 AND expires_at > ?",
        (row["id"], now_iso),
    )
    await db._db.commit()
    if not spent.rowcount:
        raise _refuse(request, 410, "spent_or_expired", user_id)

    artifact = await spine.get_artifact(db, user_id, int(row["application_id"]), int(row["artifact_id"]))
    latest = (
        await spine.latest_artifact(db, user_id, int(row["application_id"]), artifact["kind"]) if artifact else None
    )
    if artifact is None or latest is None or latest["id"] != artifact["id"]:
        raise _refuse(request, 410, "cv_changed", user_id, CHANGED_DETAIL)

    from src.services.applications import uploads  # noqa: PLC0415
    from src.services.tailoring import cv_render  # noqa: PLC0415
    from src.services.tailoring import docx as docx_render  # noqa: PLC0415 - heavy, lazy (rule #16)

    kind = str(artifact["kind"])
    title = "Curriculum Vitae" if kind == "cv" else "Cover Letter"
    fmt = "docx" if row["fmt"] == "docx" else "pdf"
    if fmt == "docx":
        content = await asyncio.to_thread(docx_render.render_docx, artifact["text"], title=title)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        renderer_version = docx_render.RENDERER_VERSION
    else:
        content = await cv_render.render_document_pdf(user_id, kind, artifact["text"])
        media_type = "application/pdf"
        renderer_version = cv_render.RENDERER_VERSION
    app_row = await spine.get_owned_application(db, user_id, int(row["application_id"])) or {}
    stem = await kit.download_stem(
        user_id, str(app_row.get("job_company") or ""), kind, int(artifact["version_no"])
    )
    # Slice 29: one document_uploaded per serve, the sha256 of exactly these bytes (no host: a link).
    # Last DB write of the request, and it never raises, so the download is served whatever happens.
    await uploads.record(
        db, user_id=user_id, application_id=int(row["application_id"]), kind=kind, artifact=artifact,
        data=content, fmt=fmt, renderer_version=renderer_version, host=None,
        recorded_by=str(row.get("created_by") or "web"),
    )
    get_audit_logger().info(
        "file_download",
        extra={
            "event": "file_download", "user_id": safe_log_value(user_id), "application_id": row["application_id"],
            "artifact_id": row["artifact_id"], "version": artifact["version_no"], "status": 200,
            "result": "ok", "client": ip,
        },
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"', **_NO_STORE},
    )
