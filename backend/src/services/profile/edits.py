"""Agent edits to the profile — an append-only overlay over extraction.

Spec: ``docs/plans/2026-09-05-contacts-stats/spec.md`` R8-R12, S2-S4, S7-S9,
S12. An edit is a row in ``profile_edits`` (migration 0038); the CURRENT
value of a path is its newest row, and ``value IS NULL`` means "cleared —
fall back to what extraction says". ``storage.load_profile`` calls
:func:`apply_overlay` after building the dataclasses so every reader — the
web route, the tailor, MCP ``get_profile`` — sees one profile.

The editable set is closed (R9) and validated against the DECLARED dataclass
fields of ``CVData``/``UserPreferences``/``UserInfo``/``AssistantSettings`` at import (S8; the
``user_info.*`` head is the user-info MEMORY, its own ``user_profiles`` column,
owner decision 2026-10-08; its record shapes live in ``user_info.py``): a path naming a
field that does not exist refuses to boot rather than being silently
accepted. Values are typed by the field's own annotation (R10), not by a
hand-written table — the two exceptions are ``preferences.work_arrangement``
and ``preferences.experience_level``, which are closed-set strings sharing
the vocabulary the web form uses (``models.VALID_WORK_ARRANGEMENTS`` /
``VALID_EXPERIENCE_LEVELS``). A third exception (slice B2): the record lists
``cv_data.cv_positions`` / ``cv_data.cv_projects`` are ``list[dict]``, which
an annotation cannot describe, so their closed key set is ``RECORD_SCHEMAS``.
"""
from __future__ import annotations

import contextlib
import json
import math
import re
import typing
import unicodedata
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

from src.core import settings
from src.core.settings import DB_PATH
from src.repositories import pgsync
from src.services.auth import rate_limit
from src.services.profile import forgiving
from src.services.profile.models import (
    VALID_CHECK_EVERY_VALUES,
    VALID_DAILY_CHECK_VALUES,
    VALID_EXPERIENCE_LEVELS,
    VALID_WORK_ARRANGEMENTS,
    AssistantSettings,
    CVData,
    UserInfo,
    UserPreferences,
    UserProfile,
)
from src.services.profile.seniority import INFERENCE_INPUT_PATHS, infer_from_cv


class ProfileEditError(Exception):
    """Domain error a route turns into ``HTTPException(status_code, detail)``.

    Mirrors ``services.applications.spine.SpineError`` — same shape, same
    seam: raise here, catch once in the route, never leak a raw exception to
    the caller.
    """

    def __init__(self, status_code: int, detail: str, reason: str = "") -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        # Closed refusal code for the audit trail (services/profile/refusals.py); "" = classify from the text.
        self.reason = reason


# The closed-set preference paths (R10). The first two share vocabulary with
# the web form's normalisers in api/routes/profile.py; `daily_check` has no
# web-form normaliser at all — it is written only through this overlay (owner
# decision 2026-09-25).
_CLOSED_SET_PATHS: dict[str, frozenset[str]] = {
    "preferences.work_arrangement": VALID_WORK_ARRANGEMENTS,
    "preferences.experience_level": VALID_EXPERIENCE_LEVELS,
    "preferences.daily_check": VALID_DAILY_CHECK_VALUES,
    "preferences.check_every": VALID_CHECK_EVERY_VALUES,
}

_editable_paths_cache: tuple[str, ...] | None = None


def _declared_fields() -> dict[str, set[str]]:
    """``{"cv_data": {...}, "preferences": {...}, "user_info": {...}, "assistant_settings": {...}}``
    — each value the set of declared field names."""
    return {
        "cv_data": {f.name for f in dataclass_fields(CVData)},
        "preferences": {f.name for f in dataclass_fields(UserPreferences)},
        "user_info": {f.name for f in dataclass_fields(UserInfo)},
        "assistant_settings": {f.name for f in dataclass_fields(AssistantSettings)},
    }


def editable_paths(refresh: bool = False) -> tuple[str, ...]:
    """The closed set of dotted paths an agent may edit (R9/S8).

    ``settings.PROFILE_EDITABLE_PATHS`` plus the env-extendable
    ``PROFILE_EXTRA_EDITABLE_PATHS``, validated against the DECLARED
    dataclass fields of ``CVData``/``UserPreferences``. A path naming a field
    that isn't declared raises ``ValueError`` (naming the offending path) —
    a bad env must refuse to boot, not silently widen what an agent may
    change (S8).

    Cached after the first successful call; pass ``refresh=True`` to
    recompute (tests monkeypatch the settings tuples and need this to see
    the change — this module also calls it once, at import, with the
    process's real settings).
    """
    global _editable_paths_cache
    if _editable_paths_cache is not None and not refresh:
        return _editable_paths_cache
    combined = tuple(settings.PROFILE_EDITABLE_PATHS) + tuple(settings.PROFILE_EXTRA_EDITABLE_PATHS)
    declared = _declared_fields()
    for path in combined:
        head, sep, field_name = path.partition(".")
        if not sep or head not in declared or field_name not in declared[head]:
            raise ValueError(
                "PROFILE_EDITABLE_PATHS/PROFILE_EXTRA_EDITABLE_PATHS names a path "
                f"that is not a declared dataclass field: {path!r}"
            )
    _editable_paths_cache = combined
    return _editable_paths_cache


# Called once at import (S8): a bad PROFILE_EXTRA_EDITABLE_PATHS refuses to
# boot the process rather than accepting an unknown path at request time.
editable_paths()


def _field_type(path: str) -> Any:
    """Resolve the REAL (non-string) annotation for one editable path.

    ``models.py`` uses ``from __future__ import annotations``, so
    ``dataclasses.fields(...)[i].type`` is a string ("list[str]",
    "Optional[float]", ...), not a usable type object. ``typing.get_type_hints``
    evaluates it back into the real annotation so callers can introspect it
    with ``get_origin``/``get_args``.
    """
    head, _, field_name = path.partition(".")
    classes: dict[str, type[Any]] = {
        "cv_data": CVData, "preferences": UserPreferences, "user_info": UserInfo,
        "assistant_settings": AssistantSettings,
    }
    hints = typing.get_type_hints(classes[head])
    return hints[field_name]


def profile_section(profile: UserProfile, head: str) -> Any:
    """The dataclass a dotted path's head names: ``cv_data`` | ``preferences``
    | ``user_info`` (the memory) | ``assistant_settings`` (S2). Any other head is a ``KeyError`` — callers
    only pass heads that passed :func:`editable_paths`."""
    if head == "cv_data":
        return profile.cv_data
    if head == "preferences":
        return profile.preferences
    if head == "user_info":
        return profile.user_info
    if head == "assistant_settings":
        return profile.assistant_settings
    raise KeyError(head)


def _validate_string(path: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ProfileEditError(422, f"{path} must be a string, got {type(value).__name__}")
    text = value.strip()
    if len(text) > settings.PROFILE_EDIT_MAX_CHARS:
        raise ProfileEditError(
            422,
            f"{path} exceeds the {settings.PROFILE_EDIT_MAX_CHARS}-character limit "
            "(PROFILE_EDIT_MAX_CHARS)",
        )
    return text


_CLOSED_ALIASES: dict[str, tuple[Any, str]] = {
    "preferences.experience_level": (lambda text, _allowed: forgiving.level_alias(text), forgiving.RULE_LEVEL),
    "preferences.work_arrangement": (
        lambda text, _allowed: forgiving.arrangement_alias(text), forgiving.RULE_ARRANGEMENT
    ),
    "preferences.check_every": (forgiving.every_alias, forgiving.RULE_EVERY),
}


def _validate_closed_set(path: str, value: Any, allowed: frozenset[str]) -> str:
    if not isinstance(value, str):
        raise ProfileEditError(422, f"{path} must be a string, got {type(value).__name__}")
    text = value.strip().lower()
    # An empty string is the explicit "unset" (R10) — never checked against
    # the allowlist, always accepted.
    if text and text not in allowed:
        alias = _CLOSED_ALIASES.get(path)
        mapped = alias[0](text, allowed) if alias else None
        if alias and mapped in allowed:
            # Owner decision 2026-10-10: a spelling that means an allowed value is stored as it, and reported.
            forgiving.note(alias[1], at=path)
            return str(mapped)
        raise ProfileEditError(
            422, f"{path} is not one of the allowed values: {', '.join(sorted(allowed))}", "bad_value"
        )
    return text


def _validate_list(path: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ProfileEditError(422, f"{path} must be a list of strings, got {type(value).__name__}")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ProfileEditError(
                422, f"{path}: every item must be a string, got {type(item).__name__}"
            )
        text = item.strip()
        if len(text) > settings.PROFILE_EDIT_MAX_ITEM_CHARS:
            raise ProfileEditError(
                422,
                f"{path}: an item exceeds the {settings.PROFILE_EDIT_MAX_ITEM_CHARS}-character "
                "limit (PROFILE_EDIT_MAX_ITEM_CHARS)",
            )
        cleaned.append(text)
    # De-dup case/space-insensitive, keeping the FIRST spelling and order —
    # done before the count cap so "same skill typed 4 ways" doesn't cost
    # the agent its list-size budget.
    deduped: list[str] = []
    seen: set[str] = set()
    for text in cleaned:
        key = " ".join(text.split()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    if len(deduped) > settings.PROFILE_EDIT_MAX_LIST_ITEMS:
        raise ProfileEditError(
            422,
            f"{path} exceeds the {settings.PROFILE_EDIT_MAX_LIST_ITEMS}-item limit "
            "(PROFILE_EDIT_MAX_LIST_ITEMS)",
        )
    return deduped


def _validate_bool(path: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ProfileEditError(422, f"{path} must be a boolean, got {type(value).__name__}")
    return value


def _validate_number(path: str, value: Any) -> float:
    # bool is a subclass of int — True/False must never pass as a number.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileEditError(
            422, f"{path} must be a non-negative number, got {type(value).__name__}"
        )
    number = float(value)
    # FINITENESS BEFORE RANGE. `float("nan") < 0` is False and
    # `float("inf") < 0` is False, so a range check alone waves both through —
    # and `json.dumps(float("nan"))` emits bare `NaN`, which is not valid JSON
    # for any other reader of the `profile_edits` row. Refuse them here, where
    # the message can say why.
    if not math.isfinite(number):
        raise ProfileEditError(422, f"{path} must be a finite number, got {value!r}")
    if number < 0:
        raise ProfileEditError(422, f"{path} must be >= 0, got {value!r}")
    return number


# ── Record lists (slice B2, decision 28) ─────────────────────────────────────
# ``cv_data.cv_positions`` and ``cv_data.cv_projects`` are ``list[dict]`` —
# the one editable shape a field annotation cannot describe, so the key set is
# declared here. It is the shape every reader already expects: the CVData
# comments in ``models.py``, ``seniority._position_boundaries`` (one combined
# ``dates`` string), ``preferences`` (title/company/location/bullets) and the
# web ``CVViewer`` (company/title/dates/location/bullets;
# name/description/technologies/dates). A key outside the set is a 422 naming
# it — never silently dropped, never stored.
#
# Field kinds: ``line`` one line, <= PROFILE_EDIT_MAX_ITEM_CHARS; ``text`` may
# keep line breaks, <= PROFILE_EDIT_MAX_CHARS; ``dates`` a date range,
# normalised (see ``_validate_dates``); ``bullets`` a list of lines, each <=
# PROFILE_EDIT_MAX_BULLET_CHARS; ``tags`` a de-duplicated list of short lines.
RECORD_SCHEMAS: dict[str, dict[str, str]] = {
    "cv_data.cv_positions": {
        "company": "line", "title": "line", "dates": "dates", "location": "line", "bullets": "bullets",
    },
    "cv_data.cv_projects": {
        "name": "line", "description": "text", "technologies": "tags", "dates": "dates",
    },
}
# A record must say WHAT it is — a position with neither a title nor a company
# (or a project with no name) is not shown by any reader and is refused.
_RECORD_IDENTITY: dict[str, tuple[str, ...]] = {
    "cv_data.cv_positions": ("title", "company"),
    "cv_data.cv_projects": ("name",),
}

# What "control character" means — the same set ``services.applications.spine``
# refuses in an email source (S3 of slice 6): Unicode Cc (C0, DEL, C1), the
# Zl/Zp line terminators, and the bidi embeddings/overrides/isolates. Here they
# are STRIPPED (replaced by a space, then whitespace collapsed) rather than
# refused: a PDF copy-paste routinely carries a stray tab or form feed.
_BANNED_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})
_BANNED_CHARS = frozenset(
    chr(cp) for cp in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069)
)


def _strip_control(text: str, *, keep_newlines: bool = False) -> str:
    """Replace every control/bidi char with a space and collapse whitespace.

    ``keep_newlines`` keeps ``\\n`` (``\\r\\n``/``\\r`` normalised to it) as the
    one allowed line break, collapsing spaces within each line.
    """
    if keep_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = "".join(
        ch if (keep_newlines and ch == "\n")
        else (" " if ch in _BANNED_CHARS or unicodedata.category(ch) in _BANNED_CATEGORIES else ch)
        for ch in text
    )
    if keep_newlines:
        lines = [" ".join(line.split()) for line in out.split("\n")]
        return "\n".join(lines).strip()
    return " ".join(out.split())


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_POINT = r"(?:(?P<{m}>" + _MONTH_PATTERN + r")\.?\s+)?(?P<{y}>(?:19|20)\d{{2}})"
_ONGOING = r"present|current|currently|now|ongoing"
# One boundary, or "start <sep> end" where end may be ongoing; "2019 to date"
# is its own spelling because "to" is also a separator.
_DATES_RE = re.compile(
    r"^" + _POINT.format(m="sm", y="sy")
    + r"(?:\s*(?:-|–|—|\bto\b)\s*(?:" + _POINT.format(m="em", y="ey") + r"|(?P<ongoing>" + _ONGOING + r"))"
    + r"|\s+(?P<todate>to\s+date))?$",
    re.IGNORECASE,
)
_DATES_HINT = "use 'Jan 2020 – Present', 'Mar 2018 – Jun 2020', '2019 – 2021' or '2020'"


def _validate_dates(where: str, value: str) -> str:
    """Validate one ``dates`` string and return its canonical form.

    Canonical is ``"Mon YYYY – Mon YYYY"`` (months optional, the end may be
    ``Present``), joined with an en dash — exactly what
    ``seniority._position_boundaries`` splits and ``_parse_year_month`` reads,
    and what the web shows verbatim. Empty stays empty (unknown dates are
    silent, rule #29). Refused: a string that is not one of those shapes, an
    end before its start, ``Present`` as a start, and a year more than one
    year in the future.
    """
    if not value:
        return ""
    match = _DATES_RE.match(value)
    if not match:
        raise ProfileEditError(422, f"{where}: {value!r} is not a date range — {_DATES_HINT}")

    def _point(month: str | None, year: str) -> tuple[str, int, int | None]:
        if month:
            idx = _MONTHS.index(month[:3].lower())
            return f"{_MONTHS[idx].title()} {year}", int(year), idx + 1
        return year, int(year), None

    start_text, start_year, start_month = _point(match.group("sm"), match.group("sy"))
    latest = datetime.now(timezone.utc).year + 1
    if start_year > latest:
        raise ProfileEditError(422, f"{where}: {value!r} starts after {latest}")
    if match.group("ongoing") or match.group("todate"):
        return f"{start_text} – Present"
    if not match.group("ey"):
        return start_text
    end_text, end_year, end_month = _point(match.group("em"), match.group("ey"))
    if end_year > latest:
        raise ProfileEditError(422, f"{where}: {value!r} ends after {latest}")
    if (end_year, end_month or 12) < (start_year, start_month or 1):
        raise ProfileEditError(422, f"{where}: {value!r} ends before it starts")
    return f"{start_text} – {end_text}"


def _record_line(where: str, value: Any, limit: int, setting: str) -> str:
    if not isinstance(value, str):
        raise ProfileEditError(422, f"{where} must be a string, got {type(value).__name__}")
    text = _strip_control(value)
    if len(text) > limit:
        raise ProfileEditError(422, f"{where} exceeds the {limit}-character limit ({setting})")
    return text


def _record_lines(where: str, value: Any, limit: int, setting: str, *, dedup: bool) -> list[str]:
    if not isinstance(value, list):
        raise ProfileEditError(422, f"{where} must be a list of strings, got {type(value).__name__}")
    out: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(value):
        text = _record_line(f"{where}[{i}]", item, limit, setting)
        key = text.lower()
        if not text or (dedup and key in seen):
            continue
        seen.add(key)
        out.append(text)
    if len(out) > settings.PROFILE_EDIT_MAX_RECORD_ITEMS:
        raise ProfileEditError(
            422,
            f"{where} exceeds the {settings.PROFILE_EDIT_MAX_RECORD_ITEMS}-item limit "
            "(PROFILE_EDIT_MAX_RECORD_ITEMS)",
        )
    return out


def _validate_records(path: str, value: Any) -> list[dict[str, Any]]:
    """Validate a whole record list (the write REPLACES the list).

    Every record comes back with EVERY key of its schema, missing ones filled
    with ``""``/``[]``, so readers always see one stable shape.
    """
    schema = RECORD_SCHEMAS[path]
    allowed = ", ".join(schema)
    if not isinstance(value, list):
        raise ProfileEditError(
            422, f"{path} must be a list of objects with keys {allowed}, got {type(value).__name__}"
        )
    if len(value) > settings.PROFILE_EDIT_MAX_RECORDS:
        raise ProfileEditError(
            422,
            f"{path} exceeds the {settings.PROFILE_EDIT_MAX_RECORDS}-record limit (PROFILE_EDIT_MAX_RECORDS)",
        )
    records: list[dict[str, Any]] = []
    for i, raw in enumerate(value):
        where = f"{path}[{i}]"
        if not isinstance(raw, dict):
            raise ProfileEditError(
                422, f"{where} must be an object with keys {allowed}, got {type(raw).__name__}"
            )
        unknown = [str(k) for k in raw if k not in schema]
        if unknown:
            raise ProfileEditError(
                422, f"{where}: unknown key {unknown[0]!r} — allowed keys: {allowed}"
            )
        record: dict[str, Any] = {}
        for key, kind in schema.items():
            item_where = f"{where}.{key}"
            field_value = raw.get(key)
            if kind == "bullets":
                record[key] = [] if field_value is None else _record_lines(
                    item_where, field_value, settings.PROFILE_EDIT_MAX_BULLET_CHARS,
                    "PROFILE_EDIT_MAX_BULLET_CHARS", dedup=False,
                )
            elif kind == "tags":
                record[key] = [] if field_value is None else _record_lines(
                    item_where, field_value, settings.PROFILE_EDIT_MAX_ITEM_CHARS,
                    "PROFILE_EDIT_MAX_ITEM_CHARS", dedup=True,
                )
            elif field_value is None:
                record[key] = ""
            elif kind == "text":
                if not isinstance(field_value, str):
                    raise ProfileEditError(
                        422, f"{item_where} must be a string, got {type(field_value).__name__}"
                    )
                text = _strip_control(field_value, keep_newlines=True)
                if len(text) > settings.PROFILE_EDIT_MAX_CHARS:
                    raise ProfileEditError(
                        422,
                        f"{item_where} exceeds the {settings.PROFILE_EDIT_MAX_CHARS}-character limit "
                        "(PROFILE_EDIT_MAX_CHARS)",
                    )
                record[key] = text
            else:  # line / dates
                text = _record_line(
                    item_where, field_value, settings.PROFILE_EDIT_MAX_ITEM_CHARS, "PROFILE_EDIT_MAX_ITEM_CHARS"
                )
                record[key] = _validate_dates(item_where, text) if kind == "dates" else text
        identity = _RECORD_IDENTITY[path]
        if not any(record[k] for k in identity):
            raise ProfileEditError(
                422, f"{where} needs a non-empty {' or '.join(identity)}"
            )
        records.append(record)
    return records


# The note lists — standing instructions to the assistant, one line each.
# Kept as a set so a second note-shaped path is one entry here, not a new branch.
NOTE_PATHS: frozenset[str] = frozenset({"preferences.assistant_notes"})


def _validate_notes(path: str, value: Any) -> list[str]:
    """A list of short one-line notes, capped per note and per list.

    Each note has control/bidi characters stripped (same set as the record
    lists) and is at most ``PROFILE_NOTE_MAX_CHARS`` long — a breach is a 422
    naming the limit, never a silent cut. Blank notes are dropped; a repeated
    note (case/space-insensitive) keeps its first spelling. At most
    ``PROFILE_EDIT_MAX_LIST_ITEMS`` notes. An empty list is valid and means
    "nothing to say" (rule #29).
    """
    if not isinstance(value, list):
        raise ProfileEditError(422, f"{path} must be a list of strings, got {type(value).__name__}")
    out: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ProfileEditError(
                422, f"{path}[{i}] must be a string, got {type(item).__name__}"
            )
        text = _strip_control(item)
        if len(text) > settings.PROFILE_NOTE_MAX_CHARS:
            raise ProfileEditError(
                422,
                f"{path}[{i}] exceeds the {settings.PROFILE_NOTE_MAX_CHARS}-character limit "
                "(PROFILE_NOTE_MAX_CHARS)",
            )
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    if len(out) > settings.PROFILE_EDIT_MAX_LIST_ITEMS:
        raise ProfileEditError(
            422,
            f"{path} exceeds the {settings.PROFILE_EDIT_MAX_LIST_ITEMS}-item limit "
            "(PROFILE_EDIT_MAX_LIST_ITEMS)",
        )
    return out


WORK_AUTH_COUNTRIES_PATH = "preferences.work_authorization_countries"

# Owner decision 2026-10-10 - years of experience the user is looking for.
YEARS_MIN_PATH = "preferences.experience_years_min"
YEARS_MAX_PATH = "preferences.experience_years_max"
YEARS_PATHS: frozenset[str] = frozenset({YEARS_MIN_PATH, YEARS_MAX_PATH})
YEARS_LIMIT = 60


def _validate_years(path: str, value: Any) -> int:
    """A whole number 0..60. ``bool`` is refused (it is an ``int`` subclass)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= YEARS_LIMIT:
        raise ProfileEditError(422, f"{path} must be a whole number from 0 to {YEARS_LIMIT}")
    return value


def check_years_order(low: Any, high: Any) -> None:
    """422 when both years bounds are set and min is above max. ``None`` = don't care."""
    if low is not None and high is not None and low > high:
        raise ProfileEditError(422, f"{YEARS_MIN_PATH} must not be above {YEARS_MAX_PATH}")


def validate_edit(path: str, value: Any) -> Any:
    """Normalise ``value`` for ``path``, or raise ``ProfileEditError(422, ...)``.

    ``value=None`` clears the path unconditionally — always valid for a
    known path (R8). An unknown path is a 422 that lists the editable set
    (R9); a value of the wrong shape is a 422 naming the path (R10).
    """
    paths = editable_paths()
    forgiving.set_path(path)
    if path not in paths:
        from src.services.profile import assistant_settings as _retired  # noqa: PLC0415 — imports this module

        if path == _retired.APPLY_MODE_PATH:
            # Retired 2026-10-10: name the replacement so a cached assistant
            # instruction fixes itself instead of guessing.
            raise ProfileEditError(422, _retired.APPLY_MODE_RETIRED_MESSAGE, "retired_path")
        raise ProfileEditError(
            422, f"{path!r} is not an editable path. Editable paths: {', '.join(paths)}", "unknown_key"
        )
    if value is None:
        return None
    from src.services.profile import assistant_settings, user_info  # noqa: PLC0415 — both import this module

    if path in YEARS_PATHS:
        return _validate_years(path, value)
    if path == assistant_settings.SETUP_PROGRESS_PATH:
        return _bound_encoded_size(path, assistant_settings.validate_setup_progress(path, value))
    if path in assistant_settings.SETTING_PATHS:
        # Owner decision 2026-10-08 (S2): ints / closed sets / a future time /
        # one plain line — each refusal names the path, never the value.
        return assistant_settings.validate_setting(path, value)
    if path in user_info.USER_INFO_PATHS:
        answers = path == user_info.USER_INFO_ANSWERS_PATH
        return _bound_encoded_size(
            path,
            user_info.validate_user_info(path, value),
            limit=settings.USER_INFO_ANSWERS_MAX_CHARS if answers else settings.PROFILE_EDIT_MAX_RECORDS_CHARS,
            setting="USER_INFO_ANSWERS_MAX_CHARS" if answers else "PROFILE_EDIT_MAX_RECORDS_CHARS",
        )
    if path == user_info.SALARY_BY_COUNTRY_PATH:
        return _bound_encoded_size(
            path,
            user_info.validate_salary_by_country(path, value),
            limit=settings.PROFILE_EDIT_MAX_RECORDS_CHARS,
            setting="PROFILE_EDIT_MAX_RECORDS_CHARS",
        )
    if path in _CLOSED_SET_PATHS:
        return _validate_closed_set(path, value, _CLOSED_SET_PATHS[path])
    if path == WORK_AUTH_COUNTRIES_PATH:
        # ISO alpha-2 codes; a country name or "UK" is converted (and reported), a lone string becomes a list.
        return _bound_encoded_size(path, user_info.validate_country_list(path, value))
    if path in NOTE_PATHS:
        # Bounded by shape alone: PROFILE_NOTE_MAX_CHARS x PROFILE_EDIT_MAX_LIST_ITEMS.
        # The generic encoded cap (PROFILE_EDIT_MAX_CHARS) would quietly allow
        # only a handful of notes, which is not what either setting says.
        return _validate_notes(path, value)
    if path in RECORD_SCHEMAS:
        return _bound_encoded_size(
            path,
            _validate_records(path, value),
            limit=settings.PROFILE_EDIT_MAX_RECORDS_CHARS,
            setting="PROFILE_EDIT_MAX_RECORDS_CHARS",
        )

    ftype = _field_type(path)
    origin = typing.get_origin(ftype)
    normalised: Any
    if origin is list:
        normalised = _validate_list(path, value)
    elif ftype is bool:
        normalised = _validate_bool(path, value)
    elif ftype is str:
        normalised = _validate_string(path, value)
    elif origin is typing.Union:  # Optional[float] == Union[float, None]
        normalised = _validate_number(path, value)
    else:  # pragma: no cover — every declared editable field is one of the above
        raise ProfileEditError(422, f"{path}: unsupported field type {ftype!r}")
    return _bound_encoded_size(path, normalised)


def _bound_encoded_size(
    path: str, value: Any, *, limit: int | None = None, setting: str = "PROFILE_EDIT_MAX_CHARS"
) -> Any:
    """S9 — the ENCODED size is bounded too, not just the shape.

    ``PROFILE_EDIT_MAX_CHARS`` already caps a single string, and
    ``PROFILE_EDIT_MAX_LIST_ITEMS`` x ``PROFILE_EDIT_MAX_ITEM_CHARS`` caps a
    list's shape — but the product of those two (100 x 200 = 20 000 chars by
    default) is an order of magnitude past what the same setting allows a
    string to be. ``value`` is stored as ``json.dumps(value)``, so the honest
    bound is over the encoded form, applied identically to every type.
    """
    cap = settings.PROFILE_EDIT_MAX_CHARS if limit is None else limit
    encoded = len(json.dumps(value))
    if encoded > cap:
        raise ProfileEditError(
            422,
            f"{path} encodes to {encoded} characters, over the "
            f"{cap}-character limit ({setting})",
        )
    return value


def _is_missing_table(exc: Exception) -> bool:
    """True when a DB error means ``profile_edits`` itself is absent.

    N1 — ONE definition, in ``services.profile.storage``, which has used the
    identical check for ``user_profile_versions`` since long before this
    module existed. Imported lazily inside the function because ``storage``
    imports this module (``load_profile`` -> :func:`apply_overlay`), so a
    top-level import here would be a cycle.

    ``load_profile`` calls :func:`apply_overlay` -> :func:`current_overlay` on
    EVERY read, including from a DB that predates migration 0038 (an older
    test fixture, a fresh dev DB mid-migration) — that must degrade to "no
    overlay", not crash the read every other route depends on.
    """
    from src.services.profile.storage import _is_missing_table as _storage_check  # noqa: PLC0415

    return _storage_check(exc)


def current_overlay(user_id: str, conn: pgsync.Connection | None = None) -> list[dict[str, Any]]:
    """The newest row per path for ``user_id``, EXCLUDING a path whose newest
    row is a clear (``value IS NULL``). Ordered by path.

    ``{"path": ..., "value": ..., "set_by": ..., "set_at": ...}`` per row —
    the shape ``GET /profile``'s ``agent_edits`` and MCP ``get_profile`` both
    return directly. Returns ``[]`` when ``profile_edits`` doesn't exist yet
    (pre-migration DB) rather than raising.

    ``conn`` — run the query on a connection the CALLER already holds
    (``storage.load_profile`` does). Opening a second Postgres connection per
    profile read is real cost on a hot path that every route, the tailor and
    MCP go through; passing the open one keeps a profile read at one
    connection (S1/N4).
    """
    sql = """
        SELECT path, value, set_by, set_at, source, remember_consent_at
        FROM profile_edits
        WHERE user_id = ? AND id IN (
            SELECT MAX(id) FROM profile_edits WHERE user_id = ? GROUP BY path
        )
        ORDER BY path
        """
    try:
        if conn is not None:
            rows = conn.execute(sql, (user_id, user_id)).fetchall()
        else:
            with pgsync.connect(str(DB_PATH)) as own_conn:
                rows = own_conn.execute(sql, (user_id, user_id)).fetchall()
    except pgsync.OperationalError as exc:
        if _is_missing_table(exc):
            return []
        raise
    out: list[dict[str, Any]] = []
    for path, value, set_by, set_at, source, consent_at in rows:
        if value is None:
            continue
        out.append({
            "path": path, "value": json.loads(value), "set_by": set_by, "set_at": set_at,
            "source": source or "", "remember_consent_at": consent_at,
        })
    return out


# ── Two assistants, one profile (gap 4, slice 26) ───────────────────────────
# Every profile_edits writer takes this per-user lock inside its transaction, so
# a guarded write's check ("nothing moved since I read it") and its insert are one
# step. int32 class id for pg_advisory_xact_lock(class, hashtext(user_id)), the
# same two-key form as proof.PROOF_LOCK_CLASS (360_007) with its own class.
EDIT_LOCK_CLASS = 360_026


def _take_edit_lock(conn: pgsync.Connection, user_id: str) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(?, hashtext(?))", (EDIT_LOCK_CLASS, user_id))


@contextlib.contextmanager
def user_edit_lock(user_id: str) -> Iterator[None]:
    """Hold ``user_id``'s profile-write lock for the block, on a connection of its own.

    The block's reads and its :func:`record_edits` (called with ``lock_held=True``,
    or it would wait on this very lock) run on other connections, so what they
    commit is visible at once; nothing is written on this one. Keep the block
    SYNCHRONOUS: an ``await`` inside it would let a second request for the same
    user, on the same event loop, block that loop on a lock only the suspended
    first one can release.
    """
    with pgsync.connect(str(DB_PATH)) as conn, conn._raw.transaction():
        _take_edit_lock(conn, user_id)
        yield


def current_edit_id(user_id: str) -> int:
    """``MAX(profile_edits.id)`` for ``user_id`` (0 = no edit yet): get_profile's ``profile_edit_id``.

    It moves on EVERY accepted write - each is a new append-only row - which is
    why ``update_profile``'s ``expected_profile_version`` is checked against it.
    ``profile_version`` (``MAX(user_profile_versions.id)``, the artifact stamp)
    does not move on an overlay write: no snapshot is taken.
    """
    try:
        with pgsync.connect(str(DB_PATH)) as conn:
            row = conn.execute("SELECT MAX(id) FROM profile_edits WHERE user_id = ?", (user_id,)).fetchone()
    except pgsync.OperationalError as exc:
        if _is_missing_table(exc):
            return 0
        raise
    return int(row[0]) if row and row[0] is not None else 0


EDIT_OPS = ("set", "append", "upsert", "remove_at")


def is_list_path(path: str) -> bool:
    """True for an editable path whose value is a list (the paths an item op works on)."""
    return path in editable_paths() and typing.get_origin(_field_type(path)) is list


def _apply_op(path: str, op: str, value: Any, index: Any, items: list[Any]) -> list[Any]:
    from src.services.profile import user_info  # noqa: PLC0415 — imports this module

    if not is_list_path(path):
        raise ProfileEditError(422, f"{path}: op {op!r} works only on a list path; send the whole value", "bad_value")
    if op == "remove_at":
        if value is not None:
            raise ProfileEditError(422, f"{path}: remove_at takes an index, not a value", "bad_value")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(items):
            raise ProfileEditError(
                422, f"{path}: remove_at index must be from 0 to {len(items) - 1} (the list has {len(items)})",
                "out_of_range",
            )
        return items[:index] + items[index + 1:]
    if value is None:
        raise ProfileEditError(422, f"{path}: {op} needs a value (one item, not the list)", "bad_value")
    if op == "upsert":
        if path != user_info.USER_INFO_ANSWERS_PATH:
            raise ProfileEditError(
                422, f"{path}: upsert works only on {user_info.USER_INFO_ANSWERS_PATH}", "bad_value"
            )
        at = user_info.answer_upsert_index(items, value)
        if at is not None:
            return items[:at] + [value] + items[at + 1:]
    return items + [value]


def resolve_ops(edits: list[tuple[str, str, Any, Any]], current: dict[str, Any]) -> list[tuple[str, Any]]:
    """Turn ``(path, op, value, index)`` edits into ``(path, whole value)`` pairs.

    ``set`` (the default) passes through untouched. Every edit on a path that
    carries an item op (``append`` one item, ``upsert`` one saved answer,
    ``remove_at`` one index) is applied IN ORDER to that path's value in
    ``current`` (what get_profile's ``fields`` shows) and collapses into ONE
    pair holding the RESULTING list, at the path's first position - so the
    append-only ``profile_edits`` row stores the full value, like any write,
    and the usual validator then checks it whole (caps included). A refused op
    is a 422 naming the path; nothing is written.
    """
    paths = editable_paths()
    op_paths = {path for path, op, _, _ in edits if op != "set"}
    running: dict[str, list[Any] | None] = {}
    order: list[tuple[str, Any]] = []
    for path, op, value, index in edits:
        if op not in EDIT_OPS:  # the request model already refuses it; kept for direct callers
            raise ProfileEditError(422, f"{path}: op must be one of {', '.join(EDIT_OPS)}", "bad_value")
        if index is not None and op != "remove_at":
            raise ProfileEditError(422, f"{path}: index is only for op remove_at", "bad_value")
        if path not in op_paths:
            order.append((path, value))
            continue
        if path not in paths:
            raise ProfileEditError(
                422, f"{path!r} is not an editable path. Editable paths: {', '.join(paths)}", "unknown_key"
            )
        if path not in running:
            order.append((path, None))
            base = current.get(path)
            running[path] = list(base) if isinstance(base, list) else []
        if op != "set":
            running[path] = _apply_op(path, op, value, index, running[path] or [])
        elif value is None or isinstance(value, list):
            running[path] = value  # null still clears, unless a later op in this call adds to it
        else:
            raise ProfileEditError(422, f"{path} must be a list, got {type(value).__name__}", "bad_type")
    return [(path, running[path]) if path in running else (path, value) for path, value in order]


# The actor ``authorship.actor_for`` gives a signed-in human at the browser.
# Rows by this author are the human's own changes: they are history, never an
# "assistant changed this" mark.
WEB_ACTOR = "web"

# Feature 9 - WHO SAID IT. ``user_said`` = the user told the assistant (or typed
# it on the web); ``agent_inferred`` = the assistant worked it out. ``''`` is a
# legacy row (before migration 0057): nobody confirmed it, so it reads as
# unconfirmed. ``set_by`` records who WROTE a fact, not whether the user said it.
SOURCE_USER_SAID = "user_said"
SOURCE_AGENT_INFERRED = "agent_inferred"
VALID_SOURCES = (SOURCE_USER_SAID, SOURCE_AGENT_INFERRED)


def is_confirmed(set_by: str, source: str) -> bool:
    """True when the user stands behind this value: a web write, or an
    assistant write explicitly marked ``user_said``. A legacy ``''`` is not."""
    return set_by == WEB_ACTOR or source == SOURCE_USER_SAID


def is_assistant_actor(set_by: str) -> bool:
    """True for a row an assistant wrote (``agent:…`` / ``token:…``), False for the web."""
    return bool(set_by) and set_by != WEB_ACTOR


def path_history(user_id: str, path: str, limit: int) -> list[dict[str, Any]]:
    """Every row for one of ``user_id``'s paths, NEWEST first, at most ``limit``.

    ``{"value", "set_by", "set_at"}`` per row; ``value`` is ``None`` for a
    clear. Scoped by ``user_id`` in the query itself (rule #12) — a caller
    can only ever read its own history.
    """
    sql = (
        "SELECT value, set_by, set_at, source FROM profile_edits "
        "WHERE user_id = ? AND path = ? ORDER BY id DESC LIMIT ?"
    )
    try:
        with pgsync.connect(str(DB_PATH)) as conn:
            rows = conn.execute(sql, (user_id, path, limit)).fetchall()
    except pgsync.OperationalError as exc:
        if _is_missing_table(exc):
            return []
        raise
    return [
        {
            "value": None if value is None else json.loads(value), "set_by": set_by, "set_at": set_at,
            "source": source or "",
        }
        for value, set_by, set_at, source in rows
    ]


def apply_overlay(
    profile: UserProfile, user_id: str, conn: pgsync.Connection | None = None
) -> UserProfile:
    """Set every current overlay value onto ``profile`` IN PLACE, and return it.

    R8 — the one door: called by ``storage.load_profile`` so every reader
    sees the same merged profile. ``setattr`` only ever targets a name that
    passed :func:`editable_paths`'s dataclass-field check (S8) — never a
    ``__dict__``/``getattr``-chain on user input.

    ``conn`` is forwarded to :func:`current_overlay` so a caller that already
    has a connection open does not make the read open a second one.
    """
    return apply_overlay_rows(profile, current_overlay(user_id, conn))


def apply_overlay_rows(profile: UserProfile, rows: list[dict[str, Any]]) -> UserProfile:
    """:func:`apply_overlay` for a caller that ALREADY read the overlay rows.

    Same setattr rules, no query — used by ``storage.load_profile_with_overlay``,
    which returns the rows to its caller and must not read them twice.
    """
    valid_paths = set(editable_paths())
    touched_inference = False
    for row in rows:
        path = row["path"]
        if path not in valid_paths:
            # Defensive only: a path recorded under an env config that has
            # since narrowed PROFILE_EXTRA_EDITABLE_PATHS. Never crash a
            # profile read over a historical row.
            continue
        head, _, field_name = path.partition(".")
        setattr(profile_section(profile, head), field_name, row["value"])
        touched_inference = touched_inference or path in INFERENCE_INPUT_PATHS
    if touched_inference:
        # The stored ``experience_level_inferred`` was computed at extraction
        # time off the STORED history, which never contains what the agent
        # wrote here. Recompute it off the EFFECTIVE history, in this one read
        # door, so every reader (web, MCP, tailor) sees the level the agent's
        # history implies — derived on read, never stored a second time. An
        # empty result stays empty (rule #29): the agent's history is now the
        # history, and it says nothing about seniority. The user's own
        # ``preferences.experience_level`` is a separate field this never
        # touches: typed wins, inferred is only the fallback (seniority.py).
        profile.preferences.experience_level_inferred = infer_from_cv(profile.cv_data)
    return profile


# The counted auto grant's two paths (``assistant_settings.AUTO_GRANT_*_PATH``; that module imports this one).
_GRANT_PATHS = frozenset({"assistant_settings.auto_grant_count", "assistant_settings.auto_grant_until"})


def record_edits(
    user_id: str,
    actor: str,
    edits: list[tuple[str, Any]],
    *,
    enforce_rate_limit: bool = True,
    store_as_given: bool = False,
    source: str | dict[str, str] = "",
    consent_paths: Iterable[str] = (),
    lock_held: bool = False,
) -> list[dict[str, Any]]:
    """Validate every edit, then insert them append-only in ONE transaction.

    All-or-nothing (R8/S12) on BOTH halves:

    * every edit is validated BEFORE any row is written, so one bad path or
      value leaves the overlay completely untouched; and
    * the inserts run inside one explicit transaction, so a connection that
      dies mid-batch cannot leave half an overlay behind. The ``pgsync``
      connection is autocommit (``commit()`` is a no-op), which means without
      the block each ``INSERT`` was its own committed statement.

    VALIDATION COMES FIRST, THE BUDGET SECOND. The per-user hourly limit (S7,
    never per IP) is consulted immediately before the first write, so a call
    that is going to 422 anyway costs the caller nothing — an agent correcting
    a typo in a path should not be able to spend its own edit budget on
    rejections.

    ``enforce_rate_limit=False`` is for the WEB's own writes — the clearing
    rows ``POST /profile/clear`` and "Take back" append, and the rows a
    preferences save appends for each field the human changed. Those are the
    human's own changes, not an agent edit, so they must not consume the
    agent's hourly budget.

    ``store_as_given=True`` still VALIDATES every value but stores it exactly
    as passed, not in its normalised form. For the web's preference-save rows:
    the value was just saved to the base, and the history row must hold that
    same value — a normalised copy (stripped, de-duplicated) would shadow the
    base with something the human did not type.

    ``source`` (feature 9) is ``user_said`` / ``agent_inferred`` / ``""``, one
    word for the whole batch or a ``{path: word}`` map; stored on each row.

    ``consent_paths`` (weak spot #6, wave 2): paths whose row carries the user's OK to
    REMEMBER a sensitive answer. ``remember_consent_at`` is stamped HERE from the server
    clock - a caller can never supply the time (mirrors job-apply-store.py L541-544:
    ``record["rememberedWithConsentAt"] = utc_now()``, set by the helper, not the input).

    ``lock_held`` (gap 4): the caller already holds :func:`user_edit_lock`.
    Otherwise the transaction takes that per-user lock itself, so no write
    can land between a guarded caller's version check and its insert.

    Returns the applied rows in the same ``{"path", "value", "set_by",
    "set_at", "source"}`` shape as :func:`current_overlay`.
    """
    # Validate everything up front — nothing below this line can 422.
    normalised: list[tuple[str, Any]] = [(path, validate_edit(path, value)) for path, value in edits]
    if store_as_given:
        normalised = [(path, value) for path, value in edits]
    if not normalised:
        return []

    key = f"profile_edit:{user_id}"
    if (
        enforce_rate_limit
        and settings.PROFILE_EDIT_MAX_PER_HOUR > 0
        and not rate_limit.check_and_record(
            key, max_in_window=settings.PROFILE_EDIT_MAX_PER_HOUR, window_seconds=3600
        )
    ):
        raise ProfileEditError(
            429,
            f"too many profile edits in the last hour "
            f"(PROFILE_EDIT_MAX_PER_HOUR is {settings.PROFILE_EDIT_MAX_PER_HOUR}); "
            "try again later",
        )

    now = datetime.now(timezone.utc).isoformat()
    consent = frozenset(consent_paths)
    applied: list[dict[str, Any]] = []
    with pgsync.connect(str(DB_PATH)) as conn, conn._raw.transaction():
        if not lock_held:
            _take_edit_lock(conn, user_id)
        grant_write = actor == WEB_ACTOR and any(
            path in _GRANT_PATHS and value is not None for path, value in normalised
        )
        if grant_write:
            from src.services.profile import assistant_settings as rules  # noqa: PLC0415 - it imports this module

            began_before = rules._grant_began(conn, user_id)[1]
        for path, value in normalised:
            encoded = None if value is None else json.dumps(value)
            row_source = source.get(path, "") if isinstance(source, dict) else source
            if not row_source and actor == WEB_ACTOR:
                row_source = SOURCE_USER_SAID  # a human at the browser is the user speaking
            marker = now if (path in consent and value is not None) else None
            conn.execute(
                "INSERT INTO profile_edits (user_id, path, value, set_by, set_at, source, remember_consent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, path, encoded, actor, now, row_source, marker),
            )
            applied.append(
                {"path": path, "value": value, "set_by": actor, "set_at": now, "source": row_source}
            )
        if grant_write and rules._grant_began(conn, user_id)[1] != began_before:
            # Gap roadmap slice 30: the user's own web write that BEGINS an auto grant (the end set
            # while none is in force, or moved later: ``assistant_settings._grant_began``) snapshots
            # what it covers (their queue now) in this same transaction. A safer edit - a shorter end,
            # a new count - keeps the grant's scope: re-snapshotting would widen it to applications
            # queued after the user looked. This is the one writer, so every web door (PATCH /profile,
            # Confirm on Needs you, Take back) does it.
            from src.services.profile import grant_scope  # noqa: PLC0415 - grant_scope imports this module

            grant_scope.record_snapshot(conn, user_id, now)
    return applied


def field_values(profile: UserProfile, paths: Iterable[str]) -> dict[str, Any]:
    """``{path: current value}`` for every path in ``paths`` (R11).

    Feeds MCP ``get_profile``'s ``fields`` map — what the agent may change,
    and what it currently says, in one call.
    """
    out: dict[str, Any] = {}
    for path in paths:
        head, _, field_name = path.partition(".")
        out[path] = getattr(profile_section(profile, head), field_name)
    return out
