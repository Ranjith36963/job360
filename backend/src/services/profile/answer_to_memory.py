"""Answer once (owner 2026-11-06): a Memory question answered on Needs you is saved into Memory.

"Repeating questions live in Memory, one page per country. When the user answers one ONCE on Needs you, it
is saved into that country's Memory permanently; nobody asks again." The user's own "remember" press
(``ask_once.remember``, a web-session-only route; an assistant has no door here) hands each row in Memory's
families (:func:`ask_once.memory_question`) to :func:`plan`, which converts the answer to the field's real
shape or refuses:

* yes/no -> a bool (sponsorship, relocation), only for a question in a known DIRECT phrasing
  (:data:`_DIRECT`, a positive allow-list; "eligible without sponsorship" is not saved); on-site ->
  ``yes`` / ``no`` / ``only_if_sponsored`` (direct phrasing only); a work authorization only as one of its
  own statuses (a yes/no is no status); travel -> a whole 0-100 percent ("No" = 0, direct phrasing only);
  a salary -> ``{min, max, currency, period}`` for THAT country, the currency as written (never
  converted), the period as said, else ``year`` when min >= :data:`YEAR_FLOOR` (else not saved); a
  floor ("at least", a "minimum salary" question) is never stored; notice, start, contact and equality
  lines as written (the validators judge).
* Never a silent overwrite: a field Memory already answers for that country (its own page, else its
  region's or ANY's, read as the kit reads it) with another value is a CONFLICT; the same value is a no-op.
* Not saved, naming the page (``ask_once.memory_note``): an answer with no clean shape, a job with no
  country, a field outside Memory (``cv_data``), an assistant-drafted country row, or a Memory path whose
  newest write is an assistant's guess (a web write of that path would vouch for it).

Words are read token by token, never with a regex on the caller's text.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

_YES, _NO = frozenset({"yes", "y", "true"}), frozenset({"no", "n", "false"})
_CAVEATS = frozenset({"but", "only", "if", "unless", "except", "maybe", "depends", "depending", "sometimes"})
_FLOOR_WORDS = frozenset({"minimum", "min", "least", "lowest", "floor"})
_YEAR = frozenset({"year", "yearly", "annual", "annually", "annum", "pa", "yr"})
_MONTH = frozenset({"month", "monthly", "mo"})
_FILLER = frozenset({"per", "a", "an", "gross", "base"})
_SYMBOLS = (("€", "EUR"), ("£", "GBP"))
YEAR_FLOOR = 10000  # owner 2026-11-06: no period word reads as a year only from this figure up


def _words(text: str) -> list[str]:
    from src.services.profile.answer_match import normalize_question  # noqa: PLC0415

    return normalize_question(text).split()


def yes_no(text: str) -> Optional[bool]:
    """``True`` / ``False`` for a plain yes or no ("No, I do not need it" too); ``None`` for anything with a
    condition ("yes, but only from May") or no yes/no at all."""
    words, raw = _words(text), text.split()
    if not words or words[0] not in _YES | _NO or _CAVEATS & set(words[1:]):
        return None
    if len(raw) > 1 and not raw[0].endswith((",", ".", "!")) and raw[1][:1] not in (",", "-"):
        return None  # "no preference", "yes please": not a bare yes/no
    return words[0] in _YES


def _number(token: str) -> Optional[int]:
    t = token.replace(",", "")
    scale = 1000 if t[-1:].lower() == "k" else 1
    digits = t[:-1] if scale > 1 else t
    return int(digits) * scale if digits.isdigit() and int(digits) > 0 else None


def salary(text: str, country: str) -> Optional[dict[str, Any]]:
    """``{country, min, max, currency, period}`` for "50000-60000 EUR", "€55k", "4000 GBP per month";
    ``None`` for a floor ("50000+", "at least 50k"), no currency or ``$`` (several currencies), a decimal
    point, any word that is not a figure, a currency, a period or "per", or no period on a figure under
    :data:`YEAR_FLOOR` ("4000-4500 GBP": a month's pay or a year's is not known)."""
    raw = text.replace("–", "-").replace("—", "-").replace("/", " ").replace("-", " - ")
    for sym, code in _SYMBOLS:
        raw = raw.replace(sym, f" {code} ")
    nums: list[int] = []
    currency: set[str] = set()
    period: set[str] = set()
    seps = 0
    for tok in raw.split():
        low = tok.lower().strip(".")
        if (n := _number(tok)) is not None:
            nums.append(n)
        elif tok == "-" or low == "to":
            seps += 1
        elif len(tok) == 3 and tok.isalpha() and tok.isupper():
            currency.add(tok)
        elif low in _YEAR | _MONTH:
            period.add("year" if low in _YEAR else "month")
        elif low not in _FILLER:
            return None
    if len(nums) not in (1, 2) or seps != len(nums) - 1 or len(currency) != 1 or len(period) > 1:
        return None
    if nums[0] > nums[-1] or (not period and nums[0] < YEAR_FLOOR):
        return None  # no period word on a small figure: a month's pay or a year's? never a guess
    return {"country": country, "min": nums[0], "max": nums[-1], "currency": currency.pop(),
            "period": period.pop() if period else "year"}


# A yes/no is stored only for a question in one of these DIRECT phrasings (a positive allow-list): a verb
# word AND a subject word of the field, and no polarity word at all. "eligible without sponsorship", "free
# of any need", "unwilling to relocate", "don't you need": an inverted or either/or question is not saved.
_DIRECT: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "needs_sponsorship": (frozenset({"need", "needs", "require", "requires"}), frozenset({"sponsorship", "sponsor"})),
    "willing_to_relocate": (frozenset({"willing", "open"}), frozenset({"relocate", "relocation", "relocating"})),
    "onsite": (frozenset({"willing", "open", "able", "happy"}), frozenset({"office", "onsite", "site"})),
    "travel_ok_pct": (frozenset({"willing", "open", "able", "happy"}), frozenset({"travel", "travelling"})),
}
# "t" is the tail of every n't ("don't" -> don t); "or" is an either/or ("on-site or hybrid").
_POLARITY = frozenset({
    "without", "free", "exempt", "unable", "unwilling", "cannot", "cant", "can", "don", "doesn", "won", "t",
    "never", "no", "none", "ineligible", "not", "or", "unless", "except", "instead", "nor", "neither",
})
_NOW_OR_FUTURE = ["now", "or", "in", "the", "future"]  # "Will you now or in the future need..." is direct


def _question_words(question: str) -> set[str]:
    """The question's words, with the standard "now or in the future" taken out (it is not an either/or)."""
    words = _words(question)
    for i in range(len(words) - len(_NOW_OR_FUTURE) + 1):
        if words[i:i + len(_NOW_OR_FUTURE)] == _NOW_OR_FUTURE:
            words = words[:i] + words[i + len(_NOW_OR_FUTURE):]
            break
    return set(words)


def direct(field: str, question: str) -> bool:
    """True when ``question`` asks ``field`` in a known direct phrasing (:data:`_DIRECT`), so "Yes" means
    the field is true. Anything else is not a yes/no Job360 may store."""
    verbs, subjects = _DIRECT[field]
    words = _question_words(question)
    return bool(words & verbs) and bool(words & subjects) and not words & _POLARITY


def convert(template: str, question: str, answer: str, country: Optional[str]) -> Optional[Any]:
    """The answer in the shape of the Memory field ``template`` names, or ``None`` (never a guess)."""
    from src.services.profile.models import VALID_WORK_AUTH_STATUSES  # noqa: PLC0415

    field = template.rsplit(".", 1)[-1]
    if field in _DIRECT and not direct(field, question):
        return None  # an inverted, negated or either/or question: a yes/no could mean its opposite
    if template == "salary.{cc}":
        words = _question_words(question)
        return None if not country or (_FLOOR_WORDS | _POLARITY) & words else salary(answer, country)
    if field in ("needs_sponsorship", "willing_to_relocate"):
        return yes_no(answer)
    if field == "onsite":
        said = "_".join(_words(answer))
        return said if said == "only_if_sponsored" else {True: "yes", False: "no", None: None}[yes_no(answer)]
    if field == "work_authorization":
        said = "_".join(_words(answer))
        return said if said in VALID_WORK_AUTH_STATUSES else None
    if field == "travel_ok_pct":
        if yes_no(answer) is False:
            return 0
        token = answer.strip().rstrip("%").strip()
        return int(token) if token.isdigit() and int(token) <= 100 else None
    if field == "current_salary":
        return None  # amount, currency AND period, said for one job: set on the Memory page
    return answer.strip() or None  # a line (notice, start date, contact, equality): the validator judges it


def _target(template: str, country: Optional[str]) -> Optional[tuple[str, Optional[str], Optional[str]]]:
    """``(update_profile path, country row or None, field or None for a whole salary record)``."""
    from src.services.profile import common_questions  # noqa: PLC0415

    if template in common_questions._SAVE_PATH_EXACT:
        return None  # the CV's name / links: not a Memory field
    path = common_questions.save_path({"kit_key": template})
    parts = template.split(".")
    if "{cc}" not in parts:
        return path, None, parts[-1]
    return (path, country, None) if parts[0] == "salary" else (path, country, parts[-1])


def _with(stored: Any, cc: Optional[str], field: Optional[str], value: Any) -> Any:
    """A copy of the stored path value with ``value`` set (a country row created when missing)."""
    if field is None:  # salary_by_country: the whole record of that country
        rows = [r for r in copy.deepcopy(stored or []) if str((r or {}).get("country") or "").upper() != cc]
        return [*rows, value]
    out = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    if cc is None:
        out[field] = value
        return out
    rows = [r for r in out.get("countries") or [] if isinstance(r, dict)]
    row = next((r for r in rows if str(r.get("country") or "").upper() == cc), None)
    if row is None:
        rows.append({"country": cc, field: value})
    else:
        row[field] = value
    out["countries"] = rows
    return out


def _guess_on_path(path: str, stored: Any, overlay: list[dict[str, Any]]) -> bool:
    """True when the path's newest write is an assistant's guess: a web write of the whole path would
    vouch for it. A right-to-work value carrying draft marks keeps them, so it is safe."""
    from src.services.profile import country_pages  # noqa: PLC0415
    from src.services.profile.edits import is_assistant_actor, is_confirmed  # noqa: PLC0415

    if path == country_pages.RTW_PATH and country_pages.row_mode(stored):
        return False
    row = next((r for r in overlay if r.get("path") == path), None)
    by, source = str((row or {}).get("set_by") or ""), str((row or {}).get("source") or "")
    return row is not None and is_assistant_actor(by) and not is_confirmed(by, source)


def plan(profile: Any, overlay: list[dict[str, Any]], working: dict[str, Any], template: Optional[str],
         question: str, answer: str, country: Optional[str]) -> tuple[str, Any]:
    """One Memory row's outcome: ``("write", (path, value, key))`` (``working`` holds the new path value),
    ``("same", key)``, ``("conflict", sentence)`` or ``("not_saved", None)`` (the caller names the page)."""
    from src.services.applications import kit  # noqa: PLC0415
    from src.services.profile import country_pages  # noqa: PLC0415
    from src.services.profile.common_questions import _shown  # noqa: PLC0415
    from src.services.profile.edits import ProfileEditError, field_values, validate_edit  # noqa: PLC0415

    if template is None or ("{cc}" in template and not country):
        return "not_saved", None
    target = _target(template, country)
    value = convert(template, question, answer, country)
    if target is None or value is None:
        return "not_saved", None
    path, cc, field = target
    key = template.replace("{cc}", cc or "")
    blocks, _missing = kit.compute_answers(profile, overlay, cc)
    have = next((i for b in blocks.values() for i in b if i.get("key") == key), None)
    new = {k: value[k] for k in ("min", "max", "currency", "period")} if field is None else value
    if have is not None:
        old = have["value"]
        if old == new or _shown(old) == _shown(new):
            return "same", key
        from src.services.profile.regions import place_name  # noqa: PLC0415

        # The page the value really comes from: the country's own, else its region's or ANY's (applies_via).
        where = f"your {place_name(str(have.get('applies_via') or cc))} page" if cc else "your Memory page"
        return "conflict", (f"{where} says {_shown(old)}; this answer says {_shown(new)}. "
                            "Change it there if it is wrong.")
    stored = working.get(path, field_values(profile, [path])[path])
    if path == country_pages.RTW_PATH and country_pages.is_drafted(country_pages.rtw_rows(stored).get(cc or "")):
        return "not_saved", None  # an assistant's draft row: the user confirms it on the page first
    if _guess_on_path(path, stored, overlay):
        return "not_saved", None
    try:
        working[path] = validate_edit(path, _with(stored, cc, field, value))
    except ProfileEditError:
        return "not_saved", None
    return "write", (path, working[path], key)
