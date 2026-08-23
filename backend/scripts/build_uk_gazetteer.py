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
    return 0


def check() -> int:
    """Verify the committed artifacts are present and sane."""
    ok = True
    for f, floor in (("uk_places.txt", 20_000), ("foreign_admin.txt", 500),
                     ("ambiguous.txt", 1)):
        path = OUT / f
        if not path.exists():
            print(f"MISSING {f}")
            ok = False
            continue
        n = len([x for x in path.read_text(encoding="utf-8").split("\n") if x])
        print(f"  {f}: {n} entries")
        if n < floor:
            print(f"  ^ too few (expected >= {floor}) — rebuild")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    sys.exit(check() if args.check else build())
