"""The Pillar-1 shelf manifest: every user-side shelf, generated FROM THE CODE.

WHY GENERATED, NOT HAND-WRITTEN. A hand-typed list of "what we extract" drifts
away from the code within days and then lies with confidence — the same failure
that produced a coverage number of 83% when the truth was 5%, and a test fixture
describing an API shape that had never existed. This reads the dataclass and the
extraction modules directly, so it cannot describe a field the code does not
have, or miss one the code does.

WHAT A "SHELF" IS. One named slot in `CVData` / `UserPreferences` that holds
extracted user information. For each shelf the manifest answers:

    which INPUT owns it     CV | LinkedIn | GitHub | preferences | derived
    which PASS writes it    deterministic | llm | api | user-typed
    who READS it            the consumers that would notice if it were empty

The third column is the one that matters most: a shelf nobody reads is not a
gap, it is dead weight, and the project's own law is that an artifact with no
consumer dies. Filling it would be busywork; deleting it is the fix.

Read-only, no DB, no network. Usage:  python scripts/shelf_manifest.py
"""
from __future__ import annotations

import dataclasses
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SRC = Path(__file__).resolve().parent.parent / "src"

# Which input a field belongs to, decided by its name prefix. Everything with
# no prefix belongs to the CV, which is the primary document.
_PREFIX_OWNER = (
    ("linkedin_", "LinkedIn"),
    ("github_", "GitHub"),
    ("cv_", "CV"),
    ("about_me_", "preferences"),
)


def _owner(field: str) -> str:
    for prefix, who in _PREFIX_OWNER:
        if field.startswith(prefix):
            return who
    return "CV"


def _writers(field: str) -> str:
    """Which pass assigns this field, found by grepping the extraction modules.

    It reports where the field is TOUCHED, not whether that code path runs.
    Proving a lane actually runs is the job of the live end-to-end audit — a
    manifest claiming otherwise would be the confident-but-unverified statement
    this file exists to avoid.

    ANY mention counts. The first version matched only `field =` assignments and
    produced two FALSE NEGATIVES: `github_llm_skills` and
    `about_me_inferred_skills` are populated by `_merge_str_list(cv.field, ...)`,
    which mutates the list in place and contains no `=` at all. An instrument
    that under-reports is as dangerous as one that over-reports — it would have
    sent someone to "fix" a field that was never broken.
    """
    hits: set[str] = set()
    pattern = re.compile(rf"\b{re.escape(field)}\b")
    for path in (_SRC / "services" / "profile").rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not pattern.search(text):
            continue
        name = path.stem
        if name == "models":
            continue  # the declaration, not a writer
        if name in ("cv_parser", "schemas"):
            hits.add("cv-parse")
        elif name == "linkedin_parser":
            hits.add("linkedin-parse")
        elif name == "github_enricher":
            hits.add("github-fetch")
        elif name == "preferences":
            hits.add("prefs")
        elif name == "two_pass":
            hits.add("merge")
        elif name in ("seniority", "skill_tiering", "llm_curate"):
            hits.add("derived")
    return ",".join(sorted(hits)) or "NOWHERE"


def _readers(field: str) -> str:
    """Who consumes it. A shelf with no reader is dead weight, not a gap."""
    hits: set[str] = set()
    pattern = re.compile(rf"\b{re.escape(field)}\b")
    interesting = {
        "skill_matcher": "scorer",
        "scoring_dimensions": "scorer",
        "llm_matcher": "judge",
        "embeddings": "semantic",
        "keyword_generator": "search-keywords",
        "prefilter": "prefilter",
        "skill_tiering": "tiering",
        "jobs": "api",
        "profile": "api",
    }
    for path in _SRC.rglob("*.py"):
        if "profile" in path.parts and path.stem not in interesting:
            continue
        label = interesting.get(path.stem)
        if not label:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern.search(text):
            hits.add(label)
    return ",".join(sorted(hits)) or "-- NO READER --"


def main() -> int:
    from src.services.profile.models import CVData, UserPreferences

    for cls, heading in ((CVData, "CV_DATA shelves"), (UserPreferences, "PREFERENCE shelves")):
        fields = [f.name for f in dataclasses.fields(cls)]
        print("\n" + "=" * 96)
        print(f"{heading}  —  {len(fields)} shelves")
        print("=" * 96)
        print(f"{'shelf':<28}{'input':<12}{'written by':<34}{'read by'}")
        print("-" * 96)
        orphans: list[str] = []
        for name in fields:
            owner = "preferences" if cls is UserPreferences else _owner(name)
            writers = _writers(name)
            # A preference nothing in extraction touches is not broken — it is
            # TYPED BY THE USER. Reporting "NOWHERE" for those would read as an
            # alarm on the most normal case in the system.
            if cls is UserPreferences and writers == "NOWHERE":
                writers = "user-typed"
            readers = _readers(name)
            if readers.startswith("--"):
                orphans.append(name)
            print(f"{name:<28}{owner:<12}{writers:<34}{readers}")
        if orphans:
            print(f"\n  {len(orphans)} shelf/shelves with NO READER "
                  f"(dead weight, not a gap): {', '.join(orphans)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
