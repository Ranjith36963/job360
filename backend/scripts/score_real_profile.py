"""Score step for the REAL profile: run all engine combos against the
hand-graded gold and print a single-profile leaderboard."""
from __future__ import annotations

import json
import logging
import sys

logging.disable(logging.WARNING)

GOLD = r"C:/Users/Ranjith/AppData/Local/Temp/real_gold.json"
UID = "eval-real"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from scripts.accuracy_audit import _fetch_feed_rows, _fetch_profile, _open_db_ro
    from scripts.engine_ablation import run_leaderboard_strong
    from src.core.settings import DB_PATH
    from src.services.profile.storage import load_profile

    gold = json.load(open(GOLD))[UID]
    conn = _open_db_ro(str(DB_PATH))
    try:
        profile_dict = _fetch_profile(conn, UID)
        feed_rows = _fetch_feed_rows(conn, UID)
    finally:
        conn.close()
    hybrid_profile = load_profile(UID)

    rows, _sig, best = run_leaderboard_strong(
        profile_dict, feed_rows, gold, hybrid_profile=hybrid_profile, n_resamples=1000
    )

    print("\n" + "=" * 80)
    print("REAL PROFILE LEADERBOARD — Ranjith (Full-Stack/Backend), 24-job hand-graded gold")
    print("=" * 80)
    print(f"{'CONFIG':<18}{'NDCG':>8}{'NDCG@3':>9}{'NDCG@5':>9}{'NDCGexp':>9}{'Spearman':>10}")
    print("-" * 80)
    for r in rows:
        s = r["strong"]
        def g(metric):
            try:
                return s[metric]["point"]
            except Exception:
                return None
        def f(x):
            return f"{x:.3f}" if isinstance(x, (int, float)) else "  -  "
        print(f"{r['name']:<18}{f(g('ndcg')):>8}{f(g('ndcg@3')):>9}{f(g('ndcg@5')):>9}"
              f"{f(g('ndcg_exp')):>9}{f(g('spearman')):>10}")
    print("-" * 80)
    print(f"BEST (by NDCG): {best}")


if __name__ == "__main__":
    main()
