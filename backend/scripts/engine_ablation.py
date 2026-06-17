"""engine_ablation.py -- read-only engine ablation / evals harness for Job360.

Question it answers: how good is EACH scoring engine, alone and in
combinations, at putting the right jobs on top -- measured against an
INDEPENDENT gold produced by a premium model (Claude subscription), NOT the
free in-app LLM APIs the product's judge uses.

Engines (per the agreed 4-engine model):
  E1 keyword   -- user_feed.score (custom weighted keyword scorer)
  E2 dims      -- sum of the per-dimension score columns on jobs
  E3 bm25      -- BM25 over (title+description) vs the profile query
                  (the offline-computable, distinctive leg of the hybrid
                  engine; full hybrid's vector+rerank legs need the [semantic]
                  stack + a populated index and are out of scope for this
                  read-only tool)
  E4 judge     -- user_feed.llm_fit_score (the in-app LLM judge's verdict)

Each config (single engine or a combination fused via RRF) produces a ranking,
and we score that ranking against the gold with NDCG (is the right job on
top?), Spearman (rank agreement), and precision@k. Output: a leaderboard.

Flow (mirrors accuracy_audit.py):
  1. --emit-prompts <path>   build grading prompts (delegates to accuracy_audit)
  2. (grade them with the Claude subscription → golds.json)
  3. --golds <path>          build per-engine rankings + print the leaderboard

Python 3.9-compatible. Read-only SQLite (mode=ro). Reuses the TDD'd maths in
accuracy_audit (spearman, ndcg) and bm25_rank from src.services.retrieval.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.accuracy_audit import (
    _fetch_feed_rows,
    _fetch_profile,
    _fetch_user_id,
    _open_db_ro,
    emit_prompts,
    ndcg,
    spearman,
)

GOLD_THRESHOLD = 60.0

# The configs evaluated by default. Combinations fuse the per-engine rankings
# with Reciprocal Rank Fusion. The 3+4 vs 1+3+4 vs 1+4 comparison is what tells
# you whether Engine 1 is redundant once Engine 3 (BM25) is present.
DEFAULT_CONFIGS = [
    {"name": "E1 keyword", "engines": ["keyword"]},
    {"name": "E2 dimensions", "engines": ["dims"]},
    {"name": "E3 hybrid(full)", "engines": ["hybrid"]},  # keyword+BM25+vector+rerank
    {"name": "  E3 bm25-only", "engines": ["bm25"]},  # diagnostic: BM25 leg alone
    {"name": "E4 judge", "engines": ["judge"]},
    {"name": "E1+E4", "engines": ["keyword", "judge"]},
    {"name": "E3+E4", "engines": ["hybrid", "judge"]},
    {"name": "E1+E3", "engines": ["keyword", "hybrid"]},
    {"name": "E2+E3", "engines": ["dims", "hybrid"]},
    {"name": "E1+E2+E3", "engines": ["keyword", "dims", "hybrid"]},
    {"name": "E1+E2+E4", "engines": ["keyword", "dims", "judge"]},
    {"name": "E1+E3+E4", "engines": ["keyword", "hybrid", "judge"]},
    {"name": "All (1+2+3+4)", "engines": ["keyword", "dims", "hybrid", "judge"]},
]


# =========================================================================== #
#  Pure functions (TDD'd first)                                                #
# =========================================================================== #


def _normalize_golds(golds: dict) -> dict:
    """Return ``{str(job_id): float_fit}`` from a golds dict whose values are
    either ``{"fit": int, ...}`` dicts or bare numbers."""
    out: dict = {}
    for k, v in golds.items():
        if isinstance(v, dict):
            if "fit" in v:
                out[str(k)] = float(v["fit"])
        else:
            out[str(k)] = float(v)
    return out


def scores_to_ranking(scores: dict) -> list:
    """Order job ids by score descending. ``None`` scores are dropped. Ties
    keep insertion order (Python's sort is stable)."""
    items = [(jid, s) for jid, s in scores.items() if s is not None]
    items.sort(key=lambda kv: -float(kv[1]))
    return [jid for jid, _s in items]


def ranking_to_scores(ranking: list) -> dict:
    """Convert an explicit ranking (best first) into descending pseudo-scores.

    Lets a pre-ordered ranking — e.g. the full Engine-3 hybrid, which produces
    an order rather than per-job scalars — slot into the score-map model used
    by ``evaluate_config`` / ``combine_rrf``.
    """
    n = len(ranking)
    return {jid: float(n - i) for i, jid in enumerate(ranking)}


def dim_only_score(breakdown) -> float:
    """Engine 2 in isolation: the enrichment dimension contribution ONLY
    (seniority + salary + visa + workplace).

    NOT ``match_score`` — match_score also contains the keyword components, and
    the stored jobs-table dim columns (role/skill/…) *sum to* match_score, so
    summing them just reproduces the keyword score (the bug that made the old
    "E2" row mirror E1). This isolates the four enrichment dims so E2 is an
    honest, independent engine.
    """
    return float(
        getattr(breakdown, "seniority_score", 0)
        + getattr(breakdown, "salary_score", 0)
        + getattr(breakdown, "visa_score", 0)
        + getattr(breakdown, "workplace_score", 0)
    )


def precision_at_k(
    ranking: list,
    golds: dict,
    k: int,
    gold_threshold: float = GOLD_THRESHOLD,
) -> float | None:
    """Fraction of the top-``k`` ranked jobs whose gold fit >= threshold.

    Returns None for an empty ranking. Jobs without a gold count as not-good.
    """
    top = ranking[:k]
    if not top:
        return None
    g = _normalize_golds(golds) if golds else {}
    hits = sum(1 for jid in top if g.get(str(jid), 0.0) >= gold_threshold)
    return hits / len(top)


def evaluate_ranking(
    ranking: list,
    golds: dict,
    *,
    k: int | None = None,
    gold_threshold: float = GOLD_THRESHOLD,
) -> dict:
    """Score one ranking against the gold.

    Returns ``{n, ndcg, spearman, precision_at_k}``. Only ranked ids that have
    a gold are scored; ``n`` is that overlap count. All metrics are None when
    there is no overlap.
    """
    g = _normalize_golds(golds)
    ranked = [jid for jid in ranking if str(jid) in g]
    n = len(ranked)
    if n == 0:
        return {"n": 0, "ndcg": None, "spearman": None, "precision_at_k": None}

    ranked_golds = [g[str(jid)] for jid in ranked]
    ndcg_val = ndcg(ranked_golds, k)

    # Spearman between rank position (higher = better) and the gold fit.
    pairs = [(float(n - idx), ranked_golds[idx]) for idx in range(n)]
    sp = spearman(pairs)

    prec = precision_at_k(ranked, g, k or n, gold_threshold)
    return {"n": n, "ndcg": ndcg_val, "spearman": sp, "precision_at_k": prec}


def combine_rrf(rankings: list, k: int = 60) -> list:
    """Fuse several ranked id lists into one via Reciprocal Rank Fusion."""
    from src.services.retrieval import reciprocal_rank_fusion  # noqa: PLC0415

    fused = reciprocal_rank_fusion(rankings, k=k)
    return [item for item, _score in fused]


def evaluate_config(
    config: dict,
    engine_scores: dict,
    golds: dict,
    *,
    k: int | None = None,
    rrf_k: int = 60,
) -> dict:
    """Build a ranking for one config (single engine or RRF combo) and score it.

    ``config`` = ``{"name": str, "engines": [engine_key, ...]}``.
    ``engine_scores`` = ``{engine_key: {job_id: score|None}}``.
    """
    engines = config["engines"]
    if len(engines) == 1:
        ranking = scores_to_ranking(engine_scores.get(engines[0], {}))
    else:
        rankings = [scores_to_ranking(engine_scores.get(e, {})) for e in engines]
        ranking = combine_rrf(rankings, k=rrf_k)
    metrics = evaluate_ranking(ranking, golds, k=k)
    return {"name": config["name"], **metrics}


# =========================================================================== #
#  Engine-score extraction (from DB rows + computed BM25)                      #
# =========================================================================== #


def _bm25_scores(profile: dict, feed_rows: list[dict]) -> dict:
    """Compute a BM25 score per job from the profile query vs (title+desc)."""
    from src.services.retrieval import bm25_rank  # noqa: PLC0415

    query = " ".join(
        (profile.get("titles") or []) + (profile.get("skills") or [])
    ).strip()
    if not query:
        return {}
    docs = [
        (row["job_id"], f"{row.get('title', '')} {row.get('description', '')}")
        for row in feed_rows
    ]
    return {jid: score for jid, score in bm25_rank(query, docs)}


def _hybrid_ranking(feed_rows: list[dict], hybrid_profile) -> list:
    """Full Engine-3 ranking via the LIVE retrieval stack: keyword + BM25 +
    vector(ANN) + cross-encoder rerank, fused by RRF.

    Reuses ``jobs.py::_maybe_apply_hybrid_reorder`` so the harness measures
    EXACTLY what production serves on ``?mode=hybrid``. Requires
    SEMANTIC_ENABLED + a populated vector index + the ``[semantic]`` extra;
    otherwise it degrades to keyword order (and this row collapses to E1).

    The feed rows key the job id as ``job_id``; the live helper expects ``id``,
    so we remap before calling it. Returns job ids, best first.
    """
    from src.api.routes.jobs import _maybe_apply_hybrid_reorder  # noqa: PLC0415

    rows = []
    for r in feed_rows:
        rr = dict(r)
        rr["id"] = r.get("id", r.get("job_id"))
        rows.append(rr)
    reordered = _maybe_apply_hybrid_reorder(rows, profile=hybrid_profile)
    return [r["id"] for r in reordered if r.get("id") is not None]


def _dim_scores(feed_rows: list[dict], hybrid_profile) -> dict:
    """Engine 2 score per job — the enrichment dims (seniority/salary/visa/
    workplace) computed LIVE, with ``job.id`` set so the enrichment lookup hits.

    Uses the default-DB enrichment + the real profile preferences. Returns
    ``{job_id: dim_sum}``. With a profile that has no differentiating
    preferences the dims come out near-constant (a known limit, not a bug).
    """
    import asyncio  # noqa: PLC0415

    from src.api.routes.jobs import _row_to_scoring_job  # noqa: PLC0415
    from src.core.settings import DB_PATH  # noqa: PLC0415
    from src.repositories.database import JobDatabase  # noqa: PLC0415
    from src.services.job_enrichment import (  # noqa: PLC0415
        ENRICHMENT_ENABLED,
        _build_enrichment_lookup,
    )
    from src.services.profile.keyword_generator import generate_search_config  # noqa: PLC0415
    from src.services.skill_matcher import JobScorer  # noqa: PLC0415

    async def _load() -> dict:
        db = JobDatabase(str(DB_PATH))
        await db.init_db()
        try:
            return await _build_enrichment_lookup(db._conn) if ENRICHMENT_ENABLED else {}
        finally:
            await db.close()

    enrichment = asyncio.run(_load())
    scorer = JobScorer(
        generate_search_config(hybrid_profile),
        user_preferences=hybrid_profile.preferences,
        enrichment_lookup=lambda j: enrichment.get(getattr(j, "id", None)),
    )
    out: dict = {}
    for r in feed_rows:
        row = dict(r)
        row["id"] = r.get("id", r.get("job_id"))
        out[r["job_id"]] = dim_only_score(scorer.score(_row_to_scoring_job(row)))
    return out


def build_engine_scores(profile: dict, feed_rows: list[dict], *, hybrid_profile=None) -> dict:
    """Build ``{engine_key: {job_id: score|None}}`` per engine.

    ``keyword`` / ``judge`` from stored values; ``bm25`` from a pure BM25 pass.
    When ``hybrid_profile`` (a real ``UserProfile``) is given, ``dims`` is the
    isolated Engine-2 enrichment-dimension score and ``hybrid`` is the FULL
    Engine-3 ranking (keyword+BM25+vector+rerank) — both computed live.
    """
    keyword = {r["job_id"]: r.get("keyword_score") for r in feed_rows}
    judge = {r["job_id"]: r.get("llm_fit_score") for r in feed_rows}
    bm25 = _bm25_scores(profile, feed_rows)
    scores = {"keyword": keyword, "bm25": bm25, "judge": judge}
    if hybrid_profile is not None:
        scores["dims"] = _dim_scores(feed_rows, hybrid_profile)
        scores["hybrid"] = ranking_to_scores(_hybrid_ranking(feed_rows, hybrid_profile))
    else:
        scores["dims"] = {}
    return scores


# =========================================================================== #
#  Reporting                                                                   #
# =========================================================================== #


def _fmt(v: float | None, decimals: int = 3) -> str:
    return "N/A" if v is None else f"{v:.{decimals}f}"


def run_leaderboard(
    profile: dict,
    feed_rows: list[dict],
    golds: dict,
    configs: list[dict] | None = None,
    *,
    hybrid_profile=None,
) -> list[dict]:
    """Evaluate every config and return rows sorted by NDCG (best first).

    ``hybrid_profile`` (a real ``UserProfile``) enables the full Engine-3
    hybrid row; without it the ``hybrid`` configs collapse to empty.
    """
    cfgs = configs or DEFAULT_CONFIGS
    engine_scores = build_engine_scores(profile, feed_rows, hybrid_profile=hybrid_profile)
    rows = [evaluate_config(c, engine_scores, golds) for c in cfgs]
    rows.sort(key=lambda r: (r["ndcg"] is not None, r["ndcg"] or 0.0), reverse=True)
    return rows


def _print_leaderboard(rows: list[dict], gold_count: int, out: object) -> None:
    def _p(*args: object) -> None:
        print(*args, file=out)

    _p("=" * 74)
    _p("JOB360 ENGINE ABLATION  (each config's ranking vs the Claude gold)")
    _p("=" * 74)
    _p(f"Gold-graded jobs: {gold_count}\n")
    _p(f"{'CONFIG':<18}{'n':>4}{'NDCG':>9}{'Spearman':>11}{'Prec@k':>9}")
    _p("-" * 74)
    for r in rows:
        _p(
            f"{r['name']:<18}{r['n']:>4}"
            f"{_fmt(r['ndcg']):>9}{_fmt(r['spearman']):>11}"
            f"{_fmt(r['precision_at_k']):>9}"
        )
    _p("-" * 74)
    _p(
        "NDCG = is the right job on top (1.0 best). Spearman = rank agreement.\n"
        "Compare E3+E4 vs E1+E3+E4 vs E1+E4: if dropping E1 barely moves NDCG,\n"
        "Engine 1 is redundant once Engine 3 (BM25) is present."
    )
    _p("=" * 74)


# =========================================================================== #
#  CLI                                                                         #
# =========================================================================== #


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="engine_ablation",
        description="Read-only engine ablation harness for Job360.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--email", metavar="EMAIL", help="User email to look up.")
    p.add_argument("--user-id", metavar="UUID", help="User UUID (alt to --email).")
    p.add_argument("--db", metavar="PATH", default="data/jobs.db", help="Path to jobs.db.")
    p.add_argument(
        "--emit-prompts",
        metavar="OUT.json",
        help="Write grading prompts (shared with accuracy_audit) then exit.",
    )
    p.add_argument(
        "--golds",
        metavar="GOLDS.json",
        help='Gold scores JSON: {"<job_id>": {"fit": 0-100, "reason": "..."}}.',
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            _ = exc

    args = _parse_args(argv)

    script_dir = Path(__file__).resolve().parent.parent  # backend/
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = script_dir / db_path
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _open_db_ro(str(db_path))
    try:
        if args.user_id:
            user_id: str | None = args.user_id
        elif args.email:
            user_id = _fetch_user_id(conn, args.email)
            if not user_id:
                print(f"ERROR: No user found with email {args.email!r}", file=sys.stderr)
                sys.exit(1)
        else:
            rows = conn.execute("SELECT id, email FROM users WHERE deleted_at IS NULL").fetchall()
            print(f"No --email/--user-id. Users in DB: {len(rows)}")
            for uid, em in rows:
                print(f"  {em!r}  id={uid}")
            return
    finally:
        conn.close()

    if args.emit_prompts:
        n = emit_prompts(str(db_path), user_id, args.emit_prompts)
        print(f"Written {n} prompts to {args.emit_prompts}")
        return

    if not args.golds:
        print("Pass --emit-prompts <file> to build prompts, then --golds <file> to rank.")
        return

    raw = json.loads(Path(args.golds).read_text(encoding="utf-8"))
    golds = {str(k): v for k, v in raw.items()}

    conn2 = _open_db_ro(str(db_path))
    try:
        profile = _fetch_profile(conn2, user_id)
        feed_rows = _fetch_feed_rows(conn2, user_id)
    finally:
        conn2.close()

    # Real UserProfile (DB-backed) enables the full Engine-3 hybrid row — it
    # supplies the cv_data the semantic query vector + cross-encoder need.
    hybrid_profile = None
    try:
        from src.services.profile.storage import load_profile  # noqa: PLC0415

        hybrid_profile = load_profile(user_id)
    except Exception as exc:  # noqa: BLE001 — hybrid row is optional
        print(f"(hybrid row disabled — could not load profile: {exc})", file=sys.stderr)

    rows = run_leaderboard(profile, feed_rows, golds, hybrid_profile=hybrid_profile)
    _print_leaderboard(rows, gold_count=len(_normalize_golds(golds)), out=sys.stdout)


if __name__ == "__main__":
    main()
