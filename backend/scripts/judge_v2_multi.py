"""Stability test: judge each v2 pool K times (independent LLM runs) and save
every run's per-job scores. Lets us measure the LLM judge's (E4) run-to-run
variance — a core trustworthiness check.

Output: /tmp/eval_v2_judge_runs.json = {uid: {job_id: [s_run1, s_run2, ...]}}
"""
from __future__ import annotations

import asyncio
import json
import logging

logging.disable(logging.WARNING)

POOL = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_pool.json"
OUT = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_judge_runs.json"
K = 3


async def _judge_once(conn, uid, ids):
    from src.models import Job
    from src.services.llm_matcher import clear_user_verdicts, match_batch, profile_to_matcher_text
    from src.services.profile.storage import load_profile

    prof = load_profile(uid)
    ptxt = profile_to_matcher_text(prof)
    await clear_user_verdicts(conn, uid)
    jobs = []
    for jid in ids:
        r = await (await conn.execute(
            "SELECT id,title,company,location,description,apply_url,source,date_found "
            "FROM jobs WHERE id=?", (jid,))).fetchone()
        if r:
            j = Job(title=r["title"] or "x", company=r["company"] or "c",
                    apply_url=r["apply_url"] or "u", source=r["source"] or "s",
                    date_found=r["date_found"] or "2026-01-01",
                    location=r["location"] or "", description=r["description"] or "")
            j.id = r["id"]
            jobs.append(j)
    await match_batch(jobs, user_id=uid, profile_text=ptxt, conn=conn,
                      semaphore_limit=2, skip_existing=True)
    cur = await conn.execute(
        "SELECT job_id, llm_fit_score FROM user_feed WHERE user_id=? AND llm_fit_score IS NOT NULL",
        (uid,))
    return {str(r[0]): r[1] for r in await cur.fetchall()}


async def _main() -> None:
    import aiosqlite

    from src.core.settings import DB_PATH

    pool = json.load(open(POOL))
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    runs: dict = {uid: {} for uid in pool}
    for k in range(K):
        for uid, d in pool.items():
            scores = await _judge_once(conn, uid, d["ids"])
            for jid, s in scores.items():
                runs[uid].setdefault(jid, []).append(s)
            print(f"run {k+1}/{K} {uid}: {len(scores)} judged", flush=True)
        # Save after each COMPLETE round so an interrupt never loses a round.
        json.dump(runs, open(OUT, "w"))
        print(f"saved {k+1} round(s) -> {OUT}", flush=True)
    await conn.close()
    print(f"done -> {OUT}")


if __name__ == "__main__":
    asyncio.run(_main())
