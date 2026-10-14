"""Place lookup for the hunt kit: free text -> set of ISO2 country codes.

Data comes from places.json (built by build_places.py from open datasets). Importing this
module does no IO and no network; call load_places(path) to read the file.

Matching rules (documented, deterministic):
- Text is accent-folded (NFKD), lower-cased and split into words; matching is on whole words,
  longest phrase first ("papua new guinea" wins over "guinea", "new mexico" over "mexico").
- Country official/common names, aliases and ISO3 codes (3 letters, upper-case in the text only)
  map to their country. Region words (EMEA, APAC, LATAM, EU, Nordics...) expand to member countries.
  "Worldwide", "Global", "Anywhere" expand to every country.
- City names (population >= 100k plus all capitals) map to their country. A name shared by
  several countries (London GB/CA, Birmingham GB/US) resolves to a country named elsewhere in
  the text if one fits, otherwise to the most populous one. City names that equal a common
  English word are dropped at build time (CITY_STOPWORDS: Reading, Nice, Orange, ...), so
  "Reading the docs" never matches.
- First-level divisions of US/CA/AU/IN/DE/BR/MX/GB (states, provinces) map to their country,
  so "Washington" and "California" mean the US.
- Two-letter codes are IGNORED (too many English words: us, in, it, or) except in an explicit
  upper-case pattern: "Remote - US", "Remote: DE", "(GB)". The one exception is an upper-case
  "US" word in mixed-case text ("Remote in the US"); all-caps text is skipped ("CONTACT US").
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PAREN_CODE = re.compile(r"\(\s*([A-Z]{2})\s*\)")
_REMOTE_CODE = re.compile(r"(?i:remote|hybrid|onsite|on-site)\s*[-\u2013\u2014:/,|]*\s*([A-Z]{2})\b")
_US_WORD = re.compile(r"\bUS\b")
_ISO3 = re.compile(r"\b[A-Z]{3}\b")
_MAX_NGRAM = 7


def fold(s: str) -> str:
    """Lower-case, strip accents, reduce to single-space separated alphanumeric words."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))


@dataclass
class Places:
    """Country / city / region lookup built from places.json."""

    countries: dict[str, dict[str, Any]]
    regions: dict[str, list[str]]
    cities: dict[str, list[str]]
    admin1: dict[str, list[str]]
    all_words: set[str]
    _names: dict[str, str] = field(default_factory=dict)  # folded country name/alias -> ISO2
    _iso3: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for cc, c in self.countries.items():
            for n in c["n"]:
                self._names.setdefault(n, cc)
            self._iso3[c["a3"]] = cc

    def name(self, iso2: str) -> str:
        """Display name of a country (ISO2 if unknown)."""
        c = self.countries.get(iso2.upper())
        return c["nm"] if c else iso2

    def region_members(self, word: str) -> set[str]:
        """Member ISO2 codes of a region word (EMEA, APAC...); worldwide words give all countries."""
        w = fold(word)
        if w in self.all_words:
            return set(self.countries)
        return set(self.regions.get(w, ()))

    def countries_in(self, text: str) -> set[str]:
        """Return every ISO2 country the free text points to (empty set if none)."""
        if not text:
            return set()
        found: set[str] = set()
        for m in _PAREN_CODE.finditer(text):
            if m.group(1) in self.countries:
                found.add(m.group(1))
        for m in _REMOTE_CODE.finditer(text):
            if m.group(1) in self.countries:
                found.add(m.group(1))
        if "US" in self.countries and not text.isupper() and _US_WORD.search(text):
            found.add("US")
        for m in _ISO3.finditer(text):
            if m.group(0) in self._iso3:
                found.add(self._iso3[m.group(0)])
        words = fold(text).split()
        pending: list[list[str]] = []  # ambiguous place candidates, resolved after the scan
        i = 0
        while i < len(words):
            hit = 0
            for n in range(min(_MAX_NGRAM, len(words) - i), 0, -1):
                key = " ".join(words[i:i + n])
                if key in self._names:
                    found.add(self._names[key])
                elif key in self.all_words or key in self.regions:
                    found |= self.region_members(key)
                elif key in self.admin1:
                    pending.append(self.admin1[key])
                elif key in self.cities:
                    pending.append(self.cities[key])
                else:
                    continue
                hit = n
                break
            i += hit or 1
        explicit = set(found)
        for cands in pending:
            fit = [c for c in cands if c in explicit]
            found.update(fit or cands[:1])
        return found


def load_places(path: str | Path) -> Places:
    """Read places.json (the only IO in this module)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Places(d["countries"], d["regions"], d["cities"], d.get("admin1", {}), set(d.get("all_words", [])))
