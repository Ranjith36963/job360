"""Answer once: a GUESS for a common question, read off what the profile already holds (owner, 2026-11-02:
"You have everything yourself... why didn't you get that?").

``guess(row_id, profile, country)`` -> ``(value, reason)`` or ``None``. ``common_questions`` calls it only for a
row with no saved or assistant-drafted answer. A guess is NEVER verified or approved: its row reads "guessed"
until the owner presses OK, and ``reason`` is the plain "from ..." line the card shows. Nothing is invented
(rule #29): no source field, no guess, and the row is still asked with an empty value. Read-only.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

Guess = Optional[tuple[str, str]]
ASSISTANT_GUESS = "your assistant's guess"
_ADULT_AFTER_DEGREE_YEARS = 3  # a degree finished this long ago means the owner is well over 18
_SURE_AUTH = frozenset({"citizen", "permanent_resident"})  # right to work with no sponsorship


def _host(url: str) -> str:
    return (urlsplit(url if "//" in url else "//" + url).hostname or "").casefold()


def _on(url: str, domain: str) -> bool:
    host = _host(url)
    return host == domain or host.endswith("." + domain)


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _contact(profile: Any) -> dict[str, Any]:
    return getattr(getattr(profile, "user_info", None), "contact", None) or {}


def _links(profile: Any) -> list[str]:
    cv = [_text(u) for u in getattr(getattr(profile, "cv_data", None), "links", None) or []]
    saved = [_text(_contact(profile).get(k)) for k in ("website", "github_url", "linkedin_url")]
    return [u for u in [*cv, *saved] if u]


def website(profile: Any, _cc: Optional[str]) -> Guess:
    """A personal site or portfolio, else GitHub, else LinkedIn: always a link the profile holds."""
    links = _links(profile)
    social = [u for u in links if _on(u, "github.com") or _on(u, "linkedin.com")]
    for label, urls in (
        ("portfolio link", [u for u in links if u not in social]),
        ("GitHub link", [u for u in links if _on(u, "github.com")]),
        ("LinkedIn link", [u for u in links if _on(u, "linkedin.com")]),
    ):
        if urls:
            return urls[0], f"from your {label}"
    return None


def _birth_age(profile: Any, today: date) -> Optional[int]:
    try:
        born = date.fromisoformat(_text(_contact(profile).get("date_of_birth")))
    except ValueError:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _saved_age(profile: Any) -> Optional[int]:
    for rec in getattr(getattr(profile, "user_info", None), "answers", None) or []:
        if isinstance(rec, dict) and str(rec.get("topic") or "").casefold() == "age":
            for word in _text(rec.get("answer")).replace(",", " ").split():
                if word.isdigit():
                    return int(word)
    return None


def _degree_year(profile: Any, today: date) -> Optional[int]:
    """The earliest year a listed education entry ended (its latest 4-digit year, not in the future)."""
    ended: list[int] = []
    for entry in getattr(getattr(profile, "cv_data", None), "education", None) or []:
        years = [int(w) for w in _text(entry).replace(",", " ").replace("-", " ").replace("/", " ").split()
                 if len(w) == 4 and w.isdigit() and 1950 <= int(w) <= today.year]
        if years:
            ended.append(max(years))
    return min(ended) if ended else None


def over_18(profile: Any, _cc: Optional[str]) -> Guess:
    today = datetime.now(timezone.utc).date()
    age = _saved_age(profile)
    if age is not None:
        return ("Yes" if age >= 18 else "No"), f"from your saved age ({age})"
    age = _birth_age(profile, today)
    if age is not None:
        return ("Yes" if age >= 18 else "No"), f"from your date of birth (age {age})"
    year = _degree_year(profile, today)
    if year is not None and today.year - year >= _ADULT_AFTER_DEGREE_YEARS:
        return "Yes", f"from your education (degree {year})"
    return None


def _split_location(profile: Any) -> list[str]:
    text = _text(getattr(getattr(profile, "cv_data", None), "location", ""))
    return [p.strip() for p in text.split(",") if p.strip()]


def city(profile: Any, _cc: Optional[str]) -> Guess:
    saved = _text(_contact(profile).get("address_city"))
    if saved:
        return saved, "from your address"
    parts = _split_location(profile)
    return (parts[0], "from your CV location") if len(parts) > 1 else None


def residence_country(profile: Any, _cc: Optional[str]) -> Guess:
    from src.services.profile.countries import to_iso2  # noqa: PLC0415

    saved = _text(_contact(profile).get("address_country"))
    if saved:
        return saved, "from your address"
    parts = _split_location(profile)
    code = to_iso2(parts[-1]) if len(parts) > 1 else None
    return (code, "from your CV location") if code else None


def full_name(profile: Any, _cc: Optional[str]) -> Guess:
    first, last = (_text(_contact(profile).get(k)) for k in ("legal_first_name", "legal_last_name"))
    return (f"{first} {last}", "from your legal name") if first and last else None


def _rtw(profile: Any, cc: Optional[str]) -> dict[str, Any]:
    rtw = getattr(getattr(profile, "user_info", None), "right_to_work", None) or {}
    return next((r for r in rtw.get("countries") or []
                 if isinstance(r, dict) and str(r.get("country") or "").upper() == cc), {})


def _can_work(profile: Any, cc: Optional[str]) -> Optional[str]:
    """Why the user may work in ``cc`` without sponsorship, in plain words; None when nothing says so."""
    rtw = getattr(getattr(profile, "user_info", None), "right_to_work", None) or {}
    rec = _rtw(profile, cc)
    if str(rec.get("work_authorization") or "") in _SURE_AUTH or rec.get("needs_sponsorship") is False:
        return f"your saved right to work in {cc}"
    if cc in [str(c).upper() for c in rtw.get("citizenship") or []]:
        return f"your citizenship ({cc})"
    allowed = getattr(getattr(profile, "preferences", None), "work_authorization_countries", None) or []
    return f"your authorised countries ({cc})" if cc in [str(c).upper() for c in allowed] else None


def work_authorization(profile: Any, cc: Optional[str]) -> Guess:
    why = _can_work(profile, cc)
    return ("Yes", f"from {why}") if why else None


def needs_sponsorship(profile: Any, cc: Optional[str]) -> Guess:
    why = _can_work(profile, cc)
    return ("No", f"from {why}") if why else None


def relocate(profile: Any, cc: Optional[str]) -> Guess:
    logistics = getattr(getattr(profile, "user_info", None), "logistics", None) or {}
    rec = next((r for r in logistics.get("countries") or []
                if isinstance(r, dict) and str(r.get("country") or "").upper() == cc), {})
    where = _text(rec.get("relocate_where"))
    return ("Yes", f"from where you said you would relocate ({where})") if where else None


def office_days(profile: Any, cc: Optional[str]) -> Guess:
    arrangement = _text(getattr(getattr(profile, "preferences", None), "work_arrangement", "")).casefold()
    if arrangement in ("hybrid", "onsite"):
        return "Yes", f"from your work arrangement ({arrangement})"
    return ("No", "from your work arrangement (remote)") if arrangement == "remote" else None


def earliest_start(profile: Any, _cc: Optional[str]) -> Guess:
    notice = _text((getattr(getattr(profile, "user_info", None), "logistics", None) or {}).get("notice_period"))
    return (f"After my notice period ({notice})", "from your notice period") if notice else None


# Catalog row id -> its derivation. Rows with no source on the profile (phone, email, travel, notice,
# salary, equality...) are not here: the assistant guesses them (see ``ASSISTANT_GUESS``) or they stay empty.
_DERIVE: dict[str, Callable[[Any, Optional[str]], Guess]] = {
    "website": website, "over_18": over_18, "city": city, "residence_country": residence_country,
    "full_name": full_name, "work_authorization": work_authorization, "needs_sponsorship": needs_sponsorship,
    "relocate": relocate, "office_days": office_days, "earliest_start": earliest_start,
}


def guess(row_id: str, profile: Any, country: Optional[str]) -> Guess:
    """``(value, reason)`` for catalog row ``row_id`` (``country``: the row's own, None for a global one)."""
    fn = _DERIVE.get(row_id)
    return fn(profile, country) if fn else None
