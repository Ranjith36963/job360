"""engine_health.py — read-only benchmark/accuracy tool for Job360.

Measures the search/match engine's ACCURACY and LATENCY/FRESHNESS and prints
a PASS/FAIL report. Does NOT modify any scoring/search/enrichment code.

Usage:
    cd backend
    python scripts/engine_health.py --help
    python scripts/engine_health.py --api http://localhost:8000 --db data/jobs.db
    python scripts/engine_health.py --email me@example.com --password secret

Pure functions (importable + unit-testable):
    percentiles, freshness_stats, judge_agreement, ghost_rate,
    actions_precision, evaluate_targets
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.repositories import pgsync

# ---------------------------------------------------------------------------
# Ensure repo's backend/ is on sys.path so `src.*` imports resolve when this
# script is invoked directly (e.g. python scripts/engine_health.py).
# Under pytest (pythonpath=["."]), backend/ is already on sys.path.
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ---------------------------------------------------------------------------
# Default targets (module-level, overridable)
# ---------------------------------------------------------------------------
DEFAULT_TARGETS: dict[str, float] = {
    "feed_keyword_p95_ms": 500,
    "feed_hybrid_p95_ms": 3000,
    "ghost_rate_pct": 5,            # MAX threshold — must be <= this
    "judge_top10_goodfit_pct": 80,  # MIN threshold — must be >= this
}

# Targets where LOWER is better (MAX thresholds).  Everything else is MIN.
_MAX_THRESHOLDS = {"feed_keyword_p95_ms", "feed_hybrid_p95_ms", "ghost_rate_pct"}

# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def percentiles(samples: list[float], ps: list[int]) -> dict[int, float]:
    """Return the requested percentiles of *samples*.

    Uses the nearest-rank method (ceiling of rank = ceil(p/100 * n)).
    Returns a dict with None values when *samples* is empty; when *samples*
    has items, every key in *ps* maps to a float.

    Args:
        samples: Raw numeric samples (any order).
        ps: List of integer percentile levels, e.g. [50, 95, 99].

    Returns:
        Dict mapping each p → value, or {p: None} when samples is empty.
    """
    if not samples:
        return {p: None for p in ps}  # type: ignore[return-value]

    sorted_s = sorted(samples)
    n = len(sorted_s)
    result: dict[int, float] = {}

    for p in ps:
        if p <= 0:
            result[p] = sorted_s[0]
        elif p >= 100:
            result[p] = sorted_s[-1]
        else:
            # Nearest-rank: index = ceil(p/100 * n) - 1
            rank = math.ceil(p / 100.0 * n)
            idx = max(0, min(rank - 1, n - 1))
            result[p] = sorted_s[idx]

    return result


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime.  Returns None on failure."""
    if not ts:
        return None
    # Python 3.9 does not support the trailing 'Z'; normalise it.
    ts_norm = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts_norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def freshness_stats(posted_ats: list[str | None], now_iso: str) -> dict:
    """Compute freshness stats over a list of posted_at strings.

    Args:
        posted_ats: ISO datetime strings, may contain None values.
        now_iso: Reference "now" as an ISO datetime string (injected for testability).

    Returns:
        Dict with keys:
            count           — total items in posted_ats
            with_date       — how many had a non-null, parseable date
            median_age_days — median age in fractional days (None if no dates)
            pct_last_24h    — % of *dated* rows posted within the last 24 h
            pct_last_7d     — % of *dated* rows posted within the last 7 days
    """
    now = _parse_iso(now_iso)
    if now is None:
        now = datetime.now(timezone.utc)

    count = len(posted_ats)
    ages: list[float] = []

    for ts in posted_ats:
        if ts is None:
            continue
        dt = _parse_iso(ts)
        if dt is None:
            continue
        age_seconds = (now - dt).total_seconds()
        ages.append(age_seconds / 86400.0)  # convert to days

    with_date = len(ages)
    if with_date == 0:
        return {
            "count": count,
            "with_date": 0,
            "median_age_days": None,
            "pct_last_24h": 0.0,
            "pct_last_7d": 0.0,
        }

    p50 = percentiles(ages, [50])
    median_age = p50.get(50)

    within_24h = sum(1 for a in ages if a <= 1.0)
    within_7d = sum(1 for a in ages if a <= 7.0)

    return {
        "count": count,
        "with_date": with_date,
        "median_age_days": median_age,
        "pct_last_24h": within_24h / with_date * 100.0,
        "pct_last_7d": within_7d / with_date * 100.0,
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Compute Pearson correlation between xs and ys.

    Returns None if fewer than 2 points or either series has zero variance.
    Pure-Python, no numpy/scipy.
    """
    n = len(xs)
    if n < 2:
        return None

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)

    denom = math.sqrt(var_x * var_y)
    if denom == 0.0:
        return None
    return cov / denom


def judge_agreement(rows: list[dict]) -> dict:
    """Measure agreement between keyword scorer and LLM judge.

    Args:
        rows: Each dict has keys:
            score         — keyword match score (int, 0-100)
            llm_fit_score — LLM fit score (int, 0-100) or None if not judged

    Returns:
        Dict with keys:
            n_judged           — count of rows where llm_fit_score is not None
            pearson            — Pearson r between score and llm_fit_score; None if <2 pts
            top10_goodfit_pct  — % of the top-10 rows (by keyword score) that are judged
                                 AND have llm_fit_score >= 60; None if none of the top-10
                                 were judged
    """
    judged = [r for r in rows if r.get("llm_fit_score") is not None]
    n_judged = len(judged)

    # Pearson between keyword score and llm_fit_score
    if n_judged >= 2:
        xs = [float(r["score"]) for r in judged]
        ys = [float(r["llm_fit_score"]) for r in judged]
        pearson: float | None = _pearson(xs, ys)
    else:
        pearson = None

    # top-10 by keyword score
    sorted_rows = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)
    top10 = sorted_rows[:10]
    top10_judged = [r for r in top10 if r.get("llm_fit_score") is not None]

    if not top10_judged:
        top10_goodfit_pct: float | None = None
    else:
        good = sum(1 for r in top10_judged if r["llm_fit_score"] >= 60)
        top10_goodfit_pct = good / len(top10_judged) * 100.0

    return {
        "n_judged": n_judged,
        "pearson": pearson,
        "top10_goodfit_pct": top10_goodfit_pct,
    }


def ghost_rate(staleness_states: list[str | None]) -> dict:
    """Compute the ghost-job rate from staleness state values.

    Args:
        staleness_states: List of staleness_state column values; may include None.

    Returns:
        Dict with keys:
            total  — total rows
            ghosts — count where state is 'GHOST' or 'CONFIRMED_EXPIRED'
            pct    — ghosts / total * 100 (0.0 if total == 0)
    """
    _ghost_states = {"GHOST", "CONFIRMED_EXPIRED"}
    total = len(staleness_states)
    ghosts = sum(1 for s in staleness_states if s in _ghost_states)
    pct = (ghosts / total * 100.0) if total > 0 else 0.0
    return {"total": total, "ghosts": ghosts, "pct": pct}


def actions_precision(rows: list[dict]) -> dict:
    """Measure precision of the scorer from user engagement signals.

    Args:
        rows: Each dict has keys:
            score  — keyword match score (int)
            action — one of 'liked', 'applied', 'not_interested', or None

    Returns:
        Dict with keys:
            n                         — total row count
            top_decile_engage_rate    — fraction of top-10% rows with action liked/applied
            bottom_decile_engage_rate — fraction of bottom-10% rows with action liked/applied
            lift                      — top / bottom; None if bottom is 0 or data insufficient
    """
    _engage = {"liked", "applied"}
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "top_decile_engage_rate": None,
            "bottom_decile_engage_rate": None,
            "lift": None,
        }

    sorted_rows = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)

    decile_size = max(1, math.ceil(n / 10))
    top = sorted_rows[:decile_size]
    bottom = sorted_rows[-decile_size:]

    def _engage_rate(bucket: list[dict]) -> float:
        if not bucket:
            return 0.0
        engaged = sum(1 for r in bucket if r.get("action") in _engage)
        return engaged / len(bucket)

    top_rate = _engage_rate(top)
    bottom_rate = _engage_rate(bottom)

    if bottom_rate == 0.0:
        lift: float | None = None
    else:
        lift = top_rate / bottom_rate

    return {
        "n": n,
        "top_decile_engage_rate": top_rate,
        "bottom_decile_engage_rate": bottom_rate,
        "lift": lift,
    }


def evaluate_targets(metrics: dict, targets: dict) -> list[dict]:
    """Evaluate collected metrics against target thresholds.

    Args:
        metrics: Dict of metric_name → value (float or None).
        targets: Dict of metric_name → threshold.  Only names present in
                 *targets* are evaluated; extras in *metrics* are ignored.

    Returns:
        List of dicts, one per target, each with:
            name   — metric name
            value  — measured value (may be None)
            target — threshold
            pass   — True/False, or None if value is None (skipped)
            note   — human-readable description
    """
    result = []
    for name, threshold in targets.items():
        value = metrics.get(name)

        if value is None:
            passed: bool | None = None
            note = "N/A (no data)"
        elif name in _MAX_THRESHOLDS:
            # Lower is better; must be <= threshold
            passed = float(value) <= float(threshold)
            note = f"must be <= {threshold}"
        else:
            # Higher is better (MIN threshold); must be >= threshold
            passed = float(value) >= float(threshold)
            note = f"must be >= {threshold}"

        result.append({
            "name": name,
            "value": value,
            "target": threshold,
            "pass": passed,
            "note": note,
        })
    return result


# ---------------------------------------------------------------------------
# I/O collectors (not unit-tested with live HTTP — rule #4)
# ---------------------------------------------------------------------------


def bench_endpoint(client: object, url: str, n: int) -> list[float]:
    """Time *n* GET requests to *url*, return latencies in milliseconds.

    Args:
        client: An httpx.Client instance (typed as object to avoid top-level import).
        url: Relative or absolute URL to GET.
        n: Number of requests to issue.

    Returns:
        List of latencies in milliseconds (failed requests are omitted).
    """
    latencies: list[float] = []
    for _ in range(n):
        elapsed = _timed_get(client, url)
        if elapsed is not None:
            latencies.append(elapsed)
    return latencies


def _timed_get(client: object, url: str) -> float | None:
    """Issue a single GET and return elapsed ms, or None on error."""
    t0 = time.perf_counter()
    try:
        client.get(url)  # type: ignore[union-attr]
        return (time.perf_counter() - t0) * 1000.0
    except Exception as exc:  # noqa: BLE001
        # Network failures are expected when the API is down; suppress and skip.
        _ = exc  # acknowledge the exception is handled (appease linters)
        return None


def _db_read_jobs(db_path: str) -> tuple[list[str | None], list[str | None]]:
    """Read posted_at and staleness_state from the jobs table.

    Returns:
        (posted_ats, staleness_states) — parallel lists, one entry per row.
        Empty lists when table is absent or has no rows.
    """
    try:
        con = pgsync.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = pgsync.Row
        cur = con.execute("SELECT posted_at, staleness_state FROM jobs")
        rows = cur.fetchall()
        con.close()
        posted_ats = [r["posted_at"] for r in rows]
        staleness_states = [r["staleness_state"] for r in rows]
        return posted_ats, staleness_states
    except Exception:  # noqa: BLE001
        return [], []


def _db_read_feed(db_path: str, user_id: str | None = None) -> list[dict]:
    """Read score and llm_fit_score from user_feed.

    Args:
        db_path: Path to the SQLite DB.
        user_id: Optional — filter to a single user.  If None, reads all rows.

    Returns:
        List of dicts with keys: score, llm_fit_score.
        Empty list when table is absent or has no rows.
    """
    try:
        con = pgsync.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = pgsync.Row
        if user_id:
            cur = con.execute(
                "SELECT score, llm_fit_score FROM user_feed WHERE user_id = ?",
                (user_id,),
            )
        else:
            cur = con.execute("SELECT score, llm_fit_score FROM user_feed")
        rows = [{"score": r["score"], "llm_fit_score": r["llm_fit_score"]} for r in cur.fetchall()]
        con.close()
        return rows
    except Exception:  # noqa: BLE001
        return []


def _db_read_actions(db_path: str, user_id: str | None = None) -> list[dict]:
    """Join user_feed.score with user_actions.action for a given user.

    Returns:
        List of dicts with keys: score, action (may be None for unactioned jobs).
    """
    try:
        con = pgsync.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = pgsync.Row
        if user_id:
            cur = con.execute(
                """
                SELECT uf.score, ua.action
                FROM user_feed uf
                LEFT JOIN user_actions ua
                    ON ua.job_id = uf.job_id AND ua.user_id = uf.user_id
                WHERE uf.user_id = ?
                """,
                (user_id,),
            )
        else:
            cur = con.execute(
                """
                SELECT uf.score, ua.action
                FROM user_feed uf
                LEFT JOIN user_actions ua
                    ON ua.job_id = uf.job_id AND ua.user_id = uf.user_id
                """
            )
        rows = [{"score": r["score"], "action": r["action"]} for r in cur.fetchall()]
        con.close()
        return rows
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

_TICK = "✓"   # ✓
_CROSS = "✗"  # ✗
_DASH = "–"   # –


def _fmt_pass(passed: bool | None) -> str:
    if passed is True:
        return _TICK
    if passed is False:
        return _CROSS
    return _DASH


def _fmt_val(v: object) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def _print_section(title: str, rows: list[tuple[str, str]]) -> None:
    """Print a named section with label -> value pairs."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    col = max(len(r[0]) for r in rows) + 2 if rows else 20
    for label, value in rows:
        print(f"  {label:<{col}}{value}")


def _print_evaluation(eval_rows: list[dict]) -> bool:
    """Print the PASS/FAIL evaluation table.  Returns True if all non-skipped pass."""
    print(f"\n{'=' * 60}")
    print("  EVALUATION (targets)")
    print(f"{'=' * 60}")
    header_lbl = f"  {'METRIC':<34} {'VALUE':>10}  {'TARGET':>8}  STATUS"
    print(header_lbl)
    print(f"  {'-' * 56}")
    all_pass = True
    for row in eval_rows:
        p = row["pass"]
        marker = _fmt_pass(p)
        if p is False:
            all_pass = False
        print(
            f"  {row['name']:<34} {_fmt_val(row['value']):>10}  "
            f"{_fmt_val(row['target']):>8}  {marker}  {row['note']}"
        )
    return all_pass


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the engine_health benchmark tool."""
    # Make stdout UTF-8 tolerant so Unicode markers (✓ ✗ –) don't crash on
    # Windows consoles whose code page is cp1252.  errors="replace" means any
    # character that still can't be encoded is swapped for '?' rather than
    # raising UnicodeEncodeError.  Guarded: reconfigure() only exists on real
    # TextIOWrapper streams (Python 3.7+); a redirected/piped stdout may not
    # have it, so we swallow any failure silently.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001,S110
        pass

    parser = argparse.ArgumentParser(
        prog="engine_health",
        description="Job360 engine health — latency + accuracy benchmark (read-only).",
    )
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument(
        "--db",
        default="data/jobs.db",
        help="Path to SQLite DB (default: data/jobs.db relative to backend/)",
    )
    parser.add_argument("--email", default=os.environ.get("BENCH_EMAIL", ""), help="Login email")
    parser.add_argument(
        "--password", default=os.environ.get("BENCH_PASSWORD", ""), help="Login password"
    )
    parser.add_argument("--samples", type=int, default=30, help="Keyword endpoint samples (default 30)")
    parser.add_argument(
        "--hybrid-samples", type=int, default=10, dest="hybrid_samples",
        help="Hybrid endpoint samples (default 10)",
    )
    parser.add_argument("--json-out", dest="json_out", default=None, help="Optional path to write JSON output")
    args = parser.parse_args()

    # Resolve DB path relative to backend/ so callers don't have to cd
    db_path = args.db if os.path.isabs(args.db) else str(_BACKEND / args.db)

    # Lazy import httpx (already a dep) — kept here so pure functions don't pull it
    import httpx  # noqa: PLC0415

    print("\nJob360 Engine Health Benchmark")
    print(f"API:  {args.api}")
    print(f"DB:   {db_path}")

    # -----------------------------------------------------------------------
    # Step 1 — Login
    # -----------------------------------------------------------------------
    client = httpx.Client(base_url=args.api, timeout=30.0, follow_redirects=True)
    user_id: str | None = None
    authed = False

    if args.email and args.password:
        try:
            resp = client.post(
                "/api/auth/login",
                json={"email": args.email, "password": args.password},
            )
            if resp.status_code == 200:
                authed = True
                try:
                    data = resp.json()
                    user_id = data.get("user_id") or data.get("id")
                except Exception:  # noqa: BLE001
                    user_id = None
                print(f"  Login: OK (user_id={user_id})")
            else:
                print(f"  Login: FAILED (HTTP {resp.status_code}) — running unauthenticated")
        except Exception as exc:  # noqa: BLE001
            print(f"  Login: FAILED ({exc}) — running unauthenticated")
    else:
        print("  Login: skipped (no --email/--password) — unauthenticated mode")

    # -----------------------------------------------------------------------
    # Step 2 — Latency benchmarks
    # -----------------------------------------------------------------------
    # Always benchmark /api/status (public); benchmark /api/jobs only when authed
    print("\nBenchmarking /api/status x 5 ...")
    status_latencies = bench_endpoint(client, "/api/status", 5)

    kw_latencies: list[float] = []
    hybrid_latencies: list[float] = []

    if authed:
        kw_url = "/api/jobs?hours=168&min_score=30"
        hybrid_url = "/api/jobs?hours=168&min_score=30&mode=hybrid"
        print(f"Benchmarking keyword endpoint x {args.samples} ...")
        kw_latencies = bench_endpoint(client, kw_url, args.samples)
        print(f"Benchmarking hybrid endpoint x {args.hybrid_samples} ...")
        hybrid_latencies = bench_endpoint(client, hybrid_url, args.hybrid_samples)
    else:
        print("  Skipping /api/jobs benchmark (unauthenticated)")

    client.close()

    kw_pct = percentiles(kw_latencies, [50, 95, 99]) if kw_latencies else {}
    hybrid_pct = percentiles(hybrid_latencies, [50, 95, 99]) if hybrid_latencies else {}
    status_pct = percentiles(status_latencies, [50, 95]) if status_latencies else {}

    _print_section(
        "LATENCY",
        [
            ("/api/status p50", f"{_fmt_val(status_pct.get(50))} ms"),
            ("/api/status p95", f"{_fmt_val(status_pct.get(95))} ms"),
            ("/api/jobs keyword p50", f"{_fmt_val(kw_pct.get(50))} ms"),
            ("/api/jobs keyword p95", f"{_fmt_val(kw_pct.get(95))} ms"),
            ("/api/jobs keyword p99", f"{_fmt_val(kw_pct.get(99))} ms"),
            ("/api/jobs hybrid p50", f"{_fmt_val(hybrid_pct.get(50))} ms"),
            ("/api/jobs hybrid p95", f"{_fmt_val(hybrid_pct.get(95))} ms"),
            ("/api/jobs hybrid p99", f"{_fmt_val(hybrid_pct.get(99))} ms"),
            ("keyword samples", str(len(kw_latencies))),
            ("hybrid samples", str(len(hybrid_latencies))),
        ],
    )

    # -----------------------------------------------------------------------
    # Step 3 — DB metrics
    # -----------------------------------------------------------------------
    now_iso = datetime.now(timezone.utc).isoformat()
    posted_ats, staleness_states = _db_read_jobs(db_path)
    feed_rows = _db_read_feed(db_path, user_id)
    action_rows = _db_read_actions(db_path, user_id)

    fresh = freshness_stats(posted_ats, now_iso)
    ghost = ghost_rate(staleness_states)
    judge = judge_agreement(feed_rows)
    actions = actions_precision(action_rows)

    _print_section(
        "FRESHNESS (jobs catalog)",
        [
            ("total jobs", str(fresh["count"])),
            ("jobs with posted_at", str(fresh["with_date"])),
            ("median age (days)", _fmt_val(fresh["median_age_days"])),
            ("% posted last 24h", f"{_fmt_val(fresh['pct_last_24h'])} %"),
            ("% posted last 7d", f"{_fmt_val(fresh['pct_last_7d'])} %"),
        ],
    )

    _print_section(
        "ACCURACY -- Ghost rate",
        [
            ("total jobs", str(ghost["total"])),
            ("ghost / expired", str(ghost["ghosts"])),
            ("ghost rate", f"{_fmt_val(ghost['pct'])} %"),
        ],
    )

    _print_section(
        "ACCURACY -- LLM judge agreement",
        [
            ("feed rows read", str(len(feed_rows))),
            ("rows judged by LLM", str(judge["n_judged"])),
            ("keyword <-> LLM Pearson r", _fmt_val(judge["pearson"])),
            ("top-10 keyword = good-fit LLM %", _fmt_val(judge["top10_goodfit_pct"])),
        ],
    )

    _print_section(
        "ACCURACY -- User actions precision",
        [
            ("action rows", str(actions["n"])),
            ("top-decile engage rate", _fmt_val(actions["top_decile_engage_rate"])),
            ("bottom-decile engage rate", _fmt_val(actions["bottom_decile_engage_rate"])),
            ("lift (top / bottom)", _fmt_val(actions["lift"])),
        ],
    )

    # -----------------------------------------------------------------------
    # Step 4 — Evaluate targets
    # -----------------------------------------------------------------------
    collected_metrics: dict[str, float | None] = {
        "feed_keyword_p95_ms": kw_pct.get(95),
        "feed_hybrid_p95_ms": hybrid_pct.get(95),
        "ghost_rate_pct": ghost["pct"] if ghost["total"] > 0 else None,
        "judge_top10_goodfit_pct": judge["top10_goodfit_pct"],
    }

    eval_rows = evaluate_targets(collected_metrics, DEFAULT_TARGETS)
    all_pass = _print_evaluation(eval_rows)

    overall = "PASS" if all_pass else "FAIL"
    print(f"\n{'=' * 60}")
    print(f"  OVERALL: {overall}")
    print(f"{'=' * 60}\n")

    # -----------------------------------------------------------------------
    # Step 5 — Optional JSON output
    # -----------------------------------------------------------------------
    if args.json_out:
        output = {
            "timestamp": now_iso,
            "api": args.api,
            "db": db_path,
            "latency": {
                "status_p50_ms": status_pct.get(50),
                "status_p95_ms": status_pct.get(95),
                "keyword_p50_ms": kw_pct.get(50),
                "keyword_p95_ms": kw_pct.get(95),
                "keyword_p99_ms": kw_pct.get(99),
                "hybrid_p50_ms": hybrid_pct.get(50),
                "hybrid_p95_ms": hybrid_pct.get(95),
                "hybrid_p99_ms": hybrid_pct.get(99),
                "keyword_samples": len(kw_latencies),
                "hybrid_samples": len(hybrid_latencies),
            },
            "freshness": fresh,
            "ghost": ghost,
            "judge": judge,
            "actions": actions,
            "evaluation": eval_rows,
            "overall_pass": all_pass,
        }
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2, default=str)
        print(f"JSON written to {out_path}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
