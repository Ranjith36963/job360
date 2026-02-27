import re
import asyncio
import aiohttp
from unittest.mock import patch, AsyncMock
from aioresponses import aioresponses

from src.main import run_search


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_run_search_completes_without_keys():
    """With no API keys and mocked free sources, run_search should complete without error."""
    async def _test():
        with aioresponses() as m:
            # Mock all free API endpoints
            m.get(re.compile(r"https://www\.arbeitnow\.com/.*"), payload={"data": []})
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

            with patch("src.main.send_email", new_callable=AsyncMock) as mock_email:
                stats = await run_search(db_path=":memory:")
                assert stats["sources_queried"] > 0
                assert isinstance(stats["total_found"], int)
    _run(_test())


def test_run_search_with_mock_jobs():
    """When sources return jobs, they should be scored, deduped, and stored."""
    async def _test():
        with aioresponses() as m:
            # Return a relevant job from arbeitnow
            m.get(re.compile(r"https://www\.arbeitnow\.com/.*"), payload={"data": [{
                "slug": "ai-1", "title": "AI Engineer",
                "company_name": "TestCo", "location": "London, UK",
                "description": "Python PyTorch TensorFlow LangChain RAG LLM Deep Learning role",
                "url": "https://example.com/ai-1", "tags": ["ai", "python"],
            }]})
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

            with patch("src.main.send_email", new_callable=AsyncMock) as mock_email:
                stats = await run_search(db_path=":memory:")
                assert stats["total_found"] >= 1
                assert stats["new_jobs"] >= 1
    _run(_test())
