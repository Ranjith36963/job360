"""Read-only instrument: "of the top 20 jobs we show him, how many are worth
applying to?" — the baseline every future Job360 change is measured against.

Reads the SAME path GET /api/jobs uses for a logged-in caller — does not
invent a new query:
    - src/repositories/database.py::JobDatabase.get_user_feed_jobs()
      (the exact SQL the route calls, same WHERE/ORDER BY)
    - src/api/routes/jobs.py::_maybe_apply_hybrid_reorder()
      (the exact function the route calls when mode=hybrid)
The dashboard's default request (frontend/src/app/dashboard/page.tsx) is
hours=168, min_score=0, mode="hybrid" — mirrored here as days=7, min_score=0,
plus the hybrid reorder. Hybrid degrades to keyword order automatically when
the semantic stack/flags are off in this environment, same as production.

READ-ONLY. Never writes to the database. Safe to run against production:
    cd backend && railway run -s Postgres python scripts/top20_report.py --email you@example.com

DATABASE_PUBLIC_URL (Railway's externally-reachable DSN) is swapped in for
DATABASE_URL before any settings import, because plain DATABASE_URL only
resolves inside Railway's private network.

Always writes UTF-8 to a file — this console is cp1252 and mangles non-ASCII
job titles/company names, which has already caused one false "corruption"
alarm on this project. Never trust console echo for the real content.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

# Ensure backend/ is on sys.path so `src.*` resolves when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# MUST happen before importing src.core.settings — it binds the Postgres DSN
# at import time via `pg.configure(DATABASE_URL)`. Plain DATABASE_URL (the
# private Railway hostname) is unreachable from a local `railway run`; the
# public one is a Railway-provided override of that env var, not a new name.
if os.getenv("DATABASE_PUBLIC_URL"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_PUBLIC_URL"]

from src.api.routes.jobs import _maybe_apply_hybrid_reorder  # noqa: E402
from src.core.settings import DB_PATH  # noqa: E402
from src.repositories.database import JobDatabase  # noqa: E402


async def _resolve_user_id(db: JobDatabase, email: Optional[str], user_id: Optional[str]) -> str:
    if user_id:
        return user_id
    cursor = await db._db.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = await cursor.fetchone()
    if not row:
        raise SystemExit(f"No user found for email {email!r}")
    return dict(row)["id"]


async def _enrichment_ids(db: JobDatabase, job_ids: list[int]) -> set[int]:
    """Authoritative "does job_enrichment have a row for this job?" check —
    queried directly rather than inferred from the LEFT JOIN's enr_* columns,
    which can legitimately be NULL even when a (mostly-empty) row exists."""
    if not job_ids:
        return set()
    placeholders = ",".join("?" for _ in job_ids)
    cursor = await db._db.execute(
        f"SELECT job_id FROM job_enrichment WHERE job_id IN ({placeholders})",  # noqa: S608 — ints only, own placeholders
        tuple(job_ids),
    )
    rows = await cursor.fetchall()
    return {dict(r)["job_id"] for r in rows}


def _desc_len(row: dict[str, Any]) -> int:
    return len(row.get("description") or "")


def _apply_host(url: Optional[str]) -> str:
    if not url:
        return "(no url)"
    try:
        return urlparse(url).netloc or "(no host)"
    except ValueError:
        return "(unparsable)"


def _row_score(row: dict[str, Any]) -> int:
    """Same value the route serializes as match_score: the caller's OWN
    user_feed score, never the shared last-writer-wins jobs.match_score."""
    v = row.get("feed_score")
    if v is None:
        v = row.get("match_score")
    return int(v) if v is not None else 0


def _primary_score(row: dict[str, Any]) -> tuple[int, bool]:
    """The number shown in the BIG badge on the card
    (frontend/src/components/jobs/JobCard.tsx:117-118): the LLM judge's
    llm_fit_score when the job has been judged, else the keyword+dims
    match_score. Returns (value, is_judged)."""
    fit = row.get("llm_fit_score")
    verdict = row.get("llm_verdict")
    if fit is not None and verdict is not None:
        return int(fit), True
    return _row_score(row), False


def _is_stale_badge(state: Optional[str]) -> bool:
    """Reproduces frontend/src/components/jobs/JobCard.tsx:146-149 EXACTLY —
    a case-sensitive comparison against uppercase literals ("ACTIVE",
    "UNKNOWN"). The DB actually stores lowercase values
    (services/ghost_detection.StalenessState: "active", "possibly_stale",
    "likely_stale", "confirmed_expired"), so this is reporting what the
    frontend really does, not what it was meant to do — verification only,
    no fix applied here."""
    return bool(state) and state != "ACTIVE" and state != "UNKNOWN"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", help="User's login email (looked up in users.email)")
    ap.add_argument("--user-id", help="User id directly (skips the email lookup)")
    ap.add_argument("--out", default=None, help="Output file path. Default: scripts/top20_report_<timestamp>.txt")
    ap.add_argument(
        "--days",
        type=int,
        default=7,
        help="Feed age window in days, matching the dashboard's hours=168 default. "
        "Only change this to diagnose a stale/empty feed — it stops being an "
        "apples-to-apples reading of what the UI actually shows at the default value.",
    )
    args = ap.parse_args()

    if not args.email and not args.user_id:
        raise SystemExit("Pass --email you@example.com or --user-id <id>")

    db = JobDatabase(str(DB_PATH))
    await db.connect()

    lines: list[str] = []

    def emit(s: str = "") -> None:
        lines.append(s)

    try:
        user_id = await _resolve_user_id(db, args.email, args.user_id)

        # Same read path GET /api/jobs uses for an authenticated caller, with
        # the dashboard's default filters (hours=168 -> days=7, min_score=0).
        all_rows = await db.get_user_feed_jobs(user_id, days=args.days, min_score=0)
        total_feed_rows = len(all_rows)

        # Same hybrid reorder the route applies for the dashboard's default
        # mode=hybrid request. Degrades to keyword order automatically if the
        # semantic stack / ENGINE3_ENABLED / SEMANTIC_ENABLED are off in this
        # environment — that IS production behaviour, not a simulation gap.
        from src.services.profile.storage import load_profile  # noqa: PLC0415

        profile = load_profile(user_id)
        all_rows = await asyncio.to_thread(_maybe_apply_hybrid_reorder, all_rows, profile=profile)

        top20 = all_rows[:20]
        enriched_top20 = await _enrichment_ids(db, [r["id"] for r in top20])

        emit("=" * 100)
        emit(f"TOP 20 REPORT  —  generated {datetime.now(timezone.utc).isoformat()}")
        emit(f"user_id={user_id}")
        emit(
            f"read path: JobDatabase.get_user_feed_jobs(user_id, days={args.days}, min_score=0) "
            f"+ _maybe_apply_hybrid_reorder  (== GET /api/jobs?hours={args.days * 24}&min_score=0&mode=hybrid"
            f"{', dashboard DEFAULT' if args.days == 7 else ' -- NON-DEFAULT WINDOW, diagnostic only'})"
        )
        emit(f"rows in user's active feed (<={args.days}d, staleness active/null, score>=0): {total_feed_rows}")
        emit("=" * 100)
        emit()

        if not top20:
            emit("!! FEED IS EMPTY — user_feed has zero active rows for this user in the last 7 days.")
        for i, row in enumerate(top20, start=1):
            keyword_score = _row_score(row)
            primary, judged = _primary_score(row)
            score_str = f"{primary} (LLM judge)" if judged else f"{primary} (keyword+dims)"
            title = row.get("title") or "(EMPTY TITLE)"
            company = row.get("company") or "(EMPTY)"
            location = row.get("location") or "(EMPTY)"
            posted_at = row.get("posted_at") or "(none)"
            date_conf = row.get("date_confidence") or "(none)"
            desc_len = _desc_len(row)
            has_enr = row["id"] in enriched_top20
            host = _apply_host(row.get("apply_url"))
            emit(f'#{i:>2}  card badge score={score_str}  "{title}" @ {company} — {location}')
            emit(
                f"      keyword+dims score={keyword_score}  llm_verdict={row.get('llm_verdict') or '(none)'}  "
                f"posted_at={posted_at}  date_confidence={date_conf}  desc_len={desc_len}  "
                f"enrichment={'yes' if has_enr else 'no '}  apply_host={host}"
            )
        emit()

        # ---- Summary stats (over the FULL ranked feed, not just the 20) ----
        emit("-" * 100)
        emit(f"SUMMARY — over the full ranked feed (N={total_feed_rows}); top 20 above is a slice of this")
        emit("-" * 100)

        scores = [_row_score(r) for r in all_rows]
        if scores:
            emit(
                f"score distribution: min={min(scores)} max={max(scores)} "
                f"mean={statistics.mean(scores):.1f} median={statistics.median(scores):.1f}"
            )
            buckets: Counter[str] = Counter()
            for s in scores:
                if s >= 80:
                    buckets["80-100"] += 1
                elif s >= 60:
                    buckets["60-79"] += 1
                elif s >= 40:
                    buckets["40-59"] += 1
                elif s >= 20:
                    buckets["20-39"] += 1
                else:
                    buckets["0-19"] += 1
            for k in ["80-100", "60-79", "40-59", "20-39", "0-19"]:
                emit(f"  {k}: {buckets.get(k, 0)}")
        else:
            emit("score distribution: N/A (empty feed)")

        under_300 = sum(1 for r in all_rows if _desc_len(r) < 300)
        empty_company = sum(
            1
            for r in all_rows
            if not (r.get("company") or "").strip() or (r.get("company") or "").strip().lower() == "unknown"
        )
        stale_badge = sum(1 for r in all_rows if _is_stale_badge(r.get("staleness_state")))
        judged = sum(1 for r in all_rows if r.get("llm_fit_score") is not None and r.get("llm_verdict") is not None)
        all_ids = [r["id"] for r in all_rows]
        all_enriched = await _enrichment_ids(db, all_ids)

        emit(f"description under 300 chars: {under_300} / {total_feed_rows}")
        emit(f"company empty/Unknown: {empty_company} / {total_feed_rows}")
        emit(f"judged by the LLM judge (llm_fit_score+verdict set — this is the card's PRIMARY badge "
             f"number when present): {judged} / {total_feed_rows}")
        emit(
            f"would render a STALE badge (frontend JobCard.tsx:146-149, case-sensitive "
            f"vs uppercase literals — see docstring): {stale_badge} / {total_feed_rows}"
        )
        emit(f"has a job_enrichment row: {len(all_enriched)} / {total_feed_rows}")

    finally:
        await db.close()

    out_path = (
        Path(args.out)
        if args.out
        else Path(__file__).resolve().parent / f"top20_report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.txt"
    )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Best-effort console echo — the file above is the source of truth
    # (this console is cp1252 and WILL mangle non-ASCII titles/companies).
    print(f"wrote {out_path}")
    for line in lines:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
