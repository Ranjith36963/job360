"""Score both real profiles across all 16 engine combos and print a side-by-side
comparison: which engines work well for Ranjith vs Pavan."""
from __future__ import annotations

import json
import logging
import sys

logging.disable(logging.WARNING)

GOLD = r"C:/Users/Ranjith/AppData/Local/Temp/two_real_gold.json"
UIDS = ["eval-ranjith", "eval-pavan"]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception as exc:
            logging.debug("stdout reconfigure failed: %s", exc)

    from scripts.accuracy_audit import _fetch_feed_rows, _fetch_profile, _open_db_ro
    from scripts.engine_ablation import run_leaderboard_strong
    from src.core.settings import DB_PATH
    from src.services.profile.storage import load_profile

    golds = json.load(open(GOLD))
    per_user = {}
    for uid in UIDS:
        gold = golds[uid]
        conn = _open_db_ro(str(DB_PATH))
        try:
            pd = _fetch_profile(conn, uid)
            fr = _fetch_feed_rows(conn, uid)
        finally:
            conn.close()
        hp = load_profile(uid)
        rows, _sig, best = run_leaderboard_strong(pd, fr, gold, hybrid_profile=hp, n_resamples=1000)
        per_user[uid] = {r["name"]: r["strong"] for r in rows}
        print(f"\n{'='*72}\n{uid} — leaderboard (NDCG / NDCG@3 / Spearman)\n{'='*72}")
        print(f"{'CONFIG':<18}{'NDCG':>8}{'NDCG@3':>9}{'Spearman':>10}")
        print("-" * 72)
        for r in rows:
            s = r["strong"]
            def g(m, s=s):
                try:
                    return f"{s[m]['point']:.3f}"
                except Exception:
                    return "  -  "
            print(f"{r['name']:<18}{g('ndcg'):>8}{g('ndcg@3'):>9}{g('spearman'):>10}")
        print(f"BEST(NDCG): {best}")

    # side-by-side: average NDCG + Spearman across the two real people
    print(f"\n{'='*72}\nACROSS BOTH REAL PEOPLE (mean of the two) — which engine generalises\n{'='*72}")
    configs = list(next(iter(per_user.values())).keys())
    agg = []
    for c in configs:
        nd = [per_user[u][c]["ndcg"]["point"] for u in UIDS if c in per_user[u]]
        sp = [per_user[u][c]["spearman"]["point"] for u in UIDS if c in per_user[u]
              and per_user[u][c]["spearman"]["point"] is not None]
        worst = min(nd) if nd else 0
        agg.append((c, sum(nd) / len(nd) if nd else 0, worst, sum(sp) / len(sp) if sp else None))
    agg.sort(key=lambda x: (-x[1], -x[2]))
    print(f"{'CONFIG':<18}{'mean NDCG':>11}{'worst NDCG':>12}{'mean Spearman':>15}")
    print("-" * 72)
    for c, mn, wr, sp in agg:
        sps = f"{sp:.3f}" if sp is not None else "N/A"
        print(f"{c:<18}{mn:>11.3f}{wr:>12.3f}{sps:>15}")


if __name__ == "__main__":
    main()
