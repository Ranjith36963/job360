"""Stability test (cheap): re-run the v2 scorer over M random seeds and report
how much each config's NDCG / Spearman / rank swings. A trustworthy tool must
give ~the same answer regardless of the tie-break / bootstrap seed.
"""
from __future__ import annotations

import json
import logging
import statistics
import sys

logging.disable(logging.WARNING)

GOLD = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_gold.json"
UIDS = ["eval-ranjith", "eval-pavan"]
SEEDS = [101, 202, 303, 404, 505, 606, 707, 808, 909, 1010, 1111, 1212]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception as exc:
            logging.debug("stdout reconfigure failed: %s", exc)

    from scripts import engine_ablation as ea
    from scripts.accuracy_audit import _fetch_feed_rows, _fetch_profile, _open_db_ro
    from scripts.score_v2 import _ranking_v2
    from src.core.settings import DB_PATH
    from src.services.profile.storage import load_profile

    golds_all = json.load(open(GOLD))
    for uid in UIDS:
        gold = golds_all[uid]
        conn = _open_db_ro(str(DB_PATH))
        try:
            pd = _fetch_profile(conn, uid)
            fr = _fetch_feed_rows(conn, uid)
        finally:
            conn.close()
        hp = load_profile(uid)
        es = ea.build_engine_scores(pd, fr, hybrid_profile=hp)

        # per-config NDCG + Spearman across seeds
        ndcg = {c["name"]: [] for c in ea.DEFAULT_CONFIGS}
        spear = {c["name"]: [] for c in ea.DEFAULT_CONFIGS}
        top1 = {c["name"]: 0 for c in ea.DEFAULT_CONFIGS}
        for seed in SEEDS:
            seed_scores = {}
            for c in ea.DEFAULT_CONFIGS:
                engines = c["engines"]
                if len(engines) == 1:
                    ranking = _ranking_v2(es.get(engines[0], {}), seed)
                else:
                    ranking = ea.combine_rrf([_ranking_v2(es.get(e, {}), seed) for e in engines], k=60)
                # use the strong evaluator's point estimate (no bootstrap -> n_resamples=1)
                strong = ea.evaluate_ranking_strong(ranking, gold, n_resamples=1, seed=seed)
                nd = strong["ndcg"]["point"]
                sp = strong["spearman"]["point"]
                ndcg[c["name"]].append(nd)
                if sp is not None:
                    spear[c["name"]].append(sp)
                seed_scores[c["name"]] = nd
            winner = max(seed_scores, key=lambda k: seed_scores[k])
            top1[winner] += 1

        print(f"\n{'='*78}\n{uid} — SEED STABILITY over {len(SEEDS)} seeds (NDCG mean±std [min,max], #wins)\n{'='*78}")
        print(f"{'CONFIG':<18}{'NDCG mean':>10}{'±std':>8}{'[min,max]':>16}{'Spear':>8}{'#1 wins':>9}")
        print("-" * 78)
        order = sorted(ea.DEFAULT_CONFIGS, key=lambda c: -statistics.mean(ndcg[c["name"]]))
        for c in order:
            n = c["name"]
            nd = ndcg[n]
            sd = statistics.pstdev(nd) if len(nd) > 1 else 0.0
            sp = statistics.mean(spear[n]) if spear[n] else float("nan")
            print(f"{n:<18}{statistics.mean(nd):>10.3f}{sd:>8.3f}"
                  f"{f'[{min(nd):.3f},{max(nd):.3f}]':>16}{sp:>8.3f}{top1[n]:>9}")
        # how unstable is the WINNER?
        wins = sorted(top1.items(), key=lambda x: -x[1])
        print(f"\nWinner stability: {wins[0][0]} won {wins[0][1]}/{len(SEEDS)} seeds; "
              f"distinct winners across seeds = {sum(1 for _, v in wins if v > 0)}")


if __name__ == "__main__":
    main()
