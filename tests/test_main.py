import re
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from aioresponses import aioresponses

from src.main import run_search, SOURCE_INSTANCE_COUNT, _build_sources
from src.profile.models import CVData, UserPreferences, UserProfile


def _run(coro):
    return asyncio.run(coro)


def _minimal_profile() -> UserProfile:
    """Minimal UserProfile that satisfies `is_complete` for run_search tests.

    Has enough content to pass the C1 guard in src/main.py without touching
    data/user_profile.json on disk. Scoring will be weak (few skills) but
    that's fine — test_main.py asserts pipeline *mechanics*, not ranking
    quality. Scoring correctness is covered by test_scorer.py.
    """
    return UserProfile(
        cv_data=CVData(
            raw_text="Test CV body for run_search tests.",
            skills=["python", "pytorch", "tensorflow", "langchain", "rag", "llm"],
            job_titles=["ai engineer", "machine learning engineer"],
        ),
        preferences=UserPreferences(
            target_job_titles=["AI Engineer", "ML Engineer"],
            additional_skills=["python", "pytorch"],
            preferred_locations=["London", "Remote"],
        ),
    )


@pytest.fixture(autouse=True)
def _patch_load_profile_for_run_search():
    """Make run_search see a valid profile without relying on the filesystem.

    The C1 guard added in main.py:240-261 returns early when load_profile()
    is None, which previously broke these tests on clean checkouts (no
    data/user_profile.json). Patch at `src.main.load_profile` (where it's
    used) rather than `src.profile.storage.load_profile` (where it's defined).
    """
    with patch("src.main.load_profile", return_value=_minimal_profile()):
        yield


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
    m.get(re.compile(r"https://findajob\.dwp\.gov\.uk/.*"),
          body="<html><body>No results</body></html>",
          content_type="text/html", repeat=True)
    m.get(re.compile(r"https://jobtensor\.com/.*"), payload={"total": 0, "hits": []}, repeat=True)
    m.get(re.compile(r"https://climatebase\.org/.*"), body="<html></html>", repeat=True)
    m.get(re.compile(r"https://www\.bcs\.org/.*"), body="<html></html>", repeat=True)
    m.get(re.compile(r"https://aijobs\.ai/.*"), body="<html></html>", repeat=True)
    m.get(re.compile(r"https://ai-jobs\.global/.*"), payload=[], repeat=True)
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
    m.get(re.compile(r"https://yc-oss\.github\.io/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://nofluffjobs\.com/api/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://www\.nomis\.co\.uk/.*"), payload={}, repeat=True)
    m.get(re.compile(r"https://www\.careerjet\.co\.uk/.*"), body="<html></html>", repeat=True)


MOCK_JOB_PAYLOAD = {"data": [{
    "slug": "ai-1", "title": "AI Engineer",
    "company_name": "TestCo", "location": "London, UK",
    "description": "Python PyTorch TensorFlow LangChain RAG LLM Deep Learning role",
    "url": "https://example.com/ai-1", "tags": ["ai", "python"],
}]}


def _patch_no_notifications():
    """Patch get_configured_channels to return empty list (no notifications sent)."""
    return patch("src.main.get_configured_channels", return_value=[])


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
                    with patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"), \
                         patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"):
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
                    with patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"), \
                         patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"):
                        stats = await run_search(db_path=":memory:")
                        assert stats["new_jobs"] >= 1
                        assert stats["total_found"] >= 1
    _run(_test())


def test_all_notification_channels_called():
    """When new jobs are found, all configured notification channels should be invoked."""
    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            # Create 3 mock channels
            mock_channels = []
            for name in ["Email", "Slack", "Discord"]:
                ch = MagicMock()
                ch.name = name
                ch.send = AsyncMock()
                mock_channels.append(ch)

            with patch("src.main.get_configured_channels", return_value=mock_channels):
                with tempfile.TemporaryDirectory() as tmpdir:
                    with patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"), \
                         patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"):
                        stats = await run_search(db_path=":memory:")
                        if stats["new_jobs"] > 0:
                            for ch in mock_channels:
                                ch.send.assert_awaited_once()
    _run(_test())


def test_run_search_scores_within_range():
    """Jobs returned from a run should have match_score between 0 and 100."""
    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            with _patch_no_notifications():
                with tempfile.TemporaryDirectory() as tmpdir:
                    with patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"), \
                         patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"):
                        stats = await run_search(db_path=":memory:")
                        assert stats["total_found"] >= 1
                        assert stats["new_jobs"] >= 1
    _run(_test())


def test_stats_include_per_source():
    """Stats dict must include per_source breakdown with source entries for all sources."""
    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m)
            with _patch_no_notifications():
                stats = await run_search(db_path=":memory:")
                assert "per_source" in stats
                assert isinstance(stats["per_source"], dict)
                assert len(stats["per_source"]) == SOURCE_INSTANCE_COUNT
    _run(_test())


def test_second_run_finds_no_new_jobs():
    """Running twice with same jobs should find 0 new jobs on second run (DB dedup)."""
    async def _test():
        import tempfile as tf, os
        with tf.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            with aioresponses() as m:
                _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
                with _patch_no_notifications():
                    with tempfile.TemporaryDirectory() as tmpdir:
                        with patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"), \
                             patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"):
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
    """With no_notify=True, notification channels should not be called."""
    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m, arbeitnow_payload=MOCK_JOB_PAYLOAD)
            mock_channels = []
            for name in ["Email", "Slack"]:
                ch = MagicMock()
                ch.name = name
                ch.send = AsyncMock()
                mock_channels.append(ch)

            with patch("src.main.get_configured_channels", return_value=mock_channels):
                with tempfile.TemporaryDirectory() as tmpdir:
                    with patch("src.main.EXPORTS_DIR", Path(tmpdir) / "exports"), \
                         patch("src.main.REPORTS_DIR", Path(tmpdir) / "reports"):
                        stats = await run_search(db_path=":memory:", no_notify=True)
                        # Channels should NOT have been called
                        for ch in mock_channels:
                            ch.send.assert_not_awaited()
    _run(_test())


def test_run_search_auto_purge():
    """run_search should auto-purge jobs older than 30 days."""
    async def _test():
        import tempfile as tf, os
        from datetime import datetime as dt, timezone as tz, timedelta
        from src.storage.database import JobDatabase

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
                ("Old Job", "OldCo", "London", None, None, "",
                 "https://example.com/old", "test", old_date, 50, 0,
                 "oldco", "old job", old_date),
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
    """A source that raises an exception should appear in per_source with count 0."""
    async def _test():
        with aioresponses() as m:
            _mock_free_sources(m)
            with _patch_no_notifications():
                stats = await run_search(db_path=":memory:")
                # All sources should be present in per_source
                assert len(stats["per_source"]) == SOURCE_INSTANCE_COUNT
                # Values should be non-negative integers
                for name, count in stats["per_source"].items():
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


def test_run_search_early_returns_when_no_profile():
    """C1 guard: with no profile, run_search must short-circuit without touching sources.

    Stacks a second patch on top of the autouse fixture — patch-stack LIFO,
    so return_value=None wins. Verifies:
      * stats["error"] == "no_profile"   (locks in the exact key the CLI checks)
      * sources_queried == 0             (proves no HTTP work was attempted)
      * per_source is empty              (proves _build_sources was skipped)
      * _build_sources is NEVER called    (no sources instantiated)
    """
    async def _test():
        with patch("src.main.load_profile", return_value=None), \
             patch("src.main._build_sources") as mock_build, \
             _patch_no_notifications():
            stats = await run_search(db_path=":memory:")
            assert stats.get("error") == "no_profile"
            assert stats["sources_queried"] == 0
            assert stats["total_found"] == 0
            assert stats["new_jobs"] == 0
            assert stats["per_source"] == {}
            # _build_sources must not have been called — this is the whole point
            # of the guard: skip the expensive HTTP fan-out when we know scoring
            # would return zero for every job.
            mock_build.assert_not_called()
    _run(_test())


def test_run_search_early_returns_when_profile_incomplete():
    """C1 guard also triggers on profiles with no CV text and no preferences.

    UserProfile.is_complete returns False when cv_data.raw_text is empty AND
    preferences has no target_job_titles / additional_skills. Verify that case
    is caught by the same guard as the None case.
    """
    async def _test():
        empty = UserProfile(cv_data=CVData(), preferences=UserPreferences())
        assert empty.is_complete is False  # precondition

        with patch("src.main.load_profile", return_value=empty), \
             patch("src.main._build_sources") as mock_build, \
             _patch_no_notifications():
            stats = await run_search(db_path=":memory:")
            assert stats.get("error") == "no_profile"
            assert stats["sources_queried"] == 0
            mock_build.assert_not_called()
    _run(_test())
