"""Judge both v2 pools (eval-ranjith, eval-pavan) with the LLM (E4)."""
from __future__ import annotations

import asyncio
import json
import logging

logging.disable(logging.WARNING)

POOL = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_pool.json"


async def _main() -> None:
    import aiosqlite

    from src.core.settings import DB_PATH
    from src.models import Job
    from src.services.llm_matcher import clear_user_verdicts, match_batch, profile_to_matcher_text
    from src.services.profile.storage import load_profile

    pool = json.load(open(POOL))
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    for uid, d in pool.items():
        ids = d["ids"]
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
        res = await match_batch(jobs, user_id=uid, profile_text=ptxt, conn=conn,
                                semaphore_limit=2, skip_existing=True)
        print(f"{uid}: judged {sum(1 for x in res if x is not None)}/{len(jobs)}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(_main())
