"""Discover new ATS company slugs and report which are worth adding.

copy_roadmap.md Tier-1 #1. Thin CLI wrapper over
``src/services/company_discovery.py``. Probes candidate slugs against a public
ATS endpoint, keeps the ones that host live jobs, and reports those NOT already
in ``core/companies.py`` as a paste-ready block.

By design it does NOT rewrite ``core/companies.py`` — that file is curated by
hand. Use ``--write`` to opt into appending the new slugs to the relevant list.

Usage (run from backend/):
    python scripts/discover_companies.py --ats greenhouse cand1 cand2 cand3
    python scripts/discover_companies.py --ats lever --candidates slugs.txt
    python scripts/discover_companies.py --ats ashby --candidates slugs.txt --min-jobs 3
    python scripts/discover_companies.py --ats greenhouse --candidates slugs.txt --write

``--candidates FILE`` reads one slug per line (blank lines / ``#`` comments
ignored). Positional slugs and ``--candidates`` may be combined.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiohttp

# Add backend/ to sys.path so ``from src...`` resolves when the script is
# invoked directly (``python scripts/discover_companies.py``).
_BACKEND = Path(__file__).resolve().parents[1]  # scripts/ → backend/
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from src.core import companies as companies_mod  # noqa: E402
from src.services.company_discovery import (  # noqa: E402
    ATS_PROBES,
    discover,
    merge_slugs,
    normalize_slug,
)

# ATS name -> the list variable in core/companies.py holding its slugs.
# Only simple string-list ATS are auto-discoverable here (Workday / Personio /
# SuccessFactors use richer per-company config and are out of scope).
_ATS_TO_LISTVAR = {
    "greenhouse": "GREENHOUSE_COMPANIES",
    "lever": "LEVER_COMPANIES",
    "workable": "WORKABLE_COMPANIES",
    "ashby": "ASHBY_COMPANIES",
    "smartrecruiters": "SMARTRECRUITERS_COMPANIES",
    "recruitee": "RECRUITEE_COMPANIES",
    "comeet": "COMEET_COMPANIES",
    "rippling": "RIPPLING_COMPANIES",
}


def _load_candidates(args: argparse.Namespace) -> list[str]:
    cands: list[str] = list(args.slugs)
    if args.candidates:
        path = Path(args.candidates)
        if not path.exists():
            sys.exit(f"error: --candidates file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                cands.append(line)
    return cands


def _existing_slugs(ats: str) -> list[str]:
    listvar = _ATS_TO_LISTVAR.get(ats)
    if not listvar:
        return []
    return list(getattr(companies_mod, listvar, []) or [])


def _append_to_companies(ats: str, new_slugs: list[str]) -> bool:
    """Append new slugs to the relevant list literal in companies.py.

    Inserts a dated block before the closing ``]`` of the target list. Returns
    True on success. Conservative: bails if the list literal can't be located.
    """
    listvar = _ATS_TO_LISTVAR[ats]
    src_path = Path(companies_mod.__file__)
    text = src_path.read_text(encoding="utf-8")
    marker = f"{listvar} = ["
    start = text.find(marker)
    if start == -1:
        print(f"  ! could not locate '{marker}' — skipping --write", file=sys.stderr)
        return False
    close = text.find("\n]", start)
    if close == -1:
        print(f"  ! could not find end of {listvar} — skipping --write", file=sys.stderr)
        return False
    block = "\n    # Auto-discovered via scripts/discover_companies.py\n    " + ", ".join(
        f'"{s}"' for s in new_slugs
    ) + ","
    updated = text[:close] + block + text[close:]
    src_path.write_text(updated, encoding="utf-8")
    return True


async def _amain(args: argparse.Namespace) -> int:
    candidates = _load_candidates(args)
    if not candidates:
        sys.exit("error: no candidate slugs given (positional or --candidates)")

    existing = _existing_slugs(args.ats)
    print(f"ATS: {args.ats}  |  candidates: {len(candidates)}  |  "
          f"already in companies.py: {len(existing)}")

    async with aiohttp.ClientSession() as session:
        found = await discover(
            session, args.ats, candidates, min_jobs=args.min_jobs
        )

    if not found:
        print("No candidate slugs validated (none host >= "
              f"{args.min_jobs} live job(s)).")
        return 0

    existing_norm = {normalize_slug(s) for s in existing}
    new_slugs = merge_slugs(existing, found.keys())

    print(f"\nValidated {len(found)} slug(s) with live jobs:")
    for slug, count in sorted(found.items(), key=lambda kv: -kv[1]):
        tag = "NEW" if normalize_slug(slug) not in existing_norm else "already present"
        print(f"  {slug:30s} {count:4d} jobs   [{tag}]")

    if not new_slugs:
        print("\nAll validated slugs are already in companies.py — nothing to add.")
        return 0

    print(f"\n{len(new_slugs)} NEW slug(s) to add to "
          f"{_ATS_TO_LISTVAR.get(args.ats, '(unmapped ATS)')}:")
    print("    " + ", ".join(f'"{s}"' for s in new_slugs) + ",")

    if args.write:
        if args.ats not in _ATS_TO_LISTVAR:
            print(f"\n! --write unsupported for ATS '{args.ats}' (no simple list var).",
                  file=sys.stderr)
            return 1
        if _append_to_companies(args.ats, new_slugs):
            print(f"\nAppended {len(new_slugs)} slug(s) to core/companies.py. "
                  "Review the diff before committing.")
    else:
        print("\n(Use --write to append these to core/companies.py.)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ats", required=True, choices=sorted(ATS_PROBES),
        help="ATS to probe candidate slugs against.",
    )
    parser.add_argument(
        "slugs", nargs="*",
        help="Candidate slugs (positional). May be combined with --candidates.",
    )
    parser.add_argument(
        "--candidates", metavar="FILE",
        help="File with one candidate slug per line (# comments ignored).",
    )
    parser.add_argument(
        "--min-jobs", type=int, default=1,
        help="Minimum live jobs for a slug to count as valid (default: 1).",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Append validated NEW slugs to core/companies.py (default: report only).",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
