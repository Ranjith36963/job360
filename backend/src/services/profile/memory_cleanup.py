"""Memory first (owner 2026-11-05, plan S3): old saved answers that Memory now covers.

Since S1 a form question in Memory's families (:func:`ask_once.memory_question`: right to work, sponsorship,
salary, notice, relocation, location, equality, contact, languages) is answered from Memory, never from a
saved answer, and since S2 no new one is saved. The old ``user_info.answers`` records in those families stay
until the user deletes them. :func:`covered` lists each one beside what Memory says for its family and
country (the country page, else its region's, else ANY's, exactly as S1 reads it) and a verdict. Deleting
is the user's own click on Needs you (a session-only route, op ``remove_at``); nothing deletes on its own.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

VERDICTS = ("same", "different", "memory_empty")
_MEMORY_SOURCES = ("memory", "profile")


def record_country(rec: dict[str, Any]) -> Optional[str]:
    """The country a saved answer is for: its own ``country``, else the one country code its topic names
    (``sponsorship.GB.yes_no``), else ``None``."""
    codes = [p for p in str(rec.get("topic") or "").split(".") if len(p) == 2 and p.isalpha() and p.isupper()]
    return str(rec.get("country") or "").upper() or (codes[0] if len(codes) == 1 else None)


def answer_id(rec: dict[str, Any]) -> str:
    """A stable id for one record: its question, country and answer (two records never share the first
    two, ``user_info._answers``). It changes when the answer does, so a stale delete finds nothing."""
    text = "|".join([" ".join(str(rec.get("question") or "").lower().split()), str(rec.get("country") or ""),
                     str(rec.get("answer") or "")])
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _same(saved: str, memory: str) -> bool:
    """The saved answer says what Memory says: the same words, or Memory's Yes/No as its first word."""
    from src.services.profile.answer_match import normalize_question  # noqa: PLC0415

    a, b = normalize_question(saved), normalize_question(memory)
    return a == b or (b in ("yes", "no") and a.split()[:1] == [b])


def _memory_says(by_key: dict[str, dict[str, Any]], template: Optional[str], country: Optional[str]) -> Optional[str]:
    """What Memory says for the family (S1's ``kit._memory_answer``); a set value that is no yes/no is
    shown as it is. ``None`` = Memory says nothing."""
    from src.services.applications import kit  # noqa: PLC0415 - kit imports this package
    from src.services.profile.common_questions import _shown  # noqa: PLC0415

    item, _why = kit._memory_answer(by_key, template, country)
    if item is not None:
        return str(item["value"])
    raw = by_key.get(template.replace("{cc}", country or "")) if template else None
    if raw is None or raw.get("source") not in _MEMORY_SOURCES:
        return None
    value = raw["value"]
    return str(_shown(value) if isinstance(value, (dict, list)) else value)


def covered(profile: Any) -> list[dict[str, Any]]:
    """Every saved answer in Memory's families, in stored order: ``{id, index, question, answer, country,
    topic, memory, verdict, saved_by}`` (``memory`` ``None`` = ``memory_empty``). Read-only."""
    from src.services.applications import kit  # noqa: PLC0415
    from src.services.profile.ask_once import memory_question  # noqa: PLC0415

    kits: dict[Optional[str], dict[str, dict[str, Any]]] = {}
    out: list[dict[str, Any]] = []
    for index, rec in enumerate(profile.user_info.answers or []):
        if not isinstance(rec, dict):
            continue
        topic, question = str(rec.get("topic") or ""), str(rec.get("question") or "")
        answer = str(rec.get("answer") or "")
        family, template = memory_question(topic, question)
        if not family:
            continue
        country = record_country(rec)
        if country not in kits:
            blocks, _missing = kit.compute_answers(profile, [], country, None)
            kits[country] = {str(i["key"]): i for block in blocks.values() for i in block}
        memory = _memory_says(kits[country], template, country)
        verdict = "memory_empty" if memory is None else "same" if _same(answer, memory) else "different"
        out.append({
            "id": answer_id(rec), "index": index, "question": question, "answer": answer, "country": country,
            "topic": topic or None, "memory": memory, "verdict": verdict, "saved_by": rec.get("saved_by"),
        })
    return out
