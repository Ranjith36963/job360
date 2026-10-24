"""``document_uploaded``: the exact file an application form was sent (gap roadmap slice 29).

A receipt used to guess its CV (the newest saved one) and could not say which FILE
left Job360. Now each place Job360 hands a document out writes one server-only
``document_uploaded`` event:

* the cloud browser's upload (``routes/browser.py`` ``_files``), with the form's host;
* the kit's file link (``GET /api/files/{token}``), once per download, host ``None``.

Payload: ``{kind, artifact_id, version, sha256, fmt, renderer_version, host}`` where
``sha256`` is the hash of the EXACT bytes served. A hash only: never the bytes, the
text, the file name or the link token. An assistant cannot write it (``record_event``
refuses :data:`SERVER_ONLY_EVENTS`), so a receipt can trust it.

``record_application`` reads :func:`uploads_since_last_receipt` to name the uploaded
artifacts and freeze them as the receipt's ``files`` (``spine.record_receipt``).

A write failure never breaks the upload or the download: :func:`record` logs
``document_uploaded_failed`` with ids only and returns ``None``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional

from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

EVENT_TYPE = "document_uploaded"
SERVER_ONLY_EVENTS = frozenset({EVENT_TYPE})
KINDS = ("cv", "cover_letter")


def sha256_bytes(data: bytes) -> str:
    """The hash a served file is identified by: sha256 of its exact bytes, hex."""
    return hashlib.sha256(data).hexdigest()


async def record(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    kind: str,
    artifact: Mapping[str, Any],
    data: bytes,
    fmt: str,
    renderer_version: str,
    host: Optional[str],
    recorded_by: str,
) -> Optional[int]:
    """Append one ``document_uploaded`` for ``data`` (the bytes just served). Returns the
    event id, or ``None`` when the write failed (logged, never raised)."""
    from src.services.applications import spine  # noqa: PLC0415 - spine reads this module lazily too

    try:
        payload: dict[str, Any] = {
            "kind": kind, "artifact_id": int(artifact["id"]), "sha256": sha256_bytes(data),
            "fmt": fmt, "renderer_version": renderer_version, "host": host or None,
        }
        if artifact.get("version_no") is not None:
            payload["version"] = int(artifact["version_no"])
        async with db._db.transaction():
            # Read the open block BEFORE the upload lands: afterwards `open_block` already reads it as cleared.
            stale_ask_id = await _failed_upload_ask_id(db, user_id, application_id)
            result = await spine.append_event(
                db, user_id=user_id, application_id=application_id, event_type=EVENT_TYPE, payload=payload,
                occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=recorded_by,
            )
            event_id = int(result["event_id"])
            if stale_ask_id is not None:
                await _close_failed_upload_ask(db, user_id, application_id, stale_ask_id, recorded_by)
    except Exception:  # noqa: BLE001 - the upload / download goes ahead whatever happens here
        get_audit_logger().warning(
            "document_uploaded_failed",
            extra={
                "event": "document_uploaded_failed", "user_id": safe_log_value(user_id),
                "application_id": int(application_id), "kind": safe_log_value(kind, max_len=20),
                "artifact_id": _int_or_none(artifact.get("id")), "result": "error",
            },
        )
        return None
    get_audit_logger().info(
        "document_uploaded",
        extra={
            "event": "document_uploaded", "user_id": safe_log_value(user_id),
            "application_id": int(application_id), "kind": safe_log_value(kind, max_len=20),
            "artifact_id": int(artifact["id"]), "fmt": safe_log_value(fmt, max_len=10),
            "event_id": event_id, "via": "browser" if host else "link", "result": "ok",
        },
    )
    return event_id


async def _failed_upload_ask_id(db: JobDatabase, user_id: str, application_id: int) -> Optional[int]:
    """The Needs-you ask id of this application's open ``upload_failed`` block, else None (any other
    reason, no block, or a block that opened no ask)."""
    from src.services.applications import blocked  # noqa: PLC0415 - blocked imports spine

    block = await blocked.open_block(db, user_id, application_id)
    if block and block["reason"] == "upload_failed" and isinstance(block["ask_id"], int):
        return int(block["ask_id"])
    return None


async def _close_failed_upload_ask(
    db: JobDatabase, user_id: str, application_id: int, ask_id: int, recorded_by: str
) -> None:
    """The later upload worked: withdraw the block's ask the way ``unblocked`` does (blocked.after_append)."""
    from src.services.applications import asks as asks_service  # noqa: PLC0415 - asks imports spine
    from src.services.applications.spine import SpineError  # noqa: PLC0415

    try:
        await asks_service.withdraw_ask(db, user_id, ask_id, recorded_by, "the later upload worked")
    except SpineError:  # already answered or withdrawn in between: nothing left to close
        return
    get_audit_logger().info(
        "upload_failed_ask_closed",
        extra={
            "event": "upload_failed_ask_closed", "user_id": safe_log_value(user_id),
            "application_id": int(application_id), "ask_id": int(ask_id), "result": "ok",
        },
    )


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def uploads_since_last_receipt(db: JobDatabase, user_id: str, application_id: int) -> list[dict[str, Any]]:
    """This application's ``document_uploaded`` events, newest first, recorded after its newest
    ``applied`` event (an earlier receipt's files belong to that submission). Each row:
    ``{event_id, kind, artifact_id, sha256, fmt, host}``; an unreadable payload is skipped."""
    cur = await db._db.execute(
        "SELECT e.id, e.payload FROM application_events e "
        "WHERE e.user_id = ? AND e.application_id = ? AND e.event_type = 'document_uploaded' "
        "AND e.id > COALESCE((SELECT MAX(a.id) FROM application_events a "
        "WHERE a.user_id = ? AND a.application_id = ? AND a.event_type = 'applied'), 0) "
        "ORDER BY e.id DESC",
        (user_id, application_id, user_id, application_id),
    )
    out: list[dict[str, Any]] = []
    for raw in await cur.fetchall():
        row = dict(raw)
        try:
            p = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"] or {})
            artifact_id = int(p["artifact_id"])
        except (TypeError, ValueError, KeyError):
            continue
        if p.get("kind") not in KINDS or not isinstance(p.get("sha256"), str):
            continue
        out.append({
            "event_id": int(row["id"]), "kind": p["kind"], "artifact_id": artifact_id, "sha256": p["sha256"],
            "fmt": str(p.get("fmt") or ""), "host": p.get("host") or None,
        })
    return out


def file_entry(upload: Mapping[str, Any]) -> dict[str, Any]:
    """What a receipt freezes of one upload (``files[]``)."""
    return {
        "kind": upload["kind"], "artifact_id": upload["artifact_id"], "sha256": upload["sha256"],
        "fmt": upload["fmt"], "event_id": upload["event_id"],
    }
