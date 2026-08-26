#!/usr/bin/env python3
"""Fire the wiring.md features ON PURPOSE, against a real database.

WHY THIS EXISTS
---------------
Two of these features only ever run on a schedule or in response to a user action
that nobody performs during development:

  * the chase cron (W-19) runs at 09:00 UTC. If it breaks, the symptom is SILENCE —
    exactly the symptom it exists to remove. Nobody would notice for months.
  * the instant notification (W-17/W-18) fires on a match. Its failure mode is a
    delivered-but-useless email, which no test of the SEND PATH would catch.

The repo already learned this lesson expensively: ten guards shipped unable to fire,
and two whole CI loops sat dead on `main` until someone fired them on purpose (see
scripts/drill_registry.py). A feature nobody deliberately triggers is a feature you
find out about from a user.

This is NOT registered in drill_registry.py, and that is deliberate: that registry is
keyed on scripts invoked from .github/, and declaring something no workflow runs makes
it fail with STALE ENTRY. This is a manual operator drill, like scripts/observe.py.

USAGE
-----
    python scripts/drill_wiring.py chase     # fire the no-reply chase cron
    python scripts/drill_wiring.py instant   # build one instant notification
    python scripts/drill_wiring.py all

Point it at a scratch database, never production:

    DATABASE_URL=postgresql://job360:job360dev@localhost:5433/job360_rung4 \
        python scripts/drill_wiring.py all

Every drill seeds its own rows under a reserved user id, asserts the OUTPUT, and
deletes what it made. It prints the real subject and body so a human can read what a
user would receive — the point is to LOOK at it, not just to see a green tick.

BOUND: the Apprise/SMTP boundary is stubbed via the supported ctx['dispatcher'] hook,
so this proves content and behaviour, not deliverability. Deliverability needs a real
send and belongs in production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.settings import SITE_BASE_URL  # noqa: E402
from src.repositories import pg  # noqa: E402
from src.repositories.database import JobDatabase  # noqa: E402
from src.workers import tasks as worker_tasks  # noqa: E402

# Reserved ids so a drill can never touch a real person's rows.
DRILL_USER = "drill-wiring-user"
DRILL_COMPANY = "drillco"


class _Recorder:
    """Stands in for dispatcher.dispatch at the Apprise boundary."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, db: Any, **kw: Any) -> list[Any]:
        self.calls.append(kw)
        return [
            type(
                "R",
                (),
                {
                    "ok": True,
                    "queued_digest": False,
                    "skipped": False,
                    "channel_type": "email",
                    "channel_id": 1,
                    "error": None,
                },
            )()
        ]


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


async def _cleanup(db: Any) -> None:
    for sql, args in (
        ("DELETE FROM tailored_document_versions WHERE user_id = ?", (DRILL_USER,)),
        ("DELETE FROM tailored_documents WHERE user_id = ?", (DRILL_USER,)),
        ("DELETE FROM applications WHERE user_id = ?", (DRILL_USER,)),
        ("DELETE FROM user_feed WHERE user_id = ?", (DRILL_USER,)),
        ("DELETE FROM notification_ledger WHERE user_id = ?", (DRILL_USER,)),
        ("DELETE FROM notification_rules WHERE user_id = ?", (DRILL_USER,)),
        (
            "DELETE FROM job_enrichment WHERE job_id IN "
            "(SELECT id FROM jobs WHERE normalized_company = ?)",
            (DRILL_COMPANY,),
        ),
        ("DELETE FROM jobs WHERE normalized_company = ?", (DRILL_COMPANY,)),
        ("DELETE FROM users WHERE id = ?", (DRILL_USER,)),
    ):
        try:
            await db.execute(sql, args)
        except Exception as exc:  # noqa: BLE001 — a missing table must not block cleanup
            print(f"  (cleanup skipped: {exc})")
    await db.commit()


async def _seed_user(db: Any) -> None:
    await db.execute(
        "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (DRILL_USER, "drill-wiring@example.invalid", "x", _iso(30)),
    )
    await db.commit()


async def _seed_job(db: Any, title: str, company: str) -> int:
    cur = await db.execute(
        "INSERT INTO jobs (title, company, location, description, apply_url, source, "
        "date_found, match_score, normalized_company, normalized_title, first_seen, "
        "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (
            title, company, "London, UK", "drill", "https://employer.invalid/apply",
            "greenhouse", _iso(2), 70, DRILL_COMPANY, title.lower(),
            _iso(2), _iso(2), _iso(2),
        ),
    )
    row = await cur.fetchone()
    await db.commit()
    return int(row[0])


async def drill_chase(db: Any) -> list[str]:
    """W-19 — an application that has gone quiet must produce a message."""
    print("\n=== DRILL: the no-reply chase cron (W-19) ===")
    failures: list[str] = []
    await _seed_user(db)
    job_id = await _seed_job(db, "Platform Engineer", "Meta")
    await db.execute(
        "INSERT INTO notification_rules (user_id, enabled) VALUES (?, 1)", (DRILL_USER,)
    )
    # Quiet for 30 days — well past the 7-day dormancy window.
    await db.execute(
        "INSERT INTO applications (user_id, job_id, stage, created_at, updated_at) "
        "VALUES (?, ?, 'applied', ?, ?)",
        (DRILL_USER, job_id, _iso(30), _iso(30)),
    )
    await db.commit()
    print(f"  seeded: application on '{'Platform Engineer'}', quiet 30 days")

    rec = _Recorder()
    result = await worker_tasks.chase_stale_applications({"db": db, "dispatcher": rec})
    mine = [c for c in rec.calls if c.get("user_id") == DRILL_USER]
    print(f"  cron returned: {result}")

    if not mine:
        failures.append("chase: a 30-day-quiet application produced NO message")
        return failures

    print("  " + "-" * 60)
    print("  SUBJECT:", mine[0]["title"])
    for line in mine[0]["body"].splitlines():
        print("  " + line)
    print("  " + "-" * 60)

    body = mine[0]["body"]
    for label, ok in (
        ("names the job", "Platform Engineer" in body),
        # 'Meta' ends in a stripped character - the truncation bug lived exactly here.
        ("names the company in full", "Meta" in body),
        ("score gate bypassed", mine[0].get("match_score") is None),
        ("quiet hours still apply", not mine[0].get("force")),
    ):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(f"chase: {label}")

    # Fire again: the cooldown must silence it.
    rec2 = _Recorder()
    await worker_tasks.chase_stale_applications({"db": db, "dispatcher": rec2})
    again = [c for c in rec2.calls if c.get("user_id") == DRILL_USER]
    ok = not again
    print(f"  [{'PASS' if ok else 'FAIL'}] second run stays silent (cooldown)")
    if not ok:
        failures.append("chase: chased twice inside the cooldown — this would spam")
    return failures


async def drill_instant(db: Any) -> list[str]:
    """W-17/W-18 — the instant email must say why, and link back to us."""
    print("\n=== DRILL: the instant match notification (W-17/W-18) ===")
    failures: list[str] = []
    await _seed_user(db)
    job_id = await _seed_job(db, "Staff Reliability Engineer", "Monzo")
    await db.execute(
        "INSERT INTO user_feed (user_id, job_id, score, bucket, llm_fit_score, "
        "llm_verdict, llm_reason) VALUES (?,?,?,?,?,?,?)",
        (DRILL_USER, job_id, 64, "good", 88, "strong fit",
         "You have run production Kubernetes at scale and they need exactly that."),
    )
    await db.execute(
        "INSERT INTO job_enrichment (job_id, title_canonical, category, salary) "
        "VALUES (?, ?, ?, ?)",
        (job_id, "staff reliability engineer", "engineering",
         json.dumps({"currency": "GBP", "min": 95000, "max": 115000,
                     "frequency": "yearly"})),
    )
    await db.commit()

    rec = _Recorder()
    result = await worker_tasks.send_notification({"db": db, "dispatcher": rec}, DRILL_USER, job_id)
    print(f"  send_notification returned: {result}")
    if not rec.calls:
        failures.append("instant: nothing was dispatched")
        return failures

    call = rec.calls[0]
    print("  " + "-" * 60)
    print("  SUBJECT:", call["title"])
    for line in call["body"].splitlines():
        print("  " + line)
    print("  " + "-" * 60)

    body = call["body"]
    for label, ok in (
        ("states the fit score", "88" in body),
        ("states the verdict", "strong fit" in body),
        ("states the reason", "Kubernetes" in body),
        ("states the salary", "95k" in body),
        ("links to Job360", f"{SITE_BASE_URL.rstrip('/')}/jobs/{job_id}" in body),
        ("does NOT link to the employer", "employer.invalid" not in body),
        ("score gate receives a real score", call.get("match_score") == 88),
    ):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(f"instant: {label}")
    return failures


DRILLS = {"chase": drill_chase, "instant": drill_instant}


async def main_async(which: str) -> int:
    chosen = list(DRILLS) if which == "all" else [which]
    failures: list[str] = []
    async with pg.connect("drill_wiring.db") as db:
        db.row_factory = pg.Row
        JobDatabase.from_connection("", db)  # ensures the shim is wired the same way
        for name in chosen:
            await _cleanup(db)
            try:
                failures.extend(await DRILLS[name](db))
            finally:
                await _cleanup(db)

    print("\n" + "=" * 66)
    if failures:
        for f in failures:
            print("FAILED:", f)
        print(f"\n{len(failures)} drill check(s) failed.")
        return 1
    print("All drills fired and behaved correctly.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("which", choices=[*DRILLS, "all"], help="which drill to fire")
    args = ap.parse_args()
    return asyncio.run(main_async(args.which))


if __name__ == "__main__":
    raise SystemExit(main())
