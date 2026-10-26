"""The common form questions (owner, 2026-10-26).

"If you observe 10-15 jobs there is a set of questions that are permanent in all the forms. Ask those in
Memory and get them verified. Only the ones that change with the job role are asked on Needs you."

The list is DATA (``src/data/common_form_questions.json``). Each question's answer is read from where the
kit already keeps it - a structured Memory field (``kit_key``, one row per saved country when it holds
``{cc}``) or the user's approved answer with that ``topic`` (``user_info.answers``) - through
:func:`kit.compute_answers`, so Memory and the form filler can never disagree. ``verified`` = the kit
reads it ``confirmed`` (the user stood behind it); the assistant fills a verified answer without asking.
Nothing is guessed: no saved answer = ``answer`` None. Read-only.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "common_form_questions.json"
GROUPS: tuple[str, ...] = ("you", "experience", "work", "pay", "form", "equality")
_COUNTRY_KEYS = ("{cc}",)


@lru_cache(maxsize=1)
def catalog() -> tuple[dict[str, Any], ...]:
    """The questions, checked once: unique ids, a known group, exactly one of kit_key / topic."""
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["questions"]
    seen: set[str] = set()
    for q in raw:
        if q["id"] in seen or q["group"] not in GROUPS or bool(q.get("kit_key")) == bool(q.get("topic")):
            raise ValueError(f"common_form_questions.json: bad entry {q.get('id')!r}")
        seen.add(q["id"])
    return tuple(raw)


def _countries(profile: Any) -> list[str]:
    """The countries the user saved a right-to-work or salary record for (not ``ANY``), in saved order."""
    out: list[str] = []
    rtw = getattr(getattr(profile, "user_info", None), "right_to_work", None) or {}
    salary = getattr(getattr(profile, "preferences", None), "salary_by_country", None) or []
    for rec in [*(rtw.get("countries") or []), *salary]:
        cc = str((rec or {}).get("country") or "").upper() if isinstance(rec, dict) else ""
        if cc and cc != "ANY" and cc not in out:
            out.append(cc)
    return out


def _shown(value: Any) -> str:
    """One readable line for a kit value (a salary dict, a yes/no, a list)."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        if "min" in value:
            low, high = value.get("min"), value.get("max")
            span = f"{low}" if high in (None, low) else f"{low}-{high}"
            return " ".join(str(p) for p in (span, value.get("currency") or "", value.get("period") or "") if p)
        if "amount" in value:
            return " ".join(str(value.get(k) or "") for k in ("amount", "currency", "period")).strip()
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _row(q: dict[str, Any], item: Optional[dict[str, Any]], country: Optional[str]) -> dict[str, Any]:
    text = q["question"].replace("{country}", country or "")
    return {
        "id": q["id"] if country is None else f"{q['id']}.{country}",
        "group": q["group"], "question": text, "country": country,
        "kit_key": (q.get("kit_key") or "").replace("{cc}", country or "") or None,
        "topic": q.get("topic"),
        "answer": _shown(item["value"]) if item is not None else None,
        "source": item.get("source") if item is not None else None,
        "state": item.get("state") if item is not None else "missing",
        "verified": item is not None and item.get("state") == "confirmed",
        "seen": int(q.get("seen") or 0),
    }


def common_questions(profile: Any, overlay: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every common question with the user's saved answer and whether they verified it. One row per
    saved country for a per-country question (none when no country is saved: the kit has nothing)."""
    from src.services.applications import kit  # noqa: PLC0415 - lazy: kit is large

    countries = _countries(profile)
    by_country = {cc: kit.compute_answers(profile, overlay, cc)[0] for cc in countries}
    plain, _ = kit.compute_answers(profile, overlay, None)

    def items(answers: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
        return {str(i["key"]): i for block in answers.values() for i in block}

    def by_topic(answers: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
        return {str(i["topic"]): i for i in answers.get("approved_text", []) if i.get("topic")}

    rows: list[dict[str, Any]] = []
    for q in catalog():
        key = q.get("kit_key")
        if key and "{cc}" in key:
            rows += [_row(q, items(by_country[cc]).get(key.replace("{cc}", cc)), cc) for cc in countries]
        elif key:
            rows.append(_row(q, items(plain).get(key), None))
        else:
            rows.append(_row(q, by_topic(plain).get(q["topic"]), None))
    return rows


def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many rows are verified, saved but not verified, and not answered yet."""
    verified = sum(1 for r in rows if r["verified"])
    missing = sum(1 for r in rows if r["answer"] is None)
    return {"total": len(rows), "verified": verified, "to_verify": len(rows) - verified - missing, "missing": missing}
