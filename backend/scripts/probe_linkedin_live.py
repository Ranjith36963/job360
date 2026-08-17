"""Live run of the REAL LinkedInSource class to see what actually lands on Job
objects, using real network. Read-only, no DB. DELETE when done."""
import asyncio
import logging

import aiohttp

from src.services.profile.models import SearchConfig
from src.sources.scrapers.linkedin import LinkedInSource

logging.basicConfig(level=logging.DEBUG)


async def main():
    async with aiohttp.ClientSession() as session:
        cfg = SearchConfig(job_titles=["software engineer"])
        src = LinkedInSource(session, search_config=cfg)
        jobs = await src.fetch_jobs()
        print("total jobs:", len(jobs))
        for j in jobs[:15]:
            print("---")
            print("title:", repr(j.title))
            print("company:", repr(j.company))
            print("location:", repr(j.location))
            print("description len:", len(j.description or ""))
            print("posted_at:", j.posted_at)
            print("deadline:", j.deadline)
            print("employment_type:", j.employment_type)


if __name__ == "__main__":
    asyncio.run(main())
