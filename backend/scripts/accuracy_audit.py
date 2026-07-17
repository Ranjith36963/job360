"""accuracy_audit.py -- read-only scoring accuracy auditor for Job360.

Three modes (pick via CLI flags):
  1. --emit-prompts <path.json>   Build grading prompts and write them out.
  2. --golds <path.json>          Load gold scores, join to DB, print report.
  3. (neither flag)               Dry-run: show what would be graded.

Python 3.9-compatible -- uses ``from __future__ import annotations`` throughout.
No numpy/scipy/sklearn.  All stats are hand-rolled.
Read-only SQLite access (mode=ro via URI).
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path for src.*

import sys
from pathlib import Path

from src.repositories import pgsync  # noqa: E402

# =========================================================================== #
#  Pure maths helpers (TDD'd first)                                            #
# =========================================================================== #


def _rank_with_ties(values: list[float]) -> list[float]:
    """Return fractional (average) ranks for a list of values (ascending)."""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks: list[float] = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and values[indexed[j + 1]] == values[indexed[j]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1  # 1-based average rank
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r between two equal-length lists.  Returns None on zero variance."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom == 0.0:
        return None
    return num / denom


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Spearman rank correlation between two paired sequences.

    Handles ties by assigning average ranks (fractional rank method).
    Returns None when fewer than 2 valid pairs or when either variable has
    zero variance (correlation is undefined in those cases).
    """
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx = _rank_with_ties(xs)
    ry = _rank_with_ties(ys)
    return _pearson(rx, ry)


def ndcg(ranked_golds: list[float], k: int | None = None) -> float | None:
    """Normalised Discounted Cumulative Gain.

    Parameters
    ----------
    ranked_golds:
        Gold scores **ordered by the app's ranking** (best app-rank first).
    k:
        Truncation cutoff.  None means use the full list.

    Returns
    -------
    NDCG in [0, 1], or None when the input is empty.
    """
    if not ranked_golds:
        return None
    cutoff = len(ranked_golds) if k is None else min(k, len(ranked_golds))

    def _dcg(scores: list[float], top: int) -> float:
        return sum(scores[i] / math.log2(i + 2) for i in range(top))

    actual_dcg = _dcg(ranked_golds, cutoff)
    ideal_golds = sorted(ranked_golds, reverse=True)
    ideal_dcg = _dcg(ideal_golds, cutoff)
    if ideal_dcg == 0.0:
        return 1.0  # all zeros -- trivially perfect
    return actual_dcg / ideal_dcg


def bucket_precision_recall(
    rows: list[dict],
    app_threshold: float,
    gold_threshold: float,
) -> dict:
    """Binary classification metrics treating scores >= threshold as 'positive'.

    Rows with ``app`` value of None are silently skipped.

    Returns a dict with keys: tp, fp, fn, precision, recall, f1.
    precision/recall/f1 may be None when the denominator is zero.
    """
    tp = fp = fn = 0
    for row in rows:
        app = row.get("app")
        gold = row.get("gold")
        if app is None or gold is None:
            continue
        app_pos = app >= app_threshold
        gold_pos = gold >= gold_threshold
        if app_pos and gold_pos:
            tp += 1
        elif app_pos and not gold_pos:
            fp += 1
        elif not app_pos and gold_pos:
            fn += 1
        # tn: not app_pos and not gold_pos -- ignored for these metrics

    precision: float | None
    recall: float | None
    f1: float | None

    predicted_pos = tp + fp
    actual_pos = tp + fn
    precision = tp / predicted_pos if predicted_pos > 0 else None
    recall = tp / actual_pos if actual_pos > 0 else None

    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = None

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def top_mismatches(rows_with_meta: list[dict], n: int) -> list[dict]:
    """Return the n rows with the largest absolute gap between app and gold scores.

    Rows where ``app`` is None are skipped.
    Each returned dict gains a ``gap`` key (positive integer).
    """
    valid = [r for r in rows_with_meta if r.get("app") is not None]
    sorted_rows = sorted(valid, key=lambda r: abs(r["app"] - r["gold"]), reverse=True)
    result = []
    for row in sorted_rows[:n]:
        entry = dict(row)
        entry["gap"] = int(abs(row["app"] - row["gold"]))
        result.append(entry)
    return result


def summary_stats(golds: list[float]) -> dict:
    """Descriptive statistics for the gold score distribution.

    Buckets:
      strong   >= 70
      moderate  40-69
      weak      <  40
    """
    if not golds:
        return {
            "count": 0,
            "mean": None,
            "min": None,
            "max": None,
            "dist": {"strong": 0, "moderate": 0, "weak": 0},
        }
    count = len(golds)
    mean = sum(golds) / count
    strong = sum(1 for g in golds if g >= 70)
    moderate = sum(1 for g in golds if 40 <= g < 70)
    weak = sum(1 for g in golds if g < 40)
    return {
        "count": count,
        "mean": mean,
        "min": min(golds),
        "max": max(golds),
        "dist": {"strong": strong, "moderate": moderate, "weak": weak},
    }


# =========================================================================== #
#  Prompt builder (pure function -- TDD'd)                                     #
# =========================================================================== #


def build_prompt(profile: dict, job: dict) -> str:
    """Build a deterministic grading prompt for a single (profile, job) pair.

    Parameters
    ----------
    profile:
        Dict with keys: titles, skills, seniority, locations, notes.
        All keys are optional; missing ones degrade gracefully.
    job:
        Dict with keys: job_id, title, company, location, description.
        All keys are optional.

    Returns
    -------
    A self-contained string asking the grader to return
    {"fit": <int 0-100>, "reason": "<short>"}.
    """
    titles = ", ".join(profile.get("titles") or []) or "(not specified)"
    skills = ", ".join(profile.get("skills") or []) or "(not specified)"
    seniority = profile.get("seniority") or "(not specified)"
    locations = ", ".join(profile.get("locations") or []) or "(not specified)"
    notes = profile.get("notes") or ""

    job_title = job.get("title") or "(unknown title)"
    company = job.get("company") or "(unknown company)"
    job_location = job.get("location") or "(unknown location)"
    description = (job.get("description") or "")[:1500].strip()

    candidate_block = (
        f"TARGET TITLES: {titles}\n"
        f"SKILLS: {skills}\n"
        f"SENIORITY: {seniority}\n"
        f"PREFERRED LOCATIONS: {locations}\n"
    )
    if notes:
        candidate_block += f"ADDITIONAL PREFERENCES: {notes}\n"

    job_block = (
        f"JOB TITLE: {job_title}\n"
        f"COMPANY: {company}\n"
        f"LOCATION: {job_location}\n"
        f"DESCRIPTION (excerpt):\n{description}\n"
    )

    return (
        "You are an independent job-fit grader.\n\n"
        "## CANDIDATE PROFILE\n"
        f"{candidate_block}\n"
        "## JOB POSTING\n"
        f"{job_block}\n"
        "## TASK\n"
        "Rate 0-100 how good a fit this job is for THIS candidate.\n"
        "Consider: (1) role/domain match, (2) seniority fit, "
        "(3) skills overlap, (4) location/remote compatibility.\n\n"
        'Respond ONLY as JSON: {"fit": <int 0-100>, "reason": "<one-line explanation>"}'
    )


# =========================================================================== #
#  DB reader (read-only)                                                       #
# =========================================================================== #


def _open_db_ro(db_path: str) -> pgsync.Connection:
    """Open SQLite in read-only mode via URI."""
    uri = f"file:{db_path}?mode=ro"
    return pgsync.connect(uri, uri=True)


def _fetch_user_id(conn: pgsync.Connection, email: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM users WHERE email = ? AND deleted_at IS NULL", (email,)
    ).fetchone()
    return row[0] if row else None


def _fetch_profile(conn: pgsync.Connection, user_id: str) -> dict:
    """Return profile dict suitable for build_prompt().  Graceful on missing rows."""
    row = conn.execute(
        "SELECT cv_data, preferences FROM user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return {}
    cv_raw = json.loads(row[0]) if row[0] else {}
    pref_raw = json.loads(row[1]) if row[1] else {}

    titles = list(cv_raw.get("job_titles") or [])
    pref_titles = list(pref_raw.get("target_job_titles") or [])
    all_titles = titles + [t for t in pref_titles if t not in titles]

    skills = list(cv_raw.get("skills") or [])
    extra_skills = list(pref_raw.get("additional_skills") or [])
    all_skills = skills + [s for s in extra_skills if s not in skills]

    seniority = pref_raw.get("experience_level") or cv_raw.get("experience_level") or ""
    locations = list(pref_raw.get("preferred_locations") or [])

    notes_parts: list[str] = []
    wa = pref_raw.get("work_arrangement") or pref_raw.get("remote_preference") or ""
    if wa:
        notes_parts.append(f"work_arrangement={wa}")
    sal_min = pref_raw.get("salary_min")
    if sal_min:
        notes_parts.append(f"salary_min={sal_min}")

    return {
        "titles": all_titles,
        "skills": all_skills[:50],
        "seniority": seniority,
        "locations": locations,
        "notes": ", ".join(notes_parts),
    }


def _fetch_feed_rows(conn: pgsync.Connection, user_id: str) -> list[dict]:
    """Return all active feed rows for the user joined to jobs.

    Columns returned per row:
      job_id, title, company, location, description (first 1500 chars),
      keyword_score (user_feed.score),
      llm_fit_score  (user_feed.llm_fit_score -- may be NULL),
      match_score    (jobs.match_score -- shared catalog last-writer-wins),
      role, skill, seniority_score, experience, credentials,
      location_score, recency, semantic  (dim columns -- may be 0 or absent).
    """
    sql = """
        SELECT
            j.id          AS job_id,
            j.title       AS title,
            j.company     AS company,
            j.location    AS location,
            substr(j.description, 1, 1500) AS description,
            f.score       AS keyword_score,
            f.llm_fit_score AS llm_fit_score,
            j.match_score AS match_score,
            COALESCE(j.role, 0)            AS role,
            COALESCE(j.skill, 0)           AS skill,
            COALESCE(j.seniority_score, 0) AS seniority_score,
            COALESCE(j.experience, 0)      AS experience,
            COALESCE(j.credentials, 0)     AS credentials,
            COALESCE(j.location_score, 0)  AS location_score,
            COALESCE(j.recency, 0)         AS recency,
            COALESCE(j.semantic, 0)        AS semantic
        FROM user_feed f
        JOIN jobs j ON j.id = f.job_id
        WHERE f.user_id = ?
          AND f.status = 'active'
        ORDER BY COALESCE(f.llm_fit_score, f.score) DESC, j.date_found DESC
    """
    try:
        rows = conn.execute(sql, (user_id,)).fetchall()
    except pgsync.OperationalError:
        # Fallback when user_feed or llm_fit_score column not yet present
        sql_fallback = """
            SELECT
                j.id AS job_id, j.title, j.company, j.location,
                substr(j.description, 1, 1500) AS description,
                f.score AS keyword_score,
                NULL AS llm_fit_score,
                j.match_score,
                0,0,0,0,0,0,0,0
            FROM user_feed f
            JOIN jobs j ON j.id = f.job_id
            WHERE f.user_id = ? AND f.status = 'active'
            ORDER BY f.score DESC
        """
        rows = conn.execute(sql_fallback, (user_id,)).fetchall()

    col_names = [
        "job_id", "title", "company", "location", "description",
        "keyword_score", "llm_fit_score", "match_score",
        "role", "skill", "seniority_score", "experience",
        "credentials", "location_score", "recency", "semantic",
    ]
    return [dict(zip(col_names, row)) for row in rows]


# =========================================================================== #
#  Emit-prompts mode                                                           #
# =========================================================================== #


def emit_prompts(db_path: str, user_id: str, out_path: str) -> int:
    """Write a JSON list of {job_id, title, company, prompt} to out_path.

    Returns the number of prompts written.
    """
    conn = _open_db_ro(db_path)
    try:
        profile = _fetch_profile(conn, user_id)
        feed_rows = _fetch_feed_rows(conn, user_id)
    finally:
        conn.close()

    items = []
    for row in feed_rows:
        job_dict = {
            "job_id": row["job_id"],
            "title": row["title"],
            "company": row["company"],
            "location": row["location"],
            "description": row["description"] or "",
        }
        prompt = build_prompt(profile, job_dict)
        items.append(
            {
                "job_id": row["job_id"],
                "title": row["title"],
                "company": row["company"],
                "prompt": prompt,
            }
        )

    Path(out_path).write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(items)


# =========================================================================== #
#  Gold-join + reporting                                                       #
# =========================================================================== #


_SCORE_TYPES: list[tuple[str, str, float]] = [
    # (display_name, row_key, app_threshold)
    ("keyword", "keyword_score", 30.0),
    ("llm_fit", "llm_fit_score", 60.0),
    ("match_score", "match_score", 50.0),
]

GOLD_THRESHOLD = 60.0


def _fmt(v: float | None, decimals: int = 3) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}"


def _print_report(
    feed_rows: list[dict],
    golds: dict[str, dict],
    out: object,
) -> None:
    """Print the comparison report to *out* (a file-like object).

    Parameters
    ----------
    feed_rows:
        Rows from _fetch_feed_rows.
    golds:
        {str(job_id): {"fit": int, "reason": str}}.
    out:
        Output stream (e.g. sys.stdout or io.StringIO).
    """

    def _p(*args: object) -> None:
        print(*args, file=out)

    # ------------------------------------------------------------------ #
    # Build joined rows (one per score type)                               #
    # ------------------------------------------------------------------ #
    gold_scores = [float(v["fit"]) for v in golds.values() if "fit" in v]
    _p("=" * 70)
    _p("JOB360 SCORING ACCURACY AUDIT")
    _p("=" * 70)

    stats = summary_stats(gold_scores)
    _p(
        f"\nGold scores summary:  n={stats['count']}  "
        f"mean={_fmt(stats['mean'])}  "
        f"min={_fmt(stats['min'])}  max={_fmt(stats['max'])}"
    )
    if stats["count"]:
        dist = stats["dist"]
        _p(
            f"  Distribution -- strong(>=70): {dist['strong']}  "
            f"moderate(40-69): {dist['moderate']}  "
            f"weak(<40): {dist['weak']}"
        )

    _p("")

    for display_name, row_key, app_thresh in _SCORE_TYPES:
        _p("-" * 70)
        _p(f"SCORE TYPE: {display_name}  (threshold={app_thresh})")

        joined: list[dict] = []
        for row in feed_rows:
            jid_str = str(row["job_id"])
            if jid_str not in golds:
                continue
            gold_entry = golds[jid_str]
            gold_val = float(gold_entry.get("fit", 0))
            app_val = row.get(row_key)
            app_float: float | None = float(app_val) if app_val is not None else None
            joined.append(
                {
                    "job_id": row["job_id"],
                    "title": row["title"],
                    "app": app_float,
                    "gold": gold_val,
                    "reason": gold_entry.get("reason", ""),
                }
            )

        valid = [r for r in joined if r["app"] is not None]
        _p(
            f"  Compared: {len(valid)} / {len(feed_rows)} feed jobs "
            f"(gold coverage: {len(joined)})"
        )

        if len(valid) < 2:
            _p("  Insufficient data for statistics (need >= 2).")
            continue

        # Spearman
        sp_pairs = [(r["app"], r["gold"]) for r in valid]
        sp = spearman(sp_pairs)
        _p(f"  Spearman vs gold: {_fmt(sp)}")

        # NDCG -- order rows by app score desc, then extract golds in that order
        ordered_by_app = sorted(valid, key=lambda r: r["app"], reverse=True)  # type: ignore[return-value]
        ranked_golds = [r["gold"] for r in ordered_by_app]
        ng = ndcg(ranked_golds)
        _p(f"  NDCG vs gold:     {_fmt(ng)}")

        # Bucket precision/recall
        bpr = bucket_precision_recall(
            valid, app_threshold=app_thresh, gold_threshold=GOLD_THRESHOLD
        )
        prec_str = _fmt(bpr["precision"])
        rec_str = _fmt(bpr["recall"])
        f1_str = _fmt(bpr["f1"])
        _p(
            f"  Bucket P/R/F1:    P={prec_str}  R={rec_str}  F1={f1_str}  "
            f"(tp={bpr['tp']} fp={bpr['fp']} fn={bpr['fn']})"
        )

        # One-line verdict
        sp_label = _fmt(sp) if sp is not None else "N/A"
        _p(f"  VERDICT: {display_name} ranking vs gold -- Spearman {sp_label}")

    # ------------------------------------------------------------------ #
    # Worst mismatches across all score types combined                     #
    # ------------------------------------------------------------------ #
    _p("")
    _p("=" * 70)
    _p("WORST MISMATCHES  (top 10 by max gap across any score type)")
    _p("=" * 70)

    # Build per-job composite mismatch info
    all_mismatches: list[dict] = []
    for row in feed_rows:
        jid_str = str(row["job_id"])
        if jid_str not in golds:
            continue
        gold_entry = golds[jid_str]
        gold_val = float(gold_entry.get("fit", 0))
        max_gap = 0
        for _, row_key, _ in _SCORE_TYPES:
            app_val = row.get(row_key)
            if app_val is not None:
                gap = int(abs(float(app_val) - gold_val))
                max_gap = max(max_gap, gap)
        if max_gap > 0:
            all_mismatches.append(
                {
                    "job_id": row["job_id"],
                    "title": row["title"],
                    "keyword": row.get("keyword_score"),
                    "llm_fit": row.get("llm_fit_score"),
                    "match_score": row.get("match_score"),
                    "gold": gold_val,
                    "reason": gold_entry.get("reason", ""),
                    "max_gap": max_gap,
                }
            )

    all_mismatches.sort(key=lambda r: r["max_gap"], reverse=True)
    for i, mm in enumerate(all_mismatches[:10], 1):
        _p(f"\n  [{i}] {mm['title']} (job_id={mm['job_id']})")
        _p(
            f"      Gold: {mm['gold']}  |  keyword: {mm['keyword']}  "
            f"llm_fit: {mm['llm_fit']}  match_score: {mm['match_score']}"
        )
        _p(f"      Gold reason: {mm['reason']}")

    if not all_mismatches:
        _p("  (no gold overlap found)")

    _p("")
    _p("=" * 70)
    _p("END OF REPORT")
    _p("=" * 70)


# =========================================================================== #
#  CLI                                                                         #
# =========================================================================== #


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="accuracy_audit",
        description=(
            "Read-only accuracy audit for Job360 scoring engines.\n\n"
            "Step 1: python scripts/accuracy_audit.py --email you@example.com "
            "--emit-prompts prompts.json\n"
            "Step 2: (run prompts through Claude to get gold scores)\n"
            "Step 3: python scripts/accuracy_audit.py --email you@example.com "
            "--golds golds.json\n"
            "(or --json-out report.json)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--email",
        metavar="EMAIL",
        help="User email to look up in the DB.",
    )
    p.add_argument(
        "--user-id",
        metavar="UUID",
        help="User UUID (alternative to --email).",
    )
    p.add_argument(
        "--db",
        metavar="PATH",
        default="data/jobs.db",
        help="Path to jobs.db (default: data/jobs.db).",
    )
    p.add_argument(
        "--emit-prompts",
        metavar="OUT.json",
        help="Write grading prompts to this JSON file then exit.",
    )
    p.add_argument(
        "--golds",
        metavar="GOLDS.json",
        help='Path to gold scores JSON: {"<job_id>": {"fit": 0-100, "reason": "..."}}.',
    )
    p.add_argument(
        "--json-out",
        metavar="REPORT.json",
        help="Also write the full report data as JSON to this file.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    # Windows cp1252 safety: reconfigure stdout to UTF-8 if possible.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            # Reconfigure can fail in some embedded/CI contexts; ignore silently.
            _ = exc

    args = _parse_args(argv)

    # Resolve DB path relative to this script's parent (backend/).
    script_dir = Path(__file__).resolve().parent.parent  # backend/
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = script_dir / db_path

    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    # Resolve user identity
    conn = _open_db_ro(str(db_path))
    try:
        user_id: str | None = None
        if args.user_id:
            user_id = args.user_id
        elif args.email:
            user_id = _fetch_user_id(conn, args.email)
            if not user_id:
                print(f"ERROR: No user found with email {args.email!r}", file=sys.stderr)
                sys.exit(1)
        else:
            # Dry-run: list all users
            rows = conn.execute(
                "SELECT id, email FROM users WHERE deleted_at IS NULL"
            ).fetchall()
            print(f"No --email or --user-id given.  Users in DB: {len(rows)}")
            for uid, em in rows:
                feed_count = conn.execute(
                    "SELECT COUNT(*) FROM user_feed WHERE user_id = ? AND status = 'active'",
                    (uid,),
                ).fetchone()
                n = feed_count[0] if feed_count else 0
                print(f"  {em!r}  id={uid}  active_feed_jobs={n}")
            return
    finally:
        conn.close()

    # Emit-prompts mode
    if args.emit_prompts:
        n = emit_prompts(str(db_path), user_id, args.emit_prompts)
        print(f"Written {n} prompts to {args.emit_prompts}")
        return

    # Load golds (if provided)
    golds: dict[str, dict] | None = None
    if args.golds:
        raw = json.loads(Path(args.golds).read_text(encoding="utf-8"))
        # Normalise: values might be plain ints (shorthand) or {fit, reason} dicts
        golds = {}
        for k, v in raw.items():
            if isinstance(v, int):
                golds[str(k)] = {"fit": v, "reason": ""}
            elif isinstance(v, dict):
                golds[str(k)] = v
            else:
                golds[str(k)] = {"fit": int(v), "reason": ""}

    # Fetch data
    conn2 = _open_db_ro(str(db_path))
    try:
        feed_rows = _fetch_feed_rows(conn2, user_id)
    finally:
        conn2.close()

    if golds is None:
        # Dry-run summary
        print(f"Dry run -- {len(feed_rows)} active feed jobs for user {user_id}.")
        print("Pass --emit-prompts <file.json> to build grading prompts.")
        print("Pass --golds <file.json> to generate the comparison report.")
        return

    # Full report
    buf = io.StringIO()
    _print_report(feed_rows, golds, buf)
    report_text = buf.getvalue()
    print(report_text)

    if args.json_out:
        # Serialise structured report data as JSON
        report_data = {
            "feed_row_count": len(feed_rows),
            "gold_count": len(golds),
        }
        Path(args.json_out).write_text(
            json.dumps(report_data, indent=2), encoding="utf-8"
        )
        print(f"JSON report written to {args.json_out}")


if __name__ == "__main__":
    main()
