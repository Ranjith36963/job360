"""Dry-run the location gate over the LIVE catalog — the rule #30 instrument.

Rule #30: "Dry-run any location rule over the live catalog first." The naive
version of the original UK rule blocked 48% of the catalog, and that was only
visible because someone replayed it over real rows. This script is that replay,
committed rather than retyped, so the next person changing a location rule has
the instrument instead of a remembered number.

It answers two questions over the same rows:

  1. WHAT DOES THE DOOR DO TODAY?  `check_uk` verdict counts, by reason. Reasons
     matter more than the total: `foreign_location` is a COUNTRY refusal, while
     `unverified_location_on_global_source` is a QUALITY refusal that has
     nothing to do with which country the job is in. Any redesign that treats
     them as one thing is trading a 4.7% problem for a 7.2% one.

  2. WHAT COULD A COUNTRY CLASSIFIER ACTUALLY PLACE?  The same rows bucketed by
     what the gazetteer can resolve from the location field ALONE. The gap
     between the two columns is the honest cost of "tag everything, refuse
     nothing".

MEASURED 2026-08-27 over 18,459 prod rows (docs/plans/2026-08-27-country-is-a-parameter.md):
    72.4% GB_named · 18.9% UNPLACEABLE_text · 4.6% AMBIGUOUS · 3.2% FOREIGN · 0.9% none
    ...against a door that admits 88.1%. 9.6% of the catalog is `remote` and
    has no country at all; 3.0% is dual-site and has TWO.

COVERAGE BOUND, STATED BECAUSE IT CHANGES THE ANSWER: with `--no-body` (the
default, because bodies are large) this replays `check_uk(location, source)`
with an EMPTY description and title. The live door passes both. So the
body-dependent branches — `uk_evidence_in_ad`, `ambiguous_name_with_uk_evidence`
and prose-triggered `remote_restricted_to_other_region` — are UNDER-counted
here. Pass `--body` for the faithful replay; it is slower and the dump is large.

Usage:
    # 1. dump the catalog from prod (writes CSV; never prints secrets)
    railway run -s Postgres python scripts/dryrun_country_gate.py --dump out.csv
    # 2. replay locally against the committed gazetteer
    python scripts/dryrun_country_gate.py --replay out.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import sys
from pathlib import Path

# The gate imports package-relative data; run from `backend/`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_FIELDS = ("id", "source", "location", "description", "title")


def dump(out_path: str, with_body: bool) -> int:
    """Pull the live catalog to CSV. Runs inside `railway run -s Postgres`.

    Uses DATABASE_PUBLIC_URL: plain DATABASE_URL only resolves inside Railway's
    private network, and the failure mode is a hang, not an error.
    """
    import psycopg  # local import: this half only runs against prod

    url = os.environ.get("DATABASE_PUBLIC_URL")
    if not url:
        print("DATABASE_PUBLIC_URL is not set — run under `railway run -s Postgres`.")
        return 2

    cols = "id, source, location" + (", description, title" if with_body else "")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM jobs")  # noqa: S608 — cols is a literal above
        rows = cur.fetchall()

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_FIELDS[: 5 if with_body else 3])
        writer.writerows(rows)
    print(f"dumped {len(rows)} rows -> {out_path}")
    return 0


def replay(in_path: str) -> int:
    """Replay the gate over the dumped rows and print both distributions."""
    from src.services.uk_gate import (
        _POSTCODE,
        _UK_SELF,
        _foreign_hit,
        _gazetteer,
        _segments,
        _uk_hit,
        check_uk,
    )

    places, foreign, ambiguous = _gazetteer()
    if not places or not foreign:
        # The gate degrades to source-trust when the data is missing (issue
        # #260) — silently, from the caller's point of view. A dry-run over a
        # blind gate would report confident nonsense, so refuse instead.
        print("GAZETTEER EMPTY — refusing to report. See uk_gate._gazetteer / issue #260.")
        return 2
    print(f"gazetteer: places={len(places)} foreign={len(foreign)} ambiguous={len(ambiguous)}")

    reasons: collections.Counter[str] = collections.Counter()
    placeable: collections.Counter[str] = collections.Counter()

    with open(in_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            loc = row.get("location") or ""
            verdict = check_uk(
                loc,
                row.get("source") or "",
                description=row.get("description") or "",
                title=row.get("title") or "",
            )
            reasons[verdict.reason] += 1

            # What the LOCATION FIELD ALONE can support. Deliberately separate
            # from the verdict: the door also reads the source and the body, so
            # these two columns must not be conflated.
            segs = _segments(loc)
            names_foreign = any(_foreign_hit(s, foreign, places, ambiguous) for s in segs)
            names_uk = any(s in _UK_SELF for s in segs) or bool(_POSTCODE.search(loc))
            has_uk_place = any(_uk_hit(s, places) for s in segs)

            if names_foreign and (names_uk or has_uk_place):
                placeable["AMBIGUOUS_two_signals"] += 1
            elif names_foreign:
                placeable["FOREIGN_named"] += 1
            elif names_uk or has_uk_place:
                placeable["GB_named"] += 1
            elif not loc.strip():
                placeable["NO_LOCATION"] += 1
            else:
                placeable["UNPLACEABLE_text"] += 1

    total = sum(reasons.values())
    if not total:
        print("no rows")
        return 1

    def _table(title: str, counter: collections.Counter[str]) -> None:
        print(f"\n--- {title} ({total} rows) ---")
        for key, n in counter.most_common():
            print(f"  {n:6d}  {100 * n / total:5.1f}%  {key}")

    _table("check_uk verdicts", reasons)
    _table("what a country classifier could place from the location field", placeable)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dump", metavar="OUT.csv", help="pull the live catalog to CSV")
    group.add_argument("--replay", metavar="IN.csv", help="replay the gate over a dump")
    parser.add_argument(
        "--body",
        action="store_true",
        help="include description+title (faithful replay; large dump)",
    )
    args = parser.parse_args()
    return dump(args.dump, args.body) if args.dump else replay(args.replay)


if __name__ == "__main__":
    raise SystemExit(main())
