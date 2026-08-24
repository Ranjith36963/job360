"""Probe the sources that returned ZERO in the last production run.

Answers one question per source, with evidence, and never prints a key value:
  * is a credential configured at all?
  * does the upstream answer?
  * how many jobs come back, and if zero — WHY (exception / empty payload)?

Run with production credentials injected:

    railway run -s backend python scripts/probe_dead_sources.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import traceback

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

sys.path.insert(0, ".")

from src.core.settings import (  # noqa: E402
    CAREERJET_AFFID,
    DFE_APPRENTICESHIPS_API_KEY,
    FINDWORK_API_KEY,
    JOOBLE_API_KEY,
    JSEARCH_API_KEY,
    SERPAPI_KEY,
)
from src.sources.apis_keyed.careerjet import CareerjetSource  # noqa: E402
from src.sources.apis_keyed.findwork import FindworkSource  # noqa: E402
from src.sources.apis_keyed.google_jobs import GoogleJobsSource  # noqa: E402
from src.sources.apis_keyed.gov_apprenticeships import GovApprenticeshipsSource  # noqa: E402
from src.sources.apis_keyed.jooble import JoobleSource  # noqa: E402
from src.sources.apis_keyed.jsearch import JSearchSource  # noqa: E402
from src.sources.ats.workday import WorkdaySource  # noqa: E402
from src.sources.other.indeed import JobSpySource  # noqa: E402


def cred(value: object) -> str:
    """Report ONLY whether a credential exists — never its value."""
    return "SET" if value else "MISSING"


async def probe(name: str, factory, credential_state: str) -> dict:
    out = {"source": name, "credential": credential_state, "jobs": 0, "error": ""}
    timeout = aiohttp.ClientTimeout(total=120)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            src = factory(session)
            configured = getattr(src, "is_configured", True)
            out["configured"] = bool(configured)
            jobs = await asyncio.wait_for(src.fetch_jobs(), timeout=150)
            out["jobs"] = len(jobs or [])
            if jobs:
                j = jobs[0]
                out["sample"] = "%s | %s | %s" % (
                    (j.title or "")[:45],
                    (j.company or "")[:22],
                    (j.location or "")[:22],
                )
    except asyncio.TimeoutError:
        out["error"] = "TIMEOUT after 150s"
    except Exception as exc:  # noqa: BLE001 — this probe reports every failure
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:220])
        out["trace"] = traceback.format_exc(limit=3)[-400:]
    return out


async def main() -> None:
    probes = [
        ("jsearch", lambda s: JSearchSource(s, api_key=JSEARCH_API_KEY), cred(JSEARCH_API_KEY)),
        ("jooble", lambda s: JoobleSource(s, api_key=JOOBLE_API_KEY), cred(JOOBLE_API_KEY)),
        ("google_jobs", lambda s: GoogleJobsSource(s, api_key=SERPAPI_KEY), cred(SERPAPI_KEY)),
        ("careerjet", lambda s: CareerjetSource(s, affid=CAREERJET_AFFID), cred(CAREERJET_AFFID)),
        ("findwork", lambda s: FindworkSource(s, api_key=FINDWORK_API_KEY), cred(FINDWORK_API_KEY)),
        (
            "gov_apprenticeships",
            lambda s: GovApprenticeshipsSource(s, api_key=DFE_APPRENTICESHIPS_API_KEY),
            cred(DFE_APPRENTICESHIPS_API_KEY),
        ),
        ("indeed+glassdoor (JobSpy)", lambda s: JobSpySource(s), "n/a (scraper)"),
        ("workday", lambda s: WorkdaySource(s), "n/a (public ATS)"),
    ]

    results = []
    for name, factory, credential_state in probes:
        print("\n" + "=" * 68)
        print("PROBING:", name, " credential:", credential_state)
        print("=" * 68)
        res = await probe(name, factory, credential_state)
        results.append(res)
        print(
            "  -> jobs=%s  configured=%s  error=%s"
            % (res["jobs"], res.get("configured"), res["error"] or "none")
        )
        if res.get("sample"):
            print("  -> sample:", res["sample"])
        if res.get("trace"):
            print("  -> trace tail:", res["trace"].replace("\n", " | ")[:380])

    print("\n\n" + "#" * 68)
    print("VERDICT TABLE")
    print("#" * 68)
    print("%-28s %-12s %6s  %s" % ("source", "credential", "jobs", "why zero"))
    for r in results:
        why = r["error"] or ("" if r["jobs"] else "returned empty payload, no exception")
        print("%-28s %-12s %6d  %s" % (r["source"], r["credential"], r["jobs"], why[:110]))


if __name__ == "__main__":
    asyncio.run(main())
