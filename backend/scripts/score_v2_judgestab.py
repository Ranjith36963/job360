"""Stability test + FIX for the dominant noise source: the LLM judge (E4).

Reads K independent judge runs (eval_v2_judge_runs.json) and:
  1. Reports per-job judge variance (how noisy is E4 job-to-job).
  2. Recomputes the FULL leaderboard for each single judge run -> shows how much
     each config's NDCG swings purely because the judge is stochastic.
  3. Computes the MEDIAN-of-K judge score per job (the stabilized E4) and its
     leaderboard -> the fix: averaging judge runs collapses the variance.

A config is only trustworthy if it's stable across judge runs. Anything whose
rank flips between runs cannot be a basis for keeping/removing an engine.
"""
from __future__ import annotations

import json
import logging
import statistics
import sys

logging.disable(logging.WARNING)

GOLD = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_gold.json"
RUNS = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_judge_runs.json"
UIDS = ["eval-ranjith", "eval-pavan"]
SEED = 20260621


def _leaderboard(es, gold, ea, ranker):
    """Return {config_name: ndcg_point} for one engine-score set."""
    out = {}
    for c in ea.DEFAULT_CONFIGS:
        engines = c["engines"]
        if len(engines) == 1:
            ranking = ranker(es.get(engines[0], {}), SEED)
        else:
            ranking = ea.combine_rrf([ranker(es.get(e, {}), SEED) for e in engines], k=60)
        strong = ea.evaluate_ranking_strong(ranking, gold, n_resamples=1, seed=SEED)
        out[c["name"]] = strong["ndcg"]["point"]
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from scripts.accuracy_audit import _fetch_feed_rows, _fetch_profile, _open_db_ro
    from scripts import engine_ablation as ea
    from scripts.score_v2 import _ranking_v2
    from src.core.settings import DB_PATH
    from src.services.profile.storage import load_profile

    golds_all = json.load(open(GOLD))
    runs_all = json.load(open(RUNS))

    for uid in UIDS:
        gold = golds_all[uid]
        runs = runs_all[uid]  # {job_id: [s1,s2,...]}
        K = max(len(v) for v in runs.values())

        # --- per-job judge variance ---
        stds = [statistics.pstdev(v) for v in runs.values() if len(v) > 1]
        ranges = [max(v) - min(v) for v in runs.values() if len(v) > 1]
        print(f"\n{'='*78}\n{uid} — JUDGE (E4) VARIANCE over {K} runs\n{'='*78}")
        print(f"per-job score std: mean={statistics.mean(stds):.1f} max={max(stds):.1f} "
              f"| per-job range: mean={statistics.mean(ranges):.1f} max={max(ranges):.1f}")

        conn = _open_db_ro(str(DB_PATH))
        try:
            pd = _fetch_profile(conn, uid)
            fr = _fetch_feed_rows(conn, uid)
        finally:
            conn.close()
        hp = load_profile(uid)
        es_base = ea.build_engine_scores(pd, fr, hybrid_profile=hp)

        # --- leaderboard per single run + per median ---
        boards = []  # list of {config: ndcg}
        for k in range(K):
            judge_k = {int(j): (v[k] if k < len(v) else None) for j, v in runs.items()}
            es = dict(es_base); es["judge"] = judge_k
            boards.append(_leaderboard(es, gold, ea, _ranking_v2))
        median_judge = {int(j): statistics.median(v) for j, v in runs.items()}
        es_med = dict(es_base); es_med["judge"] = median_judge
        board_med = _leaderboard(es_med, gold, ea, _ranking_v2)

        configs = list(board_med.keys())
        print(f"\n{uid} — NDCG per config: single-run swing vs MEDIAN-of-{K} (the fix)")
        print(f"{'CONFIG':<18}{'run mean':>9}{'±std':>7}{'[min,max]':>16}{'MEDIAN-fix':>12}")
        print("-" * 78)
        rows = []
        for c in configs:
            vals = [b[c] for b in boards if b[c] is not None]
            sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            rows.append((c, statistics.mean(vals), sd, min(vals), max(vals), board_med[c]))
        rows.sort(key=lambda r: -r[5])  # by median-fix NDCG
        for c, mean, sd, lo, hi, med in rows:
            flag = "  <-- noisy" if sd > 0.02 else ""
            print(f"{c:<18}{mean:>9.3f}{sd:>7.3f}{f'[{lo:.3f},{hi:.3f}]':>16}{med:>12.3f}{flag}")

        # winner stability across single runs
        winners = [max(b, key=lambda k: b[k]) for b in boards]
        print(f"\nWinner per run: {winners}  | median-fix winner: {rows[0][0]}")
        print(f"distinct single-run winners = {len(set(winners))} "
              f"({'STABLE' if len(set(winners)) == 1 else 'UNSTABLE -> need the median fix'})")


if __name__ == "__main__":
    main()
