"""Ready to send - the applications whose form the assistant filled and the
user has not yet answered (S5b), and the approval card for each (S5d).

An application is READY when the user owns it, it is still ``considering``, it
has no receipt, its newest ``form_filled`` event is F, and no ``submit_approved``
or ``submit_declined`` event came after F (a decline then a NEW fill is ready
again), and it is not blocked (S6: its newest ``blocked`` / ``unblocked`` event
is not ``blocked``). One SQL, no per-row query. Always scoped by ``user_id`` (rule #12).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import kit, spine
from src.services.applications.kit import _json
from src.services.profile import edits as profile_edits
from src.services.profile import user_info
from src.services.profile.edits import is_assistant_actor

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

_READY_SQL = (
    "SELECT a.id AS application_id, a.job_id, a.job_title, a.job_company, a.job_location, a.job_country, "
    "a.job_url, a.fit_score, a.fit_recorded_by, f.id AS form_filled_event_id, "
    "f.recorded_at AS filled_at, f.recorded_by AS filled_by, f.payload "
    "FROM applications a JOIN application_events f "
    "ON f.application_id = a.id AND f.user_id = a.user_id AND f.event_type = 'form_filled' "
    "AND f.id = (SELECT MAX(x.id) FROM application_events x WHERE x.application_id = a.id "
    "AND x.user_id = a.user_id AND x.event_type = 'form_filled') "
    "WHERE a.user_id = ? {only}AND a.status = 'considering' "
    "AND NOT EXISTS (SELECT 1 FROM application_receipts r WHERE r.application_id = a.id AND r.user_id = a.user_id) "
    "AND NOT EXISTS (SELECT 1 FROM application_events d WHERE d.application_id = a.id AND d.user_id = a.user_id "
    "AND d.event_type IN ('submit_approved', 'submit_declined') AND d.id > f.id) "
    # S6: an open block (newest blocked/unblocked is `blocked`) is never ready to send.
    "AND NOT EXISTS (SELECT 1 FROM application_events b WHERE b.application_id = a.id AND b.user_id = a.user_id "
    "AND b.event_type = 'blocked' AND b.id = (SELECT MAX(y.id) FROM application_events y "
    "WHERE y.application_id = a.id AND y.user_id = a.user_id AND y.event_type IN ('blocked', 'unblocked'))) "
    "ORDER BY f.id DESC LIMIT ?"
)


async def ready_rows(
    db: JobDatabase, user_id: str, *, limit: int, application_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """The caller's ready-to-send applications, newest fill first, at most
    ``min(limit, READY_TO_SEND_MAX)``. Each row: ``application_id``,
    ``form_filled_event_id``, ``filled_at``, ``filled_by``, ``payload`` (parsed),
    plus the job facts for display. ``application_id`` narrows it to one row."""
    cap = max(0, min(int(limit), settings.READY_TO_SEND_MAX))
    if cap == 0:
        return []
    only = "" if application_id is None else "AND a.id = ? "
    params: tuple[Any, ...] = (user_id, cap) if application_id is None else (user_id, application_id, cap)
    cur = await db._db.execute(_READY_SQL.format(only=only), params)
    out: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        d = dict(r)
        d["payload"] = _json(d.get("payload"))
        out.append(d)
    return out


# Approve CV on Needs you (owner decision 2026-10-23): every open application whose newest CV the user has
# not seen yet - neither a cv_seen nor a submit_approved names it, from chat or from the web.
CV_TO_APPROVE_MAX = 50
_CV_TO_APPROVE_SQL = (
    "SELECT a.id AS application_id, a.job_title, a.job_company, c.id AS artifact_id, c.version_no, c.text, "
    "c.created_at, c.made_by FROM applications a JOIN application_artifacts c "
    "ON c.application_id = a.id AND c.user_id = a.user_id AND c.kind = 'cv' "
    "AND c.version_no = (SELECT MAX(v.version_no) FROM application_artifacts v WHERE v.application_id = a.id "
    "AND v.user_id = a.user_id AND v.kind = 'cv') "
    "WHERE a.user_id = ? AND a.status = 'considering' "
    "AND NOT EXISTS (SELECT 1 FROM application_receipts r WHERE r.application_id = a.id AND r.user_id = a.user_id) "
    "ORDER BY c.id DESC LIMIT ?"
)


async def cv_to_approve(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """The caller's open applications whose newest CV has no seen mark (``kit.cv_mark_matches``, the
    test the gate's ``cv_not_seen`` uses), newest CV first, at most ``CV_TO_APPROVE_MAX``."""
    cur = await db._db.execute(_CV_TO_APPROVE_SQL, (user_id, CV_TO_APPROVE_MAX * 4))
    out: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        row = dict(r)
        app_id, sha = int(row["application_id"]), kit.sha256_text(row["text"])
        marks = await kit._events(db, user_id, app_id, ("cv_seen", "submit_approved"))
        if any(kit.cv_mark_matches(ev, row["artifact_id"], sha) for ev in marks):
            continue
        out.append({
            "application_id": app_id, "job_title": row["job_title"] or "", "job_company": row["job_company"] or "",
            "cv": {
                "artifact_id": row["artifact_id"], "version": row["version_no"], "saved_at": row["created_at"],
                "made_by": row["made_by"],
            },
        })
        if len(out) >= CV_TO_APPROVE_MAX:
            break
    return out


async def newest_fill_id(db: JobDatabase, user_id: str, application_id: int) -> Optional[int]:
    """The id of the caller's newest ``form_filled`` event on one application
    (None when there is none). "Send" checks it so a yes names the fill the user saw."""
    cur = await db._db.execute(
        "SELECT MAX(id) AS id FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type = 'form_filled'",
        (application_id, user_id),
    )
    row = await cur.fetchone()
    return None if row is None or row["id"] is None else int(row["id"])


# -- The card (S5d) -----------------------------------------------------------

_BLOCK_PATH = {
    "contact": "user_info.contact", "right_to_work": "user_info.right_to_work",
    "logistics": "user_info.logistics", "languages": "user_info.languages",
    "equality": "user_info.equality", "salary": "preferences.salary_by_country",
}
_MISSING_WORDS = {"work_authorization": "Right to work", "needs_sponsorship": "Sponsorship need"}
_DASH = "—"


def _flag(code: str, text: str, key: Optional[str] = None, country: Optional[str] = None) -> dict[str, Any]:
    return {"code": code, "text": text, "key": key, "country": country}


def _salary_saved(profile: Any, country: str) -> bool:
    """Any salary record with a value for ``country`` counts (one amount or a min / max)."""
    return any(
        str(r.get("country") or "").upper() == country
        and any(kit._present(r.get(k)) for k in ("amount", "min", "max"))
        for r in kit._records(profile.preferences.salary_by_country)
    )


def _missing_flags(profile: Any, missing: list[dict[str, str]], country: Optional[str]) -> list[dict[str, Any]]:
    """The job-country facts the form needs and memory lacks, as flags. ``missing`` passes through
    :func:`kit.kit_missing` (the one rule: only what the form asks counts, so no salary), also for the
    queue preflight, which shares this function."""
    out: list[dict[str, Any]] = []
    for m in kit.kit_missing(missing):
        key = m["key"]
        if key == "job_country":
            out.append(_flag("missing", f"The job's country is not set {_DASH} answer it first", key))
        elif country and key == f"salary.{country}" and not _salary_saved(profile, country):
            out.append(_flag("missing", f"Salary for {country} not saved yet {_DASH} answer it first", key, country))
        elif country and key.startswith(f"right_to_work.{country}."):
            word = _MISSING_WORDS.get(key.rsplit(".", 1)[1], "Right to work")
            out.append(_flag("missing", f"{word} for {country} not saved yet {_DASH} answer it first", key, country))
    return out


async def _brought(db: JobDatabase, user_id: str, ids: list[int]) -> dict[int, dict[str, Any]]:
    marks = ",".join("?" for _ in ids)
    cur = await db._db.execute(
        "SELECT application_id, recorded_by, occurred_at FROM application_events "  # noqa: S608
        f"WHERE user_id = ? AND event_type = 'brought' AND application_id IN ({marks}) ORDER BY id DESC",
        (user_id, *ids),
    )
    return {int(d["application_id"]): d for d in (dict(r) for r in await cur.fetchall())}


async def _doc(db: JobDatabase, user_id: str, application_id: int, kind: str) -> Optional[dict[str, Any]]:
    row = await spine.latest_artifact(db, user_id, application_id, kind)
    if row is None:
        return None
    return {
        "artifact_id": row["id"], "version": row["version_no"], "saved_at": row["created_at"],
        "made_by": row["made_by"],
    }


def _norm(text: Any) -> str:
    """Compare answers the way a person reads them: case and spacing do not matter."""
    return " ".join(str(text).split()).casefold()


def _field_target(key: str) -> Optional[tuple[str, str]]:
    """``(overlay path, field)`` for a memory key that is one plain text field the user
    can change in place (equality, email, phone); None for everything else."""
    head, _, field = key.partition(".")
    if head == "equality" and field in user_info.EQUALITY_KEYS:
        return "user_info.equality", field
    if key in ("contact.email", "contact.phone"):
        return "user_info.contact", field
    return None


def _company_key(company: str) -> Optional[str]:
    return kit.normalized_company_key(company) if company.strip() else None


def _approved_record(
    profile: Any, question: str, company_key: Optional[str], topic: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """The user's approved answer RECORD to ``question`` (or one of its other wordings), for this
    company or any company; the last one in the list wins. None when there is none.
    ``topic`` (the key the fill named) wins over the wording, as check_form matches it: a
    "Current location" saved on an India form is not the answer on a UK form that typed the
    international topic (application 107, 2026-10-20)."""
    q = _norm(question)
    by_topic: Optional[dict[str, Any]] = None
    hit: Optional[dict[str, Any]] = None
    for rec in kit._records(profile.user_info.answers):
        if rec.get("approved") is not True or not kit._present(rec.get("answer")):
            continue
        scoped = str(rec.get("company") or "").strip()
        if scoped and kit.normalized_company_key(scoped) != company_key:
            continue
        if topic and str(rec.get("topic") or "") == topic:
            by_topic = rec
        wordings = [rec.get("question"), *(rec.get("aliases") or [])]
        if q in {_norm(w) for w in wordings if isinstance(w, str)}:
            hit = rec
    return by_topic if by_topic is not None else hit


def _approved_for(
    profile: Any, question: str, company_key: Optional[str], topic: Optional[str] = None
) -> Optional[str]:
    """The answer text of :func:`_approved_record`; None when there is none."""
    rec = _approved_record(profile, question, company_key, topic)
    return None if rec is None else str(rec["answer"])


def _when(value: Any) -> Optional[datetime]:
    """A stored timestamp (ISO text or datetime) as an aware datetime; None when unreadable."""
    if isinstance(value, datetime):
        at = value
    else:
        try:
            at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _after_fill(saved_at: Any, filled_at: Any) -> bool:
    """True only when both times read and ``saved_at`` is later than the fill."""
    s, f = _when(saved_at), _when(filled_at)
    return s is not None and f is not None and s > f


# Why a typed answer has ``changed_to`` (application 122, 2026-10-25): only ``owner_edit`` is the
# user changing it - their web session saved this word AFTER the assistant filled this form (an Edit
# on the card, an answer on Needs you). A word saved BEFORE the fill is memory the assistant did not
# type as saved: ``approved_for_employer`` (approved for this company only) or ``saved_answer``.
OWNER_EDIT, APPROVED_FOR_EMPLOYER, SAVED_ANSWER = "owner_edit", "approved_for_employer", "saved_answer"


def _memory_text(profile: Any, key: str) -> Optional[str]:
    """The saved text behind a ``contact.*`` / ``equality.*`` / ``logistics.*`` key; None
    when the value is not one plain text (a yes/no, a list, a salary)."""
    head, _, field = key.partition(".")
    block = {"contact": profile.user_info.contact, "equality": profile.user_info.equality,
             "logistics": profile.user_info.logistics}.get(head)
    value = block.get(field) if isinstance(block, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def _user_check(
    a: dict[str, Any], profile: Any, saved: dict[str, tuple[Any, Any]], company_key: Optional[str],
    have: dict[str, dict[str, Any]], filled_at: Any = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """(verified, changed_to, changed_reason) for one typed answer, read from the user's OWN word:
    their approved answer to this question, else a memory row they saved on the web
    that still holds this key. ``changed_to`` = the user's word when it differs from
    what the assistant typed; ``changed_reason`` says whose act made it differ (see OWNER_EDIT)."""
    rec = _approved_record(profile, a["question"], company_key, str(a.get("key") or "") or None)
    if rec is not None:
        word = str(rec["answer"])
        if _typed_is(a, word):
            return True, None, None
        by_list = saved.get(user_info.USER_INFO_ANSWERS_PATH, (None, None))[0]
        if (by_list and not is_assistant_actor(str(by_list))
                and _after_fill(rec.get("recorded_at"), filled_at)):
            reason = OWNER_EDIT
        elif str(rec.get("company") or "").strip():
            reason = APPROVED_FOR_EMPLOYER
        else:
            reason = SAVED_ANSWER
        return False, word, reason
    key = str(a.get("key") or "")
    path = _BLOCK_PATH.get(key.split(".", 1)[0], "")
    by, at = saved.get(path, (None, None)) if a["source"] == "memory" and path and key in have else (None, None)
    if not by or is_assistant_actor(str(by)):
        return False, None, None
    text = _memory_text(profile, key)
    if text is not None and not _typed_is(a, text):
        if not _field_target(key):
            return False, None, None
        return False, text, OWNER_EDIT if _after_fill(at, filled_at) else SAVED_ANSWER
    return True, None, None


def _typed_is(a: dict[str, Any], text: str) -> bool:
    """``text`` is what was typed. A hashed answer (G023, ``redacted``) is compared by its sha256
    of the exact text; any other by the normalized wording."""
    if a.get("redacted"):
        return kit.sha256_text(text.strip()) == a.get("value_sha256")
    return _norm(text) == _norm(a["answer"])


_CHANGED_TEXT = {
    OWNER_EDIT: f"You changed an answer: {{q}} {_DASH} your assistant types your answer before it sends",
    APPROVED_FOR_EMPLOYER: "Your approved answer for this employer will be typed: {q}",
    SAVED_ANSWER: "Your saved answer will be typed: {q}",
}


def answer_check(answer: dict[str, Any]) -> str:
    """Owner 2026-10-26 - which of the user's two checks a typed answer needs. ``confirm``: text written
    for THIS job (an essay, a project example), the user reads it each time. ``verify``: a fact reused
    form after form (contact, right to work, logistics, consent, pay, skills, years of experience) - once
    the user has verified it in Memory it is filled without asking."""
    return "confirm" if answer.get("source") == "written" else "verify"


def _answer_rows(
    typed: list[dict[str, Any]], have: dict[str, dict[str, Any]], saved: dict[str, tuple[Any, Any]],
    profile: Any = None, company_key: Optional[str] = None, filled_at: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(answers with saved_by / saved_at / verified / changed_to / changed_reason, the flags they
    raise). ``filled_at`` is when the fill was recorded: only a web save after it is an owner edit."""
    answers: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for a in typed:
        if a.get("redacted"):
            a = {**a, "answer": "", "redacted": True}  # G023: no value kept, only its hash
        key, src = a.get("key"), a["source"]
        by = at = None
        if src in ("memory", "approved", "profile") and key in have:
            at = have[key]["saved_at"]
            if src == "memory":
                by, at = saved.get(_BLOCK_PATH.get(str(key).split(".", 1)[0], ""), (None, at))
        verified, changed_to, reason = (
            _user_check(a, profile, saved, company_key, have, filled_at) if profile is not None
            else (False, None, None)
        )
        answers.append({**a, "key": key, "saved_by": by, "saved_at": at, "verified": verified,
                        "changed_to": changed_to, "changed_reason": reason, "check": answer_check(a)})
        if changed_to is not None:
            # Owner decision 2026-10-19: the user's own word IS verified, and Send stays open -
            # check_submit hands the assistant this answer to type before it submits.
            answers[-1]["verified"] = True
            # The code stays "changed" (Send all holds on it); only the user's own edit says "You changed".
            flags.append(_flag("changed", _CHANGED_TEXT[reason or SAVED_ANSWER].format(q=a["question"]), key))
            continue  # the user's word replaces the typed one: no guessed / blank / memory flag for it
        if src == "guessed" and not verified:
            flags.append(_flag("guessed", f"An answer was guessed: {a['question']}", key))
        if src in ("written", "approved") and not verified:
            # Owner rule: "I draft, you approve" - an essay is typed only after the user approved it.
            flags.append(_flag("unapproved", f"A written answer needs your OK: {a['question']}", key))
        if not verified and kit._question_is_sensitive(str(a["question"]), str(key) if key else None):
            # Owner, 2026-10-22: a private, legal or risky question is the user's to answer, in any country.
            flags.append(_flag("sensitive", f"A private question needs your OK: {a['question']}", key))
        if not a["answer"] and not a.get("redacted"):
            flags.append(_flag("blank", f"An answer is blank: {a['question']}", key))
        if src in ("memory", "approved") and key and key not in have and not verified:
            flags.append(_flag("not_in_memory", f"No longer in memory: {a['question']}", key))
    return answers, flags


async def _unverified_flags(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], pick: Any,
) -> list[dict[str, Any]]:
    """The card's flags for the answers of the NEWEST ``form_filled`` that ``pick`` selects (the SAME reading as
    the card, :func:`_answer_rows`, so a gate and the card never disagree). Scoped by ``user_id`` (rule #12)."""
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    fills = await kit._events(db, user_id, int(app_row["id"]), ("form_filled",))
    typed = fills[0]["payload"].get("answers") if fills else None
    if not isinstance(typed, list):
        return []
    rows = [
        a for a in typed
        if isinstance(a, dict) and "question" in a and ("answer" in a or a.get("redacted")) and pick(a)
    ]
    if not rows:
        return []
    loaded, overlay = load_profile_with_overlay(user_id)
    profile = loaded or UserProfile()
    country = (str(app_row.get("job_country") or "").strip().upper()) or None
    blocks, _missing = kit.compute_answers(profile, overlay, country)
    have = {i["key"]: i for block in blocks.values() for i in block}
    saved = {str(r["path"]): (r.get("set_by"), r.get("set_at")) for r in overlay}
    _answers, flags = _answer_rows(rows, have, saved, profile, _company_key(str(app_row.get("job_company") or "")))
    return flags


async def has_unverified_guess(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> bool:
    """True when the NEWEST ``form_filled`` holds an answer the assistant marked
    ``guessed`` that the user has not stood behind (owner rule 2026-10-19: "if you
    guess you are not verified - you need to ask me"). Only the ``guessed`` flag counts: a
    guess the user EDITED is the user's word (``changed``), and :func:`type_first_lock` holds the
    submit until the form holds it."""
    flags = await _unverified_flags(db, user_id, app_row, lambda a: a.get("source") == "guessed")
    return any(f["code"] == "guessed" for f in flags)


async def has_unapproved_written(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> bool:
    """True when the NEWEST ``form_filled`` holds a ``written`` (or ``approved``) answer the user has not
    approved: no ``user_info.answers`` record for this company (or any) matches its topic or wording
    (owner rule: "I draft, you approve"). The card shows it as an ``unapproved`` flag; check_submit asks."""
    flags = await _unverified_flags(db, user_id, app_row, lambda a: a.get("source") in ("written", "approved"))
    return any(f["code"] == "unapproved" for f in flags)


async def has_unconfirmed_sensitive(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> bool:
    """True when the NEWEST ``form_filled`` holds an answer to a sensitive question (``kit`` decides which
    wording counts) that the user has not stood behind: not their approved answer, not a value they saved
    themselves (owner, 2026-10-22). The card shows it as a ``sensitive`` flag; check_submit asks."""
    flags = await _unverified_flags(
        db, user_id, app_row, lambda a: kit._question_is_sensitive(str(a["question"]), str(a.get("key") or "") or None)
    )
    return any(f["code"] == "sensitive" for f in flags)


def _wordings(row: dict[str, Any], *names: str) -> set[str]:
    """The normalized wordings a row answers to: its question text and its key (whole and last part)."""
    out = {kit._norm_question(row.get(n)) for n in names}
    key = str(row.get("key") or "")
    out |= {kit._norm_question(key), kit._norm_question(key.rsplit(".", 1)[-1])}
    return out - {""}


async def blank_unasked(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> list[str]:
    """Owner rule 2026-10-26 ("ask ALL the blank questions"): the fields the NEWEST ``form_filled`` left blank
    (``fields_skipped``; optional, consent and file fields included) that nobody asked the user about. A
    field is covered when that fill answered it, an open or answered ask on this application holds it in
    its ``questions`` (by key or wording), or the user saved an approved answer to that wording for this
    employer or any (a "Leave blank" counts). Exact normalized matching only. Scoped by ``user_id`` (#12)."""
    from src.services.applications import asks as asks_service  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    app_id = int(app_row["id"])
    fills = await kit._events(db, user_id, app_id, ("form_filled",))
    skipped = fills[0]["payload"].get("fields_skipped") if fills else None
    if not isinstance(skipped, list) or not skipped:
        return []
    typed = fills[0]["payload"].get("answers")
    covered: set[str] = set()
    for a in typed if isinstance(typed, list) else []:
        if isinstance(a, dict) and (str(a.get("answer") or "").strip() or a.get("redacted")):
            covered |= _wordings(a, "question")
    for q in await asks_service.asked_questions(db, user_id, app_id):
        covered |= _wordings(q, "question")
    loaded, _overlay = load_profile_with_overlay(user_id)
    profile = loaded or UserProfile()
    company_key = _company_key(str(app_row.get("job_company") or ""))
    out: list[str] = []
    seen: set[str] = set()
    for label in (str(s) for s in skipped):
        norm = kit._norm_question(label)
        if not norm or norm in covered or norm in seen:
            continue
        seen.add(norm)
        if _approved_record(profile, label, company_key) is None:
            out.append(label)
    return out


def type_first_lock(verdict: Any, type_first: list[dict[str, str]]) -> Any:
    """The LOCK (owner decision 2026-10-19 - not just an instruction): answers the user changed
    on the web hold every submit, even after their yes, until a ``form_swept`` newer than the fill
    reports those exact words in the form (``changed_answers`` returns ``[]`` then). A ``stop`` the
    gate already gave stays as it is (it is no less a stop); ``submit`` and ``ask`` become
    ``stop`` / ``type_first_pending``. A sweep never cancels the yes; a new fill does."""
    from src.services.profile import assistant_settings as settings_rules  # noqa: PLC0415

    if not type_first or verdict.decision == "stop":
        return verdict
    return settings_rules.SubmitDecision(
        "stop", "type_first_pending",
        f"Type the {len(type_first)} answer(s) the user changed (type_first) into their fields, "
        "then record_event form_swept with `answers` (what the form now holds). Then check again.",
    )


def _users_word(profile: Any, saved: dict[str, tuple[Any, Any]], answer: dict[str, Any],
                company_key: Optional[str]) -> Optional[str]:
    """The user's OWN answer to ``answer``'s question - their approved answer, else a plain field
    (equality / email / phone) they saved on the web - the words an Edit on the card writes. None
    when the user has given none."""
    word = _approved_for(profile, str(answer["question"]), company_key)
    if word is not None:
        return word
    key = str(answer.get("key") or "")
    target = _field_target(key)
    by = saved.get(target[0], (None, None))[0] if target else None
    return _memory_text(profile, key) if by and not is_assistant_actor(str(by)) else None


def _answers_by_question(payload: dict[str, Any]) -> Optional[dict[str, dict[str, Any]]]:
    typed = payload.get("answers")
    if not isinstance(typed, list) or not typed:
        return None
    rows = [a for a in typed if isinstance(a, dict) and "question" in a and "answer" in a]
    out = {_norm(a["question"]): a for a in rows}
    return out if len(out) == len(typed) else None  # a repeated or unreadable question: not comparable


async def refill_keeps_answers(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], was_id: int, now_id: int
) -> bool:
    """Gap roadmap slice 8: does the newer ``form_filled`` ``now_id`` still hold the user's yes given on
    ``was_id``? Only when both list their answers, the field count and the questions are the same,
    and every answer that differs is now the user's own word (owner decision 2026-10-19: the
    assistant typing what the user changed - ``type_first`` - never cancels the yes). Any other
    change is a new form the user has not seen. Scoped by ``user_id`` (rule #12)."""
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT id, payload FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type = 'form_filled' AND id IN (?, ?)",
        (app_row["id"], user_id, was_id, now_id),
    )
    fills = {int(r["id"]): _json(r["payload"]) for r in await cur.fetchall()}
    was, now = fills.get(was_id), fills.get(now_id)
    if was is None or now is None or was.get("fields_count") != now.get("fields_count"):
        return False
    before, after = _answers_by_question(was), _answers_by_question(now)
    if before is None or after is None or before.keys() != after.keys():
        return False
    changed = [a for q, a in after.items() if _norm(a["answer"]) != _norm(before[q]["answer"])]
    if not changed:
        return True
    loaded, overlay = load_profile_with_overlay(user_id)
    profile = loaded or UserProfile()
    saved = {str(r["path"]): (r.get("set_by"), r.get("set_at")) for r in overlay}
    company_key = _company_key(str(app_row.get("job_company") or ""))
    for a in changed:
        word = _users_word(profile, saved, a, company_key)
        if word is None or _norm(word) != _norm(a["answer"]):
            return False
    return True


async def changed_answers(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> list[dict[str, str]]:
    """The answers the user changed on the newest filled form: ``[{question, answer}]``
    with the USER's answer, which the assistant must type before it submits. Read
    from the newest ``form_filled`` whether or not the user already said yes."""
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT payload FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type = 'form_filled' ORDER BY id DESC LIMIT 1",
        (app_row["id"], user_id),
    )
    row = await cur.fetchone()
    typed = _json(row["payload"]).get("answers") if row is not None else None
    if not isinstance(typed, list) or not typed:
        return []
    loaded, overlay = load_profile_with_overlay(user_id)
    profile = loaded or UserProfile()
    saved = {str(r["path"]): (r.get("set_by"), r.get("set_at")) for r in overlay}
    country = (str(app_row.get("job_country") or "").strip().upper()) or None
    blocks, _ = kit.compute_answers(profile, overlay, country)
    have = {i["key"]: i for block in blocks.values() for i in block}
    answers, _ = _answer_rows(typed, have, saved, profile, _company_key(str(app_row.get("job_company") or "")))
    pending = [{"question": a["question"], "answer": str(a["changed_to"])}
               for a in answers if a["changed_to"] is not None]
    if not pending:
        return []
    # Owner decision 2026-10-19: an answer is only off this list once a `form_swept` NEWER than
    # the fill reports the form now holds the user's words - the assistant's proof that it typed them.
    # A multi-page form is swept page by page (`page`), so the NEWEST sweep of EACH page counts
    # (a page-2 sweep must not hide the page-1 sweep that holds the answer); unpaged = one page.
    cur = await db._db.execute(
        "SELECT payload FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type = 'form_swept' AND id > (SELECT MAX(id) FROM application_events "
        "WHERE application_id = ? AND user_id = ? AND event_type = 'form_filled') ORDER BY id DESC",
        (app_row["id"], user_id, app_row["id"], user_id),
    )
    newest_per_page: dict[Any, Any] = {}
    for row in await cur.fetchall():
        payload = _json(row["payload"])
        newest_per_page.setdefault(payload.get("page"), payload.get("answers"))
    held_rows = [h for held in newest_per_page.values() for h in held or [] if isinstance(h, dict)]
    in_form = {(_norm(h.get("question", "")), _norm(h.get("answer", ""))) for h in held_rows}
    # G023: a sweep's hashed answer proves the user's words by their sha256.
    hashed = {(_norm(h.get("question", "")), h.get("value_sha256")) for h in held_rows if h.get("redacted")}
    return [
        p for p in pending
        if (_norm(p["question"]), _norm(p["answer"])) not in in_form
        and (_norm(p["question"]), kit.sha256_text(p["answer"].strip())) not in hashed
    ]


async def ready_cards(
    db: JobDatabase, user_id: str, *, limit: int, application_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """The full approval card for each ready application: job, CV / letter
    versions, every answer the assistant typed with where it came from, and the
    flags that keep a card out of "Send all". A flag never blocks the one-by-one
    Send. ``answers`` come only from the stored ``form_filled`` event."""
    from src.services.profile import assistant_settings as settings_rules  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    rows = await ready_rows(db, user_id, limit=limit, application_id=application_id)
    if not rows:
        return []
    loaded, overlay = load_profile_with_overlay(user_id)
    profile = loaded or UserProfile()
    saved = {str(r["path"]): (r.get("set_by"), r.get("set_at")) for r in overlay}
    brought = await _brought(db, user_id, [r["application_id"] for r in rows])
    now = datetime.now(timezone.utc)
    cards: list[dict[str, Any]] = []
    for r in rows:
        app_id = r["application_id"]
        country = (str(r.get("job_country") or "").strip().upper()) or None
        blocks, missing = kit.compute_answers(profile, overlay, country)
        have = {i["key"]: i for block in blocks.values() for i in block}
        typed = r["payload"].get("answers")
        flags: list[dict[str, Any]] = []
        if not isinstance(typed, list) or not typed:
            flags.append(_flag("no_answers", "Your assistant did not list the answers it typed"))
            typed = []
        answers, answer_flags = _answer_rows(
            typed, have, saved, profile, _company_key(r["job_company"] or ""), r["filled_at"]
        )
        flags += answer_flags + _missing_flags(profile, missing, country)
        cv = await _doc(db, user_id, app_id, "cv")
        letter = await _doc(db, user_id, app_id, "cover_letter")
        if cv is None:
            flags.append(_flag("no_cv", "No CV is saved for this application"))
        app_row = {"id": app_id, "job_id": r.get("job_id"), "job_url": r.get("job_url")}
        dup = await kit.duplicate_facts(db, user_id, app_row, now)
        if dup["same_job"] and not await kit._events(db, user_id, app_id, ("duplicate_cleared",)):
            flags.append(_flag("duplicate", "You may have already applied to this job"))
        b = brought.get(app_id)
        score = r.get("fit_score")
        cards.append({
            "application_id": app_id, "form_filled_event_id": r["form_filled_event_id"],
            "filled_at": r["filled_at"], "filled_by": r["filled_by"],
            "job_title": r["job_title"] or "", "job_company": r["job_company"] or "",
            "job_location": r["job_location"] or "", "job_country": country,
            "brought_by": b["recorded_by"] if b else None, "brought_at": b["occurred_at"] if b else None,
            "fit_score": score, "fit_by": r.get("fit_recorded_by") if score is not None else None,
            "line_side": settings_rules.line_side(score, profile.assistant_settings.apply_min_score),
            "cv": cv, "cover_letter": letter, "answers": answers, "flags": flags,
        })
    return cards


# -- Verify / Edit one answer (owner decision 2026-10-18) ---------------------
# The user stands behind an answer (Verify) or types the right one (Edit). Both
# save it as the user's own word, so the assistant reads it next time:
# * a memory answer: its memory row is saved again by the user (it reads
#   confirmed); an equality / email / phone answer sets that field;
# * every other answer, and a non-sensitive memory one: the exact question ->
#   answer is saved as an approved answer (kit ``approved_text``).
# Sensitive answers (equality, salary, visa) stay out of the approved list when
# memory holds them. The filled form itself never changes: an edited answer
# flags the card until the assistant fills the form again with the user's word.


def _path_value(profile: Any, path: str) -> Any:
    head, _, name = path.partition(".")
    return getattr(profile.user_info if head == "user_info" else profile.preferences, name, None)


def _with_approved(
    profile: Any, question: str, text: str, company_key: Optional[str], topic: str = "", company: str = "",
) -> list[dict[str, Any]]:
    """The approved-answers list with ``question`` -> ``text`` replacing any earlier answer
    to the same question (unscoped, or scoped to this company) on the same topic. A same-worded
    answer saved for another topic (another country's right to work) is kept."""
    q = _norm(question)
    keep = [
        dict(r) for r in kit._records(profile.user_info.answers)
        if not (_norm(r.get("question") or "") == q
                and str(r.get("topic") or "").strip() == topic
                and (not str(r.get("company") or "").strip()
                     or kit.normalized_company_key(str(r["company"])) == company_key))
    ]
    return [*keep, {"question": question, "answer": text, "approved": True,
                    **({"topic": topic} if topic else {}), **({"company": company} if company else {})}]


def answer_edits(
    profile: Any, answer: dict[str, Any], text: str, *, edited: bool, company_key: Optional[str],
    company: str = "",
) -> list[tuple[str, Any]]:
    """The memory writes that make ``text`` the user's own answer to ``answer``."""
    key = str(answer.get("key") or "")
    memory = answer["source"] == "memory" and bool(key)
    sensitive = memory and kit._is_sensitive(key.split(".", 1)[0], key)
    out: list[tuple[str, Any]] = []
    target = _field_target(key) if memory else None
    if target is not None:
        path, field = target
        out.append((path, {**(_path_value(profile, path) or {}), field: text}))
    elif memory and not edited:
        path = _BLOCK_PATH.get(key.split(".", 1)[0], "")
        value = _path_value(profile, path) if path else None
        if kit._present(value):
            out.append((path, value))  # the same value, now saved by the user
    if not sensitive or not out:
        # A written answer is for this employer only: a why-this-company essay is never reused elsewhere.
        scoped = company if answer["source"] == "written" else ""
        approved = _with_approved(profile, answer["question"], text, company_key, key, scoped)
        out.append((user_info.USER_INFO_ANSWERS_PATH, approved))
    return out


async def confirm_answer(
    db: JobDatabase, user_id: str, actor: str, application_id: int, *,
    form_filled_event_id: int, index: int, new_text: Optional[str],
) -> list[str]:
    """Verify (``new_text`` None) or Edit one answer on a ready card; returns the paths
    written. Raises ``ProfileEditError`` 404 / 409 / 422 and writes nothing on a refusal."""
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile, load_profile_with_overlay, save_profile  # noqa: PLC0415

    cards = await ready_cards(db, user_id, limit=1, application_id=application_id)
    if not cards:
        raise profile_edits.ProfileEditError(404, "This form is no longer waiting for you")
    card = cards[0]
    if card["form_filled_event_id"] != form_filled_event_id:
        raise profile_edits.ProfileEditError(409, "The filled form changed since you looked - look again")
    if not 0 <= index < len(card["answers"]):
        raise profile_edits.ProfileEditError(422, "No such answer on this form")
    answer = card["answers"][index]
    if new_text is None and answer.get("redacted"):
        raise profile_edits.ProfileEditError(
            422, "This sensitive answer was not kept (you did not ask to remember it) - use Edit to type it"
        )
    text = (answer["answer"] if new_text is None else new_text).strip()
    if not text:
        raise profile_edits.ProfileEditError(
            422, "This answer is blank - use Edit to type it" if new_text is None else "Type an answer first"
        )
    loaded, _ = load_profile_with_overlay(user_id)
    profile = loaded or UserProfile()
    writes = answer_edits(profile, answer, text, edited=new_text is not None,
                          company_key=_company_key(card["job_company"]), company=card["job_company"])
    for path, value in writes:
        profile_edits.validate_edit(path, value)  # all-or-nothing: a bad value writes nothing
    if load_profile(user_id, with_overlay=False) is None:
        save_profile(UserProfile(), user_id, source_action="agent_edit")
    profile_edits.record_edits(
        user_id, actor, writes, enforce_rate_limit=False, source=profile_edits.SOURCE_USER_SAID,
    )
    return [path for path, _ in writes]
