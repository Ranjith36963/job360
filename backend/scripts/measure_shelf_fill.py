"""Live before/after measurement for the 10 assigned ATS sources.

Fetches real jobs from each source (no UK-gate bypass — same filter path as
prod), runs them through the real shelf_gate.fill_shelves(), and reports
per-shelf fill % so the improvement claim is a measured number, not a guess.
DELETE after use.
"""
import asyncio
import sys

import aiohttp

sys.path.insert(0, ".")

from src.models import UNIVERSAL_SHELF  # noqa: E402
from src.services.shelf_gate import fill_shelves  # noqa: E402
from src.sources.ats.ashby import AshbySource  # noqa: E402
from src.sources.ats.greenhouse import GreenhouseSource  # noqa: E402
from src.sources.ats.lever import LeverSource  # noqa: E402
from src.sources.ats.personio import PersonioSource  # noqa: E402
from src.sources.ats.pinpoint import PinpointSource  # noqa: E402
from src.sources.ats.recruitee import RecruiteeSource  # noqa: E402
from src.sources.ats.smartrecruiters import SmartRecruitersSource  # noqa: E402
from src.sources.ats.successfactors import SuccessFactorsSource  # noqa: E402
from src.sources.ats.workable import WorkableSource  # noqa: E402
from src.sources.ats.workday import WorkdaySource  # noqa: E402


def _fill_report(jobs):
    n = len(jobs)
    if n == 0:
        return {}
    counts = dict.fromkeys(UNIVERSAL_SHELF, 0)
    for job in jobs:
        fill_shelves(job)
        for shelf in UNIVERSAL_SHELF:
            entry = job.shelf_provenance[shelf]
            if entry["how"] != "absent":
                counts[shelf] += 1
    return {shelf: round(100 * counts[shelf] / n, 1) for shelf in UNIVERSAL_SHELF}


async def main():
    async with aiohttp.ClientSession() as session:
        runs = [
            ("greenhouse", GreenhouseSource(session, companies=["deepmind", "monzo", "stabilityai"])),
            ("lever", LeverSource(session, companies=["spotify", "palantir", "healx"])),
            ("workable", WorkableSource(session, companies=["huggingface", "appvia", "suade", "yapily"])),
            ("ashby", AshbySource(session, companies=["cohere"])),
            ("smartrecruiters", SmartRecruitersSource(session, companies=["wise"])),
            ("pinpoint", PinpointSource(session, companies=["arm", "priorygroup", "davies", "networkplus", "natcen"])),
            ("recruitee", RecruiteeSource(session, companies=["wonderkind", "dckgroup", "transperfect", "itxuklimited", "theentouragegroup"])),
            ("personio", PersonioSource(session, companies=["personio", "flatpay", "stark", "merantix", "intigriti", "olio", "gridx", "maltego"])),
            ("successfactors", SuccessFactorsSource(session)),
        ]
        for name, source in runs:
            try:
                jobs = await source.fetch_jobs()
            except Exception as e:
                print(f"{name}: FETCH ERROR {e}")
                continue
            report = _fill_report(jobs)
            print(f"\n=== {name} (n={len(jobs)}) ===")
            for shelf, pct in report.items():
                print(f"  {shelf}: {pct}%")

        # Workday: bounded run (full run times out at 240s ceiling; use a
        # short search-title list and one company to get a real, if partial,
        # sample without hitting the timeout).
        print("\n=== workday (bounded, 1 company) ===")
        from src.services.profile.models import SearchConfig
        sc = SearchConfig(job_titles=["Software Engineer"], search_queries=["engineer"])
        wd_source = WorkdaySource(
            session,
            companies=[{"tenant": "astrazeneca", "wd": "wd3", "site": "Careers", "name": "AstraZeneca"}],
            search_config=sc,
        )
        try:
            jobs = await asyncio.wait_for(wd_source.fetch_jobs(), timeout=90)
            report = _fill_report(jobs)
            print(f"n={len(jobs)}")
            for shelf, pct in report.items():
                print(f"  {shelf}: {pct}%")
        except asyncio.TimeoutError:
            print("workday: timed out even bounded (confirms the separate, out-of-scope timeout issue)")

if __name__ == "__main__":
    asyncio.run(main())
