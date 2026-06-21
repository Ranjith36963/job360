"""Trustworthy engine eval — Phase E: scoring with the audit fixes baked in.

Fixes applied here:
  * Tie-break (#6): rankings break ties by a SEEDED RANDOM jitter, not by feed
    order — so a low-signal engine can no longer silently inherit keyword's
    order. Jobs an engine has NO signal for (None) are dropped from that
    engine's ranking (it doesn't get to vote on them).
  * Stats (#7): every metric carries a bootstrap 95% CI; the best config is
    significance-tested against each other config (paired bootstrap).
  * Leak-check (#2/#4): reports Spearman(gold, keyword), Spearman(gold, judge),
    Spearman(judge, keyword) — so you can SEE whether the gold/judge are
    independent of keyword or just mirroring it.
  * Validity (#1/#5): reports E2 distinct-value count (non-degenerate?), the
    E3-vs-keyword order correlation (is E3 independent?), and per-engine
    coverage on the pool.

Run AFTER eval_v2_pool + grading + judge_v2. Reads eval_v2_gold.json.
"""
from __future__ import annotations

import json
import logging
import random
import sys

logging.disable(logging.WARNING)

GOLD = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_gold.json"
UIDS = ["eval-ranjith", "eval-pavan"]
SEED = 20260621


def _ranking_v2(scores: dict, seed: int) -> list:
    """Order job ids by score desc; DROP None (no signal); break ties by a
    seeded random jitter (NOT feed order)."""
    rng = random.Random(seed)
    items = [(jid, s) for jid, s in scores.items() if s is not None]
    jitter = {jid: rng.random() for jid, _ in items}
    items.sort(key=lambda x: (-float(x[1]), jitter[x[0]]))
    return [jid for jid, _ in items]


def _spearman(a: dict, b: dict) -> float | None:
    common = [j for j in a if j in b and a[j] is not None and b[j] is not None]
    n = len(common)
    if n < 3:
        return None
    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(vals):
            j = i
            while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    av = [float(a[j]) for j in common]
    bv = [float(b[j]) for j in common]
    ra, rb = ranks(av), ranks(bv)
    mra = sum(ra) / n
    mrb = sum(rb) / n
    cov = sum((x - mra) * (y - mrb) for x, y in zip(ra, rb))
    sa = sum((x - mra) ** 2 for x in ra) ** 0.5
    sb = sum((y - mrb) ** 2 for y in rb) ** 0.5
    return cov / (sa * sb) if sa * sb else None


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
    per_user_strong = {}

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

        # ---- validity diagnostics ----
        dims = es.get("dims", {})
        dvals = [round(v, 3) for v in dims.values() if v is not None]
        n_distinct = len(set(dvals))
        kw = es["keyword"]; hyb = es["hybrid"]; judge = es["judge"]
        kr = _ranking_v2(kw, SEED); hr = _ranking_v2(hyb, SEED)
        posk = {j: i for i, j in enumerate(kr)}; posh = {j: i for i, j in enumerate(hr)}
        common = [j for j in kr if j in posh]
        e3_corr = _spearman({j: -posk[j] for j in common}, {j: -posh[j] for j in common})
        cov = {k: sum(1 for v in m.values() if v is not None) for k, m in es.items()}

        gnum = {str(k): v["fit"] for k, v in gold.items()}
        leak_gold_kw = _spearman(gnum, {str(j): s for j, s in kw.items()})
        leak_gold_judge = _spearman(gnum, {str(j): s for j, s in judge.items()})
        leak_judge_kw = _spearman({str(j): s for j, s in judge.items()},
                                  {str(j): s for j, s in kw.items()})

        print(f"\n{'='*74}\n{uid}  (pool={len(fr)})  —  VALIDITY DIAGNOSTICS\n{'='*74}")
        print(f"E2 dims: {n_distinct} distinct values over {len(dvals)} jobs "
              f"({'OK — discriminating' if n_distinct >= 6 else 'DEGENERATE'})")
        print(f"E3 vs keyword order corr: {e3_corr:.3f} "
              f"({'independent enough' if e3_corr is not None and e3_corr < 0.9 else 'near-clone'})")
        print(f"coverage (jobs with signal): " + ", ".join(f"{k}={v}" for k, v in cov.items()))
        print("LEAK-CHECK (Spearman):")
        print(f"  gold vs keyword : {leak_gold_kw:.3f}   (how much the gold agrees with E1)")
        print(f"  gold vs judge   : {leak_gold_judge:.3f}   (how much the gold agrees with E4)")
        print(f"  judge vs keyword: {leak_judge_kw:.3f}   (is E4 just mirroring E1?)")

        # ---- leaderboard with tie-break-fixed rankings + CIs + significance ----
        rankings = {}
        rows = []
        for c in ea.DEFAULT_CONFIGS:
            engines = c["engines"]
            if len(engines) == 1:
                ranking = _ranking_v2(es.get(engines[0], {}), SEED)
            else:
                ranking = ea.combine_rrf([_ranking_v2(es.get(e, {}), SEED) for e in engines], k=60)
            rankings[c["name"]] = ranking
            strong = ea.evaluate_ranking_strong(ranking, gold, n_resamples=2000, seed=SEED)
            rows.append({"name": c["name"], "strong": strong})
        rows.sort(key=lambda r: (r["strong"]["ndcg"]["point"] or 0), reverse=True)
        best = rows[0]["name"]
        per_user_strong[uid] = {r["name"]: r["strong"] for r in rows}

        print(f"\n{uid} — LEADERBOARD (tie-break-fixed; NDCG [95% CI] / NDCG@5 / Spearman)")
        print(f"{'CONFIG':<18}{'NDCG':>7}{'  95% CI':>16}{'NDCG@5':>9}{'Spear':>8}{'  vs best':>10}")
        print("-" * 74)
        for r in rows:
            s = r["strong"]
            nd = s["ndcg"]["point"]; ci = s["ndcg"].get("ci")
            n5 = s.get("ndcg@5", {}).get("point"); sp = s.get("spearman", {}).get("point")
            cis = f"[{ci[0]:.2f},{ci[1]:.2f}]" if ci else ""
            if r["name"] == best:
                sig = "(best)"
            else:
                cmp = ea.compare_configs(rankings[best], rankings[r["name"]], gold,
                                         metric="ndcg", n_resamples=2000, seed=SEED)
                p = cmp.get("p_value") if isinstance(cmp, dict) else None
                sig = f"p={p:.2f}" if isinstance(p, (int, float)) else ""
            def f(x):
                return f"{x:.3f}" if isinstance(x, (int, float)) else "  -  "
            print(f"{r['name']:<18}{f(nd):>7}{cis:>16}{f(n5):>9}{f(sp):>8}{sig:>10}")

    # ---- cross-person summary ----
    print(f"\n{'='*74}\nACROSS BOTH REAL PEOPLE (mean / worst NDCG, mean Spearman)\n{'='*74}")
    configs = list(next(iter(per_user_strong.values())).keys())
    agg = []
    for c in configs:
        nd = [per_user_strong[u][c]["ndcg"]["point"] for u in UIDS]
        sp = [per_user_strong[u][c]["spearman"]["point"] for u in UIDS
              if per_user_strong[u][c]["spearman"]["point"] is not None]
        agg.append((c, sum(nd) / len(nd), min(nd), sum(sp) / len(sp) if sp else None))
    agg.sort(key=lambda x: (-x[1], -x[2]))
    print(f"{'CONFIG':<18}{'mean NDCG':>11}{'worst NDCG':>12}{'mean Spearman':>15}")
    print("-" * 74)
    for c, mn, wr, sp in agg:
        print(f"{c:<18}{mn:>11.3f}{wr:>12.3f}{(f'{sp:.3f}' if sp is not None else 'N/A'):>15}")


if __name__ == "__main__":
    main()
