#!/usr/bin/env python3
"""Rebuild places.json from open datasets (stdlib only, reproducible).

Sources
- datasets/country-codes (Public Domain Dedication and License, PDDL):
  ISO country codes, names, UN region and sub-region.
- GeoNames cities15000 + admin1CodesASCII (CC BY 4.0, https://www.geonames.org): cities and
  first-level divisions by country. Cities >= MIN_POP plus every national capital.

Usage:
    python build_places.py                      # downloads both files
    python build_places.py --codes country-codes.csv --cities cities15000.zip

Region words (APAC, EMEA, LATAM, ...) are marketing words, not UN regions. They are
derived from the UN regions below where possible. The small explicit tables
REGION_RULES, EU, EXTRA_COUNTRY_ALIASES and CITY_STOPWORDS are the ONLY hand-written
location data in the kit; everything else comes from the datasets.
"""
# ruff: noqa: UP031, S310
from __future__ import annotations

import argparse
import csv
import http.client
import io
import json
import re
import unicodedata
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

CODES_URL = "https://raw.githubusercontent.com/datasets/country-codes/main/data/country-codes.csv"
CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
ADMIN1_URL = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
# Countries whose first-level divisions (states/provinces) are common in job-ad locations.
ADMIN1_COUNTRIES = ("US", "CA", "AU", "IN", "DE", "BR", "MX", "GB")
# Words that mean "any country" in a job ad.
ALL_WORDS = ["worldwide", "world wide", "global", "globally", "anywhere", "anywhere in the world"]
MIN_POP = 100_000  # cities at or above this population are kept
ALT_NAME_MIN_POP = 500_000  # ascii alternate names (Bangalore, Munich) only for big cities
UA = "job360-hunt-kit-build/1.0 (+https://job360.uk)"

# Explicit, documented: EU member states (a political fact, not in the UN regions).
EU = ("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE").split()
GCC = "SA AE QA KW BH OM".split()
NORDICS = "DK FI IS NO SE".split()
DACH = "DE AT CH".split()
BENELUX = "BE NL LU".split()
ANZ = "AU NZ".split()

# Region word -> rule over UN data.  ("region"|"sub"|"codes", values..., minus sub-regions)
REGION_RULES: dict[str, list[tuple[str, list[str]]]] = {
    "asia": [("region", ["Asia"])],
    "apac": [("region", ["Asia", "Oceania"]), ("minus_sub", ["Western Asia"])],
    "asia pacific": [("region", ["Asia", "Oceania"]), ("minus_sub", ["Western Asia"])],
    "apj": [("region", ["Asia", "Oceania"]), ("minus_sub", ["Western Asia"])],
    "southeast asia": [("sub", ["South-eastern Asia"])],
    "south east asia": [("sub", ["South-eastern Asia"])],
    "south asia": [("sub", ["Southern Asia"])],
    "europe": [("region", ["Europe"])],
    "eu": [("codes", EU)],
    "european union": [("codes", EU)],
    "emea": [("region", ["Europe", "Africa"]), ("sub", ["Western Asia"])],
    "middle east": [("sub", ["Western Asia"]), ("codes", ["EG"])],
    "mena": [("sub", ["Western Asia", "Northern Africa"])],
    "gcc": [("codes", GCC)],
    "africa": [("region", ["Africa"])],
    "latam": [("sub", ["Latin America and the Caribbean"])],
    "latin america": [("sub", ["Latin America and the Caribbean"])],
    "north america": [("sub", ["Northern America"])],
    "americas": [("region", ["Americas"])],
    "oceania": [("region", ["Oceania"])],
    "anz": [("codes", ANZ)],
    "nordics": [("codes", NORDICS)],
    "nordic": [("codes", NORDICS)],
    "dach": [("codes", DACH)],
    "benelux": [("codes", BENELUX)],
    "eastern europe": [("sub", ["Eastern Europe"])],
    "western europe": [("sub", ["Western Europe"])],
    "northern europe": [("sub", ["Northern Europe"])],
    "southern europe": [("sub", ["Southern Europe"])],
    "central asia": [("sub", ["Central Asia"])],
    "caribbean": [("sub", []), ("codes", [])],  # filled from Intermediate Region below
}
EXTRA_COUNTRY_ALIASES = {
    "GB": ["england", "scotland", "wales", "northern ireland", "great britain", "britain", "uk", "u k",
           "united kingdom"],
    "US": ["usa", "united states", "united states of america", "u s a", "u s"],
    "AE": ["uae"],
    "NL": ["holland"],
    "KR": ["south korea"],
    "KP": ["north korea"],
    "CI": ["ivory coast"],
    "TR": ["turkiye", "turkey"],
    "MO": ["macau"],
    "CZ": ["czech republic"],
    "RU": ["russia"],
    "VN": ["vietnam"],
}
# City names that are also ordinary English words / labels: never treated as places.
CITY_STOPWORDS = {"nice", "reading", "mobile", "orange", "remote", "central", "hollywood", "independence",
                  "hybrid", "union", "victoria", "sandy", "normal", "industry", "commerce", "enterprise",
                  "grand", "bend", "salem", "chester", "providence", "concord", "springfield",
                  "wellington", "global", "world", "anywhere", "home", "field", "lead", "bath"}


def fold(s: str) -> str:
    """Lowercase, strip accents, keep only letters/digits separated by single spaces."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))


def _fetch(url: str, _hops: int = 5) -> bytes:
    """GET one of this script's fixed https dataset URLs (follows https redirects only)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"refusing non-https URL: {url}")
    conn = http.client.HTTPSConnection(parts.hostname, timeout=120)
    try:
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        conn.request("GET", path or "/", headers={"User-Agent": UA})
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308) and _hops > 0:
            location = urllib.parse.urljoin(url, resp.getheader("Location") or "")
            return _fetch(location, _hops - 1)
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status} for {url}")
        return resp.read()
    finally:
        conn.close()


def build(codes_csv: bytes, cities_zip: bytes, admin1_txt: bytes = b"") -> dict[str, Any]:
    """Combine the two datasets into the places.json structure."""
    rows = list(csv.DictReader(io.StringIO(codes_csv.decode("utf-8"))))
    countries: dict[str, dict[str, Any]] = {}
    for r in rows:
        cc = (r["ISO3166-1-Alpha-2"] or "").strip()
        if len(cc) != 2:
            continue
        names: set[str] = set()
        for col in ("CLDR display name", "official_name_en", "UNTERM English Short", "UNTERM English Formal"):
            v = (r.get(col) or "").strip()
            if not v:
                continue
            for cand in (v, re.sub(r"\s*\(.*?\)", "", v)):
                folded = fold(cand)
                if len(folded) >= 4:
                    names.add(folded)
        for a in EXTRA_COUNTRY_ALIASES.get(cc, []):
            names.add(fold(a))
        countries[cc] = {
            "nm": next((re.sub(r"\s*\(the\)", "", v).strip()
                       for v in (r.get("CLDR display name"), r.get("UNTERM English Short"), cc)
                       if v and len(v.strip()) > 2), cc),
            "cap": fold(r.get("Capital") or ""),
            "n": sorted(names), "a3": r["ISO3166-1-Alpha-3"], "r": r["Region Name"],
            "s": r["Sub-region Name"], "i": r["Intermediate Region Name"],
        }

    def members(rule: list[tuple[str, list[str]]]) -> list[str]:
        out: set[str] = set()
        minus: set[str] = set()
        for kind, vals in rule:
            for cc, c in countries.items():
                if kind == "region" and c["r"] in vals:
                    out.add(cc)
                elif kind == "sub" and c["s"] in vals:
                    out.add(cc)
            if kind == "codes":
                out.update(v for v in vals if v in countries)
            if kind == "minus_sub":
                minus.update(cc for cc, c in countries.items() if c["s"] in vals)
        return sorted(out - minus)

    regions = {w: members(rule) for w, rule in REGION_RULES.items()}
    regions["caribbean"] = sorted(cc for cc, c in countries.items() if c["i"] == "Caribbean")
    for c in countries.values():
        c.pop("i")
    # UN names themselves also work as words (e.g. "south eastern asia").
    for kind in ("r", "s"):
        for name in {c[kind] for c in countries.values() if c[kind]}:
            regions.setdefault(fold(name), sorted(cc for cc, c in countries.items() if c[kind] == name))

    country_words = {n for c in countries.values() for n in c["n"]}
    capitals = {c["cap"]: cc for cc, c in countries.items() if c["cap"]}
    for c in countries.values():
        c.pop("cap")
    z = zipfile.ZipFile(io.BytesIO(cities_zip))
    found: dict[str, dict[str, int]] = {}  # key -> {country: best population}
    for line in z.read("cities15000.txt").decode("utf-8").splitlines():
        f = line.split("	")
        if len(f) < 15:
            continue
        pop = int(f[14] or 0)
        cc = f[8]
        if cc not in countries:
            continue
        keys = {fold(f[1]), fold(f[2])}
        if pop < MIN_POP and not (fold(f[2]) in capitals and capitals[fold(f[2])] == cc):
            continue
        if pop >= ALT_NAME_MIN_POP:
            for alt in f[3].split(","):
                if re.fullmatch(r"[A-Za-z][A-Za-z ]{3,}", alt):
                    keys.add(fold(alt))
        for k in keys:
            if len(k) < 4 or k in CITY_STOPWORDS or k in country_words or k in regions:
                continue
            d = found.setdefault(k, {})
            d[cc] = max(d.get(cc, 0), pop)
    # Largest-population country first: the reader treats the first entry as the likely one.
    cities = {k: [cc for cc, _ in sorted(v.items(), key=lambda kv: -kv[1])][:4] for k, v in found.items()}
    admin1: dict[str, list[str]] = {}
    for line in admin1_txt.decode("utf-8").splitlines():
        code, _, rest = line.partition("	")
        cc = code.split(".")[0]
        if cc not in ADMIN1_COUNTRIES:
            continue
        k = fold(rest.split("	")[0])
        if len(k) >= 4 and k not in CITY_STOPWORDS and k not in country_words and k not in regions:
            admin1.setdefault(k, [])
            if cc not in admin1[k]:
                admin1[k].append(cc)
            cities.pop(k, None)
    return {
        "meta": {
            "sources": [
                "datasets/country-codes (PDDL): https://github.com/datasets/country-codes",
                "GeoNames cities15000 + admin1CodesASCII (CC BY 4.0): https://www.geonames.org",
            ],
            "min_city_population": MIN_POP,
            "note": "Generated by build_places.py. Do not edit by hand.",
        },
        "countries": countries,
        "regions": regions,
        "all_words": [fold(w) for w in ALL_WORDS],
        "admin1": dict(sorted(admin1.items())),
        "cities": dict(sorted(cities.items())),
    }


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--codes", help="local country-codes.csv (default: download)")
    ap.add_argument("--cities", help="local cities15000.zip (default: download)")
    ap.add_argument("--admin1", help="local admin1CodesASCII.txt (default: download)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "places.json"))
    a = ap.parse_args()
    codes = Path(a.codes).read_bytes() if a.codes else _fetch(CODES_URL)
    cities = Path(a.cities).read_bytes() if a.cities else _fetch(CITIES_URL)
    admin1 = Path(a.admin1).read_bytes() if a.admin1 else _fetch(ADMIN1_URL)
    data = build(codes, cities, admin1)
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("wrote %s: %d countries, %d regions, %d city names" % (a.out, len(data["countries"]),
                                                                  len(data["regions"]), len(data["cities"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
