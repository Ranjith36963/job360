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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _filled(v: object) -> bool:
    return v not in (None, "", [], {}, "unknown", "Unknown")


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

    def cov(sql: str) -> int:
        cur.execute(sql)
        n = cur.fetchone()[0]
        return round(100 * n / max(1, n_jobs))

    job_shelves = {
        "skills":    cov("SELECT count(*) FROM jobs WHERE length(coalesce(description,''))>200 AND (staleness_state IS NULL OR staleness_state='active')"),
        "titles":    cov("SELECT count(*) FROM jobs WHERE coalesce(title,'')<>'' AND (staleness_state IS NULL OR staleness_state='active')"),
        "dated_experience": None,  # job side needs no dates
        "education": None,
        "salary":    cov("SELECT count(*) FROM jobs j WHERE (j.salary_min IS NOT NULL OR EXISTS (SELECT 1 FROM job_enrichment e WHERE e.job_id=j.id AND e.salary NOT IN ('{}',''))) AND (j.staleness_state IS NULL OR j.staleness_state='active')"),
        "location":  cov("SELECT count(*) FROM jobs WHERE coalesce(location,'')<>'' AND (staleness_state IS NULL OR staleness_state='active')"),
        "workplace": cov("SELECT count(*) FROM jobs j WHERE EXISTS (SELECT 1 FROM job_enrichment e WHERE e.job_id=j.id AND coalesce(e.workplace_type,'unknown')<>'unknown') AND (j.staleness_state IS NULL OR j.staleness_state='active')"),
        "seniority": cov("SELECT count(*) FROM jobs j WHERE EXISTS (SELECT 1 FROM job_enrichment e WHERE e.job_id=j.id AND coalesce(e.seniority,'unknown')<>'unknown') AND (j.staleness_state IS NULL OR j.staleness_state='active')"),
        "visa":      cov("SELECT count(*) FROM jobs j WHERE EXISTS (SELECT 1 FROM job_enrichment e WHERE e.job_id=j.id AND coalesce(e.visa_sponsorship,'unknown')<>'unknown') AND (j.staleness_state IS NULL OR j.staleness_state='active')"),
        "domain":    cov("SELECT count(*) FROM jobs j WHERE EXISTS (SELECT 1 FROM job_enrichment e WHERE e.job_id=j.id AND coalesce(e.category,'')<>'') AND (j.staleness_state IS NULL OR j.staleness_state='active')"),
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
