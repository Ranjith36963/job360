"""Multi-profile engine eval — run every engine + a high-bar gold across the 10
fake technical profiles, then find which engine (alone or combined) is the
robust ship pick across ALL of them.

Pure, tested logic lives here (pooling + aggregation); the orchestration
(set profile → rescore → judge → grade → ablation) is driven per profile and
reuses scripts/engine_ablation.py. The gold for each profile is graded by Claude
(the high bar) on a POOLED set — the union of the top jobs from each engine, the
standard IR way to keep grading effort low without missing what matters.
"""
from __future__ import annotations


def pool_jobs(rankings: dict, k: int) -> list:
    """Union the top-``k`` job ids from each engine's ranking (first-seen order).

    Pooling: to grade efficiently, only judge the jobs that SOME engine ranked
    highly — that's the set that decides the metrics. Misses nothing that any
    engine surfaced; skips the long tail no engine cared about.
    """
    pool: list = []
    seen: set = set()
    for ranking in rankings.values():
        for jid in ranking[:k]:
            if jid not in seen:
                seen.add(jid)
                pool.append(jid)
    return pool


def aggregate(results: list) -> list:
    """Combine per-(profile, config) scores into a cross-profile leaderboard.

    ``results`` = ``[{profile, config, ndcg, spearman}, ...]``. For each config:
    mean NDCG, mean Spearman, and the WORST-CASE NDCG across profiles (the
    fragility signal — a config that wins on average but craters on one profile
    is not robust). Sorted by mean NDCG, then worst-case.
    """
    by_config: dict = {}
    for r in results:
        by_config.setdefault(r["config"], []).append(r)

    out: list = []
    for config, rows in by_config.items():
        ndcgs = [r["ndcg"] for r in rows if r.get("ndcg") is not None]
        sps = [r["spearman"] for r in rows if r.get("spearman") is not None]
        if not ndcgs:
            continue
        out.append(
            {
                "config": config,
                "mean_ndcg": sum(ndcgs) / len(ndcgs),
                "mean_spearman": (sum(sps) / len(sps)) if sps else None,
                "worst_ndcg": min(ndcgs),
                "n_profiles": len(ndcgs),
            }
        )
    out.sort(key=lambda r: (r["mean_ndcg"], r["worst_ndcg"]), reverse=True)
    return out


def results_from_strong(profile_id: str, rows: list) -> list:
    """Flatten a ``run_leaderboard_strong`` rows output into aggregate() input
    for one profile: ``[{profile, config, ndcg, spearman}, ...]`` (point estimates)."""
    flat = []
    for r in rows:
        s = r["strong"]
        flat.append(
            {
                "profile": profile_id,
                "config": r["name"],
                "ndcg": s["ndcg"]["point"],
                "spearman": s["spearman"]["point"],
            }
        )
    return flat
