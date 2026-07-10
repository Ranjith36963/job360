"""Panel analysis — break the E4 circularity with an independent rater panel.

Raters (independent, different labs):
  - opus   : the Opus gold (this agent) — the gold setter
  - gemini : the in-app judge E4 (median of K runs)  <-- this is the engine under test
  - gpt    : gpt-4o-mini, a NEW independent voice (rater_panel.py)

Two things it reports:
  [A] PANEL AGREEMENT — pairwise Spearman among opus/gemini/gpt per profile.
      High 3-way agreement = a real, model-independent ground truth exists.
  [B] NON-CIRCULAR ENGINE SCORES — every engine scored against a CONSENSUS GOLD
      that EXCLUDES the engine's own model. The in-app judge (gemini) is scored
      against opus+gpt only (no gemini) -> its win can no longer be manufactured
      by grading it with itself. E1/E2/E3 aren't LLMs, scored vs full consensus.
"""
from __future__ import annotations

import json
import logging
import statistics
import sys

logging.disable(logging.WARNING)

GOLD = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_gold.json"
PANEL = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_panel.json"
RUNS = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_judge_runs.json"
UIDS = ["eval-ranjith", "eval-pavan", "eval-crajappa", "eval-rohith", "eval-sofia"]
GATE = 0.5


def spear(a, b):
    c = [k for k in a if k in b and a[k] is not None and b[k] is not None]
    n = len(c)
    if n < 4:
        return None
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[o[j + 1]] == v[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2.0
            i = j + 1
        return r
    av = [float(a[k]) for k in c]; bv = [float(b[k]) for k in c]
    ra, rb = rk(av), rk(bv); m1 = sum(ra) / n; m2 = sum(rb) / n
    cov = sum((x - m1) * (y - m2) for x, y in zip(ra, rb))
    s1 = sum((x - m1) ** 2 for x in ra) ** 0.5; s2 = sum((y - m2) ** 2 for y in rb) ** 0.5
    return cov / (s1 * s2) if s1 * s2 else None


def consensus(raters, exclude=()):
    """Mean score per job across the given rater dicts, skipping excluded ones."""
    use = {name: d for name, d in raters.items() if name not in exclude}
    ids = set()
    for d in use.values():
        ids |= set(d.keys())
    out = {}
    for jid in ids:
        vals = [d[jid] for d in use.values() if jid in d and d[jid] is not None]
        if vals:
            out[jid] = statistics.mean(vals)
    return out


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

    gold = json.load(open(GOLD))
    panel = json.load(open(PANEL))
    runs = json.load(open(RUNS))

    print("=" * 80)
    print("[A] PANEL AGREEMENT — do 3 independent labs (Opus / Gemini / GPT) agree?")
    print("=" * 80)
    print(f"{'PROFILE':<14}{'Opus~Gemini':>13}{'Opus~GPT':>11}{'Gemini~GPT':>12}{'3-way mean':>12}")
    print("-" * 80)
    rater_by_uid = {}
    for uid in UIDS:
        opus = {k: v["fit"] for k, v in gold[uid].items()}
        gem = {k: statistics.median(v) for k, v in runs.get(uid, {}).items() if v}
        gpt = {k: float(v) for k, v in panel.get("gpt", {}).get(uid, {}).items()}
        rater_by_uid[uid] = {"opus": opus, "gemini": gem, "gpt": gpt}
        og = spear(opus, gem); op = spear(opus, gpt); gg = spear(gem, gpt)
        vals = [x for x in (og, op, gg) if x is not None]
        mean = statistics.mean(vals) if vals else None
        def f(x):
            return f"{x:.3f}" if x is not None else "  -  "
        print(f"{uid.replace('eval-',''):<14}{f(og):>13}{f(op):>11}{f(gg):>12}{f(mean):>12}")

    print("\n" + "=" * 80)
    print("[B] NON-CIRCULAR ENGINE SCORES (each engine vs consensus EXCLUDING its own model)")
    print("    rankable = profiles whose 3-way panel agreement >= %.2f" % GATE)
    print("=" * 80)

    rankable = {}
    for uid in UIDS:
        r = rater_by_uid[uid]
        ag = [x for x in (spear(r["opus"], r["gemini"]), spear(r["opus"], r["gpt"]),
                          spear(r["gemini"], r["gpt"])) if x is not None]
        if not ag or statistics.mean(ag) < GATE:
            continue
        conn = _open_db_ro(str(DB_PATH))
        try:
            pd = _fetch_profile(conn, uid); fr = _fetch_feed_rows(conn, uid)
        finally:
            conn.close()
        es = ea.build_engine_scores(pd, fr, hybrid_profile=load_profile(uid))
        S = {n: {str(j): s for j, s in es[n].items()} for n in es}
        S["judge"] = r["gemini"]  # E4 = the gemini judge

        cons_no_gem = consensus(r, exclude=("gemini",))   # opus + gpt
        cons_full = consensus(r)                           # opus + gemini + gpt
        eng = {}
        eng["judge"] = spear(S["judge"], cons_no_gem)      # NON-CIRCULAR: judge vs opus+gpt
        for e in ("hybrid", "dims", "keyword", "bm25"):
            eng[e] = spear({k: S[e][k] for k in S[e] if k in cons_full}, cons_full)
        rankable[uid] = eng

    if not rankable:
        print("No rankable profiles.")
        return
    cols = list(rankable.keys())
    print(f"{'ENGINE':<10}" + "".join(c.replace('eval-', '')[:9].rjust(11) for c in cols) + "   MEAN  WORST")
    print("-" * 80)
    order = []
    for e in ("judge", "hybrid", "dims", "keyword", "bm25"):
        vals = [rankable[c][e] for c in cols if rankable[c][e] is not None]
        mean = statistics.mean(vals) if vals else float("nan")
        worst = min(vals) if vals else float("nan")
        order.append((e, mean, worst))
        row = e.ljust(10) + "".join((f"{rankable[c][e]:.3f}" if rankable[c][e] is not None else " - ").rjust(11) for c in cols)
        print(f"{row}  {mean:6.3f} {worst:6.3f}")
    print("-" * 80)
    order.sort(key=lambda x: -x[1])
    print("NON-CIRCULAR ORDER:  " + "  >  ".join(f"{e}({m:.2f})" for e, m, _ in order))
    print("\nNote: the judge (E4) is now scored vs Opus+GPT only (its own Gemini votes")
    print("excluded), so its rank can no longer be manufactured by self-grading.")


if __name__ == "__main__":
    main()
