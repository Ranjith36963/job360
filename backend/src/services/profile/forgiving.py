"""Forgiving inputs — turn what an assistant sent into the stored shape, and SAY so.

Owner decision 2026-10-10 ("never lose an answer"): a value that means the right
thing but is spelled differently ("United Kingdom", "junior", "on-site", true for
an equality answer) is normalised and stored, not refused. Every normalisation is
reported back as ``{path, rule}`` where ``rule`` is a CLOSED code (never a value).
An unknown KEY or a value with no safe mapping is still refused, never dropped.

The collector is a context variable: ``recording()`` wraps one validation pass in
the route; ``note()`` is a no-op when nothing is recording, so every other caller of
``validate_edit`` is untouched.
"""
from __future__ import annotations

import contextlib
import re
from contextvars import ContextVar
from typing import Any, Iterator

# Closed rule codes (what ``normalized[].rule`` may say).
RULE_COUNTRY = "country_name_to_iso2"
RULE_BOOL_YESNO = "bool_to_yes_no"
RULE_STR_LIST = "string_to_list"
RULE_LEVEL = "experience_level_alias"
RULE_ARRANGEMENT = "work_arrangement_alias"
RULE_EVERY = "check_every_alias"
RULE_KEY = "key_alias"
RULE_COUNTRY_KEYED = "country_keyed_to_records"
RULE_LICENCE = "driving_licence_split"
RULE_WORK_AUTH = "work_authorization_alias"
RULES: frozenset[str] = frozenset({
    RULE_COUNTRY, RULE_BOOL_YESNO, RULE_STR_LIST, RULE_LEVEL, RULE_ARRANGEMENT, RULE_EVERY,
    RULE_KEY, RULE_COUNTRY_KEYED, RULE_LICENCE, RULE_WORK_AUTH,
})

_sink: ContextVar[list[dict[str, str]] | None] = ContextVar("forgiving_sink", default=None)
_where: ContextVar[str] = ContextVar("forgiving_where", default="")


@contextlib.contextmanager
def recording() -> Iterator[list[dict[str, str]]]:
    """Collect every ``note()`` made inside the block (de-duplicated, in order)."""
    notes: list[dict[str, str]] = []
    token = _sink.set(notes)
    try:
        yield notes
    finally:
        _sink.reset(token)


def set_path(path: str) -> None:
    """Name the edit path being validated (``validate_edit`` calls this first)."""
    _where.set(path)


def note(rule: str, at: str | None = None) -> None:
    """Record that ``rule`` fired at ``at`` (default: the edit path). No-op unless recording."""
    sink = _sink.get()
    if sink is None:
        return
    assert rule in RULES, rule
    row = {"path": at or _where.get(), "rule": rule}
    if row not in sink:
        sink.append(row)


# ── closed alias maps ────────────────────────────────────────────────────────

def _key(text: str) -> str:
    """Compare form: lower-case, '_' and '-' as spaces, single spaces."""
    return " ".join(re.sub(r"[_\-]+", " ", text.lower()).split())


_LEVEL_ALIASES: dict[str, str] = {
    "graduate": "entry", "grad": "entry", "new grad": "entry", "new graduate": "entry", "junior": "entry",
    "jr": "entry", "intern": "entry", "internship": "entry", "trainee": "entry", "apprentice": "entry",
    "entry level": "entry", "entry": "entry", "beginner": "entry",
    "mid level": "mid", "midlevel": "mid", "intermediate": "mid", "middle": "mid", "mid": "mid",
    "senior level": "senior", "sr": "senior",
    "lead level": "lead", "team lead": "lead", "principal": "lead",
    "executive level": "executive", "director": "executive", "vp": "executive", "c level": "executive",
}

_ARRANGEMENT_ALIASES: dict[str, str] = {
    "on site": "onsite", "onsite": "onsite", "office": "onsite", "in office": "onsite", "in person": "onsite",
    "wfh": "remote", "remote first": "remote", "fully remote": "remote", "work from home": "remote",
    "home": "remote",
}

_EVERY_WORDS: dict[str, str] = {
    "hourly": "1h", "every hour": "1h", "each hour": "1h", "an hour": "1h", "1 hour": "1h", "hour": "1h",
    "daily": "24h", "every day": "24h", "once a day": "24h", "each day": "24h", "day": "24h",
}
_EVERY_RE = re.compile(r"^(?:every\s+)?(\d{1,2})\s*(?:h|hr|hrs|hour|hours)$")


def level_alias(text: str) -> str | None:
    """The closed experience level an alias means, else ``None``."""
    return _LEVEL_ALIASES.get(_key(text))


def arrangement_alias(text: str) -> str | None:
    """The closed work arrangement an alias means, else ``None``."""
    return _ARRANGEMENT_ALIASES.get(_key(text))


def every_alias(text: str, allowed: frozenset[str]) -> str | None:
    """'hourly' -> '1h', 'every 3 hours' -> '3h' (only when the result is allowed)."""
    key = _key(text)
    hit = _EVERY_WORDS.get(key)
    if hit is None:
        m = _EVERY_RE.match(key)
        hit = f"{int(m.group(1))}h" if m else None
    return hit if hit in allowed else None


_WORK_AUTH_EXACT: dict[str, str] = {
    "needs sponsorship": "needs_sponsorship", "need sponsorship": "needs_sponsorship",
    "requires sponsorship": "needs_sponsorship", "sponsorship required": "needs_sponsorship",
    "permanent resident": "permanent_resident", "permanent residence": "permanent_resident",
    "indefinite leave to remain": "permanent_resident", "ilr": "permanent_resident",
    "settled status": "permanent_resident", "green card": "permanent_resident",
    "citizen": "citizen", "visa": "visa", "work visa": "visa",
}


def work_auth_alias(text: str) -> str | None:
    """A free-text work-authorization status mapped to the closed set, else ``None``.

    EXACT phrases only (``_WORK_AUTH_EXACT``). Anything longer ("Indian citizen on Graduate visa",
    "will need a visa") is NOT guessed: ``None`` -> refused with an Example, and the user's own
    words belong in the record's ``note``."""
    return _WORK_AUTH_EXACT.get(_key(text))


_LICENCE_TYPES = ("full", "provisional", "none")


def split_licence(text: str) -> tuple[str, str] | None:
    """'provisional UK' -> ('provisional', 'UK'); 'full' -> ('full', ''); else ``None``.

    The type word may be first or last; the rest (if any) is returned for a country lookup."""
    words = text.replace(",", " ").split()
    if not words:
        return None
    for index in (0, len(words) - 1):
        word = words[index].lower().strip(".")
        if word in _LICENCE_TYPES:
            rest = " ".join(w for i, w in enumerate(words) if i != index)
            return word, rest
    return None


def bool_to_yes_no(value: Any) -> str | None:
    """True -> 'Yes', False -> 'No' for an equality answer; anything else ``None``."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return None
