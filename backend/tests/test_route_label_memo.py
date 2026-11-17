"""``routing.site_of`` is a pure function of its link, so the needs-you badge parses each queued link once.

Measured on an owner-shaped account: 390 queued jobs cost 1,170 host parses (three ``site_of`` calls per job
in ``queue_preflight.route_label`` and ``learned.route_for``). Pinned here:
* one badge request parses a link's host once, not three times (``canonical_host`` call count);
* ``site_of`` / ``route_label`` answer exactly what the uncached function answers, for tricky links;
* the badge's JSON is identical with and without the cache.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import queue_preflight
from src.services.browser_agent import routing

HOSTS = ("jobs.lever.co", "boards.greenhouse.io", "careers.example.com")
TRICKY = [
    "", "   ", "mailto:a@b.co", "https://Jobs.Lever.CO/acme/1", "https://jobs.lever.co./acme/1",
    "https://jobs.lever.co:443/acme/1", "https://www.linkedin.com/jobs/view/1", "linkedin.com/jobs/view/1",
    "https://www.ⓛinkedin.com/x", "https://münchen.example/x", "https://xn--mnchen-3ya.example/x",
    "https://user@jobs.lever.co/x", "https://uk.indeed.com/viewjob?jk=1", "https://notindeed.com/x",
    "https://eu.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/X", "https://eu.fa.oraclecloud.com/other",
    "http://[::1", "https://a b.com/x", "www.careers.example.com/a", "//careers.example.com/a", "\0",
]


@pytest.fixture(autouse=True)
def _clean_cache():
    _clear()
    yield
    _clear()


def _clear() -> None:
    clear = getattr(routing.site_of, "cache_clear", None)
    if clear is not None:
        clear()


_RAW = getattr(routing.site_of, "__wrapped__", routing.site_of)  # the function without its cache


def _uncached(url: str) -> tuple[str, str, str]:
    return _RAW(url)


@pytest.mark.parametrize("url", TRICKY)
def test_site_of_equals_the_uncached_answer(url):
    first = routing.site_of(url)
    assert first == _uncached(url)
    assert routing.site_of(url) == first  # a cache hit answers the same
    assert isinstance(first, tuple) and all(isinstance(p, str) for p in first)  # immutable: no caller can mutate it


def test_site_of_treats_none_like_empty():
    assert routing.site_of(None) == routing.site_of("") == ("company", "host:", "")  # type: ignore[arg-type]


@pytest.mark.parametrize("url", TRICKY)
def test_route_label_equals_the_uncached_answer(url, monkeypatch):
    cached = queue_preflight.route_label(url, {}, frozenset({"careers.example.com"}))
    monkeypatch.setattr(routing, "site_of", _uncached)
    assert queue_preflight.route_label(url, {}, frozenset({"careers.example.com"})) == cached


def test_route_label_parses_a_link_once_not_three_times(monkeypatch):
    calls: list[str] = []
    real = routing.canonical_host
    monkeypatch.setattr(routing, "canonical_host", lambda u: calls.append(u) or real(u))
    urls = [f"https://{HOSTS[i % 3]}/acme/{i % 4}" for i in range(60)]  # 12 distinct links
    for url in urls:
        queue_preflight.route_label(url, {}, frozenset())
    assert len(calls) == len(set(urls)) == 12


async def _badge(client: Any) -> dict[str, Any]:
    resp = await client.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_badge_parses_each_queued_link_once_and_its_json_is_unchanged(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        for n in range(9):
            ad = {"title": f"Engineer {n}", "company": f"Co{n}", "location": "London", "country": "GB",
                  "remote": False, "apply_url": f"https://{HOSTS[n % 3]}/acme/{n}", "description": "Build things."}
            brought = await client.post("/api/jobs/bring", json=ad)
            assert brought.status_code == 200, brought.text
            queued = await client.post(
                f"/api/applications/{brought.json()['application_id']}/events", json={"event_type": "queued"})
            assert queued.status_code == 201, queued.text
        with monkeypatch.context() as m:  # the same request with the cache off: the reference answer
            m.setattr(routing, "site_of", _uncached)
            reference = await _badge(client)
        _clear()
        calls: list[str] = []
        real = routing.canonical_host
        monkeypatch.setattr(routing, "canonical_host", lambda u: calls.append(u) or real(u))
        body = await _badge(client)
    assert body == reference
    assert 0 < len(calls) <= 9  # one parse per distinct queued link; it was three per link
