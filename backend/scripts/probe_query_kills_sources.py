"""Prove WHICH input kills the 'dead' sources: the credential, or the QUERY.

The 04:00 catalog cron builds its SearchConfig from the union of every user's
profile `job_titles` (workers/tasks.py:1429-1455). Live prod values include the
literal string 'AI Solutions Engineer � R&D Department' — a CV heading with a
corrupted byte, not a searchable role.

This runs the SAME source twice: once with a neutral query, once with the real
production query. Same key, same code, same network — only the question changes.

    railway run -s backend python scripts/probe_query_kills_sources.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

import aiohttp

logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, ".")

from src.core.settings import (  # noqa: E402
    CAREERJET_AFFID,
    DFE_APPRENTICESHIPS_API_KEY,
    FINDWORK_API_KEY,
    JOOBLE_API_KEY,
    SERPAPI_KEY,
)
from src.services.profile.models import SearchConfig  # noqa: E402
from src.sources.apis_keyed.careerjet import CareerjetSource  # noqa: E402
from src.sources.apis_keyed.findwork import FindworkSource  # noqa: E402
from src.sources.apis_keyed.google_jobs import GoogleJobsSource  # noqa: E402
from src.sources.apis_keyed.gov_apprenticeships import GovApprenticeshipsSource  # noqa: E402
from src.sources.apis_keyed.jooble import JoobleSource  # noqa: E402

# Verbatim from prod `user_profiles.cv_data.job_titles` (2026-08-17).
PROD_TITLES = [
    "AI Solutions Engineer � R&D Department",
    "AI/ML Engineer Intern",
    "Blockchain Engineer Intern",
    "Software Development Engineer in Test (SDET)",
    "Intern",
    "Founder & Product Analyst",
    "Blockchain Developer",
    "QA Engineer",
    "Software Engineer",
]

NEUTRAL = SearchConfig()
NEUTRAL.job_titles = ["software engineer"]
NEUTRAL.search_titles = ["software engineer"]
NEUTRAL.search_queries = ["software engineer"]

PROD = SearchConfig()
PROD.job_titles = list(PROD_TITLES)
PROD.search_titles = list(PROD_TITLES)
PROD.search_queries = list(PROD_TITLES)

FACTORIES = {
    "findwork": lambda s, cfg: FindworkSource(s, api_key=FINDWORK_API_KEY, search_config=cfg),
    "gov_apprenticeships": lambda s, cfg: GovApprenticeshipsSource(
        s, api_key=DFE_APPRENTICESHIPS_API_KEY, search_config=cfg
    ),
    "careerjet": lambda s, cfg: CareerjetSource(s, affid=CAREERJET_AFFID, search_config=cfg),
    "jooble": lambda s, cfg: JoobleSource(s, api_key=JOOBLE_API_KEY, search_config=cfg),
    "google_jobs": lambda s, cfg: GoogleJobsSource(s, api_key=SERPAPI_KEY, search_config=cfg),
}


async def count(name: str, cfg: SearchConfig) -> str:
    timeout = aiohttp.ClientTimeout(total=150)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            src = FACTORIES[name](session, cfg)
            jobs = await asyncio.wait_for(src.fetch_jobs(), timeout=180)
            return str(len(jobs or []))
    except asyncio.TimeoutError:
        return "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return "ERR:%s" % type(exc).__name__


async def main() -> None:
    print("%-22s %14s %14s   %s" % ("source", "neutral query", "PROD query", "verdict"))
    print("-" * 76)
    for name in FACTORIES:
        neutral = await count(name, NEUTRAL)
        prod = await count(name, PROD)
        verdict = "same"
        if neutral.isdigit() and prod.isdigit():
            n, p = int(neutral), int(prod)
            if n > 0 and p == 0:
                verdict = "*** QUERY KILLS IT ***"
            elif n == 0 and p == 0:
                verdict = "dead either way (not the query)"
            elif p > 0:
                verdict = "works with prod query"
        print("%-22s %14s %14s   %s" % (name, neutral, prod, verdict))


if __name__ == "__main__":
    asyncio.run(main())
