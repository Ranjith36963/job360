"""Agent edits to the profile — an append-only overlay over extraction.

Spec: ``docs/plans/2026-09-05-contacts-stats/spec.md`` R8-R12, S2-S4, S7-S9,
S12. An edit is a row in ``profile_edits`` (migration 0038); the CURRENT
value of a path is its newest row, and ``value IS NULL`` means "cleared —
fall back to what extraction says". ``storage.load_profile`` calls
:func:`apply_overlay` after building the dataclasses so every reader — the
web route, the tailor, MCP ``get_profile`` — sees one profile.

The editable set is closed (R9) and validated against the DECLARED dataclass
fields of ``CVData``/``UserPreferences`` at import (S8): a path naming a
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

import json
import math
import re
import typing
import unicodedata
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from typing import Any, Iterable

from src.core import settings
from src.core.settings import DB_PATH
from src.repositories import pgsync
from src.services.auth import rate_limit
from src.services.profile.models import (
    VALID_EXPERIENCE_LEVELS,
    VALID_WORK_ARRANGEMENTS,
    CVData,
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

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# The two closed-set preference paths (R10) — same vocabulary as the web
# form's normalisers in api/routes/profile.py.
_CLOSED_SET_PATHS: dict[str, frozenset[str]] = {
    "preferences.work_arrangement": VALID_WORK_ARRANGEMENTS,
    "preferences.experience_level": VALID_EXPERIENCE_LEVELS,
}

_editable_paths_cache: tuple[str, ...] | None = None


def _declared_fields() -> dict[str, set[str]]:
    """``{"cv_data": {...field names...}, "preferences": {...}}``."""
    return {
        "cv_data": {f.name for f in dataclass_fields(CVData)},
        "preferences": {f.name for f in dataclass_fields(UserPreferences)},
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
    cls: type[Any] = CVData if head == "cv_data" else UserPreferences
    hints = typing.get_type_hints(cls)
    return hints[field_name]


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


def _validate_closed_set(path: str, value: Any, allowed: frozenset[str]) -> str:
    if not isinstance(value, str):
        raise ProfileEditError(422, f"{path} must be a string, got {type(value).__name__}")
    text = value.strip().lower()
    # An empty string is the explicit "unset" (R10) — never checked against
    # the allowlist, always accepted.
    if text and text not in allowed:
        raise ProfileEditError(
            422,
            f"{path}: {value!r} is not one of the allowed values: {', '.join(sorted(allowed))}",
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


def validate_edit(path: str, value: Any) -> Any:
    """Normalise ``value`` for ``path``, or raise ``ProfileEditError(422, ...)``.

    ``value=None`` clears the path unconditionally — always valid for a
    known path (R8). An unknown path is a 422 that lists the editable set
    (R9); a value of the wrong shape is a 422 naming the path (R10).
    """
    paths = editable_paths()
    if path not in paths:
        raise ProfileEditError(
            422, f"{path!r} is not an editable path. Editable paths: {', '.join(paths)}"
        )
    if value is None:
        return None
    if path in _CLOSED_SET_PATHS:
        return _validate_closed_set(path, value, _CLOSED_SET_PATHS[path])
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
        SELECT path, value, set_by, set_at
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
    for path, value, set_by, set_at in rows:
        if value is None:
            continue
        out.append({"path": path, "value": json.loads(value), "set_by": set_by, "set_at": set_at})
    return out


# The actor ``authorship.actor_for`` gives a signed-in human at the browser.
# Rows by this author are the human's own changes: they are history, never an
# "assistant changed this" mark.
WEB_ACTOR = "web"


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
        "SELECT value, set_by, set_at FROM profile_edits "
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
        {"value": None if value is None else json.loads(value), "set_by": set_by, "set_at": set_at}
        for value, set_by, set_at in rows
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
        target: Any = profile.cv_data if head == "cv_data" else profile.preferences
        setattr(target, field_name, row["value"])
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


def record_edits(
    user_id: str,
    actor: str,
    edits: list[tuple[str, Any]],
    *,
    enforce_rate_limit: bool = True,
    store_as_given: bool = False,
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

    Returns the applied rows in the same ``{"path", "value", "set_by",
    "set_at"}`` shape as :func:`current_overlay`.
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
    applied: list[dict[str, Any]] = []
    with pgsync.connect(str(DB_PATH)) as conn, conn._raw.transaction():
        for path, value in normalised:
            encoded = None if value is None else json.dumps(value)
            conn.execute(
                "INSERT INTO profile_edits (user_id, path, value, set_by, set_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, path, encoded, actor, now),
            )
            applied.append({"path": path, "value": value, "set_by": actor, "set_at": now})
    return applied


def field_values(profile: UserProfile, paths: Iterable[str]) -> dict[str, Any]:
    """``{path: current value}`` for every path in ``paths`` (R11).

    Feeds MCP ``get_profile``'s ``fields`` map — what the agent may change,
    and what it currently says, in one call.
    """
    out: dict[str, Any] = {}
    for path in paths:
        head, _, field_name = path.partition(".")
        target: Any = profile.cv_data if head == "cv_data" else profile.preferences
        out[path] = getattr(target, field_name)
    return out
