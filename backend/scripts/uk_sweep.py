"""One-time sweep: retire the foreign jobs already inside the catalog.

The UK gate now blocks these at ingest, but rows admitted BEFORE it existed
are still live. This retires them.

DELIBERATELY REVERSIBLE. Rows are marked `staleness_state='confirmed_expired'`
(the existing sticky state that excludes a job from the 24h bucket and from
active-catalog reads) rather than DELETEd. Reasons:
  * a DELETE cascades to user_feed and destroys the audit trail;
  * if the gate turns out to be wrong about any row, a mark is one UPDATE to
    undo and a delete is unrecoverable;
  * 1,247 rows is 25% of the catalog — too much to remove irreversibly on the
    first run of a brand-new rule.

Pass --apply to write; default is a dry run.
"""
import argparse
import os
import sys

sys.path.insert(0, '.')

import psycopg  # noqa: E402

from src.services.uk_gate import check_uk  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--confident-only", action="store_true",
                help="retire ONLY named-foreign-country and region-fenced-remote "
                     "rows; leave the judgement-call bucket alone")
args = ap.parse_args()

conn = psycopg.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute(
    "SELECT id, title, location, source, coalesce(description,'') FROM jobs "
    "WHERE staleness_state IS NULL OR staleness_state = 'active'"
)
rows = cur.fetchall()

doomed = []
for jid, title, loc, src, desc in rows:
    v = check_uk(loc, src, description=desc, title=title or "")
    if v.allowed:
        continue
    # The two unambiguous reasons: the ad NAMES a foreign country/city, or it
    # fences its remote role to another region. The third reason
    # ("unverified_location_on_global_source") is a judgement call about an
    # unrecognised town and is held back under --confident-only.
    if args.confident_only and v.reason not in (
        "foreign_location", "remote_restricted_to_other_region"
    ):
        continue
    doomed.append(jid)

print(f"active catalog: {len(rows)}")
print(f"fails the UK gate: {len(doomed)} ({round(100 * len(doomed) / max(1, len(rows)))}%)")

# How many feed rows point at them — the user-visible blast radius.
if doomed:
    cur.execute(
        "SELECT count(*), count(DISTINCT user_id) FROM user_feed "
        "WHERE job_id = ANY(%s) AND status = 'active'", (doomed,)
    )
    n_feed, n_users = cur.fetchone()
    print(f"active feed rows pointing at them: {n_feed} across {n_users} user(s)")

if not args.apply:
    print("\nDRY RUN — nothing written. Re-run with --apply to retire them.")
    conn.close()
    raise SystemExit(0)

cur.execute(
    "UPDATE jobs SET staleness_state = 'confirmed_expired' WHERE id = ANY(%s)",
    (doomed,),
)
retired = cur.rowcount
# Evict their feed rows too, so nobody's dashboard keeps showing them.
cur.execute(
    "UPDATE user_feed SET status = 'evicted' "
    "WHERE job_id = ANY(%s) AND status = 'active'", (doomed,)
)
evicted = cur.rowcount
conn.commit()
print(f"\nRETIRED {retired} jobs; evicted {evicted} feed rows.")
print("Reversible: UPDATE jobs SET staleness_state='active' WHERE id = ANY(...)")
conn.close()
