"""Multi-LLM rater PANEL — independent graders to break the E4 circularity.

The flaw (audit #13): the in-app judge (E4) is Gemini, and the validity gate was
Opus-gold vs Gemini-judge, so scoring E4 against that gold was circular.

Fix: add INDEPENDENT raters from DIFFERENT labs. This script rates every pooled
job for every profile with a chosen provider, using the SAME rubric the gold
uses. Run once per provider (gpt-4o-mini, etc). Output is merged so we can:
  - measure agreement ACROSS the panel (real ground-truth strength), and
  - score E4 against a consensus that EXCLUDES E4's own model (non-circular).

Opus (this agent) remains the gold setter; the panel are the cross-checks.

Run: python -m scripts.rater_panel gpt
"""
from __future__ import annotations

import json
import sys
import time

from dotenv import dotenv_values

GOLD = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_gold.json"
OUT = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_panel.json"  # {provider: {uid: {job_id: fit}}}
UIDS = ["eval-ranjith", "eval-pavan", "eval-crajappa", "eval-rohith", "eval-sofia"]

RUBRIC = (
    "You are a careful technical recruiter. Score how well a JOB fits a CANDIDATE "
    "from 0 to 100, judging FIVE things: (1) domain/role match, (2) seniority match "
    "(intern/junior/mid/senior), (3) skills overlap, (4) location/remote + visa "
    "feasibility, (5) salary fit. 0 = totally wrong, 100 = perfect fit. Be decisive "
    "and use the full range. Return ONLY a compact JSON object mapping each job id "
    "(string) to an integer score, e.g. {\"123\": 80, \"456\": 12}. No prose."
)


def _profile_text(uid):
    import sqlite3

    from src.core.settings import DB_PATH
    c = sqlite3.connect(str(DB_PATH)); c.row_factory = sqlite3.Row
    r = c.execute("SELECT cv_data, preferences FROM user_profiles WHERE user_id=?", (uid,)).fetchone()
    cv = json.loads(r["cv_data"]); p = json.loads(r["preferences"])
    titles = ", ".join((cv.get("job_titles") or [])[:6])
    skills = ", ".join((cv.get("skills") or [])[:25])
    return (f"CANDIDATE: {cv.get('name')}\nTarget titles: {titles}\n"
            f"Skills: {skills}\nLevel: {p.get('experience_level')}, "
            f"salary {p.get('salary_min')}-{p.get('salary_max')}, "
            f"workplace {p.get('preferred_workplace')}, needs_visa {p.get('needs_visa')}")


def _jobs_for(uid, ids):
    import sqlite3

    from src.core.settings import DB_PATH
    c = sqlite3.connect(str(DB_PATH)); c.row_factory = sqlite3.Row
    out = []
    for jid in ids:
        r = c.execute("SELECT id,title,company,location,description FROM jobs WHERE id=?", (jid,)).fetchone()
        if r:
            desc = (r["description"] or "").replace("\n", " ")[:280]
            out.append((jid, f"[{jid}] {r['title']} | {r['company']} | {r['location']}\n{desc}"))
    return out


def _call_gpt(key, profile_text, jobs_block):
    from openai import OpenAI
    c = OpenAI(api_key=key)
    r = c.chat.completions.create(
        model="gpt-4o-mini",  # cheap mini model per instruction
        messages=[
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": f"{profile_text}\n\nJOBS:\n{jobs_block}\n\n"
             "Return the JSON map of job id -> fit score now."},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=2000,
    )
    return json.loads(r.choices[0].message.content)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    provider = sys.argv[1] if len(sys.argv) > 1 else "gpt"
    env = dotenv_values("../.env")
    gold = json.load(open(GOLD))

    import os
    panel = json.load(open(OUT)) if os.path.exists(OUT) else {}
    panel.setdefault(provider, {})

    for uid in UIDS:
        ids = [int(k) for k in gold[uid].keys()]
        ptext = _profile_text(uid)
        jobs = _jobs_for(uid, ids)
        block = "\n\n".join(b for _, b in jobs)
        if provider == "gpt":
            key = env.get("openai_api_key", "")
            t0 = time.perf_counter()
            try:
                scores = _call_gpt(key, ptext, block)
                clean = {str(k): int(v) for k, v in scores.items() if str(k) in {str(i) for i in ids}}
                panel[provider][uid] = clean
                print(f"{provider} {uid}: rated {len(clean)}/{len(ids)} in {time.perf_counter()-t0:.1f}s", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"{provider} {uid}: FAIL {type(e).__name__}: {str(e)[:90]}", flush=True)
        json.dump(panel, open(OUT, "w"))
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
