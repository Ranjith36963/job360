"""Compare 5 enrichment strategies on a diverse, cherry-picked job sample.

Keyword + semantic are the FIXED foundation; this only tests the enrichment
layer. Levels:

  L1  Rules + narrow LLM   — rules + LLM, merged by FIELD STRENGTH:
                             salary from rules/source, seniority+workplace from
                             the LLM (it reads meaning), visa from rules if a
                             confident yes/no else the LLM.
  L2  Rules only (no LLM)  — word-boundary regex/keywords pull the 5 facts.
  L5  Narrow LLM only      — LLM extracts the 5 facts, no rules.
  L3  Rules + full matcher — rule facts handed to an LLM told to USE them,
                             judging profile-vs-job across explicit dimensions.
  L4  Full matcher only    — same matcher, no rule facts at all.

The 5 facts: salary, workplace (remote/hybrid/onsite), seniority, visa.
L1/L5 share ONE extraction call; L3/L4 are two matcher calls — ~3 LLM calls/job.

Honest by design: prints each level's output, latency, and an LLM token
ESTIMATE (chars/4). It does NOT score accuracy — that needs human labels; it
surfaces disagreements so you can see where each approach breaks. Live LLM
calls degrade gracefully (Gemini quota-blocked -> Groq/Cerebras); a dead
provider is reported per job, not hidden.

Usage:  cd backend && python scripts/compare_enrichment_levels.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import aiohttp  # noqa: E402

from src.core.settings import REQUEST_TIMEOUT  # noqa: E402
from src.models import Job  # noqa: E402
from src.services.job_enrichment import enrich_job  # noqa: E402
from src.services.profile.keyword_generator import generate_search_config  # noqa: E402
from src.services.profile.llm_provider import llm_extract  # noqa: E402
from src.services.profile.storage import load_profile  # noqa: E402
from src.services.skill_matcher import check_visa_flag, detect_experience_level  # noqa: E402

DEMO_USER = "e34aeb69e9bf4680bd143e1f3756140a"
PER_SOURCE_TIMEOUT = 45
JOBS_PER_SOURCE = 2
LLM_JOB_CAP = 10  # cap LLM-level jobs to stay inside free-tier quota


def est_tokens(*texts: str) -> int:
    return sum(len(t or "") for t in texts) // 4


# --------------------------------------------------------------------------
# L2 — rules only. Word-boundary patterns (no "intern" inside "international").
# Ordered by seniority DESC so the strongest signal wins first.
# --------------------------------------------------------------------------

_SEN_RULES = [
    ("principal", re.compile(r"\bprincipal\b", re.I)),
    ("staff", re.compile(r"\bstaff\s+(engineer|scientist|developer)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|lead|head\s+of)\b", re.I)),
    ("mid", re.compile(r"\bmid[\s-]?level\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|graduate|entry[\s-]level|trainee)\b", re.I)),
    ("intern", re.compile(r"\b(intern|internship)\b", re.I)),
]
_VISA_NEG = re.compile(r"\b(no|not|cannot|can't|unable to|won't|do not|are not able to)\b[^.]{0,40}\b(sponsor|sponsorship|visa)", re.I)
_VISA_POS = re.compile(r"\b(visa sponsorship|sponsorship (is )?available|will sponsor|can sponsor|sponsor(ed)? visa|tier 2|skilled worker visa)\b", re.I)
_SALARY = re.compile(r"[£$€]\s?(\d{2,3})(?:,?(\d{3}))?\s?(k|,000)?", re.I)


def rules_extract(job: Job) -> dict:
    text = f"{job.title}\n{job.description or ''}"

    # seniority — title helper first (most reliable), then word-boundary body scan
    seniority = detect_experience_level(job.title) or ""
    if not seniority:
        for val, pat in _SEN_RULES:
            if pat.search(text):
                seniority = val
                break
    seniority = seniority or "unknown"

    # workplace — hybrid beats remote beats onsite
    low = text.lower()
    if "hybrid" in low:
        workplace = "hybrid"
    elif re.search(r"\b(fully )?remote\b", low):
        workplace = "remote"
    elif re.search(r"\b(on[\s-]?site|in[\s-]?office|in person|on premises)\b", low):
        workplace = "onsite"
    else:
        workplace = "unknown"

    # visa — negation wins over a bare keyword
    if _VISA_NEG.search(text):
        visa = "no"
    elif _VISA_POS.search(text) or check_visa_flag(job):
        visa = "yes"
    else:
        visa = "unknown"

    # salary — source-parsed field first, else a £/$/€ regex on the text
    smin, smax = job.salary_min, job.salary_max
    if (smin is None or smin == 0) and (smax is None or smax == 0):
        m = _SALARY.search(text)
        if m:
            base = int(m.group(1) + (m.group(2) or ""))
            if m.group(3) and m.group(3).lower() == "k":
                base *= 1000
            smin = float(base)
    salary = {"min": smin or None, "max": smax or None}

    return {"seniority": seniority, "workplace": workplace, "visa": visa, "salary": salary}


def llm_facts(enr) -> dict:
    sal = getattr(enr, "salary", None)
    return {
        "seniority": getattr(enr, "seniority", None) or "unknown",
        "workplace": getattr(enr, "workplace_type", None) or "unknown",
        "visa": getattr(enr, "visa_sponsorship", None) or "unknown",
        "salary": {"min": getattr(sal, "min", None), "max": getattr(sal, "max", None)} if sal else {"min": None, "max": None},
    }


def merge_rules_llm(rules: dict, llm: dict) -> dict:
    """L1 — merge by which engine is BEST at each field (the fix from run 1):
      * salary    -> rules/source first (the LLM keeps dropping structured salaries)
      * seniority -> LLM first (it reads 'Pleno'=mid, avoids false matches), rules fallback
      * workplace -> LLM first, rules fallback
      * visa      -> rules first if a confident yes/no (negation-aware), else LLM
    """
    def pick(primary, fallback):
        return primary if primary and primary != "unknown" else fallback

    rs = rules.get("salary") or {}
    ls = llm.get("salary") or {}
    return {
        "seniority": pick(llm.get("seniority"), rules.get("seniority", "unknown")),
        "workplace": pick(llm.get("workplace"), rules.get("workplace", "unknown")),
        "visa": pick(rules.get("visa"), llm.get("visa", "unknown")),
        "salary": {
            "min": rs.get("min") if rs.get("min") is not None else ls.get("min"),
            "max": rs.get("max") if rs.get("max") is not None else ls.get("max"),
        },
    }


# --------------------------------------------------------------------------
# L3/L4 — full matcher. Explicit fit dimensions; L3 is told to USE the facts.
# --------------------------------------------------------------------------

def profile_text(profile) -> str:
    cv = getattr(profile, "cv_data", None)
    prefs = getattr(profile, "preferences", None)
    titles = ", ".join(getattr(cv, "job_titles", []) or []) if cv else ""
    skills = ", ".join((getattr(cv, "skills", []) or [])[:30]) if cv else ""
    summary = (getattr(cv, "summary", "") or "")[:600] if cv else ""
    pref_bits = []
    if prefs:
        for attr in ("experience_level", "work_arrangement"):
            v = getattr(prefs, attr, None)
            if v:
                pref_bits.append(f"{attr}={v}")
        smin = getattr(prefs, "salary_min", None)
        if smin:
            pref_bits.append(f"salary_min={smin}")
    return (f"Target titles: {titles}\nSkills: {skills}\nSummary: {summary}\n"
            f"Preferences: {', '.join(pref_bits)}")


_MATCH_RUBRIC = (
    "Score the candidate-job fit 0-100 by weighing FOUR dimensions: "
    "(1) domain/role match, (2) seniority fit vs the candidate's level, "
    "(3) skills overlap, (4) location/remote + visa fit. "
    'Return ONLY JSON: {"fit_score": <0-100 int>, "verdict": "<=8 words", '
    '"reason": "<=200 chars naming the deciding dimension"}.'
)


def matcher_prompt(prof_txt: str, job: Job, rule_facts: dict | None) -> str:
    hint = ""
    if rule_facts is not None:
        hint = ("\nPRE-EXTRACTED FACTS about this job (from rules — use them, "
                f"but trust the description if they conflict): {json.dumps(rule_facts, default=str)}\n")
    return (
        f"{_MATCH_RUBRIC}\n\n"
        f"CANDIDATE PROFILE:\n{prof_txt}\n{hint}\n"
        f"JOB:\nTitle: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        f"Description:\n{(job.description or '')[:3500]}\n"
    )


async def timed_llm(coro):
    t = time.perf_counter()
    try:
        out = await coro
        return out, (time.perf_counter() - t), None
    except Exception as e:  # noqa: BLE001 — report quota/provider death honestly
        return None, (time.perf_counter() - t), f"{type(e).__name__}: {str(e)[:120]}"


# --------------------------------------------------------------------------
# Fetch a diverse, cherry-picked sample (structured -> brutal free-text)
# --------------------------------------------------------------------------

async def fetch_sample(session, sc) -> list[tuple[str, Job]]:
    from src.core.settings import ADZUNA_APP_ID, ADZUNA_APP_KEY, REED_API_KEY
    from src.sources.apis_free.arbeitnow import ArbeitnowSource
    from src.sources.apis_free.himalayas import HimalayasSource
    from src.sources.apis_free.remoteok import RemoteOKSource
    from src.sources.apis_free.remotive import RemotiveSource
    from src.sources.apis_keyed.adzuna import AdzunaSource
    from src.sources.apis_keyed.reed import ReedSource
    from src.sources.feeds.weworkremotely import WeWorkRemotelySource
    from src.sources.other.hackernews import HackerNewsSource

    sources = [
        ("reed (keyed/structured)", ReedSource(session, api_key=REED_API_KEY, search_config=sc)),
        ("adzuna (keyed/structured)", AdzunaSource(session, app_id=ADZUNA_APP_ID, app_key=ADZUNA_APP_KEY, search_config=sc)),
        ("remotive (free-json)", RemotiveSource(session, search_config=sc)),
        ("arbeitnow (free-json)", ArbeitnowSource(session, search_config=sc)),
        ("himalayas (free-json)", HimalayasSource(session, search_config=sc)),
        ("weworkremotely (rss)", WeWorkRemotelySource(session, search_config=sc)),
        ("remoteok (sparse)", RemoteOKSource(session, search_config=sc)),
        ("hackernews (brutal free-text)", HackerNewsSource(session, search_config=sc)),
    ]
    picked: list[tuple[str, Job]] = []
    for label, src in sources:
        try:
            jobs = await asyncio.wait_for(src.fetch_jobs(), timeout=PER_SOURCE_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            print(f"  [{label}] fetch failed: {type(e).__name__}: {str(e)[:80]}")
            continue
        jobs = [j for j in (jobs or []) if (j.description or "").strip()][:JOBS_PER_SOURCE]
        for j in jobs:
            picked.append((label, j))
        print(f"  [{label}] kept {len(jobs)} job(s)")
    return picked


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

async def main() -> None:
    profile = load_profile(DEMO_USER)
    if not profile:
        print("No profile for demo user — aborting.")
        return
    sc = generate_search_config(profile)
    prof_txt = profile_text(profile)
    print("=" * 80)
    print("PROFILE USED (compact):")
    print(prof_txt[:500])
    print("=" * 80)

    connector = aiohttp.TCPConnector(limit=20, limit_per_host=5)
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        print("\nFETCHING diverse sample (1-2 jobs/source)...")
        sample = await fetch_sample(session, sc)
    print(f"\nGot {len(sample)} jobs total.\n")

    agg = {lvl: {"latency": 0.0, "calls": 0, "tokens": 0} for lvl in ("L1", "L2", "L3", "L4", "L5")}
    records = []
    disagreements = {"seniority": 0, "workplace": 0, "visa": 0, "salary": 0}

    for i, (label, job) in enumerate(sample, 1):
        print("=" * 80)
        print(f"JOB {i}/{len(sample)}  [{label}]")
        print(f"  title: {job.title!r}")
        print(f"  desc length: {len(job.description or '')} chars")

        t = time.perf_counter()
        rules = rules_extract(job)
        agg["L2"]["latency"] += time.perf_counter() - t
        print(f"  L2 rules-only        : {json.dumps(rules, default=str)}")

        run_llm = i <= LLM_JOB_CAP
        l1 = l5 = l3 = l4 = None
        if run_llm:
            enr, lat, err = await timed_llm(enrich_job(job))
            for lv in ("L5", "L1"):
                agg[lv]["latency"] += lat; agg[lv]["calls"] += 1
                agg[lv]["tokens"] += est_tokens(job.title, job.description or "")
            if err:
                print(f"  L5 llm-only          (FAILED {lat:.1f}s): {err}")
            else:
                l5 = llm_facts(enr)
                l1 = merge_rules_llm(rules, l5)
                for f in ("seniority", "workplace", "visa"):
                    if rules.get(f) != l5.get(f) and "unknown" not in (rules.get(f), l5.get(f)):
                        disagreements[f] += 1
                print(f"  L5 llm-only          ({lat:.1f}s): {json.dumps(l5, default=str)}")
                print(f"  L1 rules+llm(merged) (~{lat:.1f}s): {json.dumps(l1, default=str)}")

            p4 = matcher_prompt(prof_txt, job, None)
            out4, lat4, err4 = await timed_llm(llm_extract(p4))
            agg["L4"]["latency"] += lat4; agg["L4"]["calls"] += 1; agg["L4"]["tokens"] += est_tokens(p4)
            l4 = None if err4 else out4
            print(f"  L4 matcher-only      ({lat4:.1f}s, ~{est_tokens(p4)}tok): {err4 or json.dumps(out4, default=str)[:200]}")

            p3 = matcher_prompt(prof_txt, job, rules)
            out3, lat3, err3 = await timed_llm(llm_extract(p3))
            agg["L3"]["latency"] += lat3; agg["L3"]["calls"] += 1; agg["L3"]["tokens"] += est_tokens(p3)
            l3 = None if err3 else out3
            print(f"  L3 rules+matcher     ({lat3:.1f}s, ~{est_tokens(p3)}tok): {err3 or json.dumps(out3, default=str)[:200]}")
        else:
            print("  (LLM levels skipped — past quota cap)")

        records.append({"label": label, "title": job.title,
                        "company": job.company, "location": job.location,
                        "salary_min": job.salary_min, "salary_max": job.salary_max,
                        "description": (job.description or "")[:1800],
                        "L2": rules, "L1": l1, "L5": l5, "L3": l3, "L4": l4})

    print("\n" + "=" * 80)
    print("SUMMARY  (latency = total across LLM-run jobs; tokens = rough chars/4 estimate)")
    print("=" * 80)
    print(f"{'Level':<22}{'LLM calls':<12}{'total sec':<12}{'est tokens':<12}")
    names = {"L1": "L1 rules+llm", "L2": "L2 rules-only", "L5": "L5 llm-only",
             "L3": "L3 rules+matcher", "L4": "L4 matcher-only"}
    for lvl in ("L2", "L1", "L5", "L3", "L4"):
        a = agg[lvl]
        print(f"{names[lvl]:<22}{a['calls']:<12}{a['latency']:<12.1f}{a['tokens']:<12}")
    print(f"\nRules-vs-LLM disagreements (both confident): {disagreements}")

    Path("data/experiment").mkdir(parents=True, exist_ok=True)
    out = Path("data/experiment/enrichment_levels.json")
    out.write_text(json.dumps({"aggregate": agg, "disagreements": disagreements, "records": records},
                              indent=2, default=str), encoding="utf-8")
    print(f"\nFull per-job records: {out}")


if __name__ == "__main__":
    asyncio.run(main())
