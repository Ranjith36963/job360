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
published, and settlements do not churn), compiled into `src/data/uk_gazetteer/` by
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

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from src.services.skill_matcher import REMOTE_TERMS

logger = logging.getLogger(__name__)

# THE DATA LIVES INSIDE THE PACKAGE, AND THAT IS LOAD-BEARING (issue #260).
#
# It used to sit in `backend/data/uk_gazetteer/`. Production installs the app
# with `pip install .`, which copies only the `src*` packages into
# site-packages — so `backend/data/` never shipped, this path resolved to a
# directory that did not exist, and `_gazetteer()` degraded to empty sets.
# The gate then ran BLIND for four days: the 2026-08-11 04:06 UTC prod run
# blocked 372 jobs without a single `foreign_location` verdict among them, and
# jobs in Berlin, Warsaw and São Paulo were stored as if they were UK roles.
#
# Under `src/` the files are package data (`pyproject.toml`
# `[tool.setuptools.package-data]`), so the dev tree and the installed wheel
# resolve the SAME relative path. Moving them back out re-opens #260.
_DATA = Path(__file__).resolve().parent.parent / "data" / "uk_gazetteer"

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

    But it SAYS SO. Silent degradation is what made #260 invisible — every
    instrument stayed green (no errors, no failing tests, a gate that logged
    blocks every run) while the two decisive sets were empty. Degrade quietly
    and you have a guard that cannot be seen failing.
    """
    def _read(name: str) -> frozenset[str]:
        path = _DATA / name
        if not path.exists():
            return frozenset()
        return frozenset(
            ln for ln in path.read_text(encoding="utf-8").split("\n") if ln
        )

    places = _read("uk_places.txt")
    foreign = _read("foreign_admin.txt")
    ambiguous = _read("ambiguous.txt")
    if not places or not foreign:
        logger.error(
            "UK GATE DEGRADED — gazetteer missing or empty at %s "
            "(uk_places=%d, foreign_admin=%d, ambiguous=%d). Foreign jobs will "
            "be stored: the country override and the place lookup both need "
            "this data. This is issue #260 — check that the files ship with "
            "the package.",
            _DATA, len(places), len(foreign), len(ambiguous),
        )
    return places, foreign, ambiguous


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


# Single-word remote terms, derived from the one shared constant so the two can
# never drift. Multi-word terms ("work from home") are left out on purpose:
# stripping "home" or "work" out of an ordinary place name would change
# verdicts far outside the case this exists for.
_REMOTE_TOKENS = frozenset(t for t in REMOTE_TERMS if " " not in t)


def _foreign_hit(
    seg: str,
    foreign: frozenset[str],
    places: frozenset[str],
    ambiguous: frozenset[str],
) -> Optional[str]:
    """Return the foreign country/admin name this segment names, or None.

    Whole-segment equality first — that is the safe test, and it stays the
    only one for ordinary segments. `foreign_admin.txt` is a complete closed
    set of countries and first-level divisions, so it necessarily contains
    two-letter codes and generic words ("in" is India's ISO code, "west" and
    "manchester" are real admin divisions abroad). Matching those ANYWHERE
    inside a segment was measured over the 4,647 live rows and wrongly blocked
    "North West England", "Manchester Science Park" and "Shoreham-by-Sea".

    The one extra case: a remote word glued to a country — "US-Remote",
    "Remote India", "US Remote". Normalisation turns those into ONE segment
    ("us remote"), so whole-segment equality missed them and the remote branch
    downstream admitted the job. Strip the remote tokens and re-test the
    REMAINDER as a whole segment; a UK place wins over a foreign twin, so
    "Manchester Remote" is unaffected. Measured effect: 28 rows of 4,647 flip
    to blocked, all US-only remotes; 0 UK rows lost.
    """
    if seg in foreign:
        return seg
    tokens = seg.split()
    rest = [t for t in tokens if t not in _REMOTE_TOKENS]
    if not rest or len(rest) == len(tokens):
        return None  # no remote word here — decided exactly as before
    remainder = " ".join(rest)
    if remainder in places and remainder not in ambiguous:
        return None
    return remainder if remainder in foreign else None


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


def names_foreign_place(text: str) -> bool:
    """True only when this text NAMES a non-UK country or admin division.

    The fetch-time filter in `sources/base.py` used to answer this from a
    hand-typed list of foreign cities — an unbounded set, so it rotted (rule
    #30). This routes the question through the SAME function the door uses,
    so the two can never disagree.

    It answers CONSERVATIVELY, on purpose. Only the `foreign_location`
    verdict counts:

      * The weaker refusals ("unverified location", "no location on a global
        source", "ambiguous place name") depend on WHO the source is and what
        the ad body says — context the door has and a fetch filter does not.
        Treating them as foreign here would drop UK jobs from UK-native
        sources.
      * `remote_restricted_to_other_region` is excluded too, and that one was
        measured: four callers pass a DESCRIPTION rather than a location, and
        an ad reading "Remote - UK/Europe timezone" trips the region-fencing
        regex in prose. Those are UK-eligible jobs. The door still applies
        that rule to the real location field.

    Nothing this returns False for is admitted by that alone — the door
    judges every job again, with the full context.
    """
    return check_uk(text, "").reason == "foreign_location"


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
    if any(_foreign_hit(s, foreign, places, ambiguous) for s in segs):
        # The dual-site escape ("London / New York" — keep it, the user can
        # take the UK half) demands an UNAMBIGUOUS UK signal. A bare gazetteer
        # hit is not enough: the UK has a hamlet called Sydney, so
        # "Sydney, Australia" was being admitted as a dual-site posting.
        # Requiring either an explicit UK country/postcode or a non-ambiguous
        # place keeps London/New York while dropping Sydney/Australia.
        #
        # AND THE UK HALF MUST NOT BE THE FOREIGN HALF. The UK has hamlets
        # called California, New York, Maryland and Washington — all of which
        # are also US states, so they sit in the gazetteer AND in
        # foreign_admin. "San Jose, California, US" therefore collected a
        # "UK" gazetteer hit on `california` and walked straight through as a
        # two-site ad. Measured on the live catalog 2026-08-12: 373 rows were
        # riding this escape. So when the location names several places, the
        # UK signal has to come from a segment that is NOT itself a foreign
        # name — `london` in "London / New York", never `california` in a
        # Californian address.
        #
        # The trigger is an ORPHAN segment: one that names a foreign place and
        # has no UK reading whatsoever ("US", "Virginia", "Ontario"). That is
        # an address marker, and the segments beside it belong to that same
        # address. Deliberately narrow — dry-run over the live catalog
        # 2026-08-12 (rule #30) showed the wider version ("the UK signal must
        # always come from a non-foreign segment") also refused three genuine
        # UK rows, "Manchester, Greater Manchester" and "Manchester - Main
        # Office", because Manchester is itself an admin division abroad.
        # Never false-block a UK job to catch a foreign one.
        #
        # A location naming ONE place is exempt too: plain "Manchester" (or
        # "Manchester, Remote" — a remote word is not a second place) reads
        # both ways, and the UK reading is the right one; 238 live UK rows
        # depend on it.
        #
        # KNOWN GAP, left deliberately: "London, Ontario", "New York, NY" and
        # "Remote - New York" still pass, for exactly the reason "London, New
        # York" is asserted to pass two tests up — a UK city name beside a
        # foreign region is how BOTH a foreign address and a genuine two-site
        # ad get written, and "New York" is also a real (tiny) UK hamlet.
        # Splitting those needs ambiguity DATA — a gazetteer rebuild marking
        # `new york` and `california` ambiguous, which is where the knowledge
        # belongs — not another branch here.
        named = [s for s in segs if any(t not in _REMOTE_TOKENS for t in s.split())]
        escape_place = uk_place
        if len(named) > 1 and any(
            s in foreign and not _uk_hit(s, places) for s in named
        ):
            escape_place = next(
                (m for s in named if s not in foreign and (m := _uk_hit(s, places))),
                None,
            )
        if uk_named or (escape_place and escape_place not in ambiguous):
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
