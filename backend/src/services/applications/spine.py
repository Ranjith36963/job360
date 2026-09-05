"""The application spine's DB-facing core (spec.md R1-R11).

Every write here goes through ``from src.repositories import pg as aiosqlite``
indirectly via ``db._db`` (the guarded accessor over ``db._conn`` — C5; the
same connection every route already depends on) — SQLite-shaped SQL,
``pg.py``'s ``translate()`` rewrites it for Postgres at execute time. No
route or MCP tool talks to the tables directly; they all come through this
module, so there is exactly one place that can get the event log wrong.

Append-only, for real: nothing in this file issues ``UPDATE``/``DELETE``
against ``application_events`` or ``application_artifacts`` (grep-guarded by
``tests/test_application_spine.py::test_events_are_append_only``). The only
``UPDATE`` targets are the ``applications`` CACHE SLOT (status/fit — S7: an
update of the slot is explicitly not history) and, once, a legacy
``application_receipts`` row picking up its ``application_id``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional

from src.core import settings
from src.repositories import pg
from src.services.applications.status import replay_status, stage_for_status
from src.services.auth import rate_limit
from src.utils.logger import get_audit_logger

if TYPE_CHECKING:  # pragma: no cover — type-only; avoids a runtime import of
    # database.py (rule #16-adjacent: this module stays light at import time).
    from src.repositories.database import JobDatabase


class SpineError(Exception):
    """Domain error a route turns into ``HTTPException(status_code, detail)``."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ── Pure validation helpers (no DB — hit directly by tests) ────────────────


def validate_event_type(event_type: str) -> None:
    """R7 — closed set in code. 422 naming the allowed list on a miss."""
    allowed = (
        set(settings.APPLICATION_STATUS_EVENT_TYPES)
        | set(settings.APPLICATION_NOTE_EVENT_TYPES)
        | set(settings.APPLICATION_EXTRA_EVENT_TYPES)
    )
    if event_type not in allowed:
        raise SpineError(
            422,
            f"unknown event_type {event_type!r}; allowed: {sorted(allowed)}",
        )


def payload_bytes(payload: Mapping[str, Any]) -> int:
    """The SERIALISED size of an event payload, in bytes — what the column
    actually costs, not ``len()`` of the Python object (S5)."""
    return len(json.dumps(payload).encode("utf-8"))


def validate_payload(payload: Any) -> dict[str, Any]:
    """S5 — payload must be a JSON OBJECT (never a list/scalar), size-capped
    on the SERIALISED form, because that is what the column costs."""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise SpineError(422, "payload must be a JSON object")
    size = payload_bytes(payload)
    if size > settings.APPLICATION_EVENT_PAYLOAD_MAX_BYTES:
        raise SpineError(
            422,
            f"payload exceeds APPLICATION_EVENT_PAYLOAD_MAX_BYTES "
            f"({settings.APPLICATION_EVENT_PAYLOAD_MAX_BYTES} bytes)",
        )
    return payload


def parse_occurred_at(raw: Optional[str]) -> str:
    """S6 — ISO-8601 with a timezone, bounded on the future side only."""
    now = datetime.now(timezone.utc)
    if raw is None:
        return now.isoformat()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise SpineError(422, "occurred_at must be ISO-8601 (e.g. 2026-09-04T12:00:00+00:00)") from None
    if parsed.tzinfo is None:
        raise SpineError(422, "occurred_at must include a timezone offset")
    max_future = timedelta(seconds=settings.APPLICATION_EVENT_MAX_FUTURE_SECONDS)
    if parsed > now + max_future:
        raise SpineError(
            422,
            f"occurred_at is more than APPLICATION_EVENT_MAX_FUTURE_SECONDS "
            f"({settings.APPLICATION_EVENT_MAX_FUTURE_SECONDS}s) in the future",
        )
    # B3 fix — normalise to the canonical +00:00 offset (pg.py:178-184's own
    # convention) before storing. `application_events.occurred_at` is a TEXT
    # column ordered lexically (list_events_for_display's ORDER BY occurred_at
    # ASC); two instants recorded under different offsets (e.g. "+05:30" vs
    # "+00:00") do NOT compare correctly as strings even though they compare
    # correctly as instants, so every caller's offset must collapse to the
    # same one before it ever reaches SQL.
    return parsed.astimezone(timezone.utc).isoformat()


# ── Ownership lookups ───────────────────────────────────────────────────────


async def get_application_by_job(db: JobDatabase, user_id: str, job_id: int) -> Optional[dict[str, Any]]:
    """The caller's application for a given job, or ``None`` if they never
    brought it — the lookup ``birth_application``'s upsert-by-read and every
    write-through helper (legacy receipts, MCP ``record_application``) use to
    find the row a raw ``job_id`` maps to."""
    cur = await db._db.execute(
        "SELECT id, status FROM applications WHERE user_id = ? AND job_id = ?", (user_id, job_id)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_owned_application(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """S2 — the only lookup every other function here builds on. A foreign or
    unknown id reads as None (the route turns that into 404, never 403)."""
    cur = await db._db.execute(
        "SELECT * FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


# ── The event log ────────────────────────────────────────────────────────────


async def _events_for_replay(db: JobDatabase, application_id: int) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, event_type, corrects_event_id, recorded_at FROM application_events "
        "WHERE application_id = ?",
        (application_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def list_events_for_display(db: JobDatabase, application_id: int) -> list[dict[str, Any]]:
    """Timeline order — ``occurred_at`` (backdated events included), NOT the
    ``recorded_at`` order the status recompute uses."""
    cur = await db._db.execute(
        "SELECT id, event_type, detail, payload, occurred_at, recorded_at, recorded_by, corrects_event_id "
        "FROM application_events WHERE application_id = ? ORDER BY occurred_at ASC, id ASC",
        (application_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    superseded_ids = {r["corrects_event_id"] for r in rows if r.get("corrects_event_id") is not None}
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "detail": r["detail"],
                "payload": json.loads(r["payload"] or "{}"),
                "occurred_at": r["occurred_at"],
                "recorded_at": r["recorded_at"],
                "recorded_by": r["recorded_by"],
                "corrects_event_id": r["corrects_event_id"],
                "superseded": r["id"] in superseded_ids,
            }
        )
    return out


async def append_event(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    event_type: str,
    occurred_at: str,
    recorded_by: str,
    detail: str = "",
    payload: Optional[dict[str, Any]] = None,
    corrects_event_id: Optional[int] = None,
) -> dict[str, Any]:
    """R3/R4 — append one event, then recompute + write-through the status
    cache (and its legacy `stage` projection) in the SAME logical operation.

    No caller of this module ever computes `status` any other way — this is
    the ONE place `applications.status`/`stage`/`last_event_at` are written.

    B5 — ``corrects_event_id``, when given, must name a real event on THIS
    SAME application; otherwise a caller could silently supersede a stranger's
    event (or a typo'd id that never existed) and ``list_events_for_display``
    would mark the wrong thing (or nothing) as superseded. 422, not 404 — this
    is a body-field validation error, not a missing resource.
    """
    if corrects_event_id is not None:
        cur = await db._db.execute(
            "SELECT 1 FROM application_events WHERE id = ? AND application_id = ?",
            (corrects_event_id, application_id),
        )
        if (await cur.fetchone()) is None:
            raise SpineError(
                422,
                f"corrects_event_id {corrects_event_id} does not exist on this application",
            )

    now = datetime.now(timezone.utc).isoformat()
    payload_json = json.dumps(payload or {})
    cur = await db._db.execute(
        "INSERT INTO application_events "
        "(user_id, application_id, event_type, detail, payload, occurred_at, recorded_at, "
        " recorded_by, corrects_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, application_id, event_type, detail, payload_json, occurred_at, now, recorded_by, corrects_event_id),
    )
    event_id = cur.lastrowid

    new_status = replay_status(await _events_for_replay(db, application_id))
    stage = stage_for_status(new_status)
    if stage is not None:
        await db._db.execute(
            "UPDATE applications SET status = ?, last_event_at = ?, updated_at = ?, stage = ? WHERE id = ?",
            (new_status, now, now, stage, application_id),
        )
    else:
        # `considering` has no legacy stage — leave the column untouched (R4).
        await db._db.execute(
            "UPDATE applications SET status = ?, last_event_at = ?, updated_at = ? WHERE id = ?",
            (new_status, now, now, application_id),
        )
    await db._db.commit()
    get_audit_logger().info(
        "application_event_recorded",
        extra={
            "event": "application_event_recorded",
            "application_id": application_id,
            "event_type": event_type,
            "recorded_by": recorded_by,
        },
    )
    return {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "recorded_at": now,
        "recorded_by": recorded_by,
        "status": new_status,
    }


# ── R1/R2 — birth at bring_job ───────────────────────────────────────────────


async def birth_application(
    db: JobDatabase, *, user_id: str, job_id: int, job_row: Mapping[str, Any], recorded_by: str
) -> dict[str, Any]:
    """Upsert-by-read: bringing the same job twice returns the SAME
    application_id and appends NO second `brought` event."""
    existing = await get_application_by_job(db, user_id, job_id)
    if existing is not None:
        return {"application_id": existing["id"], "status": existing["status"], "existing": True}

    now = datetime.now(timezone.utc).isoformat()
    cur = await db._db.execute(
        "INSERT INTO applications "
        "(user_id, job_id, status, stage, job_title, job_company, job_location, job_url, job_source, "
        " job_description_snapshot, snapshot_at, created_at, updated_at, last_event_at) "
        "VALUES (?, ?, 'considering', 'considering', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id, job_id,
            job_row.get("title") or "", job_row.get("company") or "", job_row.get("location") or "",
            job_row.get("apply_url") or "", job_row.get("source") or "", job_row.get("description") or "",
            now, now, now, now,
        ),
    )
    application_id = int(cur.lastrowid or 0)
    await append_event(
        db, user_id=user_id, application_id=application_id, event_type="brought",
        occurred_at=now, recorded_by=recorded_by,
    )
    get_audit_logger().info(
        "application_born",
        extra={"event": "application_born", "application_id": application_id, "user_id": user_id, "job_id": job_id},
    )
    return {"application_id": application_id, "status": "considering", "existing": False}


# ── R5 — artifacts, versioned forever ────────────────────────────────────────


async def _artifact_version_count_and_max(db: JobDatabase, application_id: int, kind: str) -> tuple[int, int]:
    cur = await db._db.execute(
        "SELECT COUNT(*), COALESCE(MAX(version_no), 0) FROM application_artifacts "
        "WHERE application_id = ? AND kind = ?",
        (application_id, kind),
    )
    row = await cur.fetchone()
    return int(row[0]), int(row[1])


async def save_artifact(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    kind: str,
    text: str,
    made_by: str,
    label: str = "",
    model: Optional[str] = None,
) -> dict[str, Any]:
    """R5 — save a NEW version of an artifact (never an update): allocates
    ``version_no = MAX(version_no) + 1`` per ``(application_id, kind)`` inside
    the insert, retrying on a ``UNIQUE`` race (C4 — caught by ``pg.
    IntegrityError``, not a string sniff over a bare ``Exception``), and
    appends the ``artifact_saved`` event that names the new id/kind/version.
    """
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")
    if kind not in settings.APPLICATION_ARTIFACT_KINDS:
        raise SpineError(422, f"kind must be one of {settings.APPLICATION_ARTIFACT_KINDS}")
    if len(text) > settings.APPLICATION_ARTIFACT_MAX_CHARS:
        raise SpineError(
            422,
            f"text exceeds APPLICATION_ARTIFACT_MAX_CHARS ({settings.APPLICATION_ARTIFACT_MAX_CHARS} chars)",
        )

    # Lazy: profile storage pulls the scoring stack transitively (rule #16).
    from src.services.profile.storage import current_profile_version_id  # noqa: PLC0415

    profile_version = current_profile_version_id(user_id)
    now = datetime.now(timezone.utc).isoformat()

    artifact_id: Optional[int] = None
    version_no = 0
    for _attempt in range(5):
        count, max_version = await _artifact_version_count_and_max(db, application_id, kind)
        if count >= settings.APPLICATION_ARTIFACT_MAX_VERSIONS:
            raise SpineError(
                429,
                f"too many {kind!r} versions; cap is APPLICATION_ARTIFACT_MAX_VERSIONS "
                f"({settings.APPLICATION_ARTIFACT_MAX_VERSIONS})",
            )
        version_no = max_version + 1
        try:
            ins = await db._db.execute(
                "INSERT INTO application_artifacts "
                "(user_id, application_id, kind, version_no, text, made_by, model, profile_version, "
                " label, chars, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, application_id, kind, version_no, text, made_by, model,
                    profile_version, label or "", len(text), now,
                ),
            )
        except pg.IntegrityError:
            # C4 — a real UNIQUE(application_id, kind, version_no) violation
            # (a concurrent save landed the same version_no first); retry with
            # a freshly-read MAX(version_no). Any OTHER integrity error (a
            # genuine bug, e.g. a bad foreign value) is exactly what this
            # exception type means too, so a bounded retry loop still bails
            # out via the `artifact_id is None` check below rather than
            # looping forever on something that will never succeed.
            await db._db.rollback()
            continue
        artifact_id = int(ins.lastrowid or 0)
        break
    if artifact_id is None:
        raise SpineError(429, "could not allocate a version number under concurrent writes — retry")

    event = await append_event(
        db, user_id=user_id, application_id=application_id, event_type="artifact_saved",
        payload={"artifact_id": artifact_id, "kind": kind, "version_no": version_no},
        occurred_at=now, recorded_by=made_by,
    )
    get_audit_logger().info(
        "artifact_saved",
        extra={
            "event": "artifact_saved", "artifact_id": artifact_id, "kind": kind,
            "version_no": version_no, "chars": len(text),
        },
    )
    return {
        "artifact_id": artifact_id, "kind": kind, "version_no": version_no, "chars": len(text),
        "made_by": made_by, "model": model, "profile_version": profile_version, "created_at": now,
        "event_id": event["event_id"],
    }


async def get_artifact(
    db: JobDatabase, user_id: str, application_id: int, artifact_id: int
) -> Optional[dict[str, Any]]:
    """One artifact version IN FULL (never capped/truncated) — the one-version
    read `get_application`'s byte-capped list defers to (spec R11). ``None``
    for a foreign/unknown application OR artifact id; the route turns either
    into 404 (S2)."""
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        return None
    cur = await db._db.execute(
        "SELECT id, kind, version_no, text, made_by, model, profile_version, label, chars, created_at "
        "FROM application_artifacts WHERE id = ? AND application_id = ?",
        (artifact_id, application_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _list_artifacts(db: JobDatabase, application_id: int, *, with_text: bool) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, kind, version_no, text, made_by, model, profile_version, label, chars, created_at "
        "FROM application_artifacts WHERE application_id = ? ORDER BY created_at DESC, id DESC",
        (application_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    cap_bytes = max(1, settings.EXPORT_HISTORY_MAX_BYTES // 4)
    used = 0
    out = []
    for r in rows:
        entry = {
            "id": r["id"], "kind": r["kind"], "version_no": r["version_no"], "made_by": r["made_by"],
            "model": r.get("model"), "profile_version": r.get("profile_version"), "label": r.get("label") or "",
            "chars": r["chars"], "created_at": r["created_at"], "text": None, "truncated": False,
        }
        if with_text:
            size = len((r["text"] or "").encode("utf-8"))
            if used + size <= cap_bytes:
                entry["text"] = r["text"]
                used += size
            else:
                entry["truncated"] = True
        out.append(entry)
    return out


# ── R6 — the fit verdict is stored, never computed ───────────────────────────


async def save_fit(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    recorded_by: str,
    score: Optional[int],
    verdict: Optional[str],
    gaps: Optional[list[str]],
    reasoning: Optional[str],
) -> dict[str, Any]:
    """R6 — overwrite the fit-verdict SLOT on ``applications`` (the current
    answer) and append a ``fit_judged`` event carrying the whole verdict (the
    version history) — S7's "update of the slot is not history" case. Never
    calls a scorer, matcher or judge; the verdict is always the caller's own
    (VISION rule 4)."""
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")

    now = datetime.now(timezone.utc).isoformat()
    gaps_list = gaps or []
    gaps_json = json.dumps(gaps_list)
    await db._db.execute(
        "UPDATE applications SET fit_score = ?, fit_verdict = ?, fit_gaps = ?, fit_reasoning = ?, "
        "fit_recorded_by = ?, fit_recorded_at = ?, updated_at = ? WHERE id = ?",
        (score, verdict, gaps_json, reasoning, recorded_by, now, now, application_id),
    )
    event = await append_event(
        db, user_id=user_id, application_id=application_id, event_type="fit_judged",
        payload={"score": score, "verdict": verdict, "gaps": gaps_list, "reasoning": reasoning},
        occurred_at=now, recorded_by=recorded_by,
    )
    get_audit_logger().info("fit_saved", extra={"event": "fit_saved", "application_id": application_id})
    return {
        "application_id": application_id,
        "fit": {
            "score": score, "verdict": verdict, "gaps": gaps_list, "reasoning": reasoning,
            "recorded_by": recorded_by, "recorded_at": now,
        },
        "event_id": event["event_id"],
    }


# ── R8 — record_application, the rich receipt ────────────────────────────────


async def _resolve_receipt_artifact(
    db: JobDatabase, user_id: str, application_id: int, kind: str, artifact_id: Optional[int]
) -> Optional[dict[str, Any]]:
    if artifact_id is not None:
        cur = await db._db.execute(
            "SELECT id, version_no, text FROM application_artifacts "
            "WHERE id = ? AND application_id = ? AND user_id = ? AND kind = ?",
            (artifact_id, application_id, user_id, kind),
        )
        row = await cur.fetchone()
        if row is None:
            raise SpineError(404, f"{kind} artifact not found")
        return dict(row)
    cur = await db._db.execute(
        "SELECT id, version_no, text FROM application_artifacts "
        "WHERE application_id = ? AND user_id = ? AND kind = ? ORDER BY version_no DESC LIMIT 1",
        (application_id, user_id, kind),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def record_receipt(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    recorded_by: str,
    channel: str = "",
    note: str = "",
    confirmation: str = "",
    answers: Optional[list[dict[str, str]]] = None,
    fields_filled: Optional[dict[str, Any]] = None,
    cv_artifact_id: Optional[int] = None,
    cover_letter_artifact_id: Optional[int] = None,
    applied_at: Optional[str] = None,
) -> dict[str, Any]:
    """R8/S3 — the rich receipt: freeze the named artifact versions (or the
    newest, if none named — ``_resolve_receipt_artifact``) as text, and
    append the ``applied`` event. ``profile_version`` is stamped from the
    CALLER's current profile (B6 fix — this used to be hardcoded ``None``,
    unlike the legacy ``/receipts/{job_id}`` route which has always stamped
    it via ``current_profile_version_id``).
    """
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")

    cv_artifact = await _resolve_receipt_artifact(db, user_id, application_id, "cv", cv_artifact_id)
    cl_artifact = await _resolve_receipt_artifact(
        db, user_id, application_id, "cover_letter", cover_letter_artifact_id
    )

    # Lazy: profile storage pulls the scoring stack transitively (rule #16),
    # same reasoning as save_artifact's identical import above.
    from src.services.profile.storage import current_profile_version_id  # noqa: PLC0415

    profile_version = current_profile_version_id(user_id)
    now = datetime.now(timezone.utc).isoformat()
    sent_at = applied_at or now
    answers_json = json.dumps(answers or [])
    fields_json = json.dumps(fields_filled or {})

    cur = await db._db.execute(
        "INSERT INTO application_receipts "
        "(user_id, job_id, sent_at, job_title, job_company, job_location, job_apply_url, job_source, "
        " job_description, cv_text, cv_origin, cover_letter_text, cover_letter_origin, profile_version, "
        " channel, note, created_at, application_id, cv_artifact_id, cover_letter_artifact_id, answers, "
        " fields_filled, confirmation, recorded_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id, app_row["job_id"], sent_at,
            app_row.get("job_title") or "", app_row.get("job_company") or "",
            app_row.get("job_location") or "", app_row.get("job_url") or "",
            app_row.get("job_source") or "", app_row.get("job_description_snapshot") or "",
            cv_artifact["text"] if cv_artifact else None, "artifact" if cv_artifact else None,
            cl_artifact["text"] if cl_artifact else None, "artifact" if cl_artifact else None,
            profile_version, channel or "", note or "", now,
            application_id, cv_artifact["id"] if cv_artifact else None,
            cl_artifact["id"] if cl_artifact else None, answers_json, fields_json,
            confirmation or "", recorded_by,
        ),
    )
    receipt_id = int(cur.lastrowid or 0)

    event = await append_event(
        db, user_id=user_id, application_id=application_id, event_type="applied",
        payload={"receipt_id": receipt_id, "channel": channel or ""},
        occurred_at=sent_at, recorded_by=recorded_by,
    )
    get_audit_logger().info(
        "receipt_created",
        extra={
            "event": "receipt_created", "receipt_id": receipt_id,
            "cv_artifact_id": cv_artifact["id"] if cv_artifact else None,
        },
    )
    return {
        "receipt_id": receipt_id, "sent_at": sent_at,
        "cv_artifact_id": cv_artifact["id"] if cv_artifact else None,
        "cv_version_no": cv_artifact["version_no"] if cv_artifact else None,
        "cover_letter_artifact_id": cl_artifact["id"] if cl_artifact else None,
        "channel": channel or "", "confirmation": confirmation or "",
        "url": f"/applications/{application_id}", "event_id": event["event_id"],
    }


async def write_through_legacy_receipt(
    db: JobDatabase, *, user_id: str, application_id: int, receipt_id: int, sent_at: str, note: str = ""
) -> dict[str, Any]:
    """The legacy `POST /receipts/{job_id}` write-through (spec R8): appends
    the `applied` event for a receipt whose `application_id` the caller
    already resolved and passed into `db.insert_receipt`'s own INSERT — never
    backfilled by an UPDATE (tests/test_receipts.py::
    test_receipts_are_append_only greps `backend/src/` for any UPDATE/DELETE
    against `application_receipts` and must stay green).
    """
    return await append_event(
        db, user_id=user_id, application_id=application_id, event_type="applied",
        detail=note or "", payload={"receipt_id": receipt_id}, occurred_at=sent_at, recorded_by="web",
    )


# ── R11 — get_application / list_applications ────────────────────────────────


async def _list_receipts_for_application(
    db: JobDatabase, user_id: str, application_id: int, *, include_text: bool = False
) -> list[dict[str, Any]]:
    cols = "id, sent_at, channel, confirmation, cv_artifact_id, cover_letter_artifact_id, note"
    if include_text:
        cols += ", cv_text, cover_letter_text"
    cur = await db._db.execute(
        f"SELECT {cols} FROM application_receipts WHERE user_id = ? AND application_id = ? "  # noqa: S608
        f"ORDER BY sent_at DESC, id DESC",
        (user_id, application_id),
    )
    return [dict(r) for r in await cur.fetchall()]


async def get_application_detail(
    db: JobDatabase, user_id: str, application_id: int, *, with_artifact_text: bool = False
) -> Optional[dict[str, Any]]:
    """R11's full ``GET /applications/{id}`` shape: the job snapshot (plus
    whether the catalog row still exists), the fit slot, every artifact
    version (text omitted unless ``with_artifact_text``), the whole event
    timeline, and receipts. ``None`` for a foreign/unknown id (404, S2)."""
    # Lazy: contacts.py imports FROM this module (SpineError, append_event,
    # get_owned_application, parse_occurred_at) — a top-level import here
    # would be circular. Slice 4 (docs/plans/2026-09-05-contacts-stats/
    # spec.md R3).
    from src.services.applications.contacts import list_contacts  # noqa: PLC0415

    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        return None
    job_id = app_row["job_id"]
    cur = await db._db.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
    catalog_present = (await cur.fetchone()) is not None

    fit = None
    if app_row.get("fit_recorded_at"):
        fit = {
            "score": app_row.get("fit_score"),
            "verdict": app_row.get("fit_verdict"),
            "gaps": json.loads(app_row.get("fit_gaps") or "[]"),
            "reasoning": app_row.get("fit_reasoning"),
            "recorded_by": app_row.get("fit_recorded_by"),
            "recorded_at": app_row.get("fit_recorded_at"),
        }

    return {
        "id": app_row["id"],
        "job_id": job_id,
        "status": app_row["status"],
        "created_at": app_row["created_at"],
        "updated_at": app_row["updated_at"],
        "last_event_at": app_row.get("last_event_at"),
        "job": {
            "job_title": app_row.get("job_title") or "",
            "job_company": app_row.get("job_company") or "",
            "job_location": app_row.get("job_location") or "",
            "job_url": app_row.get("job_url") or "",
            "job_source": app_row.get("job_source") or "",
            "job_description_snapshot": app_row.get("job_description_snapshot") or "",
            "snapshot_at": app_row.get("snapshot_at"),
            "catalog_present": catalog_present,
        },
        "fit": fit,
        "artifacts": await _list_artifacts(db, application_id, with_text=with_artifact_text),
        "events": await list_events_for_display(db, application_id),
        "receipts": await _list_receipts_for_application(db, user_id, application_id),
        "contacts": await list_contacts(db, user_id, application_id),
    }


async def _count(db: JobDatabase, table: str, col: str, value: Any) -> int:
    cur = await db._db.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (value,))  # noqa: S608 — table/col are constants
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _artifact_counts_by_kind(db: JobDatabase, application_id: int) -> dict[str, int]:
    cur = await db._db.execute(
        "SELECT kind, COUNT(*) FROM application_artifacts WHERE application_id = ? GROUP BY kind",
        (application_id,),
    )
    return {r[0]: int(r[1]) for r in await cur.fetchall()}


async def list_applications(
    db: JobDatabase,
    user_id: str,
    *,
    status: Optional[str] = None,
    updated_since: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """R11's ``GET /applications`` list: summaries only (snapshot fields,
    status, per-kind counts) — no event/artifact bodies. Newest
    ``last_event_at`` first; optionally filtered by ``status`` and/or
    ``updated_since``."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where = ["user_id = ?"]
    params: list[Any] = [user_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if updated_since:
        where.append("updated_at >= ?")
        params.append(updated_since)
    where_sql = " AND ".join(where)

    cur = await db._db.execute(
        f"SELECT COUNT(*) FROM applications WHERE {where_sql}", params  # noqa: S608 — where_sql built from constants
    )
    total = int((await cur.fetchone())[0])

    cur = await db._db.execute(
        f"SELECT id, job_id, job_title, job_company, status, last_event_at FROM applications "  # noqa: S608
        f"WHERE {where_sql} ORDER BY last_event_at DESC NULLS LAST, id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    rows = [dict(r) for r in await cur.fetchall()]

    out = []
    for r in rows:
        app_id = r["id"]
        out.append(
            {
                "id": app_id, "job_id": r["job_id"], "job_title": r["job_title"] or "",
                "job_company": r["job_company"] or "", "status": r["status"],
                "last_event_at": r.get("last_event_at"),
                "events": await _count(db, "application_events", "application_id", app_id),
                "artifacts": await _artifact_counts_by_kind(db, app_id),
                "receipts": await _count(db, "application_receipts", "application_id", app_id),
            }
        )
    return {"applications": out, "total": total}


# ── R9 — whats_new ────────────────────────────────────────────────────────────


async def whats_new(
    db: JobDatabase,
    user_id: str,
    *,
    since: Optional[str] = None,
    after_id: Optional[int] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """R9 — what changed across ALL of the caller's applications since a
    cursor. Paged and ordered by ``recorded_at`` (when Job360 learned it),
    NEVER ``occurred_at`` (when it happened in the world, which may be
    backdated) — a cursor on the latter could silently skip a backdated
    event forever. ``(recorded_at, id)`` is a real keyset pair (B4 fix): the
    ``after_id`` half only fires alongside its own ``recorded_at`` boundary,
    never as an independent predicate, so a row from either side of that
    boundary is never dropped."""
    limit = max(1, min(int(limit), settings.WHATS_NEW_MAX_EVENTS))
    now = datetime.now(timezone.utc).isoformat()
    since_val = since or (
        datetime.now(timezone.utc) - timedelta(days=settings.WHATS_NEW_DEFAULT_WINDOW_DAYS)
    ).isoformat()

    # B4 fix — a REAL keyset cursor. `recorded_at >= since AND id > after_id`
    # as two INDEPENDENT predicates drops any event whose `recorded_at` is
    # <= the page boundary's but whose `id` is higher (a concurrently
    # committed write can easily land in that gap): it satisfies neither half
    # of the AND, so it is skipped on this page AND on the next ("since" moved
    # past it), forever. The correct predicate paginates on the PAIR:
    # everything strictly after (recorded_at, id) in that lexicographic order.
    where = ["user_id = ?"]
    params: list[Any] = [user_id]
    if after_id is not None:
        where.append("(recorded_at > ? OR (recorded_at = ? AND id > ?))")
        params.extend([since_val, since_val, after_id])
    else:
        where.append("recorded_at >= ?")
        params.append(since_val)
    where_sql = " AND ".join(where)

    cur = await db._db.execute(
        f"SELECT id, application_id, event_type, detail, payload, occurred_at, recorded_at, "  # noqa: S608
        f"recorded_by, corrects_event_id FROM application_events WHERE {where_sql} "
        f"ORDER BY recorded_at ASC, id ASC LIMIT ?",
        [*params, limit + 1],
    )
    rows = [dict(r) for r in await cur.fetchall()]
    truncated = len(rows) > limit
    rows = rows[:limit]

    events = []
    app_ids: list[int] = []
    seen = set()
    for r in rows:
        events.append(
            {
                "id": r["id"], "application_id": r["application_id"], "event_type": r["event_type"],
                "detail": r["detail"], "payload": json.loads(r["payload"] or "{}"),
                "occurred_at": r["occurred_at"], "recorded_at": r["recorded_at"],
                "recorded_by": r["recorded_by"], "corrects_event_id": r["corrects_event_id"],
            }
        )
        if r["application_id"] not in seen:
            seen.add(r["application_id"])
            app_ids.append(r["application_id"])

    applications: list[dict[str, Any]] = []
    if app_ids:
        placeholders = ",".join("?" for _ in app_ids)
        cur = await db._db.execute(
            f"SELECT id, job_title, job_company, status, last_event_at FROM applications "  # noqa: S608
            f"WHERE id IN ({placeholders})",
            app_ids,
        )
        applications = [dict(r) for r in await cur.fetchall()]

    next_since = rows[-1]["recorded_at"] if rows else since_val
    next_after_id = rows[-1]["id"] if rows else after_id

    return {
        "now": now, "since": since_val, "events": events, "applications": applications,
        "next_since": next_since, "next_after_id": next_after_id, "truncated": truncated,
    }


# ── R10 — export_history ──────────────────────────────────────────────────────


async def _artifact_metadata(db: JobDatabase, application_id: int, *, include_text: bool) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, kind, version_no, made_by, model, profile_version, label, chars, created_at, text "
        "FROM application_artifacts WHERE application_id = ? ORDER BY kind, version_no",
        (application_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    out = []
    meta_cols = (
        "id", "kind", "version_no", "made_by", "model", "profile_version",
        "label", "chars", "created_at",
    )
    for r in rows:
        entry = {k: r[k] for k in meta_cols}
        if include_text:
            entry["text"] = r["text"]
        out.append(entry)
    return out


async def _list_profile_edits_for_export(
    db: JobDatabase, user_id: str
) -> tuple[list[dict[str, Any]], bool]:
    """Slice 4 R8/R10 — export_history's ``profile_edits``, oldest first,
    INCLUDING a clear (``value IS NULL``) — unlike
    ``services.profile.edits.current_overlay`` (the live, non-cleared
    overlay), this IS the history: "nothing is deleted — the clear is a
    row" (frozen test ``test_clear_reveals_extraction_and_keeps_the_
    history``).

    BOUNDED like every other blob this export carries (review finding S3):
    the query takes the NEWEST ``EXPORT_HISTORY_MAX_PROFILE_EDITS`` rows
    (``ORDER BY id DESC LIMIT ?``) and the result is reversed so the output
    stays oldest-first. Newest-wins is the right end to keep — the current
    overlay is made of the newest row per path, so an export truncated at the
    old end still explains the profile the caller is looking at. Returns
    ``(rows, truncated)``.
    """
    limit = settings.EXPORT_HISTORY_MAX_PROFILE_EDITS
    cur = await db._db.execute(
        "SELECT path, value, set_by, set_at FROM profile_edits WHERE user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit + 1),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    truncated = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # newest N, rendered oldest-first
    return [
        {
            "path": r["path"],
            "value": json.loads(r["value"]) if r.get("value") is not None else None,
            "set_by": r["set_by"],
            "set_at": r["set_at"],
        }
        for r in rows
    ], truncated


async def export_history(
    db: JobDatabase, user_id: str, *, since: Optional[str] = None, include_text: bool = False
) -> dict[str, Any]:
    """R10/S8 — bounded on applications AND bytes; rate-limited per USER."""
    from src.services.applications.contacts import list_contacts  # noqa: PLC0415 — see get_application_detail

    key = f"export_history:{user_id}"
    if not rate_limit.check_and_record(
        key, max_in_window=settings.EXPORT_HISTORY_MAX_PER_HOUR, window_seconds=3600
    ):
        raise SpineError(429, "export rate limit exceeded; try again in an hour")

    where = ["user_id = ?"]
    params: list[Any] = [user_id]
    if since:
        where.append("updated_at >= ?")
        params.append(since)
    where_sql = " AND ".join(where)

    cur = await db._db.execute(
        f"SELECT id, job_id, status, job_title, job_company, created_at, updated_at, last_event_at "  # noqa: S608
        f"FROM applications WHERE {where_sql} ORDER BY updated_at ASC, id ASC",
        params,
    )
    all_rows = [dict(r) for r in await cur.fetchall()]

    out_apps: list[dict[str, Any]] = []
    total_bytes = 0
    truncated = False
    next_since: Optional[str] = None
    for r in all_rows:
        if len(out_apps) >= settings.EXPORT_HISTORY_MAX_APPLICATIONS:
            truncated = True
            next_since = r["updated_at"]
            break
        app_blob = {
            "id": r["id"], "job_id": r["job_id"], "status": r["status"],
            "job_title": r["job_title"] or "", "job_company": r["job_company"] or "",
            "created_at": r["created_at"], "updated_at": r["updated_at"], "last_event_at": r.get("last_event_at"),
            "events": await list_events_for_display(db, r["id"]),
            "artifacts": await _artifact_metadata(db, r["id"], include_text=include_text),
            "receipts": await _list_receipts_for_application(db, user_id, r["id"], include_text=include_text),
            "contacts": await list_contacts(db, user_id, r["id"]),
        }
        blob_size = len(json.dumps(app_blob, default=str).encode("utf-8"))
        if out_apps and total_bytes + blob_size > settings.EXPORT_HISTORY_MAX_BYTES:
            truncated = True
            next_since = r["updated_at"]
            break
        out_apps.append(app_blob)
        total_bytes += blob_size

    # S3 — the edits blob is part of the payload, so it is part of the byte
    # figure. Counting only the applications made `bytes` describe something
    # the caller never receives on its own.
    profile_edits, edits_truncated = await _list_profile_edits_for_export(db, user_id)
    total_bytes += len(json.dumps(profile_edits, default=str).encode("utf-8"))

    result: dict[str, Any] = {
        "applications": out_apps, "truncated": truncated, "bytes": total_bytes,
        "profile_edits": profile_edits, "profile_edits_truncated": edits_truncated,
    }
    if truncated:
        result["next_since"] = next_since
    get_audit_logger().info(
        "history_exported",
        extra={
            "event": "history_exported", "applications": len(out_apps),
            "bytes": total_bytes, "truncated": truncated,
        },
    )
    return result
