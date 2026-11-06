"""Memory is filled once, then only the user changes it (owner 2026-11-05, memory-first plan S2b).

"Country page is set once ... where the user directly changes in Job360. Only instructions are added from
chat." and "Setups will still be allowed to fill at the first time, through chat."

An ASSISTANT write (any non-web actor, :func:`edits.is_assistant_actor`) to a Memory path may only fill
what is still EMPTY (absent / ``""`` / ``null`` / ``[]`` / ``{}``; ``false`` is a set value). A new row
(a country with no row yet, a new language) is filling empty; inside a row that exists, only its empty
fields may be filled. A row's ``instructions`` stay writable from chat, and ``set_by`` is the server's
draft mark, not a fact. The user's own web edits never come here. :func:`overwrites` lists what a write
would change; ``edits.record_edits`` refuses the whole call (422, nothing saved) when it names anything.
"""
from __future__ import annotations

from typing import Any, Optional

MEMORY_PATHS: frozenset[str] = frozenset({
    "user_info.right_to_work", "user_info.logistics", "preferences.salary_by_country",
    "user_info.equality", "user_info.languages", "user_info.contact",
})
# Path -> the field that names each row of its list (a top-level list path, or a dict path's `countries`).
_ROW_KEY = {"preferences.salary_by_country": "country", "user_info.languages": "language"}
# Row fields an assistant may always change: the user's instructions are added from chat; set_by is a stamp.
_FREE_ROW_FIELDS = frozenset({"instructions", "set_by"})


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def _key(rec: dict[str, Any], field: str) -> str:
    text = " ".join(str(rec.get(field) or "").split())
    return text.lower() if field == "language" else text.upper()


def _rows(records: Any, field: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in records if isinstance(records, list) else []:
        if isinstance(rec, dict) and _key(rec, field):
            out.setdefault(_key(rec, field), rec)
    return out


def _row_changes(where: str, old: Any, new: Any, field: str) -> list[tuple[str, Optional[str]]]:
    """``(label, country)`` for every set field of an existing row that ``new`` changes or drops."""
    out: list[tuple[str, Optional[str]]] = []
    fresh = _rows(new, field)
    for key, rec in _rows(old, field).items():
        cc = key if field == "country" else None
        now = fresh.get(key)
        if now is None:
            out.append((f"{where} {key} row", cc))
            continue
        for name, value in rec.items():
            if name == field or name in _FREE_ROW_FIELDS or _empty(value):
                continue
            if now.get(name) != value:
                out.append((f"{where} {key} {name}", cc))
    return out


def overwrites(path: str, old: Any, new: Any) -> list[tuple[str, Optional[str]]]:
    """What a write of ``new`` over the stored ``old`` would change that is already set: ``(label,
    country or None)`` pairs; ``[]`` when it only fills empty fields (or changes nothing)."""
    if path not in MEMORY_PATHS:
        return []
    if path in _ROW_KEY:
        return _row_changes(path, old, new, _ROW_KEY[path])
    out: list[tuple[str, Optional[str]]] = []
    fresh = new if isinstance(new, dict) else {}
    for name, value in (old if isinstance(old, dict) else {}).items():
        if _empty(value):
            continue
        if name == "countries":
            out += _row_changes(f"{path}.countries", value, fresh.get(name), "country")
        elif fresh.get(name) != value:
            out.append((f"{path} {name}", None))
    return out


def refusal(changes: list[tuple[str, Optional[str]]]) -> str:
    """The 422 detail: each set field, and the page the user changes it on in Job360."""
    lines = [f"{label} is already set; the user changes it on their {cc or 'Memory'} page in Job360."
             for label, cc in changes]
    return " ".join(lines) + " Only empty fields can be filled from chat. Nothing was saved."
