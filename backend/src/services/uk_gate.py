"""The UK gate — one door every job passes through before it is stored.

Job360 is a UK-market product. A job in Warsaw or Indianapolis reaching a
user's feed is a PRODUCT FAULT, not a ranking preference — so the fix belongs
at the door, not in the scorer. The only prior defence was the legacy scorer's
-15 foreign-location penalty, which admits the job and then argues about its
rank; measured 2026-08-07, 523 clearly foreign jobs were live in prod.

THE DESIGN RULE: BOUNDED vs UNBOUNDED.

The first version of this gate enumerated FOREIGN cities by hand. The owner
rejected it: "How many will you hard-code like that? What if there is a city
out of this list? Then you missed that." Correct — foreign cities are an
UNBOUNDED set, and a hand-written sample of an unbounded set rots silently.

So the polarity is inverted. UK places are a FINITE set (~52k populated places,
published, and settlements do not churn), compiled into `data/uk_gazetteer/` by
`scripts/build_uk_gazetteer.py`. Every future miss is a data refresh, never a
code edit.

Two sets stay enumerated ON PURPOSE, and the distinction is the whole point:
countries (~250) and first-level admin divisions (~3.9k — US states, Canadian
provinces, Australian states) are CLOSED sets. A COMPLETE closed set is not the
same mistake as a SAMPLE of an unbounded one. Both are built from data, so
neither can drift.

WHY THE FOREIGN CHECK SURVIVES INVERSION. Positive-only matching has a fatal
hole: "Cambridge (USA)" contains "cambridge", a real UK town, so a pure
gazetteer lookup would ADMIT it. The country/admin override runs FIRST and
catches the "usa" segment. Same for "Darmstadt, DE" and "Boston, MA".

WHY SOURCE TRUST SURVIVES TOO. Even a 52k gazetteer misses hamlets, business
parks and new estates. A source that is UK-only by construction (Reed, NHS,
teaching_vacancies, devitjobs.uk) still keeps its unrecognised locations —
there, an unknown place is a UK place we simply do not have.

AMBIGUITY IS COMPUTED, NOT TYPED. Boston, Cambridge and Perth name real places
both here and abroad; `ambiguous.txt` is derived at build time by comparing UK
populations against world cities. On a global source those names need one
corroborating signal; on a UK-native source they pass.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from src.services.skill_matcher import REMOTE_TERMS

_DATA = Path(__file__).resolve().parent.parent.parent / "data" / "uk_gazetteer"

# Sources whose catalog is UK-only by construction. Adding a source? It
# defaults to GLOBAL (strict) — a new source opts INTO trust, never inherits it.
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

# Full UK postcode. Checked against near-misses: Canadian "M5V 2T6" and Dutch
# "1234 AB" do NOT match. Outward-only codes ("EC2") are deliberately rejected
# as far too loose.
_POSTCODE = re.compile(r"\b[A-Za-z]{1,2}\d[A-Za-z\d]?\s?\d[A-Za-z]{2}\b")

# Remote that is not remote-for-you: a global board fencing a role to another
# country's timezone or right-to-work.
_RESTRICTED_REMOTE = re.compile(
    r"\b(?:us|usa|united states|canada|emea|eu|europe|apac|latam|india|"
    r"australia|germany|brazil|nigeria|philippines)[\s-]*(?:only|based|"
    r"residents?|timezone|time zone)\b"
    r"|\bonly\s+(?:in|from)\s+(?:the\s+)?(?:us|usa|united states|eu|europe|india)\b",
    re.IGNORECASE,
)

# UK evidence in the AD BODY, used only when the location field is unhelpful.
# This stays a small curated regex and must NEVER become the gazetteer: real UK
# towns include Street, Deal, Ware, March, Eye and Sale — harmless in a
# location field, catastrophic in prose.
_UK_EVIDENCE = re.compile(
    r"£|\bGBP\b|\bright to work in the UK\b|\bUK[- ]based\b|"
    r"\bSkilled Worker\b|\bBPSS\b|\bSC clearance\b|\bNHS\b|\bHMRC\b|"
    r"\bLtd\b|\bpension.{0,20}\bUK\b|"
    r"\b(?:london|manchester|birmingham|leeds|bristol|edinburgh|glasgow|"
    r"cardiff|belfast|liverpool|sheffield|nottingham|southampton|"
    r"united kingdom)\b",
    re.IGNORECASE,
)

_UK_SELF = frozenset({"uk", "u k", "gb", "united kingdom", "great britain",
                      "england", "scotland", "wales", "northern ireland"})


@lru_cache(maxsize=1)
def _gazetteer() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Load the compiled data once. Lazy, per the heavy-import rules (#11/#16).

    A missing data directory degrades to source-trust + evidence rather than
    blocking everything: a deploy that forgot the data files must not empty the
    catalog.
    """
    def _read(name: str) -> frozenset[str]:
        path = _DATA / name
        if not path.exists():
            return frozenset()
        return frozenset(
            ln for ln in path.read_text(encoding="utf-8").split("\n") if ln
        )

    return _read("uk_places.txt"), _read("foreign_admin.txt"), _read("ambiguous.txt")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", s)).strip()


def _segments(location: str) -> list[str]:
    """Split a messy free-text location into candidate places.

    "Barcelona; Madrid" -> [barcelona, madrid]
    "Cambridge (USA)"   -> [cambridge, usa]      <- why the override matters
    "Palo Alto HQ"      -> [palo alto hq]
    """
    parts = re.split(r"[,;/|()\[\]–—]|\s+-\s+", location or "")
    return [seg for seg in (_norm(p) for p in parts) if seg]


def _uk_hit(seg: str, places: frozenset[str]) -> Optional[str]:
    """Return the matched UK place name, or None.

    Whole-segment match first (the trusted case: "newcastle upon tyne"), then
    longest-first n-grams up to 4 tokens so "newcastle upon tyne hybrid" still
    resolves. A single token is accepted only as a WHOLE segment — one bare
    word inside a longer phrase is too weak to trust.
    """
    if seg in places:
        return seg
    toks = seg.split()
    for n in range(min(4, len(toks)), 1, -1):
        for i in range(len(toks) - n + 1):
            gram = " ".join(toks[i:i + n])
            if gram in places:
                return gram
    return None


@dataclass(frozen=True)
class GateVerdict:
    """Why a job was allowed or blocked — the reason is the point.

    A bare boolean makes a wrong decision impossible to debug and impossible to
    tune. `reason` is logged and counted so tightening or loosening the gate is
    an evidence-based change, never a guess.
    """

    allowed: bool
    reason: str


def check_uk(
    location: Optional[str],
    source: str,
    *,
    description: str = "",
    title: str = "",
) -> GateVerdict:
    """Decide whether a job belongs in a UK-market catalog.

    Order is load-bearing. The foreign override and the fenced-remote check run
    BEFORE any positive UK matching, or "Cambridge (USA)" and "Remote (US only)"
    both slip through.
    """
    places, foreign, ambiguous = _gazetteer()
    raw = location or ""
    segs = _segments(raw)
    body = f"{title}\n{description}"
    is_native = source in UK_NATIVE_SOURCES

    uk_named = any(s in _UK_SELF for s in segs) or bool(_POSTCODE.search(raw))
    uk_place = next((m for s in segs if (m := _uk_hit(s, places))), None)

    # 1. A named foreign country or state loses — whoever listed it.
    if any(s in foreign for s in segs):
        # The dual-site escape ("London / New York" — keep it, the user can
        # take the UK half) demands an UNAMBIGUOUS UK signal. A bare gazetteer
        # hit is not enough: the UK has a hamlet called Sydney, so
        # "Sydney, Australia" was being admitted as a dual-site posting.
        # Requiring either an explicit UK country/postcode or a non-ambiguous
        # place keeps London/New York while dropping Sydney/Australia.
        if uk_named or (uk_place and uk_place not in ambiguous):
            return GateVerdict(True, "dual_site_includes_uk")
        return GateVerdict(False, "foreign_location")

    # 2. Remote fenced to another region is not remote for a UK user.
    if _RESTRICTED_REMOTE.search(raw) or (
        not raw.strip() and _RESTRICTED_REMOTE.search(body[:2000])
    ):
        return GateVerdict(False, "remote_restricted_to_other_region")

    # 3. An explicit UK country name or a full postcode is decisive.
    if uk_named:
        return GateVerdict(True, "uk_location")

    # 4. The gazetteer. Computed-ambiguous names (Boston, Cambridge, Perth)
    #    need corroboration on a global source — that collision set is DERIVED
    #    from population data, never typed.
    if uk_place:
        if uk_place in ambiguous and not is_native:
            if _UK_EVIDENCE.search(body[:4000]):
                return GateVerdict(True, "ambiguous_name_with_uk_evidence")
            return GateVerdict(False, "ambiguous_place_name_unconfirmed")
        return GateVerdict(True, "uk_gazetteer")

    # 5. Genuine remote (already cleared of foreign fencing above).
    low = raw.lower()
    if any(t in segs or t in low for t in REMOTE_TERMS):
        return GateVerdict(True, "remote")

    # 6. Nothing decisive in the location. WHO said it now decides.
    if is_native:
        return GateVerdict(True, "uk_native_source")
    if _UK_EVIDENCE.search(body[:4000]):
        return GateVerdict(True, "uk_evidence_in_ad")
    if not raw.strip():
        return GateVerdict(False, "no_location_on_global_source")
    return GateVerdict(False, "unverified_location_on_global_source")
