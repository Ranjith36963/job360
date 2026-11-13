"""Memory first (owner 2026-11-05): an old ask that Memory now answers closes itself.

Since #1506 a form question in Memory's families (:func:`ask_once.memory_question`) is answered from Memory
(the job country's page, else its region's, else ANY's), so no NEW ask is filed for it. Asks filed before
that stay open and keep blocking their jobs (production: application 661, ask 451, ``sponsorship.gb.yes_no``
on a GB job whose GB page says ``needs_sponsorship: false``). :func:`covered` says when EVERY question of an
open ask is a Memory fact the user has set for THAT job's country; :func:`close_covered` withdraws each such
ask through ``asks.withdraw_ask``, as Job360 (``system``) with the reason :data:`MEMORY_ANSWERS`, never as
the user, so the answer history stays true.

It runs where the asks are read anyway: the kit and check_submit for that job, the user's own Needs-you
read, and after a Memory save. There is no background worker. Never touched: an answered or partly answered
ask, one that is not open, a captcha / login / upload / safety ask, a proof ask, one the user wrote on the
web, and an ask on a job whose country is unknown. An ask tied to no job closes when every row is a
``right_to_work.<cc>.*`` fact Memory holds for THAT row's country (:func:`covered_jobless`, 2026-11-13).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications import asks
from src.services.applications.spine import SpineError
from src.utils.logger import safe_log_value

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

MEMORY_ANSWERS = "memory_answers"
MEMORY_ACTOR = asks.TIDY_ACTOR  # Job360 itself, never the user (the web shows "Job360")
CLOSED_DETAIL = "Closed by Job360: your Memory now answers this for this job's country."
# Memory paths a save of which may answer an open ask (kit.compute_answers reads these).
MEMORY_PATHS = frozenset({
    "user_info.right_to_work", "user_info.logistics", "user_info.contact", "user_info.equality",
    "user_info.languages", "preferences.salary_by_country",
})
# Only the user's own word closes an ask: ``confirmed``, or ``relayed`` (their chat words, kept by the kit
# only for a non-sensitive fact). ``inferred`` (an assistant's guess) and ``sensitive`` (an equality, visa
# or salary value the user has not confirmed, which #1506 shows on each form first) leave it open.
_USERS_WORD = ("confirmed", "relayed")


_UK = {"uk": "GB"}  # a key may say uk; Memory's page is GB


def row_country(row: Any) -> Optional[str]:
    """The country a job-less ask's row names: a ``right_to_work.<cc>.*`` key (``uk`` read as GB), else None."""
    parts = str((row or {}).get("key") or "").split(".") if isinstance(row, dict) else []
    if len(parts) < 3 or parts[0] != "right_to_work" or len(parts[1]) != 2 or not parts[1].isalpha():
        return None
    return _UK.get(parts[1].lower(), parts[1].upper())


def covered_jobless(ask: dict[str, Any], memory: dict[str, dict[str, dict[str, Any]]]) -> bool:
    """Pure (production 2026-11-13, asks 583-588): a job-less ask is covered when EVERY row is a
    ``right_to_work.<cc>.*`` fact (:func:`row_country`) that :func:`covered` finds in Memory for ITS country.
    ``memory`` = ``{cc: kit items by key}`` for every country the rows name."""
    rows = ask.get("questions") or []
    if ask.get("application_id") is not None or not rows:
        return False
    for row in rows:
        cc = row_country(row)
        if cc is None or cc not in memory:
            return False
        # The key names the fact; with its country in upper case memory_question reads it as that kit key.
        own = {**row, "key": ".".join(["right_to_work", cc, *str(row["key"]).split(".")[2:]])}
        if not covered({**ask, "application_id": 0, "questions": [own]}, cc, memory[cc]):
            return False
    return True


def covered(ask: dict[str, Any], country: Optional[str], by_key: dict[str, dict[str, Any]]) -> bool:
    """Pure: True when ``ask`` is an open, unanswered question ask and EVERY row maps to one Memory fact
    (:func:`ask_once.memory_question`) that Memory holds for ``country`` in the user's own word, read as the
    form would (:func:`kit._memory_answer`). ``by_key`` is :func:`kit.compute_answers`' items by key for that
    country. Unset is absent (rule #29); a mixed ask, an unknown country or a row with no single fact is
    never covered."""
    if not country or ask.get("status") != "open" or ask.get("application_id") is None:
        return False
    if ask.get("kind") not in asks.QUESTION_FAMILY or ask.get("asked_by") == "web":
        return False
    if str(ask.get("context") or "").startswith("proof_missing:"):
        return False
    if ask.get("answers") or ask.get("answer") or ask.get("user_words"):
        return False  # answered, or some rows saved by the user: theirs to finish
    rows = ask.get("questions") or []
    if not rows:
        return False
    return all(row_covered(row, country, by_key) for row in rows)


def row_covered(row: Any, country: Optional[str], by_key: dict[str, dict[str, Any]]) -> bool:
    """Pure: ONE question row is a Memory fact the user has set, in their own word, for ``country`` (the rule
    :func:`covered` applies to every row of an ask; the Needs-you blockers use it per row, so the two never drift)."""
    from src.services.applications import kit  # noqa: PLC0415 - kit imports this package
    from src.services.profile.ask_once import memory_question  # noqa: PLC0415

    if not country or not isinstance(row, dict):
        return False
    family, template = memory_question(str(row.get("key") or "") or None, str(row.get("question") or ""))
    if not family or template is None:
        return False
    item, _why = kit._memory_answer(by_key, template, country)
    return item is not None and item.get("source") in ("memory", "profile") and item.get("state") in _USERS_WORD


async def close_covered(db: JobDatabase, user_id: str, application_id: Optional[int] = None) -> int:
    """Withdraw every open ask of ``user_id`` (only ``application_id``'s when given) that :func:`covered`
    says Memory answers, as Job360 with the reason :data:`MEMORY_ANSWERS`. Returns how many closed.

    A side task of a read (kit, check_submit, Needs you) or a save: any failure here is logged and the caller
    goes on, never an error of the read itself."""
    try:
        return await _close_covered(db, user_id, application_id)
    except Exception:  # noqa: BLE001 - a side task never breaks the read or save that runs it
        logger.warning(
            "memory_closes_failed", exc_info=True,
            extra={"event": "memory_closes_failed", "user_id": safe_log_value(user_id),
                   "application_id": safe_log_value(application_id)},
        )
        return 0


async def _close_covered(db: JobDatabase, user_id: str, application_id: Optional[int]) -> int:
    import asyncio  # noqa: PLC0415 - stdlib, only this reader needs it

    from src.services.applications import kit  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    # A question ask (asks.QUESTION_FAMILY: "" or "question") an assistant filed; never one the user wrote.
    sql = (
        "SELECT k.id, a.job_country, k.application_id FROM application_asks k LEFT JOIN applications a "
        "ON a.id = k.application_id AND a.user_id = k.user_id WHERE k.user_id = ? AND k.answered_at IS NULL "
        "AND k.withdrawn_at IS NULL AND k.kind IN ('', 'question') AND k.asked_by <> 'web' "
    )
    if application_id is None:
        cur = await db._db.execute(sql + "ORDER BY k.id", (user_id,))
    else:
        cur = await db._db.execute(sql + "AND k.application_id = ? ORDER BY k.id", (user_id, application_id))
    rows: list[tuple[int, str]] = []
    jobless: list[int] = []  # asks tied to no job: each row names its own country (covered_jobless)
    for r in await cur.fetchall():
        country = str(r[1] or "").strip().upper()
        if r[2] is None:
            jobless.append(int(r[0]))
        elif country:
            rows.append((int(r[0]), country))
    if not rows and not jobless:
        return 0
    # Off the event loop. Safe although writes follow: every await below already separates this read from
    # them, and each withdraw is guarded in the database (``answered_at IS NULL AND withdrawn_at IS NULL``,
    # 409 on no rowcount), so an ask answered in the gap is never closed behind the user's back.
    loaded, overlay = await asyncio.to_thread(load_profile_with_overlay, user_id)
    profile = loaded or UserProfile()
    memory: dict[str, dict[str, dict[str, Any]]] = {}
    closed = 0

    def memory_of(country: str) -> dict[str, dict[str, Any]]:
        if country not in memory:
            blocks, _missing = kit.compute_answers(profile, overlay, country, None)
            memory[country] = {str(i["key"]): i for block in blocks.values() for i in block}
        return memory[country]

    for ask_id, country in [*rows, *((i, "") for i in jobless)]:
        ask = await asks._get_ask(db, user_id, ask_id)
        if ask is None:
            continue
        if country:
            hit = covered(ask, country, memory_of(country))
        else:
            ccs = {row_country(q) for q in ask.get("questions") or []}
            hit = None not in ccs and covered_jobless(ask, {cc: memory_of(cc) for cc in ccs if cc})
        if not hit:
            continue
        try:
            await asks.withdraw_ask(db, user_id, ask_id, MEMORY_ACTOR, MEMORY_ANSWERS, detail=CLOSED_DETAIL)
            closed += 1
            logger.info("memory_closed_ask", extra={"event": "memory_closed_ask", "user_id": safe_log_value(user_id),
                                                    "ask_id": ask_id, "jobless": not country})
        except SpineError:  # answered or withdrawn in between: nothing left to close
            continue
    return closed
