"""Distribution sanity: is a field FILLED, or is it filled with a LIE?

WHY THIS EXISTS. Every coverage instrument this project had asked "does the
field have a value?" and none asked "is that value plausible?". On 2026-08-07
that gap cost real users: `bool("No")` is True in Python, so devitjobs marked
100% of its 2,377 jobs as offering visa sponsorship when the API said 2 did,
and the description text the source composes for itself then carried a
fabricated "Visa sponsorship available" sentence into 1,977 rows. A visa
detector read our own sentence back as evidence. Coverage climbed. Everything
agreed with itself. Every check was green.

The cheapest instrument that would have caught it is here: a boolean sitting at
~100% one value for ONE source, against a 2-5% base rate across every other
source, is a distribution outlier — one query, no new infrastructure, no
provenance plumbing.

WHAT IT CHECKS (each is a claim about the WORLD, not about our schema):
  * boolean monoculture — one source, one value, near-100%, far off the
    cross-source base rate.
  * salary_min > salary_max — impossible.
  * salary magnitude — a UK annual salary of 3 or 3,000,000 is a units bug
    (hourly stored as annual, or minor units).
  * posted_at in the future, or before 2015 — epoch/parse damage.
  * posted_at monoculture — every job from a source sharing ONE date means we
    stamped it, we did not read it.
  * posted_at ~ first_seen_at — if a source's posted date always equals the day
    we happened to fetch it, the date is OURS, not the employer's. This is the
    FABRICATED class: it varies daily, so a constant-check misses it, and there
    is nothing upstream to compare it against.

Read-only. Exit 1 when any check fires, so it can gate CI later.

Usage:  DATABASE_URL=<dsn> python scripts/distribution_sanity.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# A source must have at least this many rows before its distribution means
# anything — 3 jobs at 100% is noise, not a monoculture.
MIN_ROWS = 20
# Flag a boolean monoculture only when the source is this far from the
# cross-source base rate (percentage points).
MONOCULTURE_GAP = 50
# UK annual salary sanity window. Outside this, the units are wrong.
SALARY_FLOOR = 8_000
SALARY_CEILING = 1_000_000


def main() -> int:
    import psycopg

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        raise SystemExit("set DATABASE_URL or DATABASE_PUBLIC_URL")
    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    active = "(staleness_state IS NULL OR staleness_state = 'active')"
    findings: list[str] = []

    print("=" * 72)
    print("DISTRIBUTION SANITY — is the value plausible, not merely present?")
    print("=" * 72)

    # ── 1. Boolean monoculture ──────────────────────────────────────────────
    cur.execute(f"SELECT count(*), sum(visa_flag) FROM jobs WHERE {active}")
    total, flagged = cur.fetchone()
    base = 100.0 * (flagged or 0) / max(1, total)
    print(f"\n[1] visa_flag base rate across catalog: {base:.1f}%  ({flagged}/{total})")
    cur.execute(
        f"SELECT source, count(*), sum(visa_flag) FROM jobs WHERE {active} "
        f"GROUP BY source HAVING count(*) >= {MIN_ROWS} ORDER BY count(*) DESC"
    )
    for src, n, yes in cur.fetchall():
        rate = 100.0 * (yes or 0) / n
        if abs(rate - base) >= MONOCULTURE_GAP:
            msg = (f"visa_flag monoculture: {src} at {rate:.0f}% vs {base:.1f}% "
                   f"base ({yes}/{n})")
            findings.append(msg)
            print(f"    FIRING  {msg}")
    if not findings:
        print("    ok — no source deviates by 50+ points")

    # ── 2. Salary impossible / wrong units ──────────────────────────────────
    cur.execute(
        f"SELECT count(*) FROM jobs WHERE {active} "
        "AND salary_min IS NOT NULL AND salary_max IS NOT NULL "
        "AND salary_min > salary_max"
    )
    inverted = cur.fetchone()[0]
    cur.execute(
        f"SELECT source, count(*) FROM jobs WHERE {active} AND salary_min IS NOT NULL "
        f"AND (salary_min < {SALARY_FLOOR} OR salary_min > {SALARY_CEILING}) "
        "GROUP BY source ORDER BY count(*) DESC"
    )
    odd = cur.fetchall()
    print(f"\n[2] salary_min > salary_max: {inverted}")
    if inverted:
        findings.append(f"{inverted} jobs have salary_min > salary_max")
    print(f"    salary outside £{SALARY_FLOOR:,}-£{SALARY_CEILING:,}: "
          f"{sum(n for _, n in odd)}")
    for src, n in odd:
        findings.append(f"salary units suspect: {src} has {n} out-of-range rows")
        print(f"    FIRING  {src}: {n}")
    if not inverted and not odd:
        print("    ok")

    # ── 3. posted_at range ──────────────────────────────────────────────────
    cur.execute(
        f"SELECT count(*) FROM jobs WHERE {active} AND posted_at ~ '^[0-9]{{4}}-' "
        "AND (substring(posted_at from 1 for 10) > "
        "to_char(now() + interval '2 days', 'YYYY-MM-DD') "
        "OR substring(posted_at from 1 for 10) < '2015-01-01')"
    )
    bad_dates = cur.fetchone()[0]
    print(f"\n[3] posted_at in the future or pre-2015: {bad_dates}")
    if bad_dates:
        findings.append(f"{bad_dates} jobs have an absurd posted_at")

    # ── 4. posted_at monoculture — we stamped it, we did not read it ────────
    print("\n[4] posted_at monoculture (one date for a whole source):")
    cur.execute(
        f"SELECT source, count(*), count(DISTINCT substring(posted_at from 1 for 10)) "
        f"FROM jobs WHERE {active} AND coalesce(posted_at,'') <> '' "
        f"GROUP BY source HAVING count(*) >= {MIN_ROWS}"
    )
    any4 = False
    for src, n, distinct in cur.fetchall():
        if distinct <= 2:
            any4 = True
            msg = f"posted_at monoculture: {src} has {distinct} distinct date(s) over {n} jobs"
            findings.append(msg)
            print(f"    FIRING  {msg}")
    if not any4:
        print("    ok")

    # ── 5. THE FABRICATED CLASS: posted_at == the day we fetched it ─────────
    # This is the check the earlier plan lacked entirely. A source that stamps
    # ingestion time as the posting date varies daily (so check 4 misses it)
    # and has no upstream counterpart to compare against (so value-equality is
    # vacuous) — yet it earns full recency credit in scoring.
    print("\n[5] posted_at == first_seen_at (the date is OURS, not the employer's):")
    cur.execute(
        f"SELECT source, count(*), "
        "sum(CASE WHEN substring(posted_at from 1 for 10) "
        "         = substring(first_seen_at from 1 for 10) THEN 1 ELSE 0 END) "
        f"FROM jobs WHERE {active} AND coalesce(posted_at,'') <> '' "
        f"AND coalesce(first_seen_at,'') <> '' "
        f"GROUP BY source HAVING count(*) >= {MIN_ROWS} ORDER BY count(*) DESC"
    )
    any5 = False
    for src, n, same in cur.fetchall():
        rate = 100.0 * (same or 0) / n
        if rate >= 95:
            any5 = True
            msg = (f"posted_at looks fabricated: {src} — {rate:.0f}% of rows have "
                   f"posted_at == the day we first saw them ({same}/{n})")
            findings.append(msg)
            print(f"    FIRING  {msg}")
    if not any5:
        print("    ok")

    print("\n" + "=" * 72)
    if findings:
        print(f"{len(findings)} FINDING(S):")
        for f in findings:
            print(f"  - {f}")
    else:
        print("no findings — every checked distribution is plausible")
    print("=" * 72)
    conn.close()
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
