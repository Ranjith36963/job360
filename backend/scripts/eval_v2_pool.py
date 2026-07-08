"""Trustworthy engine eval — Phase A+B: fair pool + full engine data + blind sheet.

Fixes the audit issues at the SOURCE:
  * Fair pooling (#3): the candidate set is the UNION of each retriever's top-K
    (keyword + BM25 + vector), not keyword alone — every engine gets to surface
    candidates it thinks are relevant.
  * E2 measurable (#1): every pooled job is ENRICHED (LLM) so the dimension
    scorer has real data instead of 0.
  * E3 measurable (#5): every pooled job is EMBEDDED so the vector leg fires on
    all of them (no silent keyword fallback).
  * Blind grading (#2/#4): the emitted sheet has NO engine scores and is in
    RANDOM order, so the human grader cannot anchor to any engine.

Run: python -m scripts.eval_v2_pool
Then: grade the blind sheet -> eval_v2_gold.json, judge, score.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3

logging.disable(logging.WARNING)

UIDS = ["eval-sofia"]
TOP_PER_ENGINE = 12  # union of 3 retrievers' top-12 -> ~25-32 unique (free-LLM budget)
SHEET = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2b_blind_sheet.txt"
POOL_OUT = r"C:/Users/Ranjith/AppData/Local/Temp/eval_v2_pool.json"  # merged (loads existing)


def _query_text(profile) -> str:
    cv = profile.cv_data
    parts = list(cv.job_titles or []) + list((cv.skills or [])[:40])
    return " ".join(parts).strip()


async def _build_for_user(uid, conn, catalog, vindex):
    from src.models import Job
    from src.services.embeddings import _encode_text, _load_encoder, encode_job
    from src.services.job_enrichment import enrich_batch
    from src.services.profile.keyword_generator import generate_search_config
    from src.services.profile.storage import load_profile
    from src.services.retrieval import bm25_rank
    from src.services.skill_matcher import JobScorer

    profile = load_profile(uid)
    cfg = generate_search_config(profile)
    scorer = JobScorer(cfg, user_preferences=None)
    qtext = _query_text(profile)

    by_id = {r["id"]: r for r in catalog}

    # --- retriever 1: keyword (E1) ---
    kw = []
    for r in catalog:
        j = Job(title=r["title"] or "", company=r["company"] or "c", apply_url="u",
                source="s", date_found=r["date_found"] or "2026-01-01",
                location=r["location"] or "", description=r["description"] or "")
        kw.append((r["id"], scorer.score(j).match_score))
    kw.sort(key=lambda x: -x[1])
    kw_top = [jid for jid, _ in kw[:TOP_PER_ENGINE]]

    # --- retriever 2: BM25 (E3 lexical leg) ---
    docs = [(r["id"], f"{r['title'] or ''} {r['description'] or ''}") for r in catalog]
    bm = bm25_rank(qtext, docs)
    bm_top = [jid for jid, _ in bm[:TOP_PER_ENGINE]]

    # --- retriever 3: vector (E3 semantic leg) ---
    vec_top = []
    try:
        qvec = _encode_text(qtext, _load_encoder())
        hits = vindex.query(qvec, k=TOP_PER_ENGINE * 2)
        vec_top = [jid for jid, _ in hits if jid in by_id][:TOP_PER_ENGINE]
    except Exception as e:  # noqa: BLE001
        print(f"  {uid}: vector retrieval failed ({type(e).__name__}: {e})")

    # --- fair union with provenance ---
    prov: dict = {}
    for src_name, ids in (("keyword", kw_top), ("bm25", bm_top), ("vector", vec_top)):
        for jid in ids:
            prov.setdefault(jid, []).append(src_name)
    pool_ids = list(prov.keys())
    kw_map = dict(kw)

    # --- make E2 measurable: enrich every pooled job ---
    pool_jobs = []
    for jid in pool_ids:
        r = by_id[jid]
        j = Job(title=r["title"] or "x", company=r["company"] or "c", apply_url="u",
                source="s", date_found=r["date_found"] or "2026-01-01",
                location=r["location"] or "", description=r["description"] or "")
        j.id = jid
        pool_jobs.append(j)
    enr = await enrich_batch(pool_jobs, semaphore_limit=3, skip_existing=True, conn=conn)
    n_enr = sum(1 for e in enr if e is not None)

    # --- make E3 measurable: embed every pooled job missing from the index ---
    enr_by_id = {j.id: e for j, e in zip(pool_jobs, enr)}
    embedded = 0
    for j in pool_jobs:
        # Always upsert (idempotent) so every pooled job is in the index with a
        # consistent (job, enrichment) embedding — the vector leg then fires on
        # ALL pooled jobs (no silent keyword fallback).
        try:
            vec = encode_job(j, enr_by_id.get(j.id))
            vindex.upsert(j.id, vec, {"job_id": int(j.id)})
            embedded += 1
        except Exception as e:  # noqa: BLE001
            print(f"  {uid}: embed failed for {j.id} ({type(e).__name__})")

    return profile, pool_ids, prov, kw_map, n_enr, embedded


def main() -> None:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from src.core.settings import DB_PATH
    from src.services.vector_index import VectorIndex

    sconn = sqlite3.connect(str(DB_PATH))
    sconn.row_factory = sqlite3.Row
    catalog = [dict(r) for r in sconn.execute(
        "SELECT id,title,company,location,description,date_found FROM jobs")]
    vindex = VectorIndex()

    import aiosqlite

    async def run():
        aconn = await aiosqlite.connect(str(DB_PATH))
        aconn.row_factory = aiosqlite.Row
        results = {}
        for uid in UIDS:
            profile, pool_ids, prov, kw_map, n_enr, embedded = await _build_for_user(
                uid, aconn, catalog, vindex)
            results[uid] = (profile, pool_ids, prov, kw_map, n_enr, embedded)
        await aconn.close()
        return results

    results = asyncio.run(run())

    by_id = {r["id"]: r for r in catalog}
    pool_out = {}
    sheet = open(SHEET, "w", encoding="utf-8")
    from src.services.profile.storage import current_profile_version_id

    for uid in UIDS:
        profile, pool_ids, prov, kw_map, n_enr, embedded = results[uid]
        # write feed
        ver = current_profile_version_id(uid)
        sconn.execute("DELETE FROM user_feed WHERE user_id=?", (uid,))
        for jid in pool_ids:
            sconn.execute(
                "INSERT INTO user_feed(user_id,job_id,score,bucket,status,profile_version) "
                "VALUES (?,?,?,?,'active',?)",
                (uid, jid, int(kw_map.get(jid, 0)), "top", ver))
        sconn.commit()
        pool_out[uid] = {"ids": pool_ids, "prov": {str(k): v for k, v in prov.items()}}
        print(f"{uid}: pool={len(pool_ids)} (kw+bm25+vector union) | enriched={n_enr} | embedded={embedded}")

        # --- BLIND sheet: shuffled, NO scores ---
        cv = profile.cv_data
        p = profile.preferences
        order = list(pool_ids)
        random.Random(hash(uid) & 0xFFFF).shuffle(order)
        sheet.write(f"\n########## {cv.name} ({uid}) — BLIND ##########\n")
        sheet.write(f"Titles: {', '.join((cv.job_titles or [])[:6])}\n")
        sheet.write(f"Skills: {', '.join((cv.skills or [])[:18])}\n")
        sheet.write(f"PREFS: level={p.experience_level}, salary={p.salary_min}-{p.salary_max}, "
                    f"workplace={p.preferred_workplace}, visa={p.needs_visa}\n")
        sheet.write("Grade 0-100 fit (domain/role, seniority, skills, location/remote+visa, salary). "
                    "NO engine scores shown — grade blind.\n\n")
        for jid in order:
            r = by_id[jid]
            desc = (r["description"] or "").replace("\n", " ")[:300]
            sheet.write(f"== {jid} == {r['title']} | {r['company']} | {r['location']}\n{desc}\n\n")
    sheet.close()
    # MERGE into existing pool file (keep prior profiles' pools).
    import os
    existing = {}
    if os.path.exists(POOL_OUT):
        try:
            existing = json.load(open(POOL_OUT))
        except Exception:
            existing = {}
    existing.update(pool_out)
    json.dump(existing, open(POOL_OUT, "w"))
    sconn.close()
    print(f"\nBLIND sheet -> {SHEET}\npool -> {POOL_OUT}")


if __name__ == "__main__":
    main()
