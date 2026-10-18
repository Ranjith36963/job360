"""Offline tests for src/hunt_kit/kit_niche.py (inline fixtures, no network).

Each fixture is shaped like the reference's own test data: career-ops tests/providers/<board>.test.mjs
(landingjobs, getonbrd, manfred, thehub, remotli, speedrun, the RSS boards, nofluffjobs, justjoin,
solidjobs, higheredjobs) and freehire internal/ingest/sources/*_test.go (4dayweek, powertofly, jobstash).
Where a fixture adds a row of ours it says so.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
_spec = importlib.util.spec_from_file_location("kit_niche_under_test", _SRC / "kit_niche.py")
kn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kn)  # type: ignore[union-attr]

JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}
BIG_DAYS = 36500


class FakeHttp:
    """Routes URL substrings to canned bodies and records every call."""

    def __init__(self, routes: dict[str, object], status: int = 200) -> None:
        self.routes, self.status, self.calls = routes, status, []

    def __call__(self, url, *, method="GET", data=None, headers=None, as_json=True):
        self.calls.append({"url": url, "method": method, "data": data, "headers": headers or {}})
        for needle, body in self.routes.items():
            if needle in url:
                return self.status, body
        return 404, None


def _rss(*items: str, ns: str = "") -> str:
    return f'<?xml version="1.0"?><rss version="2.0"{ns}><channel>{"".join(items)}</channel></rss>'


# ------------------------------------------------------------------------------ fixtures
FOURDAY = {"data": [  # freehire fourdayweek_test.go: the v2 page
    {"id": "abc-1", "slug": "senior-backend-at-acme-1", "title": "Senior Backend Engineer", "company": {"name": "Acme"},
     "work_arrangement": "remote", "level": "senior", "category": "devops", "posted_at": "2026-08-01T10:00:00Z",
     "url": "https://4dayweek.io/job/senior-backend-at-acme-1", "description": "<p>Great role &amp; team.</p>",
     "locations": [{"city": "Berlin", "country": "Germany", "is_primary": True}], "stack": [{"name": "Go"}]},
    {"id": "", "slug": "", "title": "skip me", "company": {"name": "NoID"}}], "has_more": False}
LANDING = [{"title": "  Senior Java Dev  ", "url": "https://landing.jobs/at/inscale/senior-java-dev",
            "locations": [{"city": "Lisbon", "country_code": "PT"}], "remote": False,
            "published_at": "2025-02-26T09:38:38.127Z"},
           {"title": "Off host", "url": "https://evil.example/at/acme/x"}]
GETONBRD = {"data": [
    {"attributes": {"title": "Staff AI Engineer", "remote": True, "countries": "Remote",
                    "company": {"data": {"attributes": {"name": "Acme Corp"}}}},
     "links": {"public_url": "https://www.getonbrd.com/jobs/acme-staff-ai-engineer"}},
    {"attributes": {"title": "  Platform Engineer  ", "remote": False, "countries": ["Chile"],
                    "published_at": 1700000000, "company": {"data": {"attributes": {"name": "Beta"}}}},
     "links": {"public_url": "https://www.getonbrd.com/jobs/beta-platform-engineer"}},
    {"attributes": {"title": "", "company": {"data": {"attributes": {"name": "Bad Co"}}}},
     "links": {"public_url": "https://www.getonbrd.com/jobs/bad-empty-title"}}]}
MANFRED = [{"id": 8417, "position": "  Backend Engineer  ", "slug": "wuolah-backend-engineer-jul26",
            "status": "ACTIVE", "company": {"name": "Wuolah"}, "locations": ["Madrid, Spain"],
            "remotePercentage": 0, "salaryFrom": 32000, "salaryTo": 35000, "currency": "€"},
           {"id": 1, "position": "Closed", "slug": "closed-one", "status": "CLOSED", "company": {"name": "X"}}]
THEHUB = {"jobs": {"docs": [
    {"id": "5f3a9c", "title": "Backend Developer", "company": {"name": "Zeta"},
     "location": {"locality": "Berlin", "country": "Germany"}, "isRemote": True},
    {"title": "", "id": "bad"}], "page": 1, "pages": 1, "limit": 15}}
REMOTLI = {"jobs": [{"jobs": {
    "title": "  Head of Finance  ", "slug": "head-of-finance-at-acme", "status": "active", "company": "  Acme AG  ",
    "location": "Zürich, Switzerland", "allLocations": ["Zürich, Switzerland", "Remote"],
    "description": "<p>Own the <b>numbers</b>.</p><li>FP&amp;A</li>",
    "applyUrl": "https://job-boards.greenhouse.io/acme/jobs/4012345", "publishedAt": "2026-08-01T10:00:00.000Z",
    "salaryMin": 180000, "salaryMax": 220000, "salaryCurrency": "chf"}, "companies": {}},
    {"jobs": {"title": "Closed", "slug": "closed", "status": "closed"}, "companies": {}}],
    "pagination": {"page": 1, "limit": 50, "total": 1, "totalPages": 1}}
SPEEDRUN = {"jobs": [{"title": "  Founding Engineer  ",
                      "url": "https://speedrun-talent-network.com/jobs/founding-engineer-light-abc123",
                      "company": "  Light  ", "location": "  New York, NY  ", "remote": False,
                      "published_at": "2026-07-01T12:00:00.000Z"}], "total_pages": 1}
JOBSPRESSO = _rss(  # the reference fixture declares no xmlns for job_listing:
    "<item><title><![CDATA[Senior Backend Engineer]]></title><link>https://jobspresso.co/job/acme/backend</link>"
    "<pubDate>Mon, 02 Jun 2025 12:00:00 GMT</pubDate><job_listing:company><![CDATA[Acme]]></job_listing:company>"
    "<job_listing:location><![CDATA[Remote]]></job_listing:location></item>",
    "<item><title></title><link>https://jobspresso.co/job/skip</link></item>")
NODESK = _rss(
    "<item><title><![CDATA[Senior Backend Engineer at Acme]]></title>"
    "<link>https://nodesk.co/remote-jobs/acme-senior-backend-engineer/</link>"
    "<pubDate>Mon, 02 Jun 2025 12:00:00 GMT</pubDate></item>",
    "<item><title>No company segment</title><link>https://nodesk.co/remote-jobs/x/</link></item>")
CRYPTO = _rss(
    "<item><title><![CDATA[Senior Backend Engineer at Acme Protocol]]></title>"
    "<link>https://cryptocurrencyjobs.co/engineering/acme-senior-backend/</link>"
    "<description><![CDATA[We are hiring a backend engineer. 100% remote.]]></description>"
    "<pubDate>Wed, 01 Apr 2026 10:00:00 GMT</pubDate></item>",
    "<item><title>Solidity Engineer at Beta Labs</title>"
    "<guid>https://cryptocurrencyjobs.co/engineering/beta-solidity/</guid></item>",
    "<item><title>Item with no link</title></item>")
PYTHONJOBS = _rss(
    "<item><title>Agentic Python Engineer, ExampleCo</title><link>https://www.python.org/jobs/8133/</link>"
    "<description>Remote, Remote/Worldwide, Remote\n&lt;p&gt;ExampleCo builds tools.&lt;/p&gt;</description>"
    "<pubDate>Mon, 14 Sep 2026 08:29:07 +0000</pubDate></item>",
    "<item><title>Backend &amp; ML Developer, Acme AI</title><link>https://www.python.org/jobs/8132/</link>"
    "<description>San Francisco, CA, USA&lt;p&gt;Looking for senior engineers.&lt;/p&gt;</description></item>",
    "<item><title>Single Title Without Comma</title><link>https://www.python.org/jobs/8131/</link></item>")
LARAJOBS = _rss(
    "<item><title>Laravel &amp; Vue Developer</title><link>https://larajobs.com/job/3899</link>"
    "<pubDate>Thu, 02 Jul 2026 13:09:40 +0000</pubDate><dc:creator><![CDATA[Fallback Co]]></dc:creator>"
    "<job:company><![CDATA[Acme PHP]]></job:company><job:location><![CDATA[Lakeland, FL]]></job:location></item>",
    "<item><title>Remote Backend Engineer</title><link>https://larajobs.com/job/3900</link>"
    "<dc:creator><![CDATA[Only Creator Co]]></dc:creator></item>",
    ns=' xmlns:job="https://larajobs.com/ns/job" xmlns:dc="http://purl.org/dc/elements/1.1/"')
STARTUPJOBS = _rss(
    "<item><title>Senior Platform Engineer at Acme &amp; Co</title>"
    "<link>https://startup.jobs/senior-platform-engineer-acme-10260824?utm_source=rss</link>"
    "<pubDate>Thu, 01 Oct 2026 08:19:06 +0000</pubDate><description><![CDATA[Own the platform.\n\n"
    "Amsterdam, Netherlands · €130,000 – €160,000 per year]]></description></item>",
    "<item><title>Remote SRE at bei Beta Labs</title><link>https://startup.jobs/remote-sre-beta-labs-10260825</link>"
    "<description>Remote, Germany</description></item>",
    "<item><title>No Segment Here</title><link>https://startup.jobs/no-at-segment-10260826</link></item>")
HIGHERED = _rss(
    "<item><title><![CDATA[Director of AI Strategy]]></title>"
    "<description><![CDATA[Curry College (Milton, MA)]]></description>"
    "<link>https://www.higheredjobs.com/details.cfm?JobCode=17899012</link>"
    "<pubDate>Thu, 13 Nov 2025 14:10:41 +0000</pubDate></item>",
    "<item><title>Missing Link Role</title><description>Ghost College (Nowhere, ZZ)</description></item>")
NOFLUFF = {"postings": [  # reference rows plus one office row (ours) for the gate test
    {"title": "Frontend Engineer", "name": "ExampleCo", "url": "frontend-engineer-remote", "posted": 1781270000000,
     "fullyRemote": True, "location": {"places": [{"city": "Kraków"}]}},
    {"title": "Java Dev", "name": "Other", "url": "java-dev-gdansk", "location": {"places": [{"city": "Gdańsk"}]}},
    {"title": "", "name": "Broken", "url": "missing-title"}, 7], "totalPages": 1}
JUSTJOIN = {"data": [  # reference rows plus one office row (ours)
    {"slug": "acme-senior-dev-warsaw-javascript", "title": "Senior Developer", "companyName": "Acme",
     "workplaceType": "remote", "locations": [{"city": "Warszawa"}], "publishedAt": "2026-06-12T10:00:00.000Z"},
    {"slug": "beta-java-gdansk", "title": "Java Dev", "companyName": "Beta", "workplaceType": "office",
     "locations": [{"city": "Gdańsk"}]},
    {"slug": "", "title": "Missing Slug", "companyName": "Broken"}, None], "meta": {"next": {"cursor": None}}}
SOLID = {"jobs": [
    {"title": "Senior Dev", "url": "https://solid.jobs/o/abc123/career-ops", "company": "Acme Corp",
     "locations": ["Warszawa", "Remote"]},
    {"title": "Junior Dev", "url": "https://solid.jobs/o/def456/career-ops", "company": "Beta Inc",
     "locations": ["Kraków"]}]}
POWERTOFLY = {"data": [  # freehire powertofly_test.go
    {"id": 1691729, "title": "Senior Go Engineer", "description": "<p>Build &amp; ship.</p>", "location": "Remote",
     "location_regions": ["USA"], "employment_type": "Full Time", "company": {"name": "NASCAR"},
     "country": {"title": "United States"}, "state": {"title": "Alabama"}, "city": {"title": "Talladega"}},
    {"id": 0, "title": "skip me", "company": {"name": "NoID"}}], "meta": {"next_page": None}}
JOBSTASH = {"total": 1, "data": [  # freehire jobstash_test.go: a protected posting, url null
    {"shortUUID": "UHWdXV", "title": "Head of Product", "organization": {"name": "Layer3"}, "url": None,
     "access": "protected", "location": "Remote", "locationType": "remote", "timestamp": 1781270000000,
     "description": "Build things.", "responsibilities": ["Lead"], "requirements": ["Ship"], "benefits": []}]}

# (source, route needle, body, expected first job)
CASES = [
    ("4dayweek", "4dayweek.io", FOURDAY, dict(title="Senior Backend Engineer", company="Acme", remote=True,
     url="https://4dayweek.io/job/senior-backend-at-acme-1", location="Remote, Berlin, Germany", posted="2026-08-01")),
    ("landingjobs", "landing.jobs", LANDING, dict(title="Senior Java Dev", company="Inscale", remote=None,
     url="https://landing.jobs/at/inscale/senior-java-dev", location="Lisbon, PT", posted="2025-02-26")),
    ("getonbrd", "getonbrd.com", GETONBRD, dict(title="Staff AI Engineer", company="Acme Corp", remote=True,
     url="https://www.getonbrd.com/jobs/acme-staff-ai-engineer", location="Remote", posted="")),
    ("manfred", "getmanfred.com", MANFRED, dict(title="Backend Engineer", company="Wuolah", remote=False,
     url="https://www.getmanfred.com/ofertas-empleo/8417/wuolah-backend-engineer-jul26", location="Madrid, Spain",
     posted="")),
    ("thehub", "thehub.io", THEHUB, dict(title="Backend Developer", company="Zeta", remote=True,
     url="https://thehub.io/jobs/5f3a9c", location="Remote, Berlin, Germany", posted="")),
    ("remotli", "remotli.ch", REMOTLI, dict(title="Head of Finance", company="Acme AG", remote=True,
     url="https://job-boards.greenhouse.io/acme/jobs/4012345", location="Zürich, Switzerland, Remote",
     posted="2026-08-01")),
    ("speedrun", "speedrun-talent-network.com", SPEEDRUN, dict(title="Founding Engineer", company="Light",
     remote=None, url="https://speedrun-talent-network.com/jobs/founding-engineer-light-abc123",
     location="New York, NY", posted="2026-07-01")),
    ("jobspresso", "jobspresso.co", JOBSPRESSO, dict(title="Senior Backend Engineer", company="Acme", remote=True,
     url="https://jobspresso.co/job/acme/backend", location="Remote", posted="2025-06-02")),
    ("nodesk", "nodesk.co", NODESK, dict(title="Senior Backend Engineer", company="Acme", remote=True,
     url="https://nodesk.co/remote-jobs/acme-senior-backend-engineer/", location="Remote", posted="2025-06-02")),
    ("cryptojobs", "cryptocurrencyjobs.co", CRYPTO, dict(title="Senior Backend Engineer", company="Acme Protocol",
     remote=True, url="https://cryptocurrencyjobs.co/engineering/acme-senior-backend/", location="Remote",
     posted="2026-04-01")),
    ("pythonjobs", "python.org", PYTHONJOBS, dict(title="Agentic Python Engineer", company="ExampleCo", remote=True,
     url="https://www.python.org/jobs/8133/", location="Remote, Remote/Worldwide, Remote", posted="2026-09-14")),
    ("larajobs", "larajobs.com", LARAJOBS, dict(title="Laravel & Vue Developer", company="Acme PHP", remote=None,
     url="https://larajobs.com/job/3899", location="Lakeland, FL", posted="2026-07-02")),
    ("startupjobs", "startup.jobs", STARTUPJOBS, dict(title="Senior Platform Engineer", company="Acme & Co",
     remote=None, url="https://startup.jobs/senior-platform-engineer-acme-10260824?utm_source=rss",
     location="Amsterdam, Netherlands", posted="2026-10-01")),
    ("higheredjobs", "higheredjobs.com", HIGHERED, dict(title="Director of AI Strategy", company="Curry College",
     remote=None, url="https://www.higheredjobs.com/details.cfm?JobCode=17899012", location="Milton, MA",
     posted="2025-11-13")),
    ("nofluffjobs", "nofluffjobs.com", NOFLUFF, dict(title="Frontend Engineer", company="ExampleCo", remote=True,
     url="https://nofluffjobs.com/pl/job/frontend-engineer-remote", location="Remote, Kraków", posted="2026-06-12")),
    ("justjoin", "justjoin.it", JUSTJOIN, dict(title="Senior Developer", company="Acme", remote=True,
     url="https://justjoin.it/job-offer/acme-senior-dev-warsaw-javascript", location="Remote, Warszawa",
     posted="2026-06-12")),
    ("solidjobs", "solid.jobs", SOLID, dict(title="Senior Dev", company="Acme Corp", remote=True,
     url="https://solid.jobs/o/abc123/career-ops", location="Warszawa, Remote", posted="")),
    ("powertofly", "powertofly.com", POWERTOFLY, dict(title="Senior Go Engineer", company="NASCAR", remote=True,
     url="https://powertofly.com/jobs/detail/1691729", location="Remote, Talladega, Alabama, United States",
     posted="")),
    ("jobstash", "jobstash.xyz", JOBSTASH, dict(title="Head of Product", company="Layer3", remote=True,
     url="https://jobstash.xyz/jobs/UHWdXV/details", location="Remote", posted="2026-06-12")),
]
IDS = [c[0] for c in CASES]


def _run(source: str, route: str, body, *, queries=None, countries=("PL",), days=BIG_DAYS):
    http = FakeHttp({route: body})
    st = {"tried": 0, "ok": 0, "jobs": 0}
    jobs = kn.FEEDS[source](queries or [], http, st, days=days, countries=list(countries))
    return jobs, http, st


# ------------------------------------------------------------------------------- tests
@pytest.mark.parametrize(("source", "route", "body", "want"), CASES, ids=IDS)
def test_board_parses_its_reference_shaped_fixture(source, route, body, want):
    jobs, _, st = _run(source, route, body)
    assert jobs, source
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == f"feed:{source}" and j["board"] == source
        assert j["url"].startswith("http") and j["title"] and "<" not in j["description"]
    first = jobs[0]
    assert {k: first[k] for k in want} == want
    assert st["jobs"] == len(jobs) and st["ok"] >= 1


def test_each_fixture_drops_its_unusable_rows():
    counts = {s: len(_run(s, r, b)[0]) for s, r, b, _ in CASES}
    # getonbrd/thehub/nofluff/justjoin/powertofly walk several pages of the same body: dedupe by url
    assert counts["getonbrd"] == 2 and counts["thehub"] == 1 and counts["nofluffjobs"] == 2
    assert counts["justjoin"] == 2 and counts["powertofly"] == 1 and counts["manfred"] == 1
    assert counts["jobspresso"] == 1 and counts["cryptojobs"] == 2 and counts["pythonjobs"] == 2
    assert counts["startupjobs"] == 2 and counts["higheredjobs"] == 1 and counts["remotli"] == 1


def test_field_details():
    d = {s: _run(s, r, b)[0] for s, r, b, _ in CASES}
    assert d["getonbrd"][1]["location"] == "Chile" and d["getonbrd"][1]["posted"] == "2023-11-14"
    assert "numbers" in d["remotli"][0]["description"] and d["remotli"][0]["extra"]["salary_max"] == 220000
    assert d["startupjobs"][1]["company"] == "Beta Labs" and d["startupjobs"][1]["remote"] is True
    assert d["pythonjobs"][1]["location"] == "San Francisco, CA, USA" and d["pythonjobs"][1]["company"] == "Acme AI"
    assert d["larajobs"][1]["company"] == "Only Creator Co"
    assert d["jobstash"][0]["description"].endswith("Requirements:\n- Ship")
    assert d["higheredjobs"][0]["extra"] == {}
    assert d["manfred"][0]["extra"]["currency"] == "€"


# ------------------------------------------------------------------ Poland: country gate
@pytest.mark.parametrize("source", ["nofluffjobs", "justjoin", "solidjobs"])
def test_polish_boards_keep_everything_for_pl_users_and_only_remote_otherwise(source):
    route, body = next((r, b) for s, r, b, _ in CASES if s == source)
    everything, _, _ = _run(source, route, body, countries=("PL", "GB"))
    others, _, _ = _run(source, route, body, countries=("GB",))
    nowhere, _, _ = _run(source, route, body, countries=())
    assert len(everything) == 2
    assert len(others) == 1 and others[0]["remote"] is True and "Remote" in others[0]["location"]
    assert [j["url"] for j in nowhere] == [j["url"] for j in others]
    dropped = [j for j in everything if j["url"] != others[0]["url"]]
    assert dropped[0]["remote"] is not True


def test_nofluffjobs_posts_json_with_the_reference_content_type():
    _, http, _ = _run("nofluffjobs", "nofluffjobs.com", NOFLUFF)
    call = http.calls[0]
    assert call["method"] == "POST" and call["headers"]["Content-Type"] == "application/infiniteSearch+json"
    assert b"criteriaSearch" in call["data"] and "pageTo=1" in call["url"] and "region=pl" in call["url"]
    assert len(http.calls) == kn.MAX_PAGES  # a page that keeps returning rows stops at the cap


# ------------------------------------------------------------- requests, paging, queries
def test_pages_stop_where_the_reference_stops():
    http = FakeHttp({"page=1": dict(FOURDAY, has_more=True), "page=2": {"data": [], "has_more": False}})
    kn.FEEDS["4dayweek"]([], http, {}, days=BIG_DAYS, countries=[])
    assert [c["url"].split("jobs")[1] for c in http.calls] == [
        "?page=1&limit=100", "?page=2&limit=100"]
    http = FakeHttp({"landing.jobs": LANDING})
    kn.FEEDS["landingjobs"]([], http, {}, days=BIG_DAYS, countries=[])
    assert len(http.calls) == 1  # a single list is one request per run
    _, http, _ = _run("thehub", "thehub.io", THEHUB)
    assert [("countryCode=EU" in c["url"], "isRemote=true" in c["url"]) for c in http.calls] == [
        (True, False), (False, True)]
    _, http, _ = _run("speedrun", "speedrun-talent-network.com", SPEEDRUN, queries=["python", "go"])
    assert any("q=python" in c["url"] for c in http.calls) and any("q=go" in c["url"] for c in http.calls)
    assert all("page=0" in c["url"] for c in http.calls[:1])


def test_queries_are_a_plain_local_keyword_match():
    assert _run("nodesk", "nodesk.co", NODESK, queries=["zzzz nomatch"])[0] == []
    assert len(_run("nodesk", "nodesk.co", NODESK, queries=["backend engineer"])[0]) == 1
    assert _run("manfred", "getmanfred.com", MANFRED, queries=["madrid"])[0][0]["company"] == "Wuolah"


def test_days_filter_drops_old_jobs_but_keeps_undated_ones():
    assert _run("jobspresso", "jobspresso.co", JOBSPRESSO, days=7)[0] == []
    assert len(_run("manfred", "getmanfred.com", MANFRED, days=7)[0]) == 1


# ------------------------------------------------------------------------- safety
def test_rss_rejects_doctype_and_garbage():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel><item><title>x</title></item></channel></rss>'
    assert kn._items(evil) == [] and kn._items("not xml") == [] and kn._items(None) == []
    assert kn.parse_nodesk(evil) == []


@pytest.mark.parametrize("source", IDS)
def test_failures_return_empty_never_raise(source):
    st = {"tried": 0, "ok": 0, "jobs": 0}
    assert kn.FEEDS[source](["x"], FakeHttp({}, status=0), st, days=7, countries=["PL"]) == []
    assert kn.FEEDS[source]([], lambda *a, **k: (200, "garbage"), {}, days=7, countries=["PL"]) == []
    assert kn.FEEDS[source]([], lambda *a, **k: (200, {"unexpected": [1, 2]}), {}, days=7, countries=["PL"]) == []

    def boom(*a, **k):
        raise OSError("down")

    assert kn.FEEDS[source]([], boom, {}, days=7, countries=["PL"]) == []


def test_hostile_values_do_not_become_urls_or_crash():
    bad = {"data": [{"slug": "../x", "title": "T", "company": {"name": "C"}, "locations": "oops", "stack": None}]}
    assert _run("4dayweek", "4dayweek.io", bad)[0] == []
    assert _run("thehub", "thehub.io", {"jobs": {"docs": [{"id": "a/b", "title": "T"}]}})[0] == []
    assert _run("manfred", "getmanfred.com", [{"id": "7", "position": "T", "slug": "s", "status": "ACTIVE"}])[0] == []


# ---------------------------------------------------------------------- registration
def test_every_feed_is_registered_with_the_kit():
    text = (_SRC / "hunt_kit.py").read_text(encoding="utf-8")
    modules = text[text.index("FEED_MODULES = ("):]
    assert '"kit_niche"' in modules[:modules.index(")")]
    start = text.index("FEED_SOURCES = (")
    block = text[start:text.index(")", start)]
    for name in kn.FEEDS:
        assert f'"{name}"' in block, name
    assert len(kn.FEEDS) == len(CASES)
