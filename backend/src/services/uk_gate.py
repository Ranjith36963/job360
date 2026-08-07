"""The UK gate — one door every job passes through before it is stored.

Job360 is a UK-market product. A job in Warsaw or Indianapolis reaching a
user's feed is a PRODUCT FAULT, not a ranking preference — so the fix belongs
at the door, not in the scorer. Before this module the only defence was the
legacy scorer's -15 foreign-location penalty, which lets a foreign job into
the catalog and then argues about its rank; measured 2026-08-07, 112 clearly
foreign jobs were live in prod (greenhouse 77, workday 24, devitjobs 11).

WHY NOT "no UK token -> reject". `UK_TERMS` holds 26 cities. Cardiff,
Newcastle, Brighton, York and hundreds of other real UK towns are absent, and
measurement showed 171 `teaching_vacancies` rows (UK schools) plus 42 `reed`
rows whose location carries no UK token at all. A blanket flip would delete
real UK jobs to remove foreign ones. So the gate asks a second question the
location alone cannot answer: WHO said it.

    UK-NATIVE source  (reed, NHS, jobs.ac.uk, teaching_vacancies, ...)
        -> UK by construction. An unrecognised town is a UK town we do not
           have in the list. Ambiguity is KEPT.

    GLOBAL source  (greenhouse, workday, devitjobs, remoteok, ...)
        -> lists the whole world. An unrecognised town is a foreign town
           until something says otherwise. Ambiguity needs EVIDENCE:
           a UK token, genuine remote, a GBP symbol, or UK right-to-work
           language in the ad.

Unknown sources default to GLOBAL (strict), so a newly added source must opt
into trust rather than silently inherit it.

Everything here is a decision about PLACE, deliberately kept out of the
scorer: the scorer ranks jobs the user could actually take.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.services.skill_matcher import FOREIGN_INDICATORS, REMOTE_TERMS, UK_TERMS

# Sources whose catalog is UK-only by construction: a UK job board, a UK
# public-sector feed, or an API queried with a UK region parameter. For these,
# a location we cannot classify is a UK place missing from UK_TERMS.
#
# Adding a source? It defaults to GLOBAL (strict). Add it here ONLY if the
# upstream cannot return a non-UK job.
UK_NATIVE_SOURCES = frozenset({
    "reed",                 # UK job board
    "adzuna",               # queried with UK country param
    "careerjet",            # queried with UK locale
    "findwork",             # UK-filtered query
    "nhs_jobs",             # UK public sector
    "nhs_jobs_xml",
    "jobs_ac_uk",           # UK academia
    "uni_jobs",             # UK universities
    "teaching_vacancies",   # DfE — England state schools
    "gov_apprenticeships",  # DfE Display Advert API
    "bcs_jobs",             # BCS — UK chartered IT institute
    "devitjobs",            # devitjobs.UK — the endpoint is the UK site
    "linkedin",             # our scraper queries UK geo only
})

# "Remote" that is not remote-for-you: a global board advertising a role
# restricted to another country's timezone or right-to-work.
_RESTRICTED_REMOTE = re.compile(
    r"\b(?:us|usa|united states|canada|emea|eu|europe|apac|latam|india|"
    r"australia|germany|brazil|nigeria|philippines)[\s-]*(?:only|based|"
    r"residents?|timezone|time zone)\b"
    r"|\bonly\s+(?:in|from)\s+(?:the\s+)?(?:us|usa|united states|eu|europe|india)\b",
    re.IGNORECASE,
)

# UK evidence inside the ad body when the location field is unhelpful.
#
# Two families. First, UK-specific vocabulary an advertiser only writes for a
# UK role (currency, right-to-work, clearances, UK institutions). Second,
# unambiguous UK city names — measured on prod, 14 blocked jobs named a UK
# city in the body while their location field said "4 Locations" or nothing
# at all (Workday's multi-site postings do exactly this). Cambridge, Reading,
# Boston and York are deliberately EXCLUDED from the city list: each is also
# a US city, and in body text there is no comma-country to disambiguate.
_UK_EVIDENCE = re.compile(
    r"£|\bGBP\b|\bright to work in the UK\b|\bUK[- ]based\b|"
    r"\bSkilled Worker\b|\bBPSS\b|\bSC clearance\b|\bNHS\b|\bHMRC\b|"
    r"\bLtd\b|\bpension.{0,20}\bUK\b|"
    r"\b(?:london|manchester|birmingham|leeds|bristol|edinburgh|glasgow|"
    r"cardiff|belfast|liverpool|sheffield|nottingham|southampton|"
    r"united kingdom)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GateVerdict:
    """Why a job was allowed or blocked — the reason is the point.

    A bare boolean makes a wrong decision impossible to debug and impossible
    to tune. `reason` is logged and counted so tightening or loosening the
    gate is an evidence-based change, not a guess.
    """

    allowed: bool
    reason: str


def _has_term(text: str, terms: object) -> bool:
    return any(re.search(rf"\b{re.escape(t)}\b", text) for t in terms)  # type: ignore[union-attr]


def check_uk(
    location: Optional[str],
    source: str,
    *,
    description: str = "",
    title: str = "",
) -> GateVerdict:
    """Decide whether a job belongs in a UK-market catalog.

    Order matters: an explicit foreign country beats everything (a job in
    "Remote - Berlin, Germany" is a German job), and a restricted-remote ad
    beats the bare word "remote".
    """
    loc = (location or "").strip().lower()
    body = f"{title}\n{description}"
    is_native = source in UK_NATIVE_SOURCES

    # 1. An explicit foreign country/city always loses, whoever listed it.
    if loc and _has_term(loc, FOREIGN_INDICATORS):
        if _has_term(loc, UK_TERMS):
            # "London / New York" — a genuinely dual-site posting. Keep it:
            # the user can take the UK half.
            return GateVerdict(True, "dual_site_includes_uk")
        return GateVerdict(False, "foreign_location")

    # 2. Remote that is fenced to another country is not remote for a UK user.
    if _RESTRICTED_REMOTE.search(loc) or (
        not loc and _RESTRICTED_REMOTE.search(body[:2000])
    ):
        return GateVerdict(False, "remote_restricted_to_other_region")

    # 3. Positive UK signal in the location.
    if loc and _has_term(loc, UK_TERMS):
        return GateVerdict(True, "uk_location")

    # 4. Genuine remote (already cleared of foreign fencing above).
    if loc and _has_term(loc, REMOTE_TERMS):
        return GateVerdict(True, "remote")

    # 5. Nothing decisive. WHO said it now decides.
    if is_native:
        return GateVerdict(True, "uk_native_source")
    if _UK_EVIDENCE.search(body[:4000]):
        return GateVerdict(True, "uk_evidence_in_ad")
    if not loc:
        return GateVerdict(False, "no_location_on_global_source")
    return GateVerdict(False, "unverified_location_on_global_source")
