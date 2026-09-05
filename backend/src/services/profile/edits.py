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
``VALID_EXPERIENCE_LEVELS``).
"""
from __future__ import annotations

import json
import math
import typing
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


def _bound_encoded_size(path: str, value: Any) -> Any:
    """S9 — the ENCODED size is bounded too, not just the shape.

    ``PROFILE_EDIT_MAX_CHARS`` already caps a single string, and
    ``PROFILE_EDIT_MAX_LIST_ITEMS`` x ``PROFILE_EDIT_MAX_ITEM_CHARS`` caps a
    list's shape — but the product of those two (100 x 200 = 20 000 chars by
    default) is an order of magnitude past what the same setting allows a
    string to be. ``value`` is stored as ``json.dumps(value)``, so the honest
    bound is over the encoded form, applied identically to every type.
    """
    encoded = len(json.dumps(value))
    if encoded > settings.PROFILE_EDIT_MAX_CHARS:
        raise ProfileEditError(
            422,
            f"{path} encodes to {encoded} characters, over the "
            f"{settings.PROFILE_EDIT_MAX_CHARS}-character limit (PROFILE_EDIT_MAX_CHARS)",
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
    return profile


def record_edits(
    user_id: str,
    actor: str,
    edits: list[tuple[str, Any]],
    *,
    enforce_rate_limit: bool = True,
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

    ``enforce_rate_limit=False`` is for the WEB's own bookkeeping writes — the
    clearing rows ``POST /profile/clear`` and the preferences form append to
    retire an overlay path. Those are the human's clear, not an agent edit, so
    they must not consume the agent's hourly budget.

    Returns the applied rows in the same ``{"path", "value", "set_by",
    "set_at"}`` shape as :func:`current_overlay`.
    """
    # Validate everything up front — nothing below this line can 422.
    normalised: list[tuple[str, Any]] = [(path, validate_edit(path, value)) for path, value in edits]
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
