"""Score the 5 enrichment levels for ACCURACY against a labeled gold set.

Reads scripts' frozen output (data/experiment/enrichment_levels.json) and a
hand-labeled ground truth (below). Computes:
  * fact accuracy (seniority / workplace / salary-presence) for L1/L2/L5
  * match accuracy (fit bucket) for L3/L4

GROUND TRUTH was labeled by reading each job's text (see _goldset_readable).
It is ONE labeler's judgment (Claude), not the end user's taste. Facts are
objective (they're in the text); fit buckets use obvious domain+seniority logic
for a SENIOR AI/ML ENGINEER profile, so this proves "no gross errors", not fine
discrimination. Swap in the user's labels here to get the real-taste number.

Visa is omitted from scoring: no job in this sample stated visa, so every level
trivially said "unknown" — non-discriminating.
"""
from __future__ import annotations

import json
from pathlib import Path

# idx matches records[] order (the 10 LLM-run jobs are records 0-9).
# sen=true seniority, wp=true workplace, sal=salary stated?, fit=match/partial/no
GROUND_TRUTH = [
    {"title": "Senior Data Scientist", "sen": "senior", "wp": "unknown", "sal": True, "fit": "match"},
    {"title": "Legal Innovation & AI Director", "sen": "director", "wp": "unknown", "sal": True, "fit": "no"},
    {"title": "Machine Learning Engineer", "sen": "mid", "wp": "unknown", "sal": True, "fit": "match"},
    {"title": "Freelance Writer", "sen": "unknown", "wp": "remote", "sal": True, "fit": "no"},
    {"title": "Head of Sales", "sen": "senior", "wp": "remote", "sal": False, "fit": "no"},
    {"title": "Junior AI Engineer", "sen": "junior", "wp": "hybrid", "sal": False, "fit": "partial"},
    {"title": "Junior Python Developer", "sen": "junior", "wp": "hybrid", "sal": False, "fit": "partial"},
    {"title": "Sr. Analyst, Growth", "sen": "senior", "wp": "unknown", "sal": False, "fit": "no"},
    {"title": "Senior Algorithm/AI Engineer", "sen": "senior", "wp": "unknown", "sal": True, "fit": "match"},
    {"title": "Design Engineer Part-Time", "sen": "unknown", "wp": "remote", "sal": True, "fit": "no"},
]


def salary_present(facts: dict) -> bool:
    s = (facts or {}).get("salary") or {}
    return s.get("min") is not None or s.get("max") is not None


def bucket(score) -> str:
    if score is None:
        return "none"
    if score >= 70:
        return "match"
    if score >= 40:
        return "partial"
    return "no"


def main() -> None:
    data = json.loads(Path("data/experiment/enrichment_levels.json").read_text(encoding="utf-8"))
    recs = data["records"][:len(GROUND_TRUTH)]

    fact_levels = ["L2", "L1", "L5"]
    sen = {lv: 0 for lv in fact_levels}
    wp = {lv: 0 for lv in fact_levels}
    sal = {lv: 0 for lv in fact_levels}
    match = {"L3": 0, "L4": 0}
    n = len(recs)

    for r, gt in zip(recs, GROUND_TRUTH):
        for lv in fact_levels:
            f = r.get(lv) or {}
            if (f.get("seniority") or "unknown") == gt["sen"]:
                sen[lv] += 1
            if (f.get("workplace") or "unknown") == gt["wp"]:
                wp[lv] += 1
            if salary_present(f) == gt["sal"]:
                sal[lv] += 1
        for lv in ("L3", "L4"):
            mo = r.get(lv) or {}
            if bucket(mo.get("fit_score")) == gt["fit"]:
                match[lv] += 1

    def pct(c):
        return f"{c}/{n} ({100*c//n}%)"

    print("=" * 64)
    print(f"ACCURACY on {n} hand-labeled jobs (Claude as labeler)")
    print("=" * 64)
    print("FACT EXTRACTION:")
    print(f"  {'level':<14}{'seniority':<16}{'workplace':<16}{'salary-present':<16}")
    for lv in fact_levels:
        nm = {"L2": "L2 rules", "L1": "L1 merged", "L5": "L5 llm-only"}[lv]
        print(f"  {nm:<14}{pct(sen[lv]):<16}{pct(wp[lv]):<16}{pct(sal[lv]):<16}")
    print("\nMATCH (fit bucket vs truth):")
    for lv in ("L4", "L3"):
        nm = {"L4": "L4 matcher-only", "L3": "L3 rules+matcher"}[lv]
        print(f"  {nm:<18}{pct(match[lv])}")
    print("\nNote: visa omitted (no job stated visa -> non-discriminating).")


if __name__ == "__main__":
    main()
