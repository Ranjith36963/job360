"""Slice 7 (#514) — the visa / sponsorship signal, country-agnostic.

docs/plans/2026-09-11-visa-signal/spec.md. Job360 knows NOTHING about visas:
no country rule, no keyword scan, no guess when the ad is silent (VISION
decision 20 — the agent judges, we store). This module holds the two facts
and the one comparison between them:

* fact 1, per application — ``visa_signal`` (closed set), the ad sentence it
  rests on, and the job's ISO alpha-2 country (a SLOT on ``applications``,
  overwritten like the fit verdict, spec S7);
* fact 2, per candidate — ``preferences.work_authorization_countries``, the
  ISO alpha-2 codes where they need no sponsorship;
* ``needs_sponsorship`` — ``None`` when either side is silent (rule #29),
  else "is the job's country in the candidate's list".
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications.spine import SpineError, get_owned_application

_ALPHA2 = re.compile(r"^[A-Za-z]{2}$")


def normalize_signal(value: Optional[str]) -> str:
    """``None``/'' → 'unknown'; anything outside the closed set is a ValueError."""
    signal = (value or "unknown").strip().lower()
    if signal not in settings.APPLICATION_VISA_SIGNALS:
        raise ValueError(f"visa_signal must be one of {settings.APPLICATION_VISA_SIGNALS}; got {value!r}")
    return signal


def normalize_country(value: Optional[str]) -> str:
    """ISO 3166-1 alpha-2, upper-cased; '' stays ''. Two letters is the whole
    rule — Job360 keeps no country table to check against."""
    code = (value or "").strip()
    if not code:
        return ""
    if not _ALPHA2.match(code):
        raise ValueError(f"country must be an ISO alpha-2 code like 'GB' or 'IN'; got {value!r}")
    return code.upper()


def normalize_country_codes(value: Any) -> list[str]:
    """The candidate's list: strings only, each alpha-2, upper, de-duplicated,
    order kept. Empty in, empty out."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("work_authorization_countries must be a list of ISO alpha-2 codes")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("work_authorization_countries: every item must be a string")
        code = normalize_country(item)
        if code and code not in out:
            out.append(code)
    return out


def normalize_detail(value: Optional[str]) -> str:
    detail = (value or "").strip()
    if len(detail) > settings.APPLICATION_VISA_DETAIL_MAX_CHARS:
        raise ValueError(
            f"visa_detail exceeds APPLICATION_VISA_DETAIL_MAX_CHARS ({settings.APPLICATION_VISA_DETAIL_MAX_CHARS})"
        )
    return detail


def needs_sponsorship(signal: str, country: str, countries: list[str]) -> Optional[bool]:
    """The one comparison. ``None`` = nothing to say (rule #29): the ad said
    nothing, the job's country is unknown, or the candidate gave no list."""
    if signal == "unknown" or not country or not countries:
        return None
    return country.upper() not in {c.upper() for c in countries}


async def user_work_countries(db: JobDatabase, user_id: str) -> list[str]:
    """Fact 2 — read THROUGH the agent-edit overlay (slice 4): a list set via
    ``update_profile`` lives in ``profile_edits`` until the web saves, and the
    comparison must see it, so this goes through ``load_profile`` (the same
    read ``GET /profile`` makes) rather than the base ``user_profiles`` row.
    Lazy import: the profile stack is heavy (rule #16). ``db`` is accepted
    for signature parity with the other readers on the request path."""
    del db  # the profile read is sync (pgsync) and opens its own connection
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    profile = load_profile(user_id)
    if profile is None:
        return []
    raw = getattr(profile.preferences, "work_authorization_countries", None)
    return [c for c in raw if isinstance(c, str)] if isinstance(raw, list) else []


def visa_view(app_row: dict[str, Any], countries: list[str]) -> dict[str, Any]:
    """The ``visa`` object every reader emits for an application row."""
    signal = app_row.get("visa_signal") or "unknown"
    country = app_row.get("visa_country") or ""
    return {
        "signal": signal,
        "detail": app_row.get("visa_detail") or "",
        "country": country,
        "recorded_by": app_row.get("visa_recorded_by") or "",
        "recorded_at": app_row.get("visa_recorded_at") or "",
        "needs_sponsorship": needs_sponsorship(signal, country, countries),
    }


async def set_visa_signal(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    recorded_by: str,
    signal: Optional[str],
    detail: Optional[str] = None,
    country: Optional[str] = None,
) -> dict[str, Any]:
    """Overwrite the slot (spec S7 — a slot is not history). Validation is
    the same three rules every door uses; a bad value is a 422 naming it."""
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")
    try:
        sig = normalize_signal(signal)
        det = normalize_detail(detail)
        cc = normalize_country(country)
    except ValueError as exc:
        raise SpineError(422, str(exc)) from None
    now = datetime.now(timezone.utc).isoformat()
    await db._db.execute(
        "UPDATE applications SET visa_signal = ?, visa_detail = ?, visa_country = ?, "
        "visa_recorded_by = ?, visa_recorded_at = ?, updated_at = ? WHERE id = ?",
        (sig, det, cc, recorded_by, now, now, application_id),
    )
    countries = await user_work_countries(db, user_id)
    return {
        "application_id": application_id,
        "visa": visa_view(
            {
                "visa_signal": sig, "visa_detail": det, "visa_country": cc,
                "visa_recorded_by": recorded_by, "visa_recorded_at": now,
            },
            countries,
        ),
    }
