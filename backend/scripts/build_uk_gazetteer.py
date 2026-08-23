"""Compile the UK gazetteer the UK gate matches against. Run rarely; commit output.

WHY THIS EXISTS — the bounded/unbounded distinction.

The first version of the gate enumerated FOREIGN cities by hand: warsaw, madrid,
munich, shanghai... The owner rejected it, correctly: "How many will you
hard-code like that? What if there is a city out of this list? Then you missed
that." Foreign cities are an UNBOUNDED set — a hand list is a permanent
liability that silently rots.

The insight is to invert polarity. UK places are a FINITE set: ~44k populated
places, published, and settlements do not churn. Enumerating the finite side is
not "hardcoding", it is using data — and every future miss becomes a data
refresh, never a code edit.

But two sets stay enumerated ON PURPOSE, and this is the distinction that
matters: countries (~250) and first-level admin divisions (~3.9k, i.e. US
states, Canadian provinces, Australian states) are CLOSED sets. A COMPLETE
closed set is not the same mistake as a SAMPLE of an unbounded one. They are
still taken from data here rather than typed, so they cannot drift.

The foreign check must survive because positive-only matching has a fatal hole:
"Cambridge (USA)" contains "cambridge", a real UK town, and pure gazetteer
matching would ADMIT it. The country/state override runs first and catches the
"USA" segment.

SOURCES (all GeoNames, CC BY 4.0 — attribution in src/data/uk_gazetteer/NOTICE):
  GB.zip                whole UK incl. Northern Ireland (verified: belfast,
                        derry, lisburn, armagh all present; admin1 = ENG/SCT/
                        WLS/NIR). One file, so no GB-only gap to patch.
  countryInfo.txt       every country + its common name
  admin1CodesASCII.txt  every first-level admin division worldwide
  cities500.zip         world cities, used ONLY to COMPUTE the ambiguity set

AMBIGUITY IS COMPUTED, NEVER TYPED. Boston, Cambridge, Perth, Newcastle and
York name real places both in the UK and abroad. Hand-listing them would repeat
the original sin, so this script derives them: a UK name is ambiguous when a
non-GB city of comparable-or-greater population shares it. London survives
(London, Ontario is far smaller than London, UK); Boston does not.

A COUNTRY OR A STATE MAKES A NAME AMBIGUOUS TOO (issue #330, fixed 2026-08-19).
The ambiguity set was first computed against `cities500` ALONE, so it only ever
saw world CITY primary names. The UK has hamlets literally called New York
(pop 0), California (pop 830) and Canada (pop 0) — and none of those three names
is a cities500 primary name: GeoNames calls NYC "New York City", California is a
US STATE and Canada is a COUNTRY. All three therefore scored as TRUSTED,
unambiguous UK places, and `uk_gate.check_uk` handed them its dual-site escape:
"New York City, New York, United States" was stored as a two-site ad that
included the UK. Measured in prod 2026-08-19: those three names carried 153 of
the 190 live foreign rows. So the country and admin1 lists — already downloaded,
already closed sets — now feed the ambiguity computation as well, weighted (see
FOREIGN_ADMIN_WEIGHT). This is the DATA fix `uk_gate`'s own docstring asked for;
no branch was added to the gate.

Usage:  python scripts/build_uk_gazetteer.py            # download + compile
        python scripts/build_uk_gazetteer.py --check    # verify committed data
"""
from __future__ import annotations

import argparse
import io
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://download.geonames.org/export/dump/"
# Inside the package, NOT backend/data/ — `pip install .` ships only `src*`, so
# data written outside it never reaches production (issue #260).
OUT = Path(__file__).resolve().parent.parent / "src" / "data" / "uk_gazetteer"

# A foreign city makes a UK name ambiguous when it is at least this fraction of
# the UK place's population. Tuned by eyeballing the output: at 0.5, Boston and
# Cambridge are flagged (their foreign twins are comparable or larger) while
# London is not (London, Ontario is ~4% of London, UK). See --check output.
AMBIGUITY_RATIO = 0.5
# Below this, a foreign twin is too small to plausibly host job ads.
MIN_FOREIGN_POP = 20_000

# A country or a first-level admin division has no city population to compare
# against, so it enters the ambiguity computation with a flat effective weight.
# Via AMBIGUITY_RATIO this makes the rule "a UK settlement of more than
# 2 x FOREIGN_ADMIN_WEIGHT people keeps its name outright" — Manchester
# (568,996), Southampton (269,781) and Canterbury (55,087) are UK towns first,
# whatever they also name abroad, while the hamlets that merely SHARE a name
# with a state or a country (New York 0, Canada 0, California 830) lose their
# blanket trust.
#
# 20,000 is not a taste call: the 84 UK names that collide with a foreign
# country/admin1 were measured (2026-08-19) and their UK populations have one
# clean gap, between Warwick (37,267 — already ambiguous via cities500) and
# Portsmouth/"prt" (47,350). A 40,000 cut-off sits inside it, so no threshold
# nearby either loses a real town or keeps a hamlet. Effect: +53 names, -0.
FOREIGN_ADMIN_WEIGHT = 20_000

# Names the committed artifact MUST classify this way. These are the exact
# cases #330 leaked (and the exact towns a too-eager fix would false-block), so
# `--check` fails loudly rather than letting a silent rebuild regress either
# side. Guarded by tests/test_uk_gate.py::test_gazetteer_canaries.
AMBIGUOUS_CANARIES = ("new york", "california", "canada")
UNAMBIGUOUS_CANARIES = ("london", "manchester", "southampton", "birmingham",
                        "canterbury", "leeds", "bristol", "glasgow")


def _norm(s: str) -> str:
    """Lowercase + strip accents, so 'Münchén' and 'munchen' compare equal."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _fetch(name: str) -> bytes:
    url = BASE + name
    print(f"  fetching {url}", flush=True)
    with urllib.request.urlopen(url, timeout=180) as r:  # noqa: S310 — fixed host
        return r.read()


def _tsv_from_zip(blob: bytes, member: str) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(member).decode("utf-8", "replace")
    return [ln.split("\t") for ln in text.split("\n") if ln]


def build() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # ── UK places (feature class P = populated place) ────────────────────────
    gb = _tsv_from_zip(_fetch("GB.zip"), "GB.txt")
    uk: dict[str, int] = {}
    for p in gb:
        if len(p) < 15 or p[6] != "P":
            continue
        pop = int(p[14] or 0)
        for nm in [p[1], p[2]] + (p[3].split(",") if p[3] else []):
            nm = _norm(nm)
            # 3+ chars: 2-letter "places" collide with country codes and noise.
            if len(nm) >= 3:
                uk[nm] = max(uk.get(nm, 0), pop)
    print(f"  UK places: {len(uk)}")

    # ── Closed sets: countries + first-level admin divisions ────────────────
    foreign: set[str] = set()
    for ln in _fetch("countryInfo.txt").decode("utf-8", "replace").split("\n"):
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.split("\t")
        if len(f) > 4 and f[0] != "GB":
            # Name AND both ISO codes. Ads write "USA" and "Darmstadt, DE" far
            # more often than "United States" or "Germany"; without the codes,
            # "Indianapolis, IN, USA" fell through to the judgement bucket
            # instead of being blocked outright. The 2-letter codes are safe
            # because segments match WHOLE — a segment that is exactly "de" is
            # a country code, never a word.
            foreign.add(_norm(f[4]))          # United States
            foreign.add(_norm(f[1]))          # USA  (ISO3)
            foreign.add(_norm(f[0]))          # US   (ISO2)
    for ln in _fetch("admin1CodesASCII.txt").decode("utf-8", "replace").split("\n"):
        f = ln.split("\t")
        # f[0] = "US.CA"; skip GB's own divisions or England blocks itself.
        if len(f) > 1 and not f[0].startswith("GB."):
            nm = _norm(f[1])
            if len(nm) >= 4:  # "goa", "ohio" ok; shorter is collision-prone
                foreign.add(nm)
    foreign = {f for f in foreign if f and f not in ("united kingdom",)}
    print(f"  foreign countries + admin1: {len(foreign)}")

    # ── Ambiguity, COMPUTED from world cities ───────────────────────────────
    world = _tsv_from_zip(_fetch("cities500.zip"), "cities500.txt")
    foreign_pop: dict[str, int] = {}
    for p in world:
        if len(p) < 15 or p[8] == "GB":
            continue
        pop = int(p[14] or 0)
        if pop < MIN_FOREIGN_POP:
            continue
        nm = _norm(p[1])
        foreign_pop[nm] = max(foreign_pop.get(nm, 0), pop)
    print(f"  world city names >= {MIN_FOREIGN_POP}: {len(foreign_pop)}")

    # ...and from the closed country/admin1 sets, at a flat weight. `max` so a
    # name that is BOTH a big world city and a state keeps the city's real
    # population — the weight may only ever raise a name's foreign standing,
    # never lower it.
    for nm in foreign:
        foreign_pop[nm] = max(foreign_pop.get(nm, 0), FOREIGN_ADMIN_WEIGHT)

    ambiguous = {
        nm for nm, uk_pop in uk.items()
        if nm in foreign_pop and foreign_pop[nm] >= max(1, uk_pop) * AMBIGUITY_RATIO
    }
    print(f"  computed ambiguous names: {len(ambiguous)}")

    (OUT / "uk_places.txt").write_text("\n".join(sorted(uk)), encoding="utf-8")
    (OUT / "foreign_admin.txt").write_text("\n".join(sorted(foreign)), encoding="utf-8")
    (OUT / "ambiguous.txt").write_text("\n".join(sorted(ambiguous)), encoding="utf-8")
    (OUT / "NOTICE").write_text(
        "UK gazetteer data derived from GeoNames (https://www.geonames.org/),\n"
        "licensed under Creative Commons Attribution 4.0 (CC BY 4.0).\n"
        "Files: GB.zip, countryInfo.txt, admin1CodesASCII.txt, cities500.zip.\n"
        "Rebuild with: python scripts/build_uk_gazetteer.py\n",
        encoding="utf-8",
    )

    for f in ("uk_places.txt", "foreign_admin.txt", "ambiguous.txt"):
        print(f"  wrote {f}: {(OUT / f).stat().st_size // 1024} KB")

    # Eyeball the decisions that matter most.
    print("\n  spot checks:")
    for t in ("belfast", "telford", "shieldfield", "cardiff", "wellingborough"):
        print(f"    UK place {t:<16} {t in uk}")
    for t in ("boston", "cambridge", "london", "york", "perth", "manchester"):
        print(f"    ambiguous {t:<16} {t in ambiguous}")
    return check()


def check() -> int:
    """Verify the committed artifacts are present, sane, AND still decide the
    cases #330 turned on.

    A row count alone cannot catch this class of bug: `ambiguous.txt` had 495
    plausible-looking entries for four days while `new york`, `california` and
    `canada` were missing from it and 190 foreign jobs were live. So the canary
    names are asserted BOTH ways — the ones that must be ambiguous, and the real
    UK cities a too-eager rebuild would false-block.
    """
    ok = True
    sets: dict[str, frozenset[str]] = {}
    for f, floor in (("uk_places.txt", 20_000), ("foreign_admin.txt", 500),
                     ("ambiguous.txt", 1)):
        path = OUT / f
        if not path.exists():
            print(f"MISSING {f}")
            ok = False
            continue
        entries = frozenset(
            x for x in path.read_text(encoding="utf-8").split("\n") if x
        )
        sets[f] = entries
        print(f"  {f}: {len(entries)} entries")
        if len(entries) < floor:
            print(f"  ^ too few (expected >= {floor}) — rebuild")
            ok = False

    ambiguous = sets.get("ambiguous.txt", frozenset())
    places = sets.get("uk_places.txt", frozenset())
    for nm in AMBIGUOUS_CANARIES:
        if nm not in ambiguous:
            print(f"  CANARY FAILED: '{nm}' must be ambiguous (issue #330) — rebuild")
            ok = False
    for nm in UNAMBIGUOUS_CANARIES:
        if nm not in places:
            print(f"  CANARY FAILED: '{nm}' missing from uk_places.txt — rebuild")
            ok = False
        if nm in ambiguous:
            print(f"  CANARY FAILED: '{nm}' is a real UK city and must NOT be "
                  "ambiguous — the rebuild is over-flagging")
            ok = False
    print("  canaries:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    sys.exit(check() if args.check else build())
