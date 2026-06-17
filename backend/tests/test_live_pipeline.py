"""Real ONLINE end-to-end test — NO mocks.

Unlike the rest of the suite (which mocks HTTP per rule #4 to stay offline +
fast), this test actually calls live free job-site APIs and runs the real
pipeline (fetch -> parse -> dedup -> score) on what comes back. It exists to
catch the failure that fake tests cannot: an upstream API that quietly changed
its shape, started blocking us, or went away.

It is marked ``live`` and EXCLUDED from the default run via the ``-m 'not live'``
addopts in pyproject.toml, so it never slows or flakes the per-commit gate.

Run it on demand:        python -m pytest -m live -v
(or via the Makefile:    make test-live)
It also runs nightly in CI (.github/workflows/live-e2e.yml).

Only keyless, reliable free sources are used so no API keys are needed. To
tolerate a single upstream being momentarily down, the test requires that
*at least one* source returns jobs — it fails only if the whole live path is
broken.
"""
import asyncio

import aiohttp
import pytest

from src.services.deduplicator import deduplicate
from src.services.profile.models import SearchConfig
from src.services.skill_matcher import score_job
from src.sources.apis_free.arbeitnow import ArbeitnowSource
from src.sources.apis_free.remoteok import RemoteOKSource
from src.sources.apis_free.remotive import RemotiveSource

pytestmark = pytest.mark.live


def _search_config() -> SearchConfig:
    """A realistic profile so query-driven sources actually loop."""
    return SearchConfig(
        job_titles=["Software Engineer", "Developer"],
        search_queries=["software engineer"],
        relevance_keywords=["python", "javascript", "engineer", "developer"],
        primary_skills=["Python"],
        secondary_skills=["Docker"],
    )


@pytest.mark.real_sleep
def test_live_pipeline_fetches_and_scores_real_jobs():
    """Hit live free APIs, then run the real dedup + scoring on the results.

    Proves the end-to-end path works against the real internet — not against
    canned responses.
    """
    async def _run():
        async with aiohttp.ClientSession() as session:
            sc = _search_config()
            sources = [
                ArbeitnowSource(session, search_config=sc),
                RemotiveSource(session, search_config=sc),
                RemoteOKSource(session, search_config=sc),
            ]
            results = await asyncio.gather(
                *(s.fetch_jobs() for s in sources),
                return_exceptions=True,
            )

        live_jobs = []
        ok_sources = []
        failures = []
        for src, res in zip(sources, results):
            if isinstance(res, Exception):
                failures.append(f"{src.name}: {res!r}")
            elif isinstance(res, list) and res:
                ok_sources.append(src.name)
                live_jobs.extend(res)

        # At least one real source must answer with real jobs. If ALL are
        # empty/erroring, the live fetch path is broken — fail loudly.
        assert ok_sources, (
            "No live source returned any jobs — the online pipeline is broken "
            f"or every upstream is down. Failures: {failures}"
        )

        # Real jobs must carry the core fields the pipeline relies on.
        sample = live_jobs[0]
        assert sample.title, "live job missing title"
        assert sample.company, "live job missing company"
        assert sample.apply_url, "live job missing apply_url"

        # The real dedup must survive real data (never inflate the count).
        deduped = deduplicate(live_jobs)
        assert 0 < len(deduped) <= len(live_jobs)

        # The real scorer must return an in-range score for real jobs.
        for job in deduped[:25]:
            score = score_job(job)
            assert 0 <= score <= 100, f"out-of-range score {score} for {job.title!r}"

    asyncio.run(_run())
