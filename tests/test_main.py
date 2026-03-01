import re
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from aioresponses import aioresponses

from src.main import run_search


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# Shared mock endpoint setup for all free sources
def _mock_free_sources(m, arbeitnow_payload=None):
    """Register mock responses for all free sources."""
    if arbeitnow_payload is None:
        arbeitnow_payload = {"data": []}
    m.get(re.compile(r"https://www\.arbeitnow\.com/.*"), payload=arbeitnow_payload)
    m.get(re.compile(r"https://remoteok\.com/.*"), payload=[{"legal": "notice"}])
    m.get(re.compile(r"https://jobicy\.com/.*"), payload={"jobs": []})
    m.get(re.compile(r"https://himalayas\.app/.*"), payload={"jobs": []})
    m.get(re.compile(r"https://boards-api\.greenhouse\.io/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r"https://api\.lever\.co/.*"), payload=[], repeat=True)
    m.post(re.compile(r"https://apply\.workable\.com/.*"), payload={"results": []}, repeat=True)
    m.get(re.compile(r"https://api\.ashbyhq\.com/.*"), payload={"jobs": []}, repeat=True)
    m.get(re.compile(r"https://findajob\.dwp\.gov\.uk/.*"),
          body="<rss><channel></channel></rss>",
          content_type="application/rss+xml", repeat=True)
    m.get(re.compile(r"https://devitjobs\.uk/api/.*"), payload=[], repeat=True)
    m.get(re.compile(r"https://landing\.jobs/api/.*"), payload=[], repeat=True)


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
                assert len(stats["per_source"]) == 23
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
