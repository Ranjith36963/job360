"""Asks - the "Needs you" queue (owner plan 2026-10-01).

When the user's assistant is stuck or would have to guess (a form question the
profile cannot answer, an unclear email) it records an ASK. The user answers
ONCE - in chat (the assistant calls ``answer_ask``) or on the Job360
Needs-you page. Job360 only stores the question and the answer; it never
guesses, never answers for the user (decision 28: no LLM of its own).

An ask may be about one application (``application_id``) or general (None).
An ask about an application also leaves an ``asked`` / ``answered`` /
``ask_withdrawn`` NOTE event on that application's timeline, so the history
reads true; those event types never change status (they are in
``APPLICATION_NOTE_EVENT_TYPES``).

Every read and write filters by ``user_id`` by hand (the shim strips FK
clauses). An answer may be CHANGED (owner decision): the row holds the latest
answer, and every answer is also an ``answered`` event carrying the answer, so
the old ones stay readable as history. An ask can be withdrawn (the question
was taken back); a withdrawn ask is not open and does not count to the cap.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications.spine import SpineError, append_event, get_owned_application
from src.utils.logger import get_audit_logger

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

STATUS_FILTERS = ("open", "answered", "all")

# Same banned set spine.validate_source uses (Cc/Zl/Zp + bidi overrides),
# except a plain newline and tab are kept: a question or answer is prose and
# may span lines.
_KEEP = frozenset({"\n", "\t"})
_BIDI = frozenset(chr(cp) for cp in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069))


def clean_text(raw: Optional[str]) -> str:
    """Trim and drop control characters (keeping newline/tab) from free text."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    kept = [
        ch for ch in text
        if ch in _KEEP or (ch not in _BIDI and unicodedata.category(ch) not in {"Cc", "Zl", "Zp"})
    ]
    return "".join(kept).strip()


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    answered_at = row.get("answered_at")
    withdrawn_at = row.get("withdrawn_at")
    if withdrawn_at:
        status = "withdrawn"
    else:
        status = "answered" if answered_at else "open"
    return {
        "id": row["id"],
        "application_id": row.get("application_id"),
        "job_title": row.get("job_title"),
        "job_company": row.get("job_company"),
        "question": row["question"],
        "context": row.get("context") or "",
        "asked_by": row["asked_by"],
        "asked_at": row["asked_at"],
        "answer": row.get("answer"),
        "answered_by": row.get("answered_by"),
        "answered_at": answered_at,
        # True when the USER typed it on Job360 ("web"); False when the
        # assistant recorded the user's chat answer (token:/agent: actor).
        "answered_by_user": row.get("answered_by") == "web",
        "withdrawn_at": withdrawn_at,
        "status": status,
    }


_SELECT = (
    "SELECT k.id, k.application_id, a.job_title, a.job_company, k.question, k.context, k.asked_by, "
    "k.asked_at, k.answer, k.answered_by, k.answered_at, k.withdrawn_at "
    "FROM application_asks k LEFT JOIN applications a ON a.id = k.application_id AND a.user_id = k.user_id "
)


async def _get_ask(db: JobDatabase, user_id: str, ask_id: int) -> Optional[dict[str, Any]]:
    cur = await db._db.execute(_SELECT + "WHERE k.id = ? AND k.user_id = ?", (ask_id, user_id))
    row = await cur.fetchone()
    return _serialize(dict(row)) if row else None


async def count_open_asks(db: JobDatabase, user_id: str) -> int:
    """How many asks of this user are still open (not answered, not withdrawn)."""
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_asks WHERE user_id = ? AND answered_at IS NULL AND withdrawn_at IS NULL",
        (user_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def create_ask(
    db: JobDatabase,
    user_id: str,
    application_id: Optional[int],
    question: str,
    context: str,
    asked_by: str,
) -> dict[str, Any]:
    """Record one ask. 422 on empty/oversized text, 404 if the application is
    not the caller's, 429 when too many asks are already open."""
    clean_q = clean_text(question)
    if not clean_q:
        raise SpineError(422, "question must not be empty")
    if len(clean_q) > settings.ASKS_QUESTION_MAX_CHARS:
        raise SpineError(422, f"question exceeds ASKS_QUESTION_MAX_CHARS ({settings.ASKS_QUESTION_MAX_CHARS} chars)")
    clean_ctx = clean_text(context)
    if len(clean_ctx) > settings.ASKS_CONTEXT_MAX_CHARS:
        raise SpineError(422, f"context exceeds ASKS_CONTEXT_MAX_CHARS ({settings.ASKS_CONTEXT_MAX_CHARS} chars)")
    if application_id is not None and await get_owned_application(db, user_id, application_id) is None:
        raise SpineError(404, "application not found")
    if await count_open_asks(db, user_id) >= settings.ASKS_MAX_OPEN_PER_USER:
        raise SpineError(
            429, f"too many open asks (ASKS_MAX_OPEN_PER_USER = {settings.ASKS_MAX_OPEN_PER_USER}); answer some first"
        )
    now = datetime.now(timezone.utc).isoformat()
    async with db._db.transaction():
        cur = await db._db.execute(
            "INSERT INTO application_asks (user_id, application_id, question, context, asked_by, asked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, application_id, clean_q, clean_ctx, asked_by, now),
        )
        ask_id = int(cur.lastrowid or 0)
        if application_id is not None:
            await append_event(
                db, user_id=user_id, application_id=application_id, event_type="asked",
                detail=clean_q[: settings.APPLICATION_EVENT_DETAIL_MAX_CHARS], payload={"ask_id": ask_id},
                occurred_at=now, recorded_by=asked_by,
            )
    get_audit_logger().info(
        "ask_created",
        extra={
            "event": "ask_created", "user_id": user_id, "ask_id": ask_id,
            "has_application": application_id is not None,
        },
    )
    ask = await _get_ask(db, user_id, ask_id)
    assert ask is not None
    return ask


async def answer_ask(
    db: JobDatabase, user_id: str, ask_id: int, answer: str, answered_by: str
) -> dict[str, Any]:
    """Record the user's answer. May be called again to CHANGE it: the row
    holds the latest answer and each answer appends an ``answered`` event
    (payload ``{ask_id, answer}``), so earlier answers stay in the history.
    404 if not the caller's, 422 on empty/oversized text, 409 if withdrawn."""
    existing = await _get_ask(db, user_id, ask_id)
    if existing is None:
        raise SpineError(404, "ask not found")
    if existing["withdrawn_at"]:
        raise SpineError(409, "ask was withdrawn")
    clean_a = clean_text(answer)
    if not clean_a:
        raise SpineError(422, "answer must not be empty")
    if len(clean_a) > settings.ASKS_ANSWER_MAX_CHARS:
        raise SpineError(422, f"answer exceeds ASKS_ANSWER_MAX_CHARS ({settings.ASKS_ANSWER_MAX_CHARS} chars)")
    now = datetime.now(timezone.utc).isoformat()
    async with db._db.transaction():
        cur = await db._db.execute(
            "UPDATE application_asks SET answer = ?, answered_by = ?, answered_at = ? "
            "WHERE id = ? AND user_id = ? AND withdrawn_at IS NULL",
            (clean_a, answered_by, now, ask_id, user_id),
        )
        if not cur.rowcount:
            raise SpineError(404, "ask not found")
        if existing["application_id"] is not None:
            await append_event(
                db, user_id=user_id, application_id=int(existing["application_id"]), event_type="answered",
                detail=clean_a[: settings.APPLICATION_EVENT_DETAIL_MAX_CHARS],
                payload={"ask_id": ask_id, "answer": clean_a},
                occurred_at=now, recorded_by=answered_by,
            )
    get_audit_logger().info(
        "ask_answered", extra={"event": "ask_answered", "user_id": user_id, "ask_id": ask_id}
    )
    ask = await _get_ask(db, user_id, ask_id)
    assert ask is not None
    return ask


async def withdraw_ask(db: JobDatabase, user_id: str, ask_id: int, withdrawn_by: str) -> dict[str, Any]:
    """Take an OPEN ask back (no longer needed). 404 if not the caller's,
    409 if it is already answered or already withdrawn."""
    existing = await _get_ask(db, user_id, ask_id)
    if existing is None:
        raise SpineError(404, "ask not found")
    if existing["withdrawn_at"]:
        raise SpineError(409, "already withdrawn")
    if existing["answered_at"]:
        raise SpineError(409, "already answered")
    now = datetime.now(timezone.utc).isoformat()
    async with db._db.transaction():
        cur = await db._db.execute(
            "UPDATE application_asks SET withdrawn_at = ? "
            "WHERE id = ? AND user_id = ? AND answered_at IS NULL AND withdrawn_at IS NULL",
            (now, ask_id, user_id),
        )
        if not cur.rowcount:
            raise SpineError(409, "ask is no longer open")
        if existing["application_id"] is not None:
            await append_event(
                db, user_id=user_id, application_id=int(existing["application_id"]), event_type="ask_withdrawn",
                detail=existing["question"][: settings.APPLICATION_EVENT_DETAIL_MAX_CHARS],
                payload={"ask_id": ask_id}, occurred_at=now, recorded_by=withdrawn_by,
            )
    get_audit_logger().info(
        "ask_withdrawn", extra={"event": "ask_withdrawn", "user_id": user_id, "ask_id": ask_id}
    )
    ask = await _get_ask(db, user_id, ask_id)
    assert ask is not None
    return ask


async def list_asks(
    db: JobDatabase,
    user_id: str,
    status: str = "open",
    application_id: Optional[int] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The caller's asks, newest first (open ones first when ``status="all"``,
    which also lists withdrawn ones)."""
    if status not in STATUS_FILTERS:
        raise SpineError(422, f"status must be one of {list(STATUS_FILTERS)}")
    where = ["k.user_id = ?"]
    params: list[Any] = [user_id]
    if status == "open":
        where.append("k.answered_at IS NULL AND k.withdrawn_at IS NULL")
    elif status == "answered":
        where.append("k.answered_at IS NOT NULL AND k.withdrawn_at IS NULL")
    if application_id is not None:
        where.append("k.application_id = ?")
        params.append(application_id)
    limit = max(1, int(limit))
    cur = await db._db.execute(
        _SELECT + "WHERE " + " AND ".join(where)
        + " ORDER BY (CASE WHEN k.answered_at IS NULL AND k.withdrawn_at IS NULL THEN 0 ELSE 1 END),"
        + " k.asked_at DESC, k.id DESC LIMIT ?",
        [*params, limit],
    )
    return [_serialize(dict(r)) for r in await cur.fetchall()]
