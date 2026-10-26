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
from src.services.profile.regions import REGIONS, is_region

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


class WorkCountries(list[str]):
    """The candidate's sponsorship-free countries (a plain list, so every reader
    keeps working) plus ``needs``: countries the candidate SAID need sponsorship.
    An explicit "needs sponsorship here" is an answer even when no free country
    is known."""

    needs: frozenset[str] = frozenset()


def needs_sponsorship(signal: str, country: str, countries: list[str]) -> Optional[bool]:
    """The one comparison. ``None`` = nothing to say (rule #29): the ad said
    nothing, the job's country is unknown, or the candidate gave no answer
    for it (no list, and the country is not one they said needs sponsorship)."""
    if signal == "unknown" or not country:
        return None
    if country.upper() in getattr(countries, "needs", frozenset()):
        return True
    if not countries:
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
    out = WorkCountries()
    if profile is None:
        return out
    raw = getattr(profile.preferences, "work_authorization_countries", None)
    if isinstance(raw, list):
        out.extend(c.upper() for c in raw if isinstance(c, str))
    # The setup interview saves right to work in user_info.right_to_work (one record
    # per country). A country whose record says no sponsorship is needed counts too,
    # so the two places never disagree about the same fact.
    rtw = getattr(profile.user_info, "right_to_work", None)
    for code in _no_sponsorship_countries(rtw):
        if code not in out:
            out.append(code)
    out.needs = frozenset(_sponsorship_countries(rtw))
    return out


def _country_records(right_to_work: Any) -> list[tuple[str, dict[str, Any]]]:
    """``(code, record)`` per place: each country's own record, then each region record
    once per member country WITHOUT its own record (owner decision 2026-10-26: a region
    line answers for its countries; a country's own line wins). ``ANY`` passes as it is."""
    records = right_to_work.get("countries") if isinstance(right_to_work, dict) else None
    recs = [
        (rec["country"].strip().upper(), rec) for rec in (records if isinstance(records, list) else [])
        if isinstance(rec, dict) and isinstance(rec.get("country"), str) and rec["country"].strip()
    ]
    own = {code for code, _ in recs if not is_region(code)}
    out = [(code, rec) for code, rec in recs if not is_region(code)]
    for code, rec in recs:
        if is_region(code):
            out += [(cc, rec) for cc in sorted(REGIONS[code] - own)]
    return out


def _sponsorship_countries(right_to_work: Any) -> list[str]:
    """ISO2 codes whose ``right_to_work`` record (or region record) says ``needs_sponsorship`` true."""
    return [code for code, rec in _country_records(right_to_work) if rec.get("needs_sponsorship") is True]


def _no_sponsorship_countries(right_to_work: Any) -> list[str]:
    """ISO2 codes the user can work in without sponsorship, from
    ``user_info.right_to_work``: a country record saying ``needs_sponsorship``
    false, or saying citizen / permanent resident (unless it says sponsorship
    true), plus the ``citizenship`` list. A record with neither answer is
    skipped (rule #29)."""
    if not isinstance(right_to_work, dict):
        return []
    out: list[str] = []

    def _add(code: Any) -> None:
        if isinstance(code, str) and code.strip() and code.strip().upper() not in out:
            out.append(code.strip().upper())

    for code, rec in _country_records(right_to_work):
        if rec.get("needs_sponsorship") is True:
            continue
        if rec.get("needs_sponsorship") is False or rec.get("work_authorization") in ("citizen", "permanent_resident"):
            _add(code)
    citizenship = right_to_work.get("citizenship")
    for code in citizenship if isinstance(citizenship, list) else []:
        _add(code)
    return out


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
