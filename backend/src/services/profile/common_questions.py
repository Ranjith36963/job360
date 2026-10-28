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
# Owner 2026-10-26: "from memory" (the user's own), "guessed" (an assistant's draft), and "not known" when
# not even a guess is saved yet. The user can edit every one.
LABELS: tuple[str, ...] = ("memory", "guessed", "not_known")
# Where an answer to a kit_key row is saved (update_profile path), by the key's first part.
_SAVE_PATH = {
    "contact": "user_info.contact", "logistics": "user_info.logistics", "equality": "user_info.equality",
    "right_to_work": "user_info.right_to_work", "salary": "preferences.salary_by_country",
}
_SAVE_PATH_EXACT = {"contact.name": "cv_data.name", "contact.github_url": "cv_data.links"}
# "experience" left with the CV-answered rows (plan B1): the CV answers title / years / education.
GROUPS: tuple[str, ...] = ("you", "work", "pay", "form", "equality")
# Ids whose answer is sensitive (plan section 5): visa, salary, address, birth date; equality is a group.
_SENSITIVE_IDS = frozenset(
    {"visa_type", "visa_expires", "expected_salary", "current_salary", "address", "date_of_birth"})
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


def save_path(row: dict[str, Any]) -> str:
    """The update_profile path a row's answer is saved to (a topic row: ``user_info.answers``)."""
    key = row.get("kit_key")
    if not key:
        return "user_info.answers"
    return _SAVE_PATH_EXACT.get(key) or _SAVE_PATH[key.split(".", 1)[0]]


def _is_sensitive(q: dict[str, Any]) -> bool:
    return q["group"] == "equality" or q["id"] in _SENSITIVE_IDS


# Who stands behind an answer, read off the kit item's OWN state (the kit judges each answer, so a web
# save of one field never vouches for another; review of #1357). relayed = the user's chat word that an
# assistant wrote down; inferred / sensitive = an assistant's draft the user has not confirmed.
_SET_BY = {"confirmed": "user", "relayed": "assistant", "inferred": "assistant", "sensitive": "assistant"}
_GUESS_SOURCE = {"relayed": "user_said", "inferred": "agent_inferred", "sensitive": "agent_inferred"}


def _row(q: dict[str, Any], item: Optional[dict[str, Any]], country: Optional[str],
         relayed: frozenset[str] = frozenset()) -> dict[str, Any]:
    """``relayed`` = the save paths whose newest row an assistant wrote as the user's chat word: the kit
    folds an unconfirmed private answer into ``sensitive``, so this tells how that guess got here."""
    text = q["question"].replace("{country}", country or "")
    sensitive = _is_sensitive(q)
    state = str(item.get("state") or "") if item is not None else ""
    # Verified = the kit reads this very answer confirmed (an assistant's user_said write reads relayed,
    # an unconfirmed private one sensitive); an approved answer must also be the user's own write.
    verified = item is not None and state == "confirmed" and (
        item.get("source") != "approved_text" or bool(item.get("by_user")))
    if sensitive and state == "relayed":
        verified = False  # plan B1: a private answer is "From memory" only from the user's own save
    label = "not_known" if item is None else ("memory" if verified else "guessed")
    return {
        "id": q["id"] if country is None else f"{q['id']}.{country}",
        "group": q["group"], "question": text, "country": country,
        "kit_key": (q.get("kit_key") or "").replace("{cc}", country or "") or None,
        "topic": q.get("topic"),
        "answer": _shown(item["value"]) if item is not None else None,
        "source": item.get("source") if item is not None else None,
        "state": item.get("state") if item is not None else "missing",
        # Verified = the USER stood behind it: an approved answer an assistant saved itself is not (review
        # of #1263: an assistant can write approved: true).
        "verified": verified,
        # Owner 2026-10-26: "memory" (the user's own), "guessed" (anything else the assistant drafted), or
        # "not_known" until the assistant drafts it (get_profile common_questions_open).
        "label": label,
        "seen": int(q.get("seen") or 0),
        "per_job": bool(q.get("per_job")),
        "save_to": save_path({"kit_key": q.get("kit_key")}),
        "sensitive": sensitive,
        # Who stands behind this answer ("user" / "assistant") and when its block was last saved.
        "set_by": ("user" if verified else _SET_BY.get(state, "assistant")) if item is not None else None,
        "set_at": (str(item["saved_at"]) if item.get("saved_at") else None) if item is not None else None,
        # How an assistant's draft got here (agent_inferred / user_said); None unless the row reads guessed.
        "guess_source": (
            ("user_said" if state == "sensitive" and save_path(q) in relayed else _GUESS_SOURCE.get(state))
            if label == "guessed" else None
        ),
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

    # An answer the assistant drafted for the user to check (approved: false) is a guess: shown, not used.
    drafts = {
        str(r["topic"]): {"value": r["answer"], "source": "guessed", "state": "guessed"}
        for r in (getattr(getattr(profile, "user_info", None), "answers", None) or [])
        if isinstance(r, dict) and r.get("topic") and r.get("approved") is not True
        and str(r.get("answer") or "").strip()
    }

    def by_topic(answers: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
        return {**drafts, **{str(i["topic"]): i for i in answers.get("approved_text", []) if i.get("topic")}}

    from src.services.profile.edits import is_assistant_actor  # noqa: PLC0415

    relayed = frozenset(
        str(r["path"]) for r in overlay if isinstance(r, dict) and r.get("path")
        and is_assistant_actor(str(r.get("set_by") or "")) and str(r.get("source") or "") == "user_said"
    )
    rows: list[dict[str, Any]] = []
    for q in catalog():
        key = q.get("kit_key")
        if key and "{cc}" in key:
            rows += [_row(q, items(by_country[cc]).get(key.replace("{cc}", cc)), cc, relayed) for cc in countries]
        elif key:
            rows.append(_row(q, items(plain).get(key), None, relayed))
        else:
            rows.append(_row(q, by_topic(plain).get(q["topic"]), None, relayed))
    return rows


def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many rows are verified, saved but not verified, and not answered yet (per-job rows skipped)."""
    rows = [r for r in rows if not r.get("per_job")]
    verified = sum(1 for r in rows if r["verified"])
    missing = sum(1 for r in rows if r["answer"] is None)
    return {"total": len(rows), "verified": verified, "to_verify": len(rows) - verified - missing, "missing": missing}


def open_questions(profile: Any) -> list[dict[str, str]]:
    """The rows with no answer at all - what the assistant drafts next, as Guessed, from the profile.
    Reads the base profile only (no overlay needed to know whether an answer exists)."""
    return [
        {"id": r["id"], "question": r["question"], "save_to": save_path(r),
         **({"topic": r["topic"]} if r.get("topic") else {"kit_key": r["kit_key"]})}
        for r in common_questions(profile, []) if r["answer"] is None and not r["per_job"]
    ]
