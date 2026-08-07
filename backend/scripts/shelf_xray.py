"""SHELF X-RAY — both sides of the match, one picture.

The matching model is a product: user-side shelf x job-side shelf, per
dimension. A dimension can only fire when BOTH sides are filled. This script
reads production and reports, for every matching pair, whether the left
(user) shelf and the right (job) shelf actually hold data — so "the shelf is
empty" is seen, not discovered weeks later.

Born 2026-08-07 after three separate blindness incidents in one day: an audit
that stopped one step before storage, a field table read from code instead of
prod, and scoring dimensions built job-side while their user-side partners
were empty for every user. One instrument, run any time, ends that class.

Read-only. Usage:  DATABASE_URL=<dsn> python scripts/shelf_xray.py

Output is committed to the PUBLIC repo by the weekly scorecard, so users are
masked to u1/u2/... by default. Set XRAY_SHOW_EMAILS=1 for a local run that
needs to know which row is which.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.coverage import DIMENSIONS, job_has_value  # noqa: E402 — needs sys.path fixup above

# job/enrichment columns fetched for the job-side coverage pass. Kept as an
# explicit tuple (rather than SELECT *) so the SQL column order and the dict
# keys `job_has_value` expects can never drift apart silently.
_JOB_COLS = ("title", "location", "description", "salary_min", "salary_max")
_ENRICH_COLS = (
    "workplace_type",
    "seniority",
    "visa_sponsorship",
    "category",
    "salary",
    "required_skills",
)


def _filled(v: object) -> bool:
    return v not in (None, "", [], {}, "unknown", "Unknown")


def _job_side_coverage(cur, n_jobs: int) -> dict[str, int]:
    """Percent of the active catalog carrying a real value per dimension.

    THE FIX: this used to be N inline SQL predicates, one of which
    (`e.salary NOT IN ('{}', '')`) tested the salary column's *shape*, not
    its *value* — `{"min": null, "max": null}` matched neither literal and
    passed, reporting 83% coverage against a real figure near 5%. Counting
    is now done in Python, one row at a time, through `job_has_value` — the
    same value-presence rule the canary test (`tests/test_coverage_canary.py`)
    pins down — so no predicate here can silently drift from the definition
    the test enforces.
    """
    select_cols = ", ".join(f"j.{c}" for c in _JOB_COLS) + ", " + ", ".join(
        f"e.{c}" for c in _ENRICH_COLS
    )
    cur.execute(
        f"SELECT {select_cols} FROM jobs j "
        "LEFT JOIN job_enrichment e ON e.job_id = j.id "
        "WHERE j.staleness_state IS NULL OR j.staleness_state = 'active'"
    )
    n_job_cols = len(_JOB_COLS)
    counts = dict.fromkeys(DIMENSIONS, 0)
    for row in cur.fetchall():
        job_row = dict(zip(_JOB_COLS, row[:n_job_cols]))
        enrich_vals = row[n_job_cols:]
        # A LEFT JOIN miss comes back as an all-None tuple — treat that as
        # "no enrichment row" (None) rather than a dict of Nones, matching
        # what a caller without a JOIN (e.g. two separate queries) would pass.
        enrichment_row: Optional[dict] = (
            dict(zip(_ENRICH_COLS, enrich_vals))
            if any(v is not None for v in enrich_vals)
            else None
        )
        for dim in DIMENSIONS:
            if job_has_value(dim, job_row, enrichment_row):
                counts[dim] += 1
    return {dim: round(100 * counts[dim] / max(1, n_jobs)) for dim in DIMENSIONS}


def main() -> int:
    import psycopg

    conn = psycopg.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    # ---------- LEFT: user shelves ----------
    cur.execute(
        "SELECT u.email, p.cv_data, p.preferences FROM user_profiles p "
        "JOIN users u ON u.id = p.user_id ORDER BY u.email"
    )
    users = [(e, json.loads(cv), json.loads(pr)) for e, cv, pr in cur.fetchall()]

    user_shelves = {
        "skills":     lambda c, p: c.get("skills") or c.get("linkedin_skills"),
        "titles":     lambda c, p: c.get("job_titles") or p.get("target_job_titles"),
        "dated_experience": lambda c, p: c.get("cv_positions") or c.get("linkedin_positions"),
        "education":  lambda c, p: c.get("education"),
        "salary":     lambda c, p: p.get("salary_min") or p.get("salary_max"),
        "location":   lambda c, p: p.get("preferred_locations") or c.get("location"),
        "workplace":  lambda c, p: p.get("preferred_workplace") or p.get("work_arrangement"),
        "seniority":  lambda c, p: p.get("experience_level"),
        "visa":       lambda c, p: p.get("needs_visa") is not None,
        "domain":     lambda c, p: c.get("career_domain") or (p.get("industries") or c.get("industries")),
        "about_me":   lambda c, p: p.get("about_me"),
    }

    # ---------- RIGHT: job shelves ----------
    cur.execute("SELECT count(*) FROM jobs WHERE staleness_state IS NULL OR staleness_state='active'")
    n_jobs = cur.fetchone()[0]

    # Every job-side percentage is now decided by job_has_value (coverage.py)
    # — no inline SQL predicate may reappear here. See _job_side_coverage's
    # docstring for the incident that made that a hard rule.
    _cov = _job_side_coverage(cur, n_jobs)
    job_shelves = {
        "skills":    _cov["skills"],
        "titles":    _cov["titles"],
        "dated_experience": None,  # job side needs no dates
        "education": None,
        "salary":    _cov["salary"],
        "location":  _cov["location"],
        "workplace": _cov["workplace"],
        "seniority": _cov["seniority"],
        "visa":      _cov["visa"],
        "domain":    _cov["domain"],
        "about_me":  None,
    }

    # ---------- THE PAIRS TABLE ----------
    print(f"jobs in catalog: {n_jobs}   profiles: {len(users)}")
    show = os.getenv("XRAY_SHOW_EMAILS", "") == "1"
    labels = [(e.split("@")[0][:14] if show else f"u{i + 1}") for i, (e, _, _) in enumerate(users)]
    header = f"{'DIM':<18}{'job side':<10}" + "".join(f"{lb:<16}" for lb in labels) + "pair"
    print(header)
    print("-" * len(header))
    dead_pairs: list[str] = []
    for dim, ufn in user_shelves.items():
        j = job_shelves.get(dim)
        jtxt = "  n/a" if j is None else f"{j:>3}%"
        cells, any_user = [], False
        for _, cvd, prefs in users:
            got = _filled(ufn(cvd, prefs))
            any_user = any_user or got
            cells.append("FILLED" if got else "EMPTY")
        alive = (j is None or j > 0) and any_user
        if not alive:
            dead_pairs.append(dim)
        print(f"{dim:<18}{jtxt:<10}" + "".join(f"{c:<16}" for c in cells)
              + ("ALIVE" if alive else "** DEAD **"))

    # ---------- field-level detail for still-empty user fields ----------
    print("\nuser fields empty for EVERY profile (stale-or-broken candidates):")
    all_keys: set[str] = set()
    for _, cvd, prefs in users:
        all_keys |= set(cvd.keys())
    empty_everywhere = sorted(
        k for k in all_keys
        if not any(_filled(cvd.get(k)) for _, cvd, _ in users)
    )
    print("  " + (", ".join(empty_everywhere) if empty_everywhere else "none"))

    print(f"\nDEAD PAIRS: {dead_pairs if dead_pairs else 'none — every dimension can fire'}")
    conn.close()
    return 1 if dead_pairs else 0


if __name__ == "__main__":
    raise SystemExit(main())
