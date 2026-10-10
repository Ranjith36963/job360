"""Ready to send - the applications whose form the assistant filled and the
user has not yet answered (S5b), and the approval card for each (S5d).

An application is READY when the user owns it, it is still ``considering``, it
has no receipt, its newest ``form_filled`` event is F, and no ``submit_approved``
or ``submit_declined`` event came after F (a decline then a NEW fill is ready
again). One SQL, no per-row query. Always scoped by ``user_id`` (rule #12).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import kit, spine
from src.services.applications.kit import _json

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
    """The job-country facts the form needs and memory lacks, as flags."""
    out: list[dict[str, Any]] = []
    for m in missing:
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


def _answer_rows(
    typed: list[dict[str, Any]], have: dict[str, dict[str, Any]], saved: dict[str, tuple[Any, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(answers with saved_by / saved_at, the flags they raise)."""
    answers: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for a in typed:
        key, src = a.get("key"), a["source"]
        by = at = None
        if src in ("memory", "approved", "profile") and key in have:
            at = have[key]["saved_at"]
            if src == "memory":
                by, at = saved.get(_BLOCK_PATH.get(str(key).split(".", 1)[0], ""), (None, at))
        answers.append({**a, "key": key, "saved_by": by, "saved_at": at})
        if src == "guessed":
            flags.append(_flag("guessed", f"An answer was guessed: {a['question']}", key))
        if not a["answer"]:
            flags.append(_flag("blank", f"An answer is blank: {a['question']}", key))
        if src in ("memory", "approved") and key and key not in have:
            flags.append(_flag("not_in_memory", f"No longer in memory: {a['question']}", key))
    return answers, flags


async def ready_cards(
    db: JobDatabase, user_id: str, *, limit: int, application_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """The full approval card for each ready application: job, CV / letter
    versions, every answer the assistant typed with where it came from, and the
    flags that keep a card out of "Send all". A flag never blocks the one-by-one
    Send. ``answers`` come only from the stored ``form_filled`` event."""
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
        answers, answer_flags = _answer_rows(typed, have, saved)
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
            "cv": cv, "cover_letter": letter, "answers": answers, "flags": flags,
        })
    return cards
