import asyncio
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses

from src.main import SOURCE_INSTANCE_COUNT, _build_sources, run_search


def _run(coro):
    return asyncio.run(coro)


# Shared mock endpoint setup for all free sources
def _mock_free_sources(m, arbeitnow_payload=None):
    """Register mock responses for ALL sources so tests don't make real HTTP calls."""
    if arbeitnow_payload is None:
        arbeitnow_payload = {"data": []}
    # Group A: Keyed APIs (return empty when key is present)
    m.get(re.compile(r"https://www\.reed\.co\.uk/.*"), payload={"results": []}, repeat=True)
    m.get(re.compile(r"https://api\.adzuna\.com/.*"), payload={"results": []}, repeat=True)
    m.get(re.compile(r"https://jsearch\.p\.rapidapi\.com/.*"), payload={"data": []}, repeat=True)
    m.post(re.compile(r"https://jooble\.org/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r"https://serpapi\.com/.*"), payload={"jobs_results": []}, repeat=True)
    m.get(re.compile(r"https://findwork\.dev/.*"), payload={"results": []}, repeat=True)
    # Group B: Free APIs
    m.get(re.compile(r"https://www\.arbeitnow\.com/.*"), payload=arbeitnow_payload)
    m.get(re.compile(r"https://remoteok\.com/.*"), payload=[{"legal": "notice"}])
    m.get(re.compile(r"https://jobicy\.com/.*"), payload={"jobs": []})
    m.get(re.compile(r"https://himalayas\.app/.*"), payload={"jobs": []})
    m.get(re.compile(r"https://remotive\.com/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r"https://aijobs\.net/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://devitjobs\.uk/api/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://landing\.jobs/api/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://www\.themuse\.com/.*"), payload={"results": []}, repeat=True)
    # Group C: ATS boards
    m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r"https://api\.lever\.co/.*"), payload=[], repeat=True)
    m.post(re.compile(r"https://apply\.workable\.com/.*"), payload={"results": []}, repeat=True)
    m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r"https://api\.smartrecruiters\.com/.*"), payload={"content": []}, repeat=True)
    m.get(re.compile(r".*\.pinpointhq\.com/.*"), payload=[], repeat=True)
    m.get(re.compile(r".*\.recruitee\.com/.*"), payload={"offers": []}, repeat=True)
    m.post(re.compile(r".*\.myworkdayjobs\.com/.*"), payload={"jobPostings": []}, repeat=True)
    m.get(re.compile(r".*\.jobs\.personio\.de/.*"), body="<xml></xml>", repeat=True)
    m.get(re.compile(r".*\.baesystems\.com/.*"), body="<urlset></urlset>", repeat=True)
    m.get(re.compile(r".*\.qinetiq\.com/.*"), body="<urlset></urlset>", repeat=True)
    m.get(re.compile(r".*\.thalesgroup\.com/.*"), body="<urlset></urlset>", repeat=True)
    # Group D: HTML scrapers
    m.get(re.compile(r"https://www\.linkedin\.com/.*"), body="<html></html>", repeat=True)
    m.get(re.compile(r"https://climatebase\.org/.*"), body="<html></html>", repeat=True)
    m.get(re.compile(r"https://www\.bcs\.org/.*"), body="<html></html>", repeat=True)
    m.get(re.compile(r"https://aijobs\.ai/.*"), body="<html></html>", repeat=True)
    # Group E: RSS/XML feeds
    m.get(re.compile(r"https://www\.jobs\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://www\.nhsbsa\.nhs\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://weworkremotely\.com/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://www\.realworkfromanywhere\.com/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://www\.biospace\.com/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://workanywhere\.pro/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"http://www\.jobs\.cam\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://hr-jobs\.lancs\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://jobs\.kent\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://jobs\.royalholloway\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://jobs\.surrey\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://www\.uukjobs\.co\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    # Group F: Algolia/other APIs
    m.get(re.compile(r"https://hacker-news\.firebaseio\.com/.*"), payload={"kids": []}, repeat=True)
    m.get(re.compile(r"https://hn\.algolia\.com/.*"), payload={"hits": []}, repeat=True)
    m.post(re.compile(r"https://w6km1udib3-dsn\.algolia\.net/.*"), payload={"hits": []}, repeat=True)
    m.get(re.compile(r"https://nofluffjobs\.com/api/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://www\.careerjet\.co\.uk/.*"), body="<html></html>", repeat=True)
    # Batch-3 sources (post-rotation: 50 sources). Without these mocks
    # aioresponses raises ConnectionRefusedError or hangs forever — both
    # show up here as the asyncio "Timeout" the Step-1.5 fixture pass
    # reproduced. Adding empty-shape responses keeps run_search progress
    # but doesn't add jobs.
    m.get(re.compile(r"https://teaching-vacancies\.service\.gov\.uk/.*"), payload={"vacancies": []}, repeat=True)
    m.get(re.compile(r"https://www\.jobs\.nhs\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://ats\.rippling\.com/.*"), payload={"jobs": []}, repeat=True)
    # Step-1.5 fixture-debt closure — sources that hung the original
    # Step-1.5 broad sweep because the inline URL list missed them:
    m.get(re.compile(r"https://www\.aijobs\.net/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://aijobs\.net/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://api\.workable\.com/.*"), payload={"results": []}, repeat=True)
    m.get(re.compile(r"https://www\.workday\.com/.*"), payload={"jobPostings": []}, repeat=True)
    m.get(re.compile(r"https://api\.lever\.co/v0/postings/.*"), payload=[], repeat=True)
    # M8 rehab — additional sources whose URLs were absent from the catalog:
    # nhs_jobs + nhs_jobs_xml use jobs.nhs.uk (not nhsbsa.nhs.uk), biospace
    # uses biospace.com (RSS), climatebase uses climatebase.org/jobs (JSON/HTML),
    # uni_jobs uses multiple RSS URLs, jobs_ac_uk has subject-area feed URLs.
    m.get(re.compile(r"https://www\.jobs\.nhs\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://www\.biospace\.com/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://climatebase\.org/.*"), body="<html></html>", repeat=True)
    m.get(re.compile(r"http://.*\.cam\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://.*\.cam\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://.*\.lancs\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://.*\.kent\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://.*\.royalholloway\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://.*\.surrey\.ac\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    m.get(re.compile(r"https://www\.uukjobs\.co\.uk/.*"), body="<rss><channel></channel></rss>", repeat=True)
    # Generic catch-alls. aioresponses dispatches the most-specific
    # match first, so the more granular patterns above always win.
    m.get(re.compile(r".*\.workable\.com/.*"), payload={"results": []}, repeat=True)
    m.get(re.compile(r".*\.lever\.co/.*"), payload=[], repeat=True)
    m.get(re.compile(r".*\.greenhouse\.io/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r".*\.ashbyhq\.com/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r".*\.smartrecruiters\.com/.*"), payload={"content": []}, repeat=True)
    m.get(re.compile(r".*\.greenhouse-mail\.io/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r".*\.sapsf\.com/.*"), body="<urlset></urlset>", repeat=True)
    m.get(re.compile(r".*\.successfactors\.eu/.*"), body="<urlset></urlset>", repeat=True)
    m.get(re.compile(r".*\.successfactors\.com/.*"), body="<urlset></urlset>", repeat=True)
    m.get(re.compile(r".*\.findwork\.dev/.*"), payload={"results": []}, repeat=True)


MOCK_JOB_PAYLOAD = {
    "data": [
        {
            "slug": "ai-1",
            "title": "AI Engineer",
            "company_name": "TestCo",
            "location": "London, UK",
            "description": "Python PyTorch TensorFlow LangChain RAG LLM Deep Learning role",
            "url": "https://example.com/ai-1",
            "tags": ["ai", "python"],
        }
    ]
}


def _patch_no_notifications():
    """No-op context: legacy global notification path removed (migration 0020).

    The per-env webhook channels (email/Slack/Discord) are gone; notifications
    now flow through the per-user ARQ worker path.  Tests that used to silence
    them by patching ``get_configured_channels`` just need a null context.
    """
    import contextlib
    return contextlib.nullcontext()


# Step-1.5 Cohort-X follow-up: the orchestrator's no-profile guard
# (main.py:344-362) returns early with {error: "no_profile"} when
# load_profile(DEFAULT_TENANT_ID) yields nothing — but the legacy
# test_main.py tests below never seed a profile, so every run_search call
# returned 0 jobs and assertions like ``stats["total_found"] >= 1`` blew
# up. Patch ``load_profile`` at module scope so all 9 legacy tests get a
# minimal-but-complete UserProfile before run_search runs. Tests that
# explicitly patch load_profile themselves (lines ~439, ~493) override
# this autouse patch via context manager — that nesting is safe.
import pytest as _pytest  # noqa: E402 — late import, intentional ordering

from src.services.profile.models import (  # noqa: E402
    UserPreferences,
    UserProfile,
)

# ---------------------------------------------------------------------------
# Autouse fixtures that make every test in this file OFFLINE and FAST
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_breaker_registry():
    """The default circuit-breaker registry is a module-level singleton.
    Sources that fail in one test trip their breaker; a OPEN breaker in the
    next test means the scheduler skips the source entirely — it's never
    added to per_source, causing ``len(per_source) < SOURCE_INSTANCE_COUNT``.
    Reset to a fresh registry before every test in this file.
    """
    import src.main as _main_mod
    import src.services.circuit_breaker as _cb_mod

    fresh = _cb_mod.BreakerRegistry()
    original = _cb_mod._DEFAULT_REGISTRY
    _cb_mod._DEFAULT_REGISTRY = fresh
    # main.py imported default_registry as an alias — patch both
    original_main_alias = getattr(_main_mod, "default_breaker_registry", None)
    _main_mod.default_breaker_registry = lambda: fresh  # type: ignore[method-assign]
    yield
    _cb_mod._DEFAULT_REGISTRY = original
    if original_main_alias is not None:
        _main_mod.default_breaker_registry = original_main_alias


@pytest.fixture(autouse=True)
def _stub_jobspy():
    """JobSpySource uses sync ``requests`` → un-mockable by aioresponses.
    It hits live Indeed (~30 s/query × 8 queries). Stub fetch_jobs to []
    so run_search() stays fully offline on every test in this file.
    """

    async def _empty(self) -> list:
        return []

    with patch("src.sources.other.indeed.JobSpySource.fetch_jobs", _empty):
        yield


@pytest.fixture(autouse=True)
def _stub_load_profile():
    """run_search() returns early with {error: 'no_profile'} when
    load_profile() yields None, so every test that calls run_search would
    get 0 sources_queried and 0 jobs.  Inject a complete profile that
    matches the MOCK_JOB_PAYLOAD (AI Engineer, Python, PyTorch, etc.) so
    the scored job clears the MIN_MATCH_SCORE=30 gate.

    Tests that need to control the profile themselves (e.g.
    test_run_search_wires_user_preferences_and_enrichment_lookup) override
    this via their own ``patch("src.main.load_profile", ...)`` context
    manager — context-manager patches win over the autouse fixture because
    they execute inside the test body and re-wrap the target.
    """
    from src.services.profile.models import CVData

    _profile = UserProfile(
        cv_data=CVData(
            raw_text=(
                "AI Engineer with expertise in Python, PyTorch, TensorFlow, "
                "LangChain, RAG, LLM, Deep Learning. 5 years experience."
            ),
            skills=["Python", "PyTorch", "TensorFlow", "LangChain", "RAG", "LLM", "Deep Learning"],
        ),
        preferences=UserPreferences(target_job_titles=["AI Engineer", "ML Engineer", "Software Engineer"]),
    )

    with patch("src.main.load_profile", return_value=_profile):
        yield

# M8 rehab note: the three layers of fixture debt described in the Step-1.5
# reviewer notes above the autouse fixtures have been resolved:
#
# 1. _stub_load_profile (autouse) patches load_profile → full UserProfile so
#    run_search() never returns early with {error: "no_profile"}.
# 2. _stub_jobspy (autouse) patches JobSpySource.fetch_jobs → [] so no live
#    Indeed/Glassdoor calls are made (~32 min hang fully eliminated).
# 3. _mock_free_sources() URL catalog extended to cover all keyless sources
#    including post-Batch-3 additions (nhs_jobs, rippling, teaching_vacancies).
#    gov_apprenticeships (keyed, restored 2026-06-16) skips with no key, so it
#    needs no mock here.
#
# This file now runs offline in ~8 s (14 tests, 0 skips) and is part of the
# canonical pre-commit suite. Do NOT add --ignore=tests/test_main.py back.
_PRE_STEP_1_5_SCAFFOLDING_DEBT = _pytest.mark.skip(
    reason=(
        "OBSOLETE — kept only so any lingering @_PRE_STEP_1_5_SCAFFOLDING_DEBT "
        "decorator surfaces as a skipped test rather than a NameError. All tests "
        "in this file were un-skipped in the M8 rehab batch. Remove this marker "
        "once no test references it."
    )
)


# ---- Existing tests ----


def test_run_search_completes_without_keys():
    """With no API keys and mocked free sources, run_search should complete without error."""

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m)
            with _patch_no_notifications():
                stats = await run_search(db_path=":memory:")
                assert stats["sources_queried"] > 0
                assert isinstance(stats["total_found"], int)

    _run(_test())


def test_run_search_with_mock_jobs():
    """When sources return jobs, they should be scored, deduped, and stored."""

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            with _patch_no_notifications():
                with tempfile.TemporaryDirectory() as tmpdir:
                    with (
                        patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"),
                        patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"),
                    ):
                        stats = await run_search(db_path=":memory:")
                        assert stats["total_found"] >= 1
                        assert stats["new_jobs"] >= 1

    _run(_test())


# ---- New integration tests ----


def test_jobs_are_scored_with_recency():
    """Mock job posted today should have recency points included in score."""

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            with _patch_no_notifications():
                with tempfile.TemporaryDirectory() as tmpdir:
                    with (
                        patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"),
                        patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"),
                    ):
                        stats = await run_search(db_path=":memory:")
                        assert stats["new_jobs"] >= 1
                        assert stats["total_found"] >= 1

    _run(_test())


def test_all_notification_channels_called():
    """Legacy global channel notification path removed (migration 0020).

    Notifications now flow through the per-user ARQ worker/dispatcher path.
    This test is kept as a placeholder to confirm run_search completes without
    error when new jobs are found — the channel calls are no longer in-process.
    """

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            with tempfile.TemporaryDirectory() as tmpdir:
                with (
                    patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"),
                    patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"),
                ):
                    stats = await run_search(db_path=":memory:")
                    # Just verify run_search completes with a valid stats dict.
                    assert isinstance(stats, dict)
                    assert "new_jobs" in stats

    _run(_test())


def test_run_search_scores_within_range():
    """Jobs returned from a run should have match_score between 0 and 100."""

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            with _patch_no_notifications():
                with tempfile.TemporaryDirectory() as tmpdir:
                    with (
                        patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"),
                        patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"),
                    ):
                        stats = await run_search(db_path=":memory:")
                        assert stats["total_found"] >= 1
                        assert stats["new_jobs"] >= 1

    _run(_test())


def test_stats_include_per_source():
    """Stats dict must include per_source breakdown with source entries for all sources.

    Domain filtering (_build_sources Batch 2.4) narrows the source list when a
    profile has a classified domain.  Patch classify_user_domain to return an
    empty set so _build_sources includes every source (the "no-profile" fallback
    path) and the count assertion stays meaningful.
    """

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m)
            with _patch_no_notifications():
                with patch("src.main.classify_user_domain", return_value=set()):
                    stats = await run_search(db_path=":memory:")
                    assert "per_source" in stats
                    assert isinstance(stats["per_source"], dict)
                    assert len(stats["per_source"]) == SOURCE_INSTANCE_COUNT

    _run(_test())


def test_second_run_finds_no_new_jobs():
    """Running twice with same jobs should find 0 new jobs on second run (DB dedup)."""

    async def _test():
        import os
        import tempfile as tf

        with tf.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            with aioresponses() as m:
                _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
                with _patch_no_notifications():
                    with tempfile.TemporaryDirectory() as tmpdir:
                        with (
                            patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"),
                            patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"),
                        ):
                            stats1 = await run_search(db_path=db_path)
                            assert stats1["new_jobs"] >= 1

            # Second run — same job should be recognized as seen
            with aioresponses() as m:
                _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
                with _patch_no_notifications():
                    stats2 = await run_search(db_path=db_path)
                    assert stats2["new_jobs"] == 0
        finally:
            os.unlink(db_path)

    _run(_test())


def test_run_search_no_notify_skips_channels():
    """With no_notify=True, run_search completes without dispatching notifications.

    Legacy global channel path removed; no_notify=True is still accepted as a
    flag but the worker dispatch path is already gated per-user.
    """

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            with tempfile.TemporaryDirectory() as tmpdir:
                with (
                    patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"),
                    patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"),
                ):
                    stats = await run_search(db_path=":memory:", no_notify=True)
                    assert isinstance(stats, dict)

    _run(_test())


def test_run_search_auto_purge():
    """run_search should auto-purge jobs older than 30 days."""

    async def _test():
        import os
        import tempfile as tf
        from datetime import datetime as dt
        from datetime import timedelta
        from datetime import timezone as tz

        from src.repositories.database import JobDatabase

        with tf.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Seed an old job directly
            db = JobDatabase(db_path)
            await db.init_db()
            old_date = (dt.now(tz.utc) - timedelta(days=60)).isoformat()
            await db._conn.execute(
                """INSERT INTO jobs
                (title, company, location, salary_min, salary_max, description,
                 apply_url, source, date_found, match_score, visa_flag,
                 normalized_company, normalized_title, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "Old Job",
                    "OldCo",
                    "London",
                    None,
                    None,
                    "",
                    "https://example.com/old",
                    "test",
                    old_date,
                    50,
                    0,
                    "oldco",
                    "old job",
                    old_date,
                ),
            )
            await db._conn.commit()
            count_before = await db.count_jobs()
            assert count_before == 1
            await db.close()

            # Run search — should purge old job
            with aioresponses() as m:
                _mock_free_sources(m)
                with _patch_no_notifications():
                    await run_search(db_path=db_path)

            # Verify the specific old job was purged
            db2 = JobDatabase(db_path)
            await db2.init_db()
            cursor = await db2._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
                ("oldco", "old job"),
            )
            row = await cursor.fetchone()
            await db2.close()
            assert row[0] == 0, "Old job should have been purged"
        finally:
            try:
                os.unlink(db_path)
            except PermissionError:
                pass  # Windows file lock

    _run(_test())


def test_source_instance_count_matches_build_sources():
    """SOURCE_INSTANCE_COUNT constant must match actual _build_sources output."""
    import aiohttp

    async def _test():
        with aioresponses():
            async with aiohttp.ClientSession() as session:
                sources = _build_sources(session)
                assert len(sources) == SOURCE_INSTANCE_COUNT

    _run(_test())


def test_failed_source_tracked_as_none():
    """A source that raises an exception should appear in per_source with count 0.

    Domain filtering (_build_sources Batch 2.4) narrows the source list when a
    profile has a classified domain.  Patch classify_user_domain to return an
    empty set so _build_sources includes every source (the "no-profile" fallback
    path) and the count assertion stays meaningful.
    """

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m)
            with _patch_no_notifications():
                with patch("src.main.classify_user_domain", return_value=set()):
                    stats = await run_search(db_path=":memory:")
                    # All sources should be present in per_source
                    assert len(stats["per_source"]) == SOURCE_INSTANCE_COUNT
                    # Values should be non-negative integers
                    for _name, count in stats["per_source"].items():
                        assert isinstance(count, int)
                        assert count >= 0

    _run(_test())


def test_dry_run_skips_db_writes():
    """Dry run should return stats without writing to DB."""

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            with _patch_no_notifications():
                stats = await run_search(db_path=":memory:", dry_run=True)
                assert stats["sources_queried"] > 0
                assert isinstance(stats["total_found"], int)

    _run(_test())


# Step-1 B5 — multi-dim wiring at the CLI JobScorer call site.


def test_run_search_wires_user_preferences_and_enrichment_lookup():
    """When a profile + at least one job_enrichment row exist AND
    ENRICHMENT_ENABLED is true, run_search MUST construct JobScorer with
    BOTH user_preferences (from the loaded profile) AND a non-trivial
    enrichment_lookup callable. Without these kwargs, the Pillar 2 Batch
    2.9 multi-dim path stays inert and the upgrade is invisible.
    """
    from src.services.profile.models import CVData

    fake_profile = UserProfile(
        cv_data=CVData(raw_text="dummy CV content"),
        preferences=UserPreferences(target_job_titles=["Engineer"]),
    )

    captured: dict = {}

    class _SpyScorer:
        def __init__(self, config, *, user_preferences=None, enrichment_lookup=None):
            captured["config"] = config
            captured["user_preferences"] = user_preferences
            captured["enrichment_lookup"] = enrichment_lookup

        def score(self, job):
            from src.services.skill_matcher import ScoreBreakdown

            return ScoreBreakdown(match_score=0)

        def check_visa_flag(self, job):
            return False

    fake_lookup = {42: object()}  # one fake enrichment "row"

    async def _fake_build_lookup(_conn):
        return fake_lookup

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m)
            with (
                _patch_no_notifications(),
                patch("src.main.load_profile", return_value=fake_profile),
                patch("src.main.ENRICHMENT_ENABLED", True),
                patch("src.main._build_enrichment_lookup", _fake_build_lookup),
                patch("src.main.JobScorer", _SpyScorer),
            ):
                await run_search(db_path=":memory:", dry_run=True)

    _run(_test())

    assert (
        captured["user_preferences"] is fake_profile.preferences
    ), "JobScorer must receive the loaded profile's preferences"
    assert callable(captured["enrichment_lookup"]), "enrichment_lookup must be a callable (job)->Enrichment|None"
    # Sanity: the callable must be backed by the dict we supplied — pass it a
    # job-like object with id=42 and confirm it returns our sentinel.
    sentinel = fake_lookup[42]
    fake_job = MagicMock()
    fake_job.id = 42
    assert captured["enrichment_lookup"](fake_job) is sentinel
    # And id absent / unknown returns None (so default 4-component path holds).
    no_id_job = MagicMock(spec=[])  # no id attr
    assert captured["enrichment_lookup"](no_id_job) is None


def test_run_search_inert_when_enrichment_disabled():
    """ENRICHMENT_ENABLED=false ⇒ enrichment_lookup is built from {} so the
    callable returns None for every job. This preserves the legacy
    4-component formula per CLAUDE.md rule #19.
    """
    from src.services.profile.models import CVData

    fake_profile = UserProfile(
        cv_data=CVData(raw_text="dummy CV content"),
        preferences=UserPreferences(target_job_titles=["Engineer"]),
    )
    captured: dict = {}

    class _SpyScorer:
        def __init__(self, config, *, user_preferences=None, enrichment_lookup=None):
            captured["enrichment_lookup"] = enrichment_lookup

        def score(self, job):
            from src.services.skill_matcher import ScoreBreakdown

            return ScoreBreakdown(match_score=0)

        def check_visa_flag(self, job):
            return False

    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m)
            with (
                _patch_no_notifications(),
                patch("src.main.load_profile", return_value=fake_profile),
                patch("src.main.ENRICHMENT_ENABLED", False),
                patch("src.main.JobScorer", _SpyScorer),
            ):
                await run_search(db_path=":memory:", dry_run=True)

    _run(_test())

    # Lookup is built but every call returns None (empty dict backing it).
    assert callable(captured["enrichment_lookup"])
    fake_job = MagicMock()
    fake_job.id = 1
    assert captured["enrichment_lookup"](fake_job) is None


def test_metrics_export_uses_resolved_path_not_none_string():
    """When run_search is called without db_path, the metrics exporters must
    receive the resolved DB path (str(DB_PATH)), never the string "None".

    Root cause: the bug passes str(db_path) where db_path=None at the call
    site, yielding the literal string "None".  The fix uses `path` (already
    resolved via `path = db_path or str(DB_PATH)`) instead.
    """
    import os
    import tempfile

    from src.core.settings import DB_PATH as REAL_DB_PATH

    async def _test():
        # Use a real temp file so DB_PATH resolution has a valid target.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name
        try:
            with (
                aioresponses() as m,
                patch("src.main.DB_PATH", tmp_db),
                patch("src.main.export_pipeline_metrics", new_callable=AsyncMock) as mock_pipeline,
                patch("src.main.export_notification_metrics", new_callable=AsyncMock) as mock_notif,
                _patch_no_notifications(),
            ):
                _mock_free_sources(m)
                # Call without explicit db_path — this is the failure path.
                await run_search(db_path=None)

            # The exporters must have been called exactly once each.
            mock_pipeline.assert_awaited_once()
            mock_notif.assert_awaited_once()

            # Extract the first positional argument each was called with.
            pipeline_arg = mock_pipeline.await_args[0][0]
            notif_arg = mock_notif.await_args[0][0]

            # Neither should be the string "None" or the Python None object.
            assert pipeline_arg != "None", (
                f"export_pipeline_metrics got literal string 'None'; "
                f"the bug is still present. Got: {pipeline_arg!r}"
            )
            assert pipeline_arg is not None, "export_pipeline_metrics got Python None"
            assert notif_arg != "None", (
                f"export_notification_metrics got literal string 'None'; "
                f"the bug is still present. Got: {notif_arg!r}"
            )
            assert notif_arg is not None, "export_notification_metrics got Python None"

            # Both should equal the resolved path (our tmp_db).
            assert pipeline_arg == tmp_db, (
                f"Expected resolved path {tmp_db!r}, got {pipeline_arg!r}"
            )
            assert notif_arg == tmp_db, (
                f"Expected resolved path {tmp_db!r}, got {notif_arg!r}"
            )

            # Confirm no stray file named "None" was created in cwd.
            assert not os.path.exists("None"), (
                "A file named 'None' was created in cwd — the bug fired."
            )
        finally:
            try:
                os.unlink(tmp_db)
            except OSError:
                pass

    _run(_test())
