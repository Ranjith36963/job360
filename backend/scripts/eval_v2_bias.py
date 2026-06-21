"""Hunt for bias INSIDE the eval itself (not just re-run it).

Three deep checks:
  1. INTER-RATER AGREEMENT — Spearman between my Opus gold and the independent
     Gemini judge (median of K runs). Two expert graders. If they DON'T agree,
     there is no stable ground truth to rank engines against -> the eval has no
     discriminating power for that profile, no matter how good the engines are.
     This is the deepest "is the eval even meaningful" test.
  2. POOLING SELF-SELECTION — each retriever (keyword/bm25/vector) put some jobs
     in the pool. Does an engine predict the gold BETTER on the jobs it itself
     contributed than on jobs only other engines found? If yes, the fair-pool
     still gives it a home-field advantage (a residual bias).
  3. PROVENANCE PRECISION — mean gold fit of the jobs each retriever contributed.
     Which engine actually surfaces the better candidates.
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
UIDS = ["eval-ranjith", "eval-pavan"]


def _spearman(a: dict, b: dict):
    common = [j for j in a if j in b and a[j] is not None and b[j] is not None]
    n = len(common)
    if n < 3:
        return None, n
    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals); i = 0
        while i < len(vals):
            j = i
            while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    av = [float(a[j]) for j in common]; bv = [float(b[j]) for j in common]
    ra, rb = ranks(av), ranks(bv)
    mra = sum(ra) / n; mrb = sum(rb) / n
    cov = sum((x - mra) * (y - mrb) for x, y in zip(ra, rb))
    sa = sum((x - mra) ** 2 for x in ra) ** 0.5
    sb = sum((y - mrb) ** 2 for y in rb) ** 0.5
    return (cov / (sa * sb) if sa * sb else None), n


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from scripts.accuracy_audit import _fetch_feed_rows, _fetch_profile, _open_db_ro
    from scripts import engine_ablation as ea
    from src.core.settings import DB_PATH
    from src.services.profile.storage import load_profile

    golds_all = json.load(open(GOLD))
    pools = json.load(open(POOL))
    runs_all = json.load(open(RUNS))

    for uid in UIDS:
        gold = {str(k): v["fit"] for k, v in golds_all[uid].items()}
        prov = {int(k): v for k, v in pools[uid]["prov"].items()}
        runs = runs_all[uid]
        K = max(len(v) for v in runs.values())
        med_judge = {str(j): statistics.median(v) for j, v in runs.items() if v}

        conn = _open_db_ro(str(DB_PATH))
        try:
            pd = _fetch_profile(conn, uid); fr = _fetch_feed_rows(conn, uid)
        finally:
            conn.close()
        hp = load_profile(uid)
        es = ea.build_engine_scores(pd, fr, hybrid_profile=hp)
        S = {name: {str(j): s for j, s in es[name].items()} for name in es}

        print(f"\n{'='*76}\n{uid}  —  BIAS HUNT\n{'='*76}")

        # 1) inter-rater agreement (Opus gold vs Gemini judge) + engine vs gold
        ir, n = _spearman(gold, med_judge)
        print(f"[1] INTER-RATER (Opus gold vs Gemini judge, median of {K}): "
              f"Spearman = {ir:.3f} over {n} jobs")
        verdict = ("STRONG ground truth — eval is meaningful" if ir and ir >= 0.5 else
                   "WEAK/EMPTY ground truth — even 2 expert graders disagree; "
                   "eval can't rank engines here" if ir is not None and ir < 0.3 else
                   "MODERATE")
        print(f"    -> {verdict}")
        print("    each engine vs the gold (Spearman):")
        for name in ("keyword", "dims", "bm25", "hybrid"):
            sp, _ = _spearman(gold, S[name]); print(f"        {name:<9}: {sp:.3f}" if sp is not None else f"        {name:<9}: -")
        spj, _ = _spearman(gold, med_judge); print(f"        judge(med): {spj:.3f}" if spj is not None else "        judge: -")

        # 2) self-selection / home-field
        print("[2] POOLING SELF-SELECTION (engine predicts gold on OWN vs OTHER jobs):")
        eng_to_prov = {"keyword": "keyword", "bm25": "bm25", "hybrid": "vector"}
        for eng, pname in eng_to_prov.items():
            own = {str(j): S[eng][str(j)] for j in prov if pname in prov[j] and str(j) in S[eng]}
            oth = {str(j): S[eng][str(j)] for j in prov if pname not in prov[j] and str(j) in S[eng]}
            g_own = {k: gold[k] for k in own if k in gold}
            g_oth = {k: gold[k] for k in oth if k in gold}
            so, no_ = _spearman(own, g_own); st, nt = _spearman(oth, g_oth)
            so_s = f"{so:.3f}" if so is not None else " - "
            st_s = f"{st:.3f}" if st is not None else " - "
            flag = ""
            if so is not None and st is not None and (so - st) > 0.25:
                flag = "  <-- home-field bias"
            print(f"    {eng:<9}: own({no_})={so_s}  other({nt})={st_s}{flag}")

        # 3) provenance precision (mean gold of each retriever's contributed jobs)
        print("[3] PROVENANCE PRECISION (mean gold of jobs each retriever surfaced):")
        for pname in ("keyword", "bm25", "vector"):
            fits = [gold[str(j)] for j in prov if pname in prov[j] and str(j) in gold]
            if fits:
                print(f"    {pname:<9}: mean gold {statistics.mean(fits):5.1f} over {len(fits)} jobs")
        allfits = list(gold.values())
        print(f"    {'(pool)':<9}: mean gold {statistics.mean(allfits):5.1f} over {len(allfits)} jobs")


if __name__ == "__main__":
    main()
