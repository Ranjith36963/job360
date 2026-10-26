"""REGIONS (owner decision 2026-10-26) - one Where-you'll-work line can cover a
whole region instead of one country: "Europe, on-site only if sponsored".

A right-to-work record's ``country`` may be one of these region codes as well as
an ISO alpha-2 code or ``"ANY"``. Which record answers for a job in a country:
the country's own record, else its region's, else ``ANY`` (``record_code_for``).
Each country sits in exactly one region, so that order is never ambiguous.

Pure geography (a fixed, closed table, like ``countries.py``): it only says
which countries a region the USER picked covers. Job360 never ranks, picks or
recommends a place with it (VISION rule 4).
"""
from __future__ import annotations

from typing import Optional

_MEMBERS: dict[str, str] = {
    "NORTH_AMERICA": (
        "US CA MX GL PM BM UM BZ CR GT HN NI PA SV AG AI AW BB BL BQ BS CU CW DM DO GD GP HT JM KN KY LC "
        "MF MQ MS PR SX TC TT VC VG VI"
    ),
    "SOUTH_AMERICA": "AR BO BR CL CO EC FK GF GY PE PY SR UY VE GS BV",
    "EUROPE": (
        "AD AL AT AX BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GG GI GR HR HU IE IM IS IT JE LI LT LU "
        "LV MC MD ME MK MT NL NO PL PT RO RS RU SE SI SJ SK SM UA VA XK"
    ),
    "MIDDLE_EAST": "AE BH IL IQ IR JO KW LB OM PS QA SA SY TR YE",
    "ASIA": (
        "AF AM AZ BD BN BT CC CN CX GE HK ID IN IO JP KG KH KP KR KZ LA LK MM MN MO MV MY NP PH PK SG TH "
        "TJ TL TM TW UZ VN"
    ),
    "AFRICA": (
        "AO BF BI BJ BW CD CF CG CI CM CV DJ DZ EG EH ER ET GA GH GM GN GQ GW KE KM LR LS LY MA MG ML MR "
        "MU MW MZ NA NE NG RE RW SC SD SH SL SN SO SS ST SZ TD TF TG TN TZ UG YT ZA ZM ZW"
    ),
    "OCEANIA": "AS AU CK FJ FM GU HM KI MH MP NC NF NR NU NZ PF PG PN PW SB TK TO TV VU WF WS",
}

REGIONS: dict[str, frozenset[str]] = {code: frozenset(m.split()) for code, m in _MEMBERS.items()}
REGION_NAMES: dict[str, str] = {
    "NORTH_AMERICA": "North America", "SOUTH_AMERICA": "South America", "EUROPE": "Europe",
    "MIDDLE_EAST": "Middle East", "ASIA": "Asia", "AFRICA": "Africa", "OCEANIA": "Oceania",
}
_REGION_OF: dict[str, str] = {cc: code for code, members in REGIONS.items() for cc in members}
_SPOKEN: dict[str, str] = {
    "north america": "NORTH_AMERICA", "south america": "SOUTH_AMERICA", "latin america": "SOUTH_AMERICA",
    "europe": "EUROPE", "middle east": "MIDDLE_EAST", "gulf": "MIDDLE_EAST", "asia": "ASIA",
    "africa": "AFRICA", "oceania": "OCEANIA", "australia and new zealand": "OCEANIA",
}


def is_region(code: object) -> bool:
    return isinstance(code, str) and code in REGIONS


def region_code(text: str) -> Optional[str]:
    """``"Europe"`` / ``"EUROPE"`` / ``"middle east"`` -> its code; ``None`` when not a region."""
    t = " ".join(str(text).replace("_", " ").lower().split())
    up = t.upper().replace(" ", "_")
    return up if up in REGIONS else _SPOKEN.get(t)


def region_of(country: Optional[str]) -> Optional[str]:
    """The region an ISO alpha-2 code sits in, or ``None``."""
    return _REGION_OF.get((country or "").strip().upper())


def place_name(code: str) -> str:
    """A region's plain name; any other code comes back as it is."""
    return REGION_NAMES.get(code, code)


def record_code_for(codes: set[str] | frozenset[str], country: Optional[str], any_code: str = "ANY") -> Optional[str]:
    """Which of the user's record codes answers for ``country``: its own, else its region's, else ANY."""
    cc = (country or "").strip().upper()
    if cc and cc in codes:
        return cc
    region = region_of(cc)
    if region and region in codes:
        return region
    return any_code if any_code in codes else None
