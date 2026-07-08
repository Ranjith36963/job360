"""CONVERGENCE: the final, defensible engine ranking.

Only profiles with a TRUSTWORTHY gold (inter-rater agreement Opus-gold vs
Gemini-judge >= GATE) are allowed to vote — measuring engines against a gold
two experts disagree on is measuring noise (audit #10).

For each rankable profile it computes engine-vs-gold Spearman, both raw and
DE-BIASED (for retrievers: correlation on jobs OTHER engines surfaced, removing
the home-field advantage, audit #11). It aggregates (mean + worst) across the
rankable profiles and prints the converged order + per-profile consistency.
"""
from __future__ import annotations

import json
import logging
import statistics
import sys

logging.disable(logging.WARNING)

GOLD = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_gold.json"
POOL = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_pool.json"
RUNS = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_judge_runs.json"
UIDS = ["eval-ranjith", "eval-pavan", "eval-crajappa", "eval-rohith", "eval-sofia"]
GATE = 0.5  # inter-rater agreement required for a profile's vote to count
ENGINES = ["judge", "hybrid", "dims", "keyword", "bm25"]
PROV = {"keyword": "keyword", "bm25": "bm25", "hybrid": "vector"}  # engine -> provenance tag


def _spearman(a, b):
    common = [j for j in a if j in b and a[j] is not None and b[j] is not None]
    n = len(common)
    if n < 4:
        return None
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0
            i = j + 1
        return r
    av = [float(a[j]) for j in common]; bv = [float(b[j]) for j in common]
    ra, rb = ranks(av), ranks(bv); m1 = sum(ra) / n; m2 = sum(rb) / n
    cov = sum((x - m1) * (y - m2) for x, y in zip(ra, rb))
    s1 = sum((x - m1) ** 2 for x in ra) ** 0.5; s2 = sum((y - m2) ** 2 for y in rb) ** 0.5
    return cov / (s1 * s2) if s1 * s2 else None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from scripts import engine_ablation as ea
    from scripts.accuracy_audit import _fetch_feed_rows, _fetch_profile, _open_db_ro
    from src.core.settings import DB_PATH
    from src.services.profile.storage import load_profile

    golds = json.load(open(GOLD)); pools = json.load(open(POOL)); runs = json.load(open(RUNS))

    rankable = {}      # uid -> {engine: debiased_corr}
    interrater = {}
    for uid in UIDS:
        if uid not in runs or not runs[uid]:
            continue
        gold = {str(k): v["fit"] for k, v in golds[uid].items()}
        med = {str(j): statistics.median(v) for j, v in runs[uid].items() if v}
        K = max((len(v) for v in runs[uid].values()), default=0)
        ir = _spearman(gold, med)
        interrater[uid] = (ir, K)
        if ir is None or ir < GATE:
            continue

        conn = _open_db_ro(str(DB_PATH))
        try:
            pd = _fetch_profile(conn, uid); fr = _fetch_feed_rows(conn, uid)
        finally:
            conn.close()
        es = ea.build_engine_scores(pd, fr, hybrid_profile=load_profile(uid))
        S = {n: {str(j): s for j, s in es[n].items()} for n in es}
        S["judge"] = med
        prov = {int(k): v for k, v in pools[uid]["prov"].items()}

        eng = {}
        for e in ENGINES:
            if e in PROV:  # retriever -> de-biased = correlation on OTHER engines' jobs
                oth = {k: S[e][k] for k in S[e] if PROV[e] not in prov.get(int(k), []) and k in gold}
                g = {k: gold[k] for k in oth}
                eng[e] = _spearman(oth, g)
                if eng[e] is None:  # fallback to raw if too few held-out
                    eng[e] = _spearman({k: S[e][k] for k in S[e] if k in gold}, gold)
            else:  # judge/dims are re-rankers, not retrievers -> raw
                eng[e] = _spearman({k: S[e][k] for k in S[e] if k in gold}, gold)
        rankable[uid] = eng

    print("=" * 78)
    print("CONVERGENCE — trustworthy engine ranking (de-biased; rankable profiles only)")
    print("=" * 78)
    print(f"Inter-rater gate (Opus gold vs Gemini judge, >= {GATE:.2f} to count):")
    for uid, (ir, K) in interrater.items():
        tag = "USED" if uid in rankable else "excluded (no agreed gold)"
        irs = f"{ir:.3f}" if ir is not None else "n/a"
        print(f"   {uid:<16} inter-rater={irs} (K={K})  -> {tag}")

    if not rankable:
        print("\nNo rankable profiles yet (judge runs may still be accruing).")
        return

    print("\nDe-biased engine-vs-gold Spearman per rankable profile:")
    hdr = "ENGINE".ljust(10) + "".join(u.replace("eval-", "")[:9].rjust(11) for u in rankable)
    print(hdr + "   MEAN   WORST")
    print("-" * 78)
    agg = []
    for e in ENGINES:
        vals = [rankable[u][e] for u in rankable if rankable[u][e] is not None]
        mean = statistics.mean(vals) if vals else float("nan")
        worst = min(vals) if vals else float("nan")
        agg.append((e, mean, worst))
        row = e.ljust(10) + "".join((f"{rankable[u][e]:.3f}" if rankable[u][e] is not None else " - ").rjust(11) for u in rankable)
        print(f"{row}  {mean:6.3f} {worst:6.3f}")
    print("-" * 78)
    order = sorted(agg, key=lambda x: -x[1])
    print("CONVERGED ORDER (by mean de-biased correlation with the gold):")
    print("   " + "  >  ".join(f"{e}({m:.2f})" for e, m, _ in order))


if __name__ == "__main__":
    main()
