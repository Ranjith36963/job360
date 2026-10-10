"""User info memory — the facts a job form asks, stored once by Job360.

Owner decision 2026-10-08, THREE STORES. Six memory paths under the
``user_info.*`` head (their own ``user_profiles.user_info`` column, written by
``storage.save_user_info``, overlaid by ``profile_edits`` rows) plus one normal
preference, ``preferences.salary_by_country``. Every write REPLACES the whole
value (like ``cv_data.cv_positions``). Rules, all seven:

* an empty answer (``""`` / ``null`` / ``[]``) is DROPPED — a missing key means
  "not answered" (rule #29), never a default we invent; ``false`` is a real
  answer and is kept; ``{}`` / ``[]`` for the whole path means nothing answered;
* control and bidi characters are stripped (``edits._strip_control``); only an
  ``answer`` keeps its newlines;
* an unknown key, a wrong type or a value out of range is a 422 naming the
  path and the rule — the message NEVER echoes the submitted value (a date of
  birth or a phone number must not reach a log or an error body).

:func:`validate_user_info` and :func:`validate_salary_by_country` are called
from ``edits.validate_edit``; :func:`answered_count` exists for the audit log
only (a count, never a value).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.core import settings
from src.services.applications.visa import normalize_country, normalize_country_codes
from src.services.profile.edits import ProfileEditError, _strip_control
from src.services.profile.models import VALID_LANGUAGE_LEVELS, VALID_WORK_AUTH_STATUSES

USER_INFO_PATHS: frozenset[str] = frozenset({
    "user_info.contact",
    "user_info.right_to_work",
    "user_info.logistics",
    "user_info.languages",
    "user_info.equality",
    "user_info.answers",
})
USER_INFO_ANSWERS_PATH = "user_info.answers"
SALARY_BY_COUNTRY_PATH = "preferences.salary_by_country"

# Closed key schemas: key -> kind. Dict paths first, then the record lists.
_CONTACT: dict[str, str] = {
    "email": "email", "phone": "phone", "address_lines": "address_lines",
    "address_city": "line", "address_postcode": "line", "address_country": "iso2",
    "date_of_birth": "dob", "residence_city": "line", "residence_country": "iso2",
    "legal_first_name": "line", "legal_last_name": "line", "preferred_name": "line",
}
_RTW_COUNTRY: dict[str, str] = {
    "country": "iso2", "work_authorization": "work_auth", "needs_sponsorship": "bool",
    "visa_type": "line", "visa_expires": "visa_expires",
}
_RIGHT_TO_WORK: dict[str, str] = {
    "countries": "rtw_countries", "citizenship": "iso2_list", "sanctions_country_citizen": "bool_or_pns",
}
_LOGISTICS_COUNTRY: dict[str, str] = {
    "country": "iso2", "willing_to_relocate": "bool", "relocate_where": "line",
    "travel_ok_pct": "pct", "driving_licence": "bool", "driving_licence_country": "iso2",
}
_LOGISTICS: dict[str, str] = {
    "notice_period": "line", "earliest_start": "start", "countries": "logistics_countries",
}
_LANGUAGE: dict[str, str] = {"language": "line", "level": "level"}
_EQUALITY: dict[str, str] = {
    "gender": "line", "ethnicity": "line", "disability": "line", "veteran": "line",
    "sexual_orientation": "line", "transgender": "line",
}
_ANSWER_KEYS = ("question", "answer", "approved", "recorded_at")
_SALARY_KEYS = ("country", "min", "max", "currency", "period")
_SALARY_LEGACY_KEY = "amount"  # old single figure: read as min = max, never stored
PREFER_NOT_TO_SAY = "Prefer not to say"
_SALARY_PERIODS = ("year", "month")
_SALARY_AMOUNT_MAX = 1e12

_DICT_SCHEMAS: dict[str, dict[str, str]] = {
    "user_info.contact": _CONTACT,
    "user_info.right_to_work": _RIGHT_TO_WORK,
    "user_info.logistics": _LOGISTICS,
    "user_info.equality": _EQUALITY,
}

_PHONE_CHARS_RE = re.compile(r"^[0-9 +().\-]+$")
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
_EMAIL_MAX = 254


def _is_email(text: str) -> bool:
    """name@domain.tld without a regex (CodeQL py/polynomial-redos): one '@',
    no whitespace, non-empty name, and a dot inside the domain."""
    if any(ch.isspace() for ch in text) or text.count("@") != 1:
        return False
    name, domain = text.split("@")
    return bool(name) and "." in domain[1:-1]
_PHONE_MAX = 40
_ADDRESS_LINES_MAX = 3
_COUNTRY_MESSAGE = (
    "must be an ISO alpha-2 code like 'GB' or 'IN' (no 'remote' record — a remote "
    "job uses the hiring country's record)"
)


def _fail(detail: str) -> ProfileEditError:
    return ProfileEditError(422, detail)


def _safe_key(key: Any) -> str:
    """An unknown key name made safe to put in an error: control chars out, 60 chars."""
    return _strip_control(str(key))[:60]


def _type_error(where: str, expected: str, value: Any) -> ProfileEditError:
    return _fail(f"{where} must be {expected}, got {type(value).__name__}")


def _line(where: str, value: Any, limit: int | None = None) -> str:
    """One line of text, stripped; ``""`` when empty. Never echoes ``value``."""
    cap = settings.PROFILE_EDIT_MAX_ITEM_CHARS if limit is None else limit
    if not isinstance(value, str):
        raise _type_error(where, "a string", value)
    text = _strip_control(value)
    if len(text) > cap:
        suffix = " (PROFILE_EDIT_MAX_ITEM_CHARS)" if limit is None else ""
        raise _fail(f"{where} exceeds the {cap}-character limit{suffix}")
    return text


def _bool(where: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise _type_error(where, "a boolean", value)
    return value


def _iso2(where: str, value: Any) -> str:
    text = _line(where, value)
    if not text:
        return ""
    try:
        return normalize_country(text)
    except ValueError:
        raise _fail(f"{where} {_COUNTRY_MESSAGE}") from None


def _real_date(text: str) -> date | None:
    if not _DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _value(where: str, key: str, kind: str, raw: Any) -> Any:
    """Validate one field; return the cleaned value or ``None`` to DROP it."""
    at = f"{where}.{key}"
    if raw is None:
        return None
    if kind == "line":
        return _line(at, raw) or None
    if kind == "bool":
        return _bool(at, raw)
    if kind == "bool_or_pns":
        if isinstance(raw, str) and " ".join(raw.lower().split()) == PREFER_NOT_TO_SAY.lower():
            return PREFER_NOT_TO_SAY
        if not isinstance(raw, bool):
            raise _fail(f"{at} must be a boolean or \"{PREFER_NOT_TO_SAY}\", got {type(raw).__name__}")
        return raw
    if kind == "iso2":
        return _iso2(at, raw) or None
    if kind == "email":
        text = _line(at, raw, _EMAIL_MAX)
        if not text:
            return None
        if not _is_email(text):
            raise _fail(f"{at} is not an email address (expected name@domain)")
        return text
    if kind == "phone":
        if not isinstance(raw, str):
            raise _type_error(at, "a string", raw)
        text = _strip_control(raw)
        if not text:
            return None
        digits = sum(ch.isdigit() for ch in text)
        if len(text) > _PHONE_MAX or not _PHONE_CHARS_RE.match(text) or not 6 <= digits <= 15:
            raise _fail(f"{at} must be 6-15 digits, with only + ( ) - . and spaces")
        return text
    if kind == "address_lines":
        if not isinstance(raw, list):
            raise _type_error(at, "a list of strings", raw)
        lines = [t for i, item in enumerate(raw) if (t := _line(f"{at}[{i}]", item))]
        if len(lines) > _ADDRESS_LINES_MAX:
            raise _fail(f"{at} exceeds the {_ADDRESS_LINES_MAX}-line limit")
        return lines or None
    if kind == "dob":
        text = _line(at, raw)
        if not text:
            return None
        parsed = _real_date(text)
        if parsed is None or parsed >= datetime.now(timezone.utc).date():
            raise _fail(f"{at} must be a real date as YYYY-MM-DD in the past")
        return text
    if kind == "visa_expires":
        text = _line(at, raw)
        if not text:
            return None
        ok = bool(_MONTH_RE.match(text))
        if ok and len(text) == 7:
            ok = 1 <= int(text[5:7]) <= 12
        elif ok:
            ok = _real_date(text) is not None
        if not ok:
            raise _fail(f"{at} must be YYYY-MM or YYYY-MM-DD")
        return text
    if kind == "start":
        text = _line(at, raw)
        if not text:
            return None
        if _DATE_RE.match(text) and _real_date(text) is None:
            raise _fail(f"{at} looks like a date (YYYY-MM-DD) but is not a real date")
        return text
    if kind == "work_auth":
        text = _line(at, raw).lower()
        if not text:
            return None
        if text not in VALID_WORK_AUTH_STATUSES:
            raise _fail(f"{at} must be one of: {', '.join(sorted(VALID_WORK_AUTH_STATUSES))}")
        return text
    if kind == "level":
        text = _line(at, raw).lower()
        if not text:
            return None
        if text not in VALID_LANGUAGE_LEVELS:
            raise _fail(f"{at} must be one of: {', '.join(sorted(VALID_LANGUAGE_LEVELS))}")
        return text
    if kind == "pct":
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 100:
            raise _fail(f"{at} must be a whole number 0-100")
        return raw
    if kind == "rtw_countries":
        return _countries(at, raw, _RTW_COUNTRY, contradictions=True)
    if kind == "logistics_countries":
        return _countries(at, raw, _LOGISTICS_COUNTRY, contradictions=False)
    if kind == "iso2_list":
        if not isinstance(raw, list):
            raise _type_error(at, "a list of ISO alpha-2 codes", raw)
        for i, item in enumerate(raw):
            if not isinstance(item, str):
                raise _type_error(f"{at}[{i}]", "a string", item)
        try:
            codes = normalize_country_codes([_strip_control(item) for item in raw])
        except ValueError:
            raise _fail(f"{at}: every item {_COUNTRY_MESSAGE}") from None
        return codes or None
    raise AssertionError(f"unknown user-info kind {kind!r}")  # pragma: no cover


def _unknown_key(where: str, key: Any, allowed: str) -> ProfileEditError:
    return _fail(f"{where}: unknown key '{_safe_key(key)}' — allowed keys: {allowed}")


def _object(where: str, raw: dict[str, Any], schema: dict[str, str]) -> dict[str, Any]:
    allowed = ", ".join(schema)
    for key in raw:
        if key not in schema:
            raise _unknown_key(where, key, allowed)
    out: dict[str, Any] = {}
    for key, kind in schema.items():
        if key not in raw:
            continue
        cleaned = _value(where, key, kind, raw[key])
        if cleaned is not None:
            out[key] = cleaned
    return out


def _dict_path(path: str, value: Any) -> dict[str, Any]:
    schema = _DICT_SCHEMAS[path]
    if not isinstance(value, dict):
        raise _fail(
            f"{path} must be an object with keys {', '.join(schema)}, got {type(value).__name__}"
        )
    return _object(path, value, schema)


def _check_record_count(path: str, value: list[Any]) -> None:
    if len(value) > settings.PROFILE_EDIT_MAX_RECORDS:
        raise _fail(
            f"{path} exceeds the {settings.PROFILE_EDIT_MAX_RECORDS}-record limit "
            "(PROFILE_EDIT_MAX_RECORDS)"
        )


def _record_list(path: str, value: Any, schema: dict[str, str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _fail(
            f"{path} must be a list of objects with keys {', '.join(schema)}, "
            f"got {type(value).__name__}"
        )
    _check_record_count(path, value)
    records: list[dict[str, Any]] = []
    for i, raw in enumerate(value):
        where = f"{path}[{i}]"
        if not isinstance(raw, dict):
            raise _fail(
                f"{where} must be an object with keys {', '.join(schema)}, got {type(raw).__name__}"
            )
        records.append(_object(where, raw, schema))
    return records


def _countries(
    path: str, value: Any, schema: dict[str, str], *, contradictions: bool
) -> list[dict[str, Any]] | None:
    """A per-country record list: country required and unique; an empty list
    is "not answered" (``None`` -> dropped). ``contradictions`` adds the two
    work-authorization rules."""
    records = _record_list(path, value, schema)
    if not records:
        return None
    seen: set[str] = set()
    for i, rec in enumerate(records):
        code = rec.get("country")
        if not code:
            raise _fail(f"{path}[{i}] needs a country (ISO alpha-2)")
        if code in seen:
            raise _fail(f"{path}: country '{code}' appears twice — one record per country")
        seen.add(code)
        if not contradictions:
            continue
        status = rec.get("work_authorization")
        sponsor = rec.get("needs_sponsorship")
        if status in ("citizen", "permanent_resident") and sponsor is True:
            raise _fail(f"{path}[{i}]: work_authorization '{status}' contradicts needs_sponsorship true")
        if status == "needs_sponsorship" and sponsor is False:
            raise _fail(
                f"{path}[{i}]: work_authorization 'needs_sponsorship' contradicts needs_sponsorship false"
            )
    return records


def _languages(path: str, value: Any) -> list[dict[str, Any]]:
    records = _record_list(path, value, _LANGUAGE)
    seen: set[str] = set()
    for i, rec in enumerate(records):
        if not rec.get("language") or not rec.get("level"):
            raise _fail(f"{path}[{i}] needs a language and a level")
        key = " ".join(rec["language"].lower().split())
        if key in seen:
            raise _fail(f"{path}: language '{key}' appears twice")
        seen.add(key)
    return records


def _recorded_at(where: str, raw: Any) -> str:
    """Stamp now (UTC) when absent; else an ISO date/datetime at most a day ahead."""
    if raw is None or raw == "":
        return datetime.now(timezone.utc).isoformat()
    text = _line(where, raw)
    bad = _fail(f"{where} must be an ISO date or datetime, not more than a day in the future")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise bad from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed > datetime.now(timezone.utc) + timedelta(days=1):
        raise bad
    return text


def _answers(path: str, value: Any) -> list[dict[str, Any]]:
    allowed = ", ".join(_ANSWER_KEYS)
    if not isinstance(value, list):
        raise _fail(f"{path} must be a list of objects with keys {allowed}, got {type(value).__name__}")
    _check_record_count(path, value)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(value):
        where = f"{path}[{i}]"
        if not isinstance(raw, dict):
            raise _fail(f"{where} must be an object with keys {allowed}, got {type(raw).__name__}")
        for key in raw:
            if key not in _ANSWER_KEYS:
                raise _unknown_key(where, key, allowed)
        question = raw.get("question")
        answer = raw.get("answer")
        for key, item in (("question", question), ("answer", answer)):
            if item is not None and not isinstance(item, str):
                raise _type_error(f"{where}.{key}", "a string", item)
        q = _strip_control(question or "")
        a = _strip_control(answer or "", keep_newlines=True)
        if not q or not a:
            raise _fail(f"{where} needs a non-empty question and answer")
        if len(q) > settings.USER_INFO_QUESTION_MAX_CHARS:
            raise _fail(
                f"{where}.question exceeds the {settings.USER_INFO_QUESTION_MAX_CHARS}-character "
                "limit (USER_INFO_QUESTION_MAX_CHARS)"
            )
        if len(a) > settings.USER_INFO_ANSWER_MAX_CHARS:
            raise _fail(
                f"{where}.answer exceeds the {settings.USER_INFO_ANSWER_MAX_CHARS}-character "
                "limit (USER_INFO_ANSWER_MAX_CHARS)"
            )
        key = " ".join(q.lower().split())
        if key in seen:
            raise _fail(f"{path}: the same question appears twice")
        seen.add(key)
        approved = raw.get("approved")
        out.append({
            "question": q,
            "answer": a,
            "approved": False if approved is None else _bool(f"{where}.approved", approved),
            "recorded_at": _recorded_at(f"{where}.recorded_at", raw.get("recorded_at")),
        })
    return out


def validate_user_info(path: str, value: Any) -> Any:
    """Normalise ``value`` for one ``user_info.*`` path, or raise
    ``ProfileEditError(422, ...)``. The write REPLACES the whole value; empty
    answers are dropped (rule #29)."""
    if path in _DICT_SCHEMAS:
        return _dict_path(path, value)
    if path == "user_info.languages":
        return _languages(path, value)
    if path == USER_INFO_ANSWERS_PATH:
        return _answers(path, value)
    raise _fail(f"{path!r} is not a user-info path")  # pragma: no cover — callers gate on USER_INFO_PATHS


def _salary_figure(value: Any) -> bool:
    """A finite number > 0, not a bool, <= 1e12. The range check runs BEFORE
    isfinite: a JSON integer with hundreds of digits makes ``math.isfinite``
    raise OverflowError (a 500), while the int-vs-float comparison is exact and
    refuses it as a 422. NaN fails the comparison too; isfinite stays for inf."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and 0 < value <= _SALARY_AMOUNT_MAX
        and math.isfinite(value)
    )


def validate_salary_by_country(path: str, value: Any) -> list[dict[str, Any]]:
    """Normalise ``preferences.salary_by_country``: a list (<= 50) of flat
    records, EXACTLY the keys country (ISO2, unique), min and max (finite
    numbers > 0, min <= max; equal = one figure), currency (ISO 4217,
    upper-cased) and period (``year`` | ``month``), all required. The old
    single ``amount`` is accepted INSTEAD of min/max (never both) and is read
    as min = max; ``min`` alone is one figure (max = min), ``max`` alone is
    refused; writes always store min/max. Order is kept, nothing is
    converted; ``[]`` = not set. A ``minimum`` key is refused like any unknown
    key."""
    allowed = ", ".join(_SALARY_KEYS)
    if not isinstance(value, list):
        raise _fail(
            f"{path} must be a list of objects with keys {allowed}, got {type(value).__name__}"
        )
    _check_record_count(path, value)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(value):
        where = f"{path}[{i}]"
        need = _fail(
            f"{where} needs country, min and max (numbers > 0, min not above max), currency "
            "(ISO 4217, 3 letters like 'EUR') and period (year or month)"
        )
        if not isinstance(raw, dict):
            raise _fail(f"{where} must be an object with keys {allowed}, got {type(raw).__name__}")
        for key in raw:
            if key not in _SALARY_KEYS and key != _SALARY_LEGACY_KEY:
                raise _unknown_key(where, key, allowed)
        country = raw.get("country")
        if not isinstance(country, str) or not _strip_control(country):
            raise need
        code = _iso2(f"{where}.country", country)
        if _SALARY_LEGACY_KEY in raw:
            if "min" in raw or "max" in raw:
                raise _fail(f"{where}: send min and max, or the old amount — never both")
            low = high = raw[_SALARY_LEGACY_KEY]
        else:
            if "max" in raw and "min" not in raw:
                raise _fail(f"{where}: max needs a min — send min alone for one figure, or min and max")
            # min alone = one figure (max = min); the stored record always has both.
            low = raw.get("min")
            high = raw["max"] if "max" in raw else low
        currency, period = raw.get("currency"), raw.get("period")
        if (
            not _salary_figure(low)
            or not _salary_figure(high)
            or low > high
            or not isinstance(currency, str)
            or not _CURRENCY_RE.match(_strip_control(currency))
            or not isinstance(period, str)
            or period.strip().lower() not in _SALARY_PERIODS
        ):
            raise need
        if code in seen:
            raise _fail(f"{path}: country '{code}' appears twice — one record per country")
        seen.add(code)
        out.append({
            "country": code, "min": low, "max": high,
            "currency": _strip_control(currency).upper(), "period": period.strip().lower(),
        })
    return out


def answered_count(path: str, value: Any) -> int:
    """How many answers ``value`` holds — for the audit log ONLY (a count,
    never a value or a key name). ``None`` / empty -> 0. A dict path counts its
    top-level keys present plus its country records (``countries``)."""
    if isinstance(value, dict):
        countries = value.get("countries")
        return len(value) + (len(countries) if isinstance(countries, list) else 0)
    if isinstance(value, list):
        return len(value)
    return 0
