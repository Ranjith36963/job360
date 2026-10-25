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

import json
import re
import unicodedata
import zlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications.spine import SpineError, append_event, get_owned_application
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

STATUS_FILTERS = ("open", "answered", "all")

# The user's exact chat words relayed with an answer (answer_ask `user_words`).
ASKS_USER_WORDS_MAX_CHARS = 2000
# Why an ask was taken back (owner, 2026-10-23); required when an assistant withdraws its own ask.
ASKS_WITHDRAW_REASON_MAX_CHARS = 200

# Owner 2026-10-24 - one ask per job carries every open form question. `kind` says what the ask is:
# a question, or a blocker only the user can clear on the form ("" = an ask from before 0087).
ASK_KINDS = ("question", "captcha", "login", "upload", "safety")
# Where an answer the assistant filled in came from (the web labels each row with it). `none` = not
# known yet: its answer must be empty. A guess is never `memory` - the recipes say so.
ASK_QUESTION_SOURCES = ("profile", "memory", "cv", "guess", "draft", "none")
ASKS_QUESTIONS_MAX = 20
ASK_ITEM_QUESTION_MAX_CHARS = 500
ASK_ITEM_ANSWER_MAX_CHARS = 2000
ASK_CHOICES_MAX = 6
ASK_CHOICE_MAX_CHARS = 100
_KEY_RE = re.compile(r"^[a-z0-9_.]{1,60}$")
# Owner 2026-10-25: ONE open ask per job and kind. Hourly runs filed the same form questions again under new
# names (how_heard, heard_from, referral_source, palantir_how_heard...). A key that IS one of these names, or
# ends with "_name" / ".name", is stored under the fixed name, so a repeat merges instead of piling up.
CANONICAL_KEYS: dict[str, tuple[str, ...]] = {
    "how_heard": ("how_heard", "heard_from", "referral_source", "how_did_you_hear", "source_of_application"),
    "consent": ("consent", "consents", "data_consent", "consent_radio", "privacy_consent"),
    "demographic_consent": ("demographic_consent", "demographics_consent"),
    "referred": ("referred", "referral", "referred_by_employee"),
    "worked_before": ("worked_before", "previously_employed", "prior_employment"),
    "languages": ("languages", "language_skills"),
    "university": ("university", "school"),
    "equality.gender": ("gender",),
    "equality.ethnicity": ("ethnicity",),
    "cv_ok": ("cv_ok", "cv_approve", "cv_approval"),
    "cv_unsupported_terms": ("cv_unsupported_terms", "cv_unsupported", "unsupported_terms", "cv_terms"),
    "essays": ("essays", "essay_questions"),
    "job_country": ("job_country",),
    "location_city": ("location_city", "location", "city"),
}
_ALIAS_TO_KEY = {alias: key for key, aliases in CANONICAL_KEYS.items() for alias in aliases}
QUESTION_FAMILY = ("", "question")  # an older ask without a kind is a question ask
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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


def _item_text(where: str, raw: Any, limit: int, *, allow_empty: bool = False) -> str:
    text = clean_text(raw if isinstance(raw, str) else "")
    if not text and not allow_empty:
        raise SpineError(422, f"{where} must not be empty")
    if len(text) > limit:
        raise SpineError(422, f"{where} exceeds {limit} chars")
    return text


def validate_questions(raw: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The ask's question list, cleaned: unique keys, closed sources, capped text. [] = none."""
    if not raw:
        return []
    if len(raw) > ASKS_QUESTIONS_MAX:
        raise SpineError(422, f"at most {ASKS_QUESTIONS_MAX} questions per ask")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        where = f"questions[{i}]"
        key = item.get("key")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise SpineError(422, f"{where}.key must be 1-60 of a-z 0-9 _ .")
        if any(q["key"] == key for q in out):
            raise SpineError(422, f"{where}.key repeats an earlier key")
        source = item.get("source")
        if source not in ASK_QUESTION_SOURCES:
            raise SpineError(422, f"{where}.source must be one of {', '.join(ASK_QUESTION_SOURCES)}")
        answer = _item_text(f"{where}.answer", item.get("answer"), ASK_ITEM_ANSWER_MAX_CHARS, allow_empty=True)
        if (source == "none") != (answer == ""):
            raise SpineError(422, f"{where}: source none goes with an empty answer, any other source with an answer")
        q: dict[str, Any] = {
            "key": key, "source": source, "answer": answer,
            "question": _item_text(f"{where}.question", item.get("question"), ASK_ITEM_QUESTION_MAX_CHARS),
        }
        choices = item.get("choices") or []
        if len(choices) > ASK_CHOICES_MAX:
            raise SpineError(422, f"{where}.choices: at most {ASK_CHOICES_MAX}")
        if choices:
            q["choices"] = [_item_text(f"{where}.choices", c, ASK_CHOICE_MAX_CHARS) for c in choices]
        remembered = item.get("remembered_on")
        if remembered is not None:
            if source != "memory" or not isinstance(remembered, str) or not _DATE_RE.match(remembered):
                raise SpineError(422, f"{where}.remembered_on is a YYYY-MM-DD date, only with source memory")
            q["remembered_on"] = remembered
        out.append(q)
    return out


def canonical_key(key: str) -> str:
    """The fixed name for a common form question (``CANONICAL_KEYS``), else the key as given."""
    for whole in (key, key.replace(".", "_")):
        if whole in _ALIAS_TO_KEY:
            return _ALIAS_TO_KEY[whole]
    for sep in (".", "_"):
        head, _, tail = key.partition(sep)
        while tail:
            if tail in _ALIAS_TO_KEY:
                return _ALIAS_TO_KEY[tail]
            head, _, tail = tail.partition(sep)
    return key


def _fixed(rows: list[dict[str, Any]], keep: frozenset[str] | set[str] = frozenset()) -> list[dict[str, Any]]:
    """Rows under their fixed names, unless two different keys of the same list share one (marketing_consent
    and gdpr_consent are two boxes on one form): those keep their own keys, so neither row is lost. A key
    in ``keep`` (an older row already kept under it) stays as it is, so it merges with that row."""
    fixed = [q["key"] if q["key"] in keep else canonical_key(q["key"]) for q in rows]
    owners: dict[str, set[str]] = {}
    for q, key in zip(rows, fixed):
        owners.setdefault(key, set()).add(q["key"])
    return [{**q, "key": key if len(owners[key]) == 1 else q["key"]} for q, key in zip(rows, fixed)]


def merge_questions(older: list[dict[str, Any]], newer: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per fixed key, oldest first. A newer row replaces an older one, except a newer "not known
    yet" row never drops an answer an older one proposed. At most ``ASKS_QUESTIONS_MAX`` rows (the newest)."""
    rows: dict[str, dict[str, Any]] = {}
    first = _fixed(older)
    for q in [*first, *_fixed(newer, keep={q["key"] for q in first})]:
        key = q["key"]
        merged = q
        before = rows.get(key)
        if before is not None and merged["source"] == "none" and before["source"] != "none":
            merged = {**merged, "source": before["source"], "answer": before["answer"]}
        rows.pop(key, None)
        rows[key] = merged
    return list(rows.values())[-ASKS_QUESTIONS_MAX:]


def _rows_of(ask: dict[str, Any]) -> list[dict[str, Any]]:
    """An open ask's rows; an ask with only a sentence keeps that sentence as one row, so a merge loses nothing."""
    if ask.get("questions"):
        return list(ask["questions"])
    return [_sentence_row(f"earlier_{int(ask['id'])}", str(ask["question"]))]


def _sentence_row(key: str, text: str) -> dict[str, Any]:
    return {"key": key, "question": text[:ASK_ITEM_QUESTION_MAX_CHARS], "source": "none", "answer": ""}


async def _open_family(
    db: JobDatabase, user_id: str, application_id: int, kind: str, asked_by: str
) -> list[dict[str, Any]]:
    """This job's OPEN asks of the same family as ``kind`` (a question ask, or that one blocker), oldest first.
    An assistant's ask never merges with (or closes) one the user wrote on the web, nor the other way round."""
    kinds = QUESTION_FAMILY if kind in QUESTION_FAMILY else (kind,)
    marks = ",".join("?" for _ in kinds)
    side = "k.asked_by = 'web'" if asked_by == "web" else "k.asked_by <> 'web'"
    cur = await db._db.execute(
        _SELECT + f"WHERE k.user_id = ? AND k.application_id = ? AND k.kind IN ({marks}) "  # noqa: S608
        f"AND {side} AND k.answered_at IS NULL AND k.withdrawn_at IS NULL AND k.context NOT LIKE ? "
        "ORDER BY k.id",
        (user_id, application_id, *kinds, "proof_missing:%"),
    )
    return [_serialize(dict(r)) for r in await cur.fetchall()]


async def supersede(
    db: JobDatabase, user_id: str, olds: list[dict[str, Any]], new_id: int, now: str, actor: str
) -> None:
    """Close ``olds`` as replaced by ask ``new_id`` (append-only: they stay, withdrawn by "superseded")."""
    for old in olds:
        reason = f"replaced by ask {new_id}"
        await db._db.execute(
            "UPDATE application_asks SET withdrawn_at = ?, withdrawn_by = 'superseded', withdrawn_reason = ? "
            "WHERE id = ? AND user_id = ? AND answered_at IS NULL AND withdrawn_at IS NULL",
            (now, reason, int(old["id"]), user_id),
        )
        if old["application_id"] is not None:
            await append_event(
                db, user_id=user_id, application_id=int(old["application_id"]), event_type="ask_withdrawn",
                detail=f"Why: {reason}. Asked: {old['question']}"[: settings.APPLICATION_EVENT_DETAIL_MAX_CHARS],
                payload={"ask_id": int(old["id"]), "reason": reason}, occurred_at=now, recorded_by=actor,
            )


TIDY_ACTOR = "system"
# A row that asks the user to paste the form's own question: the assistant reads the form itself (owner 2026-10-25).
_PASTE_RE = re.compile(r"\bpaste\b", re.IGNORECASE)
READ_FORM_NOTE = (
    "Read this form's free-text questions yourself and draft the answers; do not ask the user to paste them."
)


async def tidy_open_asks(db: JobDatabase, user_id: str) -> int:
    """Owner 2026-10-25: the one-time clean-up of asks filed before one-ask-per-job, run on the user's own
    read (a no-op once tidy). Per job, family and side, the NEWEST open ask keeps every row (merged under
    fixed names) and the older ones close as superseded; an open ask on a closed job closes too. Nothing is
    deleted. Returns how many asks it closed."""
    from src.core.settings import APPLICATION_FOLLOW_UP_CLOSED_STATUSES  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT k.id, a.status AS app_status FROM application_asks k JOIN applications a "
        "ON a.id = k.application_id AND a.user_id = k.user_id WHERE k.user_id = ? AND k.answered_at IS NULL "
        "AND k.withdrawn_at IS NULL AND k.context NOT LIKE ? ORDER BY k.id",
        (user_id, "proof_missing:%"),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    if not rows:
        return 0
    groups: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    closed_job: list[dict[str, Any]] = []
    for r in rows:
        ask = await _get_ask(db, user_id, int(r["id"]))
        if ask is None:
            continue
        if r["app_status"] in APPLICATION_FOLLOW_UP_CLOSED_STATUSES:
            closed_job.append(ask)
            continue
        family = "question" if ask["kind"] in QUESTION_FAMILY else ask["kind"]
        side = "web" if ask["asked_by"] == "web" else "assistant"
        groups.setdefault((int(ask["application_id"]), family, side), []).append(ask)
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    pasted = [a for (_app, fam, _side), v in groups.items() if fam == "question"
              for a in v[-1:] if any(_PASTE_RE.search(str(q["question"])) for q in _rows_of_all(v))]
    if not dupes and not closed_job and not pasted:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    closed = 0
    async with db._db.transaction():
        for (_app, family, _side), asks_ in dupes.items():
            keep, olds = asks_[-1], asks_[:-1]
            if family == "question":
                merged = merge_questions([q for old in olds for q in _rows_of(old)], _rows_of(keep))
                await db._db.execute(
                    "UPDATE application_asks SET questions = ? WHERE id = ? AND user_id = ? AND answered_at IS NULL",
                    (json.dumps(merged) if merged else "", int(keep["id"]), user_id),
                )
            await supersede(db, user_id, olds, int(keep["id"]), now, TIDY_ACTOR)
            closed += len(olds)
        for keep in pasted:
            closed += await _drop_paste_rows(db, user_id, int(keep["id"]), now)
        for ask in closed_job:
            await db._db.execute(
                "UPDATE application_asks SET withdrawn_at = ?, withdrawn_by = 'job_closed', "
                "withdrawn_reason = 'the job is closed' WHERE id = ? AND user_id = ? AND answered_at IS NULL "
                "AND withdrawn_at IS NULL",
                (now, int(ask["id"]), user_id),
            )
            closed += 1
    get_audit_logger().info("asks_tidied", extra={"event": "asks_tidied", "user_id": user_id, "closed": closed})
    return closed


def _rows_of_all(asks_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [q for a in asks_ for q in _rows_of(a)]


async def _drop_paste_rows(db: JobDatabase, user_id: str, ask_id: int, now: str) -> int:
    """Take the "paste the question" rows off an open ask and leave the job a note to read the form. An ask
    with no row left closes. Returns 1 when the ask closed, else 0."""
    ask = await _get_ask(db, user_id, ask_id)
    if ask is None:
        return 0
    rows = _rows_of(ask)
    kept = [q for q in rows if not _PASTE_RE.search(str(q["question"]))]
    if len(kept) == len(rows):
        return 0
    await append_event(
        db, user_id=user_id, application_id=int(ask["application_id"]), event_type="note",
        detail=READ_FORM_NOTE, occurred_at=now, recorded_by=TIDY_ACTOR,
    )
    if kept:
        await db._db.execute(
            "UPDATE application_asks SET questions = ? WHERE id = ? AND user_id = ? AND answered_at IS NULL",
            (json.dumps(kept), ask_id, user_id),
        )
        return 0
    await db._db.execute(
        "UPDATE application_asks SET withdrawn_at = ?, withdrawn_by = ?, withdrawn_reason = ? "
        "WHERE id = ? AND user_id = ? AND answered_at IS NULL AND withdrawn_at IS NULL",
        (now, TIDY_ACTOR, "the assistant reads the form's questions itself", ask_id, user_id),
    )
    return 1


def _validate_answers(questions: list[dict[str, Any]], raw: Optional[dict[str, Any]]) -> dict[str, str]:
    """One non-empty answer per question key, nothing else; a question with choices takes one of them."""
    if not questions:
        if raw:
            raise SpineError(422, "this ask has no questions; pass `answer`")
        return {}
    if not raw:
        raise SpineError(422, "this ask lists questions; pass `answers` with one answer per key")
    keys = [q["key"] for q in questions]
    if set(raw) != set(keys):
        raise SpineError(422, f"answers must have exactly these keys: {', '.join(keys)}")
    out: dict[str, str] = {}
    for q in questions:
        text = _item_text(f"answers.{q['key']}", raw[q["key"]], ASK_ITEM_ANSWER_MAX_CHARS)
        if q.get("choices") and text not in q["choices"]:
            raise SpineError(422, f"answers.{q['key']} must be one of its choices")
        out[q["key"]] = text
    return out


def _json_or(raw: Any, empty: Any) -> Any:
    try:
        return json.loads(raw) if raw else empty
    except ValueError:
        return empty


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    answered_at = row.get("answered_at")
    withdrawn_at = row.get("withdrawn_at")
    if withdrawn_at:
        status = "withdrawn"
    else:
        status = "answered" if answered_at else "open"
    user_words = row.get("user_words") or ""
    confirmed_at = row.get("confirmed_at")
    relayed = bool(answered_at) and row.get("answered_by") != "web" and bool(user_words)
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
        # The user's exact chat words, when the assistant relayed them.
        "user_words": user_words,
        # True when an assistant (not the web) recorded an answer AND passed the
        # user's own words. answered_by_user keeps its strict meaning.
        "relayed_from_chat": relayed,
        # Whose decision it reads as: "you" for a web answer or a relayed chat
        # answer; "assistant" when the assistant answered with no user words.
        "said_by": "you" if (row.get("answered_by") == "web" or relayed) else "assistant",
        # When the user confirmed the relayed answer on the web; None until then,
        # and None again if the answer was changed after the confirmation.
        "confirmed_at": confirmed_at if relayed and confirmed_at and confirmed_at >= answered_at else None,
        "withdrawn_at": withdrawn_at,
        # Who took the ask back and why (0082; "" = not withdrawn, or before 0082 / no reason given).
        "withdrawn_by": row.get("withdrawn_by") or "",
        "withdrawn_reason": row.get("withdrawn_reason") or "",
        "status": status,
        # 0087 - what the ask is, its questions (each with where the answer came from) and the
        # answers the user approved ("" / [] / {} on an older ask).
        "kind": row.get("kind") or "",
        "questions": _json_or(row.get("questions"), []),
        "answers": _json_or(row.get("answers"), {}),
    }


_SELECT = (
    "SELECT k.id, k.application_id, a.job_title, a.job_company, k.question, k.context, k.asked_by, "
    "k.asked_at, k.answer, k.answered_by, k.answered_at, k.withdrawn_at, k.user_words, k.confirmed_at, "
    "k.withdrawn_by, k.withdrawn_reason, k.kind, k.questions, k.answers "
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


def _log_no_proof_ask(user_id: str, application_id: Optional[int], result: str) -> None:
    get_audit_logger().info("no_proof_ask", extra={
        "event": "no_proof_ask", "user_id": safe_log_value(user_id),
        "application_id": safe_log_value(str(application_id)), "result": safe_log_value(result),
    })


async def _other_jobs_named(db: JobDatabase, user_id: str, app_row: dict[str, Any], text: str) -> list[str]:
    """Owner 2026-10-24: ONE ask per job. The companies of the user's OTHER open applications that
    ``text`` names as a whole word (case-insensitive; a company shorter than 3 letters, or the same
    company as this application, never counts)."""
    own = str(app_row.get("job_company") or "").strip().lower()
    cur = await db._db.execute(
        "SELECT DISTINCT job_company FROM applications WHERE user_id = ? AND id <> ? AND status = 'considering'",
        (user_id, int(app_row["id"])),
    )
    named: list[str] = []
    for row in await cur.fetchall():
        company = str(dict(row)["job_company"] or "").strip()
        if len(company) < 3 or company.lower() == own or company in named:
            continue
        if re.search(rf"(?<!\w){re.escape(company)}(?!\w)", text, re.IGNORECASE):
            named.append(company)
    return named


async def _first_proof_ask(
    db: JobDatabase, user_id: str, application_id: Optional[int], proof_ctx: str
) -> Optional[dict[str, Any]]:
    """The "no proof yet" ask already recorded for this application (any state), logged as a duplicate."""
    cur = await db._db.execute(
        "SELECT id FROM application_asks WHERE user_id = ? AND application_id = ? AND context = ? "
        "ORDER BY id LIMIT 1",
        (user_id, application_id, proof_ctx),
    )
    first = await cur.fetchone()
    if not first:
        return None
    _log_no_proof_ask(user_id, application_id, "duplicate")
    return await _get_ask(db, user_id, int(first[0]))


async def create_ask(
    db: JobDatabase,
    user_id: str,
    application_id: Optional[int],
    question: str,
    context: str,
    asked_by: str,
    kind: str = "",
    questions: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Record one ask. 422 on empty/oversized text, 404 if the application is
    not the caller's, 429 when too many asks are already open. ``kind`` (ASK_KINDS, "" = unsaid)
    and ``questions`` (validate_questions) are optional; a blocker carries no questions."""
    if kind and kind not in ASK_KINDS:
        raise SpineError(422, f"kind must be one of {', '.join(ASK_KINDS)}")
    clean_qs = validate_questions(questions)
    if clean_qs and kind not in ("", "question"):
        raise SpineError(422, "a blocker ask carries no questions: send them in their own question ask")
    clean_q = clean_text(question)
    if not clean_q:
        raise SpineError(422, "question must not be empty")
    if len(clean_q) > settings.ASKS_QUESTION_MAX_CHARS:
        raise SpineError(422, f"question exceeds ASKS_QUESTION_MAX_CHARS ({settings.ASKS_QUESTION_MAX_CHARS} chars)")
    clean_ctx = clean_text(context)
    if len(clean_ctx) > settings.ASKS_CONTEXT_MAX_CHARS:
        raise SpineError(422, f"context exceeds ASKS_CONTEXT_MAX_CHARS ({settings.ASKS_CONTEXT_MAX_CHARS} chars)")
    app_row = await get_owned_application(db, user_id, application_id) if application_id is not None else None
    if application_id is not None and app_row is None:
        raise SpineError(404, "application not found")
    if app_row is not None and (others := await _other_jobs_named(db, user_id, app_row, f"{clean_q}\n{clean_ctx}")):
        raise SpineError(
            422,
            f"one ask per job: this ask also names {', '.join(others)} (another open application). "
            "Call ask_user once for each application_id, so Needs you shows one card per job.",
        )
    # S7 - the "no proof yet" ask is asked ONCE per application, whatever its state:
    # a repeat (the assistant re-reading whats_new) returns the first one, writes nothing.
    proof_ctx = f"proof_missing:{application_id}" if application_id is not None else ""
    is_proof = bool(proof_ctx) and clean_ctx == proof_ctx
    if is_proof and (first := await _first_proof_ask(db, user_id, application_id, proof_ctx)):
        return first
    clean_qs = merge_questions([], clean_qs)  # fixed names, one row per name
    family = (
        [] if is_proof or application_id is None
        else await _open_family(db, user_id, application_id, kind, asked_by)
    )
    # A merge replaces asks, it never adds one: the cap only stops a NEW card.
    if not family and await count_open_asks(db, user_id) >= settings.ASKS_MAX_OPEN_PER_USER:
        raise SpineError(
            429, f"too many open asks (ASKS_MAX_OPEN_PER_USER = {settings.ASKS_MAX_OPEN_PER_USER}); answer some first"
        )
    now = datetime.now(timezone.utc).isoformat()
    async with db._db.transaction():
        if is_proof:  # two parallel callers: the loser waits here, then finds the winner's ask
            await db._db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"{user_id}:{clean_ctx}",))
            if first := await _first_proof_ask(db, user_id, application_id, proof_ctx):
                return first
        elif application_id is not None:  # two runs at once: the second merges into the first's ask
            await db._db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"{user_id}:ask:{application_id}",))
            family = await _open_family(db, user_id, application_id, kind, asked_by)
            if family and kind in QUESTION_FAMILY:
                # A sentence-only ask joins as a row too, or the merged card would hide what it asked.
                mine = clean_qs or [_sentence_row(f"said_{zlib.crc32(clean_q.encode()):08x}", clean_q)]
                clean_qs = merge_questions([q for old in family for q in _rows_of(old)], mine)
        cur = await db._db.execute(
            "INSERT INTO application_asks (user_id, application_id, question, context, asked_by, asked_at, "
            "kind, questions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, application_id, clean_q, clean_ctx, asked_by, now, kind,
             json.dumps(clean_qs) if clean_qs else ""),
        )
        ask_id = int(cur.lastrowid or 0)
        if family:
            await supersede(db, user_id, family, ask_id, now, asked_by)
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
            "has_application": application_id is not None, "merged": len(family),
        },
    )
    if is_proof:
        _log_no_proof_ask(user_id, application_id, "created")
    ask = await _get_ask(db, user_id, ask_id)
    assert ask is not None
    return {**ask, "merged_from": [int(old["id"]) for old in family]}


async def answer_ask(
    db: JobDatabase, user_id: str, ask_id: int, answer: str, answered_by: str, user_words: str = "",
    answers: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Record the user's answer. May be called again to CHANGE it: the row
    holds the latest answer and each answer appends an ``answered`` event
    (payload ``{ask_id, answer}``), so earlier answers stay in the history.
    An ask with ``questions`` takes ``answers`` (one per key); ``answer`` then defaults to
    their "question: answer" lines. 404 if not the caller's, 422 on empty/oversized text, 409 if withdrawn."""
    existing = await _get_ask(db, user_id, ask_id)
    if existing is None:
        raise SpineError(404, "ask not found")
    if existing["withdrawn_at"]:
        raise SpineError(409, "ask was withdrawn")
    clean_answers = _validate_answers(existing["questions"], answers)
    if clean_answers and not clean_text(answer):
        lines = "\n".join(f"{q['question']}: {clean_answers[q['key']]}" for q in existing["questions"])
        answer = lines[: settings.ASKS_ANSWER_MAX_CHARS]  # the full answers stay in `answers`
    clean_a = clean_text(answer)
    if not clean_a:
        raise SpineError(422, "answer must not be empty")
    if len(clean_a) > settings.ASKS_ANSWER_MAX_CHARS:
        raise SpineError(422, f"answer exceeds ASKS_ANSWER_MAX_CHARS ({settings.ASKS_ANSWER_MAX_CHARS} chars)")
    clean_w = clean_text(user_words)
    if len(clean_w) > ASKS_USER_WORDS_MAX_CHARS:
        raise SpineError(422, f"user_words exceeds {ASKS_USER_WORDS_MAX_CHARS} chars")
    now = datetime.now(timezone.utc).isoformat()
    async with db._db.transaction():
        cur = await db._db.execute(
            "UPDATE application_asks SET answer = ?, answered_by = ?, answered_at = ?, user_words = ?, answers = ? "
            "WHERE id = ? AND user_id = ? AND withdrawn_at IS NULL",
            (clean_a, answered_by, now, clean_w, json.dumps(clean_answers) if clean_answers else "", ask_id, user_id),
        )
        if not cur.rowcount:
            raise SpineError(404, "ask not found")
        if existing["application_id"] is not None:
            await append_event(
                db, user_id=user_id, application_id=int(existing["application_id"]), event_type="answered",
                detail=clean_a[: settings.APPLICATION_EVENT_DETAIL_MAX_CHARS],
                payload={"ask_id": ask_id, "answer": clean_a, **({"user_words": clean_w} if clean_w else {}),
                         **({"answers": clean_answers} if clean_answers else {})},
                occurred_at=now, recorded_by=answered_by,
            )
    # ``ask_id`` is the ``/asks/{ask_id}/…`` path parameter, so CodeQL's
    # py/log-injection traces it request -> logger and flags this call and the
    # ``ask_withdrawn`` one below (alerts 271/272). Runtime was already safe
    # twice over -- the route and the MCP tool both declare ``ask_id: int``, so
    # a segment carrying CR/LF is a 422 before this runs, and
    # ``CRLFScrubFilter`` is attached to every handler -- but CodeQL cannot see
    # a ``logging.Filter`` wired at handler-setup time, so the value is
    # sanitized AT THE SOURCE with the existing helper (rule #7: no second
    # sanitizer). ``ask_created`` above is deliberately NOT wrapped: there
    # ``ask_id`` is ``cur.lastrowid``, ours and never request data -- the
    # asymmetry is the point, not an oversight. ``safe_log_value`` returns a
    # str, so the audit ``ask_id`` lands in ``audit_log.detail`` as a JSON
    # string rather than a number; nothing reads that field (grepped).
    get_audit_logger().info(
        "ask_answered", extra={"event": "ask_answered", "user_id": user_id, "ask_id": safe_log_value(ask_id)}
    )
    ask = await _get_ask(db, user_id, ask_id)
    assert ask is not None
    return ask


async def confirm_ask(db: JobDatabase, user_id: str, ask_id: int) -> dict[str, Any]:
    """The user confirms an answer their assistant relayed from chat. Sets
    ``confirmed_at`` once per answer (a repeat is a no-op); nothing is
    rewritten and no event is added. 404 if not the caller's, 409 if the ask
    was withdrawn or has no relayed answer to confirm."""
    existing = await _get_ask(db, user_id, ask_id)
    if existing is None:
        raise SpineError(404, "ask not found")
    if existing["withdrawn_at"]:
        raise SpineError(409, "ask was withdrawn")
    if not existing["relayed_from_chat"]:
        raise SpineError(409, "only an answer your assistant relayed from chat can be confirmed")
    if existing["confirmed_at"]:
        return existing
    now = datetime.now(timezone.utc).isoformat()
    await db._db.execute(
        "UPDATE application_asks SET confirmed_at = ? "
        "WHERE id = ? AND user_id = ? AND withdrawn_at IS NULL AND answered_at IS NOT NULL "
        "AND (confirmed_at IS NULL OR confirmed_at < answered_at)",
        (now, ask_id, user_id),
    )
    get_audit_logger().info(
        "ask_confirmed", extra={"event": "ask_confirmed", "user_id": user_id, "ask_id": safe_log_value(ask_id)}
    )
    ask = await _get_ask(db, user_id, ask_id)
    assert ask is not None
    return ask


def clean_withdraw_reason(raw: Any) -> str:
    """The reason an ask was taken back: one line of plain text, at most
    :data:`ASKS_WITHDRAW_REASON_MAX_CHARS` characters (422 otherwise)."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise SpineError(422, "withdraw reason must be text")
    text = " ".join(clean_text(raw).split())
    if len(text) > ASKS_WITHDRAW_REASON_MAX_CHARS:
        raise SpineError(422, f"withdraw reason must be at most {ASKS_WITHDRAW_REASON_MAX_CHARS} characters")
    return text


async def withdraw_ask(
    db: JobDatabase, user_id: str, ask_id: int, withdrawn_by: str, reason: str = "", *, own_only: bool = False
) -> dict[str, Any]:
    """Take an OPEN ask back (no longer needed). 404 if not the caller's,
    409 if it is already answered or already withdrawn. ``own_only`` (an
    assistant calling, owner 2026-10-23): only an ask ``withdrawn_by`` itself
    asked (403 otherwise), and only with a ``reason`` (422 when empty). The
    reason is stored on the ask and shown on the application's timeline."""
    clean_reason = clean_withdraw_reason(reason)
    existing = await _get_ask(db, user_id, ask_id)
    if existing is None:
        raise SpineError(404, "ask not found")
    if own_only and existing["asked_by"] != withdrawn_by:
        raise SpineError(403, "only the user can withdraw an ask you did not ask; ask them to, on Needs you")
    if own_only and not clean_reason:
        raise SpineError(
            422, f"say why the ask is out of date (withdraw reason, at most {ASKS_WITHDRAW_REASON_MAX_CHARS} chars)"
        )
    if existing["withdrawn_at"]:
        raise SpineError(409, "already withdrawn")
    if existing["answered_at"]:
        raise SpineError(409, "already answered")
    now = datetime.now(timezone.utc).isoformat()
    async with db._db.transaction():
        cur = await db._db.execute(
            "UPDATE application_asks SET withdrawn_at = ?, withdrawn_by = ?, withdrawn_reason = ? "
            "WHERE id = ? AND user_id = ? AND answered_at IS NULL AND withdrawn_at IS NULL",
            (now, withdrawn_by, clean_reason, ask_id, user_id),
        )
        if not cur.rowcount:
            raise SpineError(409, "ask is no longer open")
        if existing["application_id"] is not None:
            shown = f"Why: {clean_reason}. Asked: {existing['question']}" if clean_reason else existing["question"]
            await append_event(
                db, user_id=user_id, application_id=int(existing["application_id"]), event_type="ask_withdrawn",
                detail=shown[: settings.APPLICATION_EVENT_DETAIL_MAX_CHARS],
                payload={"ask_id": ask_id, **({"reason": clean_reason} if clean_reason else {})},
                occurred_at=now, recorded_by=withdrawn_by,
            )
    # Same path-parameter taint as ``ask_answered`` above -- see the note there.
    get_audit_logger().info(
        "ask_withdrawn", extra={"event": "ask_withdrawn", "user_id": user_id, "ask_id": safe_log_value(ask_id)}
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
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The caller's asks, newest first (open ones first when ``status="all"``,
    which also lists withdrawn ones). ``offset`` pages further back — the
    answered history is never capped, only paged."""
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
    offset = max(0, int(offset))
    cur = await db._db.execute(
        _SELECT + "WHERE " + " AND ".join(where)
        + " ORDER BY (CASE WHEN k.answered_at IS NULL AND k.withdrawn_at IS NULL THEN 0 ELSE 1 END),"
        + " k.asked_at DESC, k.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return [_serialize(dict(r)) for r in await cur.fetchall()]
