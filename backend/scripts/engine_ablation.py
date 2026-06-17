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
    # E2+E3+E4 = the "clean pipeline": search + dims + judge, dropping keyword
    # (E1 is redundant inside the hybrid). Does removing the double-count help?
    {"name": "E2+E3+E4", "engines": ["dims", "hybrid", "judge"]},
    # E2+E4 = preference + judge with NO search — is retrieval essential?
    {"name": "E2+E4", "engines": ["dims", "judge"]},
    # E1+E2 = lightweight pair (no embeddings, no LLM) — cheap/fast baseline.
    {"name": "E1+E2", "engines": ["keyword", "dims"]},
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


def _position_gold_pairs(ranking: list, golds: dict) -> list:
    """For each gold-graded job, its ``(position, gold_fit)`` in the config's
    gold-restricted ranking (position 0 = ranked best)."""
    g = _normalize_golds(golds)
    ranked = [jid for jid in ranking if str(jid) in g]
    return [(i, g[str(jid)]) for i, jid in enumerate(ranked)]


def _ndcg_from_pairs(pairs: list, k=None):
    seq = [gold for _pos, gold in sorted(pairs, key=lambda pg: pg[0])]
    return ndcg(seq, k)


def _spearman_from_pairs(pairs: list):
    n = len(pairs)
    return spearman([(float(n - pos), gold) for pos, gold in pairs])


def _percentile_ci(values: list, lo: float = 0.025, hi: float = 0.975):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return (None, None)
    n = len(vals)
    li = max(0, min(n - 1, int(lo * n)))
    hi_i = max(0, min(n - 1, int(hi * n) - 1))
    return (vals[li], vals[hi_i])


_STRONG_METRICS = ("ndcg", "ndcg@5", "ndcg@10", "spearman")


def _metric_from_pairs(metric: str, pairs: list):
    if metric == "ndcg":
        return _ndcg_from_pairs(pairs)
    if metric == "ndcg@5":
        return _ndcg_from_pairs(pairs, 5)
    if metric == "ndcg@10":
        return _ndcg_from_pairs(pairs, 10)
    if metric == "spearman":
        return _spearman_from_pairs(pairs)
    raise ValueError(f"unknown metric {metric!r}")


def evaluate_ranking_strong(ranking: list, golds: dict, *, n_resamples: int = 2000, seed: int = 12345) -> dict:
    """Point estimate + bootstrap 95% CI for NDCG, NDCG@5, NDCG@10, Spearman.

    Bootstrap = resample the gold-graded jobs with replacement ``n_resamples``
    times (seeded → reproducible); the 2.5/97.5 percentiles of each metric form
    the 95% CI. A tight CI = a trustworthy number; a wide CI = the sample is too
    small to trust the point estimate.
    """
    import random  # noqa: PLC0415

    pairs = _position_gold_pairs(ranking, golds)
    n = len(pairs)
    if n == 0:
        return {"n": 0, **{m: {"point": None, "ci": (None, None)} for m in _STRONG_METRICS}}

    point = {m: _metric_from_pairs(m, pairs) for m in _STRONG_METRICS}
    rng = random.Random(seed)
    samples = {m: [] for m in _STRONG_METRICS}
    for _ in range(n_resamples):
        rs = [pairs[rng.randrange(n)] for _ in range(n)]
        for m in _STRONG_METRICS:
            samples[m].append(_metric_from_pairs(m, rs))

    out = {"n": n}
    for m in _STRONG_METRICS:
        out[m] = {"point": point[m], "ci": _percentile_ci(samples[m])}
    return out


def compare_configs(
    ranking_a: list,
    ranking_b: list,
    golds: dict,
    *,
    metric: str = "ndcg",
    n_resamples: int = 2000,
    seed: int = 12345,
) -> dict:
    """Paired bootstrap significance test: is config A's ``metric`` really
    higher than B's, or is the gap noise?

    Resamples the jobs golded in BOTH rankings, recomputes (metricA - metricB)
    on each resample, and returns the 95% CI of the difference.
    ``significant`` is True when that CI excludes 0.
    """
    import random  # noqa: PLC0415

    g = _normalize_golds(golds)
    ranked_a = [jid for jid in ranking_a if str(jid) in g]
    ranked_b = [jid for jid in ranking_b if str(jid) in g]
    pos_a = {jid: i for i, jid in enumerate(ranked_a)}
    pos_b = {jid: i for i, jid in enumerate(ranked_b)}
    triples = [(pos_a[jid], pos_b[jid], g[str(jid)]) for jid in ranked_a if jid in pos_b]
    n = len(triples)
    if n == 0:
        return {"diff": 0.0, "ci": (None, None), "significant": False, "n": 0}

    def _a(tr):
        return _metric_from_pairs(metric, [(pa, gold) for pa, _pb, gold in tr]) or 0.0

    def _b(tr):
        return _metric_from_pairs(metric, [(pb, gold) for _pa, pb, gold in tr]) or 0.0

    point_diff = _a(triples) - _b(triples)
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_resamples):
        rs = [triples[rng.randrange(n)] for _ in range(n)]
        diffs.append(_a(rs) - _b(rs))
    lo, hi = _percentile_ci(diffs)
    significant = lo is not None and (lo > 0 or hi < 0)
    return {"diff": point_diff, "ci": (lo, hi), "significant": significant, "n": n}


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


def _config_ranking(config: dict, engine_scores: dict, rrf_k: int = 60) -> list:
    """Produce one config's ranking (single engine or RRF-fused combo)."""
    engines = config["engines"]
    if len(engines) == 1:
        return scores_to_ranking(engine_scores.get(engines[0], {}))
    return combine_rrf([scores_to_ranking(engine_scores.get(e, {})) for e in engines], k=rrf_k)


def run_leaderboard_strong(
    profile: dict,
    feed_rows: list[dict],
    golds: dict,
    configs: list[dict] | None = None,
    *,
    hybrid_profile=None,
    n_resamples: int = 2000,
    seed: int = 12345,
) -> tuple:
    """Rich leaderboard: each config with point + bootstrap-CI metrics
    (NDCG, NDCG@5, NDCG@10, Spearman), its top-5 gold-ranked jobs, plus a
    significance comparison of the best (by NDCG) config vs every other.

    Returns ``(rows, significance, best_name)``.
    """
    cfgs = configs or DEFAULT_CONFIGS
    engine_scores = build_engine_scores(profile, feed_rows, hybrid_profile=hybrid_profile)
    g = _normalize_golds(golds)
    rankings: dict = {}
    rows: list[dict] = []
    for c in cfgs:
        ranking = _config_ranking(c, engine_scores)
        rankings[c["name"]] = ranking
        strong = evaluate_ranking_strong(ranking, golds, n_resamples=n_resamples, seed=seed)
        top5 = [(jid, g[str(jid)]) for jid in ranking if str(jid) in g][:5]
        rows.append({"name": c["name"], "strong": strong, "top5": top5})

    rows.sort(
        key=lambda r: (r["strong"]["ndcg"]["point"] is not None, r["strong"]["ndcg"]["point"] or 0.0),
        reverse=True,
    )
    best = rows[0]["name"]
    significance = {
        r["name"]: compare_configs(
            rankings[best], rankings[r["name"]], golds, metric="ndcg", n_resamples=n_resamples, seed=seed
        )
        for r in rows[1:]
    }
    return rows, significance, best


def _fmt_ci(metric: dict) -> str:
    pt = metric.get("point")
    lo, hi = metric.get("ci", (None, None))
    if pt is None:
        return "  N/A"
    if lo is None:
        return f"{pt:.3f}"
    return f"{pt:.3f} [{lo:.2f},{hi:.2f}]"


def _print_strong_report(rows: list, significance: dict, best: str, gold_count: int, title_map: dict, out) -> None:
    def _p(*a):
        print(*a, file=out)

    _p("=" * 92)
    _p("JOB360 ENGINE ABLATION — STRONG (bootstrap 95% CIs + significance)")
    _p("=" * 92)
    _p(f"Gold-graded jobs (the Claude benchmark): {gold_count}")
    _p("NDCG = are the BEST jobs at the top (top-heavy; your priority). NDCG@5/@10 = top-5/10 only.")
    _p("Spearman = is the WHOLE list in order. [lo,hi] = 95% confidence interval (bootstrap).\n")
    _p(f"{'CONFIG':<16}{'NDCG [95% CI]':<22}{'NDCG@5':<10}{'NDCG@10':<10}{'Spearman [95% CI]':<22}{'n':>4}")
    _p("-" * 92)
    for r in rows:
        s = r["strong"]
        _p(
            f"{r['name']:<16}{_fmt_ci(s['ndcg']):<22}"
            f"{(_fmt_ci(s['ndcg@5']).split(' ')[0]):<10}{(_fmt_ci(s['ndcg@10']).split(' ')[0]):<10}"
            f"{_fmt_ci(s['spearman']):<22}{s['n']:>4}"
        )
    _p("-" * 92)
    _p(f"\nSIGNIFICANCE — is the top config ({best}) really better on NDCG, or noise?")
    _p("(paired bootstrap of the NDCG difference; SIGNIFICANT = 95% CI of the gap excludes 0)\n")
    for name, cmp in significance.items():
        lo, hi = cmp["ci"]
        verdict = "SIGNIFICANT" if cmp["significant"] else "not significant (overlaps)"
        ci_txt = f"[{lo:.3f},{hi:.3f}]" if lo is not None else "N/A"
        _p(f"  {best} vs {name:<16} ΔNDCG={cmp['diff']:+.3f} CI={ci_txt:<18} {verdict}")
    _p("\n" + "=" * 92)
    _p("TOP-5 JOBS PER CONFIG (job_id · gold-fit · title) — the 'why'")
    _p("=" * 92)
    for r in rows:
        _p(f"\n[{r['name']}]")
        for jid, fit in r["top5"]:
            _p(f"   {int(fit):>3}  #{jid}  {title_map.get(jid, '')[:60]}")
    _p("=" * 92)


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

    title_map = {r.get("job_id", r.get("id")): (r.get("title") or "") for r in feed_rows}
    rows, significance, best = run_leaderboard_strong(profile, feed_rows, golds, hybrid_profile=hybrid_profile)
    _print_strong_report(
        rows, significance, best, gold_count=len(_normalize_golds(golds)), title_map=title_map, out=sys.stdout
    )


if __name__ == "__main__":
    main()
