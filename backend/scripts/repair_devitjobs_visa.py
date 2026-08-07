"""Repair the false "Visa sponsorship available" claim on stored devitjobs rows.

THE BUG (fixed in sources/apis_free/devitjobs.py, this repairs its damage).
`hasVisaSponsorship` arrives from the API as the STRING "Yes"/"No", and
`bool("No")` is True in Python. So `visa_flag = bool(item.get(...))` marked
EVERY devitjobs row as sponsoring, and the description this source composes
itself then had "Visa sponsorship available" appended to it.

Measured on prod 2026-08-07: 1,977 active rows carried that sentence while the
API reports exactly 2 of 2,377 jobs actually offering sponsorship.

WHY IT MATTERS MORE THAN A WRONG FLAG. A user with `needs_visa=True` gets the
FULL visa weight on a job that "sponsors", so ~1,975 jobs that explicitly
refuse sponsorship were being ranked UP for exactly the users who cannot take
them. Worse, `visa_signal.detect_visa_status` reads description text — so it
read our own fabricated sentence back as evidence, inflating measured visa
coverage. A fabricated fact that a detector then "confirms" is the most
expensive kind of wrong.

WHAT THIS DOES. Re-reads the live API (the authority), and for every stored
devitjobs row: sets `visa_flag` to the correctly-parsed value and strips the
"Visa sponsorship available" sentence from the description unless the API
actually says Yes. Descriptions are otherwise untouched.

Idempotent. Pass --apply to write; default is a dry run.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_CLAIM = re.compile(r"\.?\s*Visa sponsorship available\.?", re.IGNORECASE)


def _sponsors(raw: object) -> bool:
    """The parse the source should always have used."""
    return str(raw or "").strip().lower() in ("yes", "true", "1")


async def main(apply: bool) -> int:
    import aiohttp
    import psycopg

    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("set DATABASE_PUBLIC_URL or DATABASE_URL")

    async with aiohttp.ClientSession() as session:
        async with session.get("https://devitjobs.uk/api/jobsLight") as resp:
            live = await resp.json(content_type=None)

    # Key by (company, title) — the same pair the catalog dedupes on, since the
    # API's own id is not stored on our rows.
    truth: dict[tuple[str, str], bool] = {}
    for item in live:
        key = (
            str(item.get("company") or "").strip().lower(),
            str(item.get("name") or "").strip().lower(),
        )
        truth[key] = _sponsors(item.get("hasVisaSponsorship"))
    print(f"live API rows: {len(live)}; genuinely sponsoring: {sum(truth.values())}")

    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, company, title, description, visa_flag FROM jobs "
        "WHERE source = 'devitjobs'"
    )
    rows = cur.fetchall()

    fixes: list[tuple[int, str, int]] = []
    unmatched = 0
    for jid, company, title, desc, flag in rows:
        key = (str(company or "").strip().lower(), str(title or "").strip().lower())
        if key not in truth:
            # Not in the live feed any more (expired listing). Its stored claim
            # is still unverifiable, so strip it rather than leave a fabricated
            # fact standing.
            unmatched += 1
            sponsors = False
        else:
            sponsors = truth[key]
        new_desc = desc or ""
        if not sponsors:
            new_desc = _CLAIM.sub("", new_desc).strip()
        new_flag = 1 if sponsors else 0
        if new_desc != (desc or "") or int(flag or 0) != new_flag:
            fixes.append((jid, new_desc, new_flag))

    print(f"stored devitjobs rows: {len(rows)}  (not in live feed: {unmatched})")
    print(f"rows needing repair: {len(fixes)}")
    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        conn.close()
        return 0

    # Batched: ~2,000 individual round-trips over a public DSN exceeded a
    # 250s ceiling on the first run. executemany pipelines them.
    BATCH = 200
    for i in range(0, len(fixes), BATCH):
        chunk = fixes[i:i + BATCH]
        cur.executemany(
            "UPDATE jobs SET description = %s, visa_flag = %s WHERE id = %s",
            [(d, f, j) for j, d, f in chunk],
        )
        conn.commit()
        print(f"  ...{min(i + BATCH, len(fixes))}/{len(fixes)}", flush=True)
    print(f"\nREPAIRED {len(fixes)} rows.")
    conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    raise SystemExit(asyncio.run(main(ap.parse_args().apply)))
