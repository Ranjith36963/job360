"""Score step: for each fake profile, run all engine combos against its gold,
then aggregate across the 10 profiles to find the robust ship pick.

Run AFTER prep + judge. Reads golds from the temp file, the per-user feeds from
the DB, and prints a cross-profile leaderboard (mean NDCG, mean Spearman,
WORST-CASE NDCG = robustness).
"""
from __future__ import annotations

import json
import logging
import sys

logging.disable(logging.WARNING)

GOLDS = r"C:/Users/Ranjith/AppData/Local/Temp/fake_golds.json"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception as exc:
            logging.debug("stdout reconfigure failed: %s", exc)

    from scripts.accuracy_audit import _fetch_feed_rows, _fetch_profile, _open_db_ro
    from scripts.engine_ablation import run_leaderboard_strong
    from scripts.eval_fake_profiles import aggregate, results_from_strong
    from src.core.settings import DB_PATH
    from src.services.profile.storage import load_profile

    golds = json.load(open(GOLDS))
    all_results: list = []
    per_profile_best: list = []

    for uid, gold in golds.items():
        conn = _open_db_ro(str(DB_PATH))
        try:
            profile_dict = _fetch_profile(conn, uid)
            feed_rows = _fetch_feed_rows(conn, uid)
        finally:
            conn.close()
        hybrid_profile = load_profile(uid)
        rows, _sig, best = run_leaderboard_strong(
            profile_dict, feed_rows, gold, hybrid_profile=hybrid_profile, n_resamples=1000
        )
        all_results.extend(results_from_strong(uid, rows))
        top = rows[0]
        per_profile_best.append((uid.replace("fake-", ""), best,
                                 top["strong"]["ndcg"]["point"], top["strong"]["spearman"]["point"]))
        print(f"  {uid.replace('fake-',''):<20} best={best:<16} "
              f"NDCG={top['strong']['ndcg']['point']:.3f}")

    agg = aggregate(all_results)
    print("\n" + "=" * 78)
    print("CROSS-PROFILE LEADERBOARD (10 fake technical profiles)")
    print("=" * 78)
    print(f"{'CONFIG':<18}{'mean NDCG':>11}{'worst NDCG':>12}{'mean Spearman':>15}{'n':>4}")
    print("-" * 78)
    for r in agg:
        ms = f"{r['mean_spearman']:.3f}" if r["mean_spearman"] is not None else "N/A"
        print(f"{r['config']:<18}{r['mean_ndcg']:>11.3f}{r['worst_ndcg']:>12.3f}{ms:>15}{r['n_profiles']:>4}")
    print("-" * 78)
    print("worst NDCG = the robustness signal (a config that craters on any one")
    print("profile is not safe to ship). mean = average across all 10 profiles.")


if __name__ == "__main__":
    main()
