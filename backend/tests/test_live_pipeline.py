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


def test_live_full_pipeline_all_three_pillars(tmp_path, monkeypatch):
    """ONLINE end-to-end across ALL THREE PILLARS via the real run_search().

    Drives the actual pipeline against the live internet, into a throwaway DB:
      * Pillar 1 (sources): every source is built + queried live; we assert
        several real sources returned jobs.
      * Pillar 2 (search/match): the real dedup + scoring runs and inserts
        real jobs (new_jobs > 0).
      * Pillar 3 (delivery): the per-user user_feed is written by run_search
        and read back through the real DB read path, scored + non-empty.

    Notifications are off (no_notify=True, and no channels configured in CI).
    Enrichment/semantic/LLM-judge stay off by default (they need API keys);
    this proves the core spine of all three pillars against real data.
    """
    import uuid
    from pathlib import Path

    import src.core.settings as _settings
    import src.services.profile.storage as _storage
    from migrations import runner
    from src.main import run_search
    from src.repositories import pg
    from src.repositories.database import JobDatabase
    from src.services.auth.passwords import hash_password
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    db_file = str(tmp_path / "live_e2e.db")
    # DB_PATH gotcha: profile save/load read the module-level constant bound at
    # import time, so patch it on BOTH the settings module and the storage module.
    monkeypatch.setattr(_settings, "DB_PATH", Path(db_file))
    monkeypatch.setattr(_storage, "DB_PATH", Path(db_file))

    async def _bootstrap():
        db = JobDatabase(db_file)
        await db.init_db()
        await db.close()
        await runner.up(db_file)  # users, user_feed, user_profiles, ...

    async def _create_user():
        uid = uuid.uuid4().hex
        async with pg.connect(db_file) as c:
            await c.execute(
                "INSERT INTO users(id, email, password_hash) VALUES (?, ?, ?)",
                (uid, "live-e2e@example.com", hash_password("testpassword123")),
            )
            await c.commit()
        return uid

    asyncio.run(_bootstrap())
    user_id = asyncio.run(_create_user())

    profile = UserProfile(
        cv_data=CVData(
            raw_text="Python software engineer with backend and data experience.",
            skills=["Python", "JavaScript", "SQL"],
            job_titles=["Software Engineer", "Backend Engineer"],
        ),
        preferences=UserPreferences(
            target_job_titles=["Software Engineer", "Developer", "Backend Engineer"],
            additional_skills=["Python", "Docker"],
        ),
    )
    save_profile(profile, user_id)

    # --- Pillar 1 + Pillar 2: the REAL pipeline, all sources, live network ---
    stats = asyncio.run(
        run_search(db_path=db_file, user_id=user_id, no_notify=True)
    )

    # Pillar 1 — many sources queried; several returned real live jobs.
    assert stats.get("error") != "no_profile", "profile not loaded — DB_PATH not patched?"
    assert stats["sources_queried"] >= 10, stats
    sources_with_jobs = [s for s, n in stats["per_source"].items() if n > 0]
    assert len(sources_with_jobs) >= 3, (
        f"only {len(sources_with_jobs)} live source(s) returned jobs: {stats['per_source']}"
    )

    # Pillar 2 — dedup + scoring ran and inserted real jobs.
    assert stats["new_jobs"] > 0, stats

    # --- Pillar 3: per-user feed was written by run_search + is readable ---
    async def _read_feed():
        db = JobDatabase(db_file)
        await db.init_db()
        try:
            return await db.get_user_feed_jobs(user_id, days=7, min_score=0)
        finally:
            await db.close()

    feed = asyncio.run(_read_feed())
    assert feed, "Pillar 3: user_feed is empty after a live run"
    top = feed[0]
    assert top.get("title"), "feed row missing title"
    assert 0 <= top.get("match_score", -1) <= 100, f"feed score out of range: {top.get('match_score')}"
