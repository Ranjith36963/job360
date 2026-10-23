"""Offline tests for src/hunt_kit/kit_markets3.py (EchoJobs, Torre, Tecla, Japan Dev; fixtures only, no network).

Fixture origins (freehire, MIT): EchoJobs follows echojobs.go (sitemap index, /sitemap-jobs/N.xml shards, ld+json
JobPosting); Torre follows torre.go (sitemap-index.xml, /post/<id>-<slug>, ld+json); Tecla follows tecla.go (the
data.jobs[] / data.pagination.countPages shape); Japan Dev follows japandev.go (sitemap, __NUXT_DATA__ table). The
hosts are unreachable from the build container: nothing here was checked against a live response.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("kit_markets3_under_test", _KIT / "kit_markets3.py")
m3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m3)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(m3, "PAUSE_S", 0)


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets the url."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False, max_bytes=None):
        calls.append({"url": url, "as_json": as_json, "once": once})
        for needle, resp in routes.items():
            if needle in url:
                return resp(url) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _hosts(http):
    return {urlsplit(c["url"]).hostname for c in http.calls}


def _check(jobs, name, host):
    assert jobs
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == "feed:" + name and j["board"] == name
        assert j["title"] and j["company"] and j["extra"]["attribution"]
        assert urlsplit(j["url"]).scheme == "https" and urlsplit(j["url"]).hostname == host
        assert ("title_only" in j["extra"]) == (not j["description"])


def _ld(**kw):
    base = {"@context": "https://schema.org", "@type": "JobPosting"}
    return '<script type="application/ld+json">' + json.dumps({**base, **kw}) + "</script>"


def _page(body):
    return f"<html><head>{body}</head><body></body></html>"


def _urlset(*entries):
    rows = "".join(f"<url><loc>{e[0]}</loc>" + (f"<lastmod>{e[1]}</lastmod>" if len(e) > 1 else "") + "</url>"
                   for e in entries)
    return "<?xml version='1.0'?><urlset>" + rows + "</urlset>"


def _index(*locs):
    return "<?xml version='1.0'?><sitemapindex>" + "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in locs) \
        + "</sitemapindex>"


def _day(delta=0):
    return (datetime.now(timezone.utc) - timedelta(days=delta)).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------- EchoJobs
ECHO = "https://echojobs.io"


def _echo_ad(title="Backend Engineer", company="Acme", **kw):
    return _page(_ld(title=title, description="&lt;p&gt;Build &amp;amp; ship APIs.&lt;/p&gt;", datePosted=_day(1),
                     hiringOrganization={"name": company}, jobLocationType="TELECOMMUTE",
                     applicantLocationRequirements={"@type": "Country", "name": "Germany"}, **kw))


def _echo_routes(**extra):
    return {
        "sitemap.xml": (200, _index(f"{ECHO}/sitemap-nav.xml", f"{ECHO}/sitemap-jobs/1.xml", f"{ECHO}/sitemap-jobs/2.xml",
                                    "https://evil.example/sitemap-jobs/9.xml")),
        "sitemap-jobs/1.xml": (200, _urlset((f"{ECHO}/job/backend-engineer-acme-1", _day(1)),
                                            (f"{ECHO}/job/data-analyst-beta-2", _day(2)))),
        "sitemap-jobs/2.xml": (200, _urlset((f"{ECHO}/job/old-job-3", _day(60)))),
        "/job/backend-engineer-acme-1": (200, _echo_ad()),
        "/job/data-analyst-beta-2": (200, _echo_ad("Data Analyst", "Beta")),
        **extra,
    }


def test_echojobs_happy_path_slug_prefilter_and_window():
    http = _http(_echo_routes())
    stats: dict = {}
    jobs = m3.fetch_echojobs(["backend"], http, stats, days=BIG, countries=["US"])
    _check(jobs, "echojobs", "echojobs.io")
    assert [j["title"] for j in jobs] == ["Backend Engineer"]
    j = jobs[0]
    assert j["company"] == "Acme" and j["remote"] is True and "Germany" in j["location"]
    assert j["description"] == "Build & ship APIs." and j["posted"] == _day(1)
    assert j["url"] == f"{ECHO}/job/backend-engineer-acme-1"
    # the slug pre-filter spared the analyst page; the two-week-old shard entry is never opened
    assert not any("data-analyst" in c["url"] or "old-job" in c["url"] for c in http.calls)
    assert _hosts(http) == {"echojobs.io"} and all(c["once"] for c in http.calls)


def test_echojobs_walk_stops_at_a_stale_shard_and_pages_to_the_second():
    routes = _echo_routes()
    routes["sitemap-jobs/1.xml"] = (200, _urlset((f"{ECHO}/job/backend-engineer-acme-1", _day(1))))
    http = _http(routes)
    jobs = m3.fetch_echojobs([], http, {}, days=BIG, countries=["*"])
    assert any("sitemap-jobs/2.xml" in c["url"] for c in http.calls)  # fresh first shard: the second is read
    assert [j["title"] for j in jobs] == ["Backend Engineer"]
    http2 = _http(_echo_routes())  # default routes: shard 1 is fresh, shard 2 holds only a stale entry
    m3.fetch_echojobs([], http2, {}, days=BIG, countries=["*"])
    assert sum("sitemap-jobs" in c["url"] for c in http2.calls) == 2


def test_echojobs_one_bad_posting_is_dropped_not_the_board():
    routes = _echo_routes()
    routes["/job/data-analyst-beta-2"] = (200, _page(_ld(title="No Employer")))
    routes["sitemap-jobs/1.xml"] = (200, _urlset((f"{ECHO}/job/data-analyst-beta-2", _day(1)),
                                                 (f"{ECHO}/job/broken-page-9", _day(1)),
                                                 (f"{ECHO}/job/backend-engineer-acme-1", _day(1))))
    routes["/job/broken-page-9"] = (200, "<html><script type='application/ld+json'>{not json</script>")
    jobs = m3.fetch_echojobs([], _http(routes), {}, days=BIG, countries=["*"])
    assert [j["title"] for j in jobs] == ["Backend Engineer"]


def test_echojobs_refusal_ends_the_board():
    routes = _echo_routes()
    routes["/job/backend-engineer-acme-1"] = (429, None)
    http = _http(routes)
    jobs = m3.fetch_echojobs([], http, {}, days=BIG, countries=["*"])
    assert jobs == []
    assert not any("data-analyst" in c["url"] for c in http.calls)  # the 429 stopped the run
    assert m3.fetch_echojobs([], _http({"sitemap.xml": (403, None)}), {}, days=BIG, countries=["*"]) == []


def test_echojobs_off_host_and_non_https_entries_are_never_fetched():
    routes = _echo_routes()
    routes["sitemap-jobs/1.xml"] = (200, _urlset(
        ("http://echojobs.io/job/plain-http-4", _day(1)), ("https://evil.example/job/off-host-5", _day(1)),
        ("https://echojobs.io.evil.example/job/lookalike-6", _day(1)),
        ("https://user@echojobs.io/job/userinfo-7", _day(1)), ("https://echojobs.io:8443/job/port-8", _day(1)),
        (f"{ECHO}/job/backend-engineer-acme-1", _day(1))))
    http = _http(routes)
    jobs = m3.fetch_echojobs([], http, {}, days=BIG, countries=["*"])
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"echojobs.io"}
    assert _hosts(http) == {"echojobs.io"}
    assert {urlsplit(c["url"]).scheme for c in http.calls} == {"https"}


# ------------------------------------------------------------------------------------- Torre
TORRE = "https://torre.ai"


def _torre_ad(title="Product Designer", company="Globex", **kw):
    return _page(_ld(title=title, description="&lt;p&gt;Design things.&lt;/p&gt;", datePosted=_day(2),
                     hiringOrganization={"name": company}, jobLocationType="TELECOMMUTE",
                     jobLocation=[{"@type": "Place", "address": {"addressLocality": "Bogota", "addressCountry": "CO"}}],
                     **kw))


def _torre_routes():
    return {
        "sitemap-index.xml": (200, _index(f"{TORRE}/sitemap-1.xml", "https://evil.example/sitemap-2.xml")),
        "sitemap-1.xml": (200, _urlset((f"{TORRE}/post/AbC123-product-designer", _day(1)),
                                       (f"{TORRE}/post/Zz9-data-engineer", _day(3)),
                                       (f"{TORRE}/post/AbC123-product-designer", _day(1)),
                                       (f"{TORRE}/people/not-a-post", _day(1)))),
        "/post/AbC123-product-designer": (200, _torre_ad()),
        "/post/Zz9-data-engineer": (200, _torre_ad("Data Engineer", "Initech")),
    }


def test_torre_happy_path_dedup_and_remote_place():
    http = _http(_torre_routes())
    jobs = m3.fetch_torre(["designer"], http, {}, days=BIG, countries=["US"])
    _check(jobs, "torre", "torre.ai")
    j = jobs[0]
    assert (j["title"], j["company"], j["remote"]) == ("Product Designer", "Globex", True)
    assert j["location"] == "Remote, Bogota, CO" and j["description"] == "Design things."
    assert j["url"] == f"{TORRE}/post/AbC123-product-designer"
    assert _hosts(http) == {"torre.ai"} and sum("AbC123" in c["url"] for c in http.calls) == 1
    both = m3.fetch_torre([], _http(_torre_routes()), {}, days=BIG, countries=["*"])
    assert [x["title"] for x in both] == ["Product Designer", "Data Engineer"]  # newest lastmod first


def test_torre_unescapes_json_and_html_escaped_descriptions():
    assert m3._torre_text("&lt;p&gt;Hi&lt;/p&gt;") == "<p>Hi</p>"
    assert m3._torre_text("<p>Line\\nTwo</p>") == "<p>Line\nTwo</p>"
    assert m3._torre_text('bad "\\ quote') == 'bad "\\ quote'


def test_torre_bad_posting_and_refusal():
    routes = _torre_routes()
    routes["/post/AbC123-product-designer"] = (200, _page(_ld(title="Designer", hiringOrganization={"name": "X"})))
    jobs = m3.fetch_torre([], _http(routes), {}, days=BIG, countries=["*"])
    assert [j["title"] for j in jobs] == ["Data Engineer"]  # no description: dropped; the board goes on
    refused = _torre_routes()
    refused["/post/AbC123-product-designer"] = (403, None)
    http = _http(refused)
    assert m3.fetch_torre([], http, {}, days=BIG, countries=["*"]) == []
    assert not any("Zz9" in c["url"] for c in http.calls)


def test_torre_off_host_post_links_and_child_sitemaps_are_never_fetched():
    routes = _torre_routes()
    routes["sitemap-1.xml"] = (200, _urlset(
        ("http://torre.ai/post/Plain1-x", _day(1)), ("https://evil.example/post/Off2-x", _day(1)),
        ("https://torre.ai.evil.example/post/Look3-x", _day(1)), (f"{TORRE}/post/AbC123-product-designer", _day(1))))
    http = _http(routes)
    jobs = m3.fetch_torre([], http, {}, days=BIG, countries=["*"])
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"torre.ai"} and len(jobs) == 1
    assert _hosts(http) == {"torre.ai"}


# ------------------------------------------------------------------------------------- Tecla
def _tecla_page(ids, pages):
    return 200, {"data": {"jobs": [{"id": i, "name": f"Dev {i}", "company": {"name": "Acme"},
                                    "createdAt": f"{_day(1)}T10:00:00.123456789", "description": "<p>Remote work</p>"}
                                   for i in ids], "pagination": {"countPages": pages}}}


def test_tecla_pages_until_count_pages_and_job_shape():
    http = _http({"page=1": _tecla_page([1, 2], 3), "page=2": _tecla_page([3], 3), "page=3": _tecla_page([4], 3),
                  "page=4": _tecla_page([5], 3)})
    jobs = m3.fetch_tecla([], http, {}, days=BIG, countries=["US"])
    assert [j["url"] for j in jobs] == [f"https://app.tecla.io/job?id={i}" for i in (1, 2, 3, 4)]
    assert len(http.calls) == 3 and _hosts(http) == {"api.tecla.io"}
    j = jobs[0]
    assert (j["company"], j["remote"], j["location"], j["posted"], j["description"]) == (
        "Acme", True, "Remote", _day(1), "Remote work")
    _check(jobs, "tecla", "app.tecla.io")


def test_tecla_bad_row_empty_page_and_refusals():
    rows = {"data": {"jobs": [{"id": "x", "name": "Bad id", "company": {"name": "A"}}, "junk", None,
                              {"id": 7, "name": "", "company": {"name": "A"}},
                              {"id": 8, "name": "No company", "company": {}},
                              {"id": 9, "name": "Good", "company": {"name": "A"}}], "pagination": {"countPages": 1}}}
    jobs = m3.fetch_tecla([], _http({"page=1": (200, rows)}), {}, days=BIG, countries=["*"])
    assert [j["title"] for j in jobs] == ["Good"]
    http = _http({"page=1": _tecla_page([1], 5), "page=2": (429, None), "page=3": _tecla_page([3], 5)})
    assert [j["title"] for j in m3.fetch_tecla([], http, {}, days=BIG, countries=["*"])] == ["Dev 1"]
    assert len(http.calls) == 2  # the 429 on page 2 ended the walk
    assert m3.fetch_tecla([], _http({"page=1": _tecla_page([], 0)}), {}, days=BIG, countries=["*"]) == []
    assert m3.fetch_tecla([], _http({"page=1": (200, "not json")}), {}, days=BIG, countries=["*"]) == []


def test_tecla_page_cap():
    http = _http({"page=": lambda url: _tecla_page([int(url.rsplit("=", 1)[1])], 100)})
    jobs = m3.fetch_tecla([], http, {}, days=BIG, countries=["*"])
    assert len(jobs) == m3.TECLA_PAGES == len(http.calls)


# ------------------------------------------------------------------------------------- Japan Dev
JD = "https://japan-dev.com"


def _nuxt(**fields):
    """A Nuxt devalue-style table: every value sits in the table and the job object points at it by index."""
    table: list = [{"job": 1}, None]
    idx: dict = {}

    def put(v):
        if isinstance(v, dict):
            v = {k: put(x) for k, x in v.items()}
        elif isinstance(v, list):
            v = [put(x) for x in v]
        table.append(v)
        return len(table) - 1

    table[1] = {k: put(v) for k, v in fields.items()}
    del idx
    return f'<html><script type="application/json" id="__NUXT_DATA__">{json.dumps(table)}</script></html>'


def _jd_ad(**kw):
    base = dict(title="Backend Engineer", slug="backend-engineer-1", company={"name": "Acme KK"}, location="Tokyo",
                raw_content="<p>Build <b>services</b>.</p>", published_at=f"{_day(2)}T00:00:00Z",
                remote_level="remote_level_full_japan", employment_type="employment_type_full_time",
                candidate_location="candidate_location_anywhere", sponsors_visas="sponsors_visas_yes",
                japanese_level_enum="japanese_level_not_required", english_level_enum="english_level_business_level",
                salary_min=6000000, salary_max=9000000, skills=[{"name": "Go"}, {"name": "Go"}, {"name": "AWS"}],
                application_url="https://evil.example/apply")
    return _nuxt(**{**base, **kw})


def _jd_routes():
    return {
        "japan-dev.com/sitemap.xml": (200, _index("https://japan-dev.com/sitemap-pages.xml",
                                                  "https://japan-dev.com/sitemaps/jobs.xml")),
        "sitemaps/jobs.xml": (200, _urlset((f"{JD}/jobs/acme/backend-engineer-1", _day(1)),
                                           (f"{JD}/jobs/beta/data-analyst-2", _day(2)),
                                           (f"{JD}/jobs/acme/backend-engineer-1", _day(1)),
                                           (f"{JD}/companies/acme", _day(1)))),
        "backend-engineer-1": (200, _jd_ad()),
        "data-analyst-2": (200, _jd_ad(title="Data Analyst", slug="data-analyst-2", company={"name": "Beta"})),
    }


def test_japandev_happy_path_facts_and_no_application_url():
    http = _http(_jd_routes())
    jobs = m3.fetch_japandev(["backend"], http, {}, days=BIG, countries=["US"])
    _check(jobs, "japandev", "japan-dev.com")
    j = jobs[0]
    assert (j["title"], j["company"], j["remote"], j["posted"]) == ("Backend Engineer", "Acme KK", True, _day(2))
    assert j["location"] == "Tokyo, Japan" and j["url"] == f"{JD}/jobs/acme/backend-engineer-1"
    assert "Visa sponsorship: available" in j["description"] and "Japanese: Not required" in j["description"]
    assert "English: Business level" in j["description"] and "Japan Dev tags: Go, AWS" in j["description"]
    assert "6,000,000 - 9,000,000 JPY a year" in j["description"] and j["description"].endswith("Build services.")
    assert j["extra"]["tags"] == ["Go", "AWS"]
    assert _hosts(http) == {"japan-dev.com"} and not any("data-analyst" in c["url"] for c in http.calls)


def test_japandev_on_site_ads_follow_the_users_country_and_worldwide_remote_has_none():
    routes = _jd_routes()
    routes["backend-engineer-1"] = (200, _jd_ad(remote_level="remote_level_none"))
    assert m3.fetch_japandev([], _http(routes), {}, days=BIG, countries=["US"]) != []  # no place country: kept
    routes["backend-engineer-1"] = (200, _jd_ad(remote_level="remote_level_full_worldwide", location="Remote"))
    jobs = m3.fetch_japandev(["backend"], _http(routes), {}, days=BIG, countries=["US"])
    assert jobs[0]["location"] == "Remote" and jobs[0]["remote"] is True


def test_japandev_bad_postings_are_dropped_not_the_board():
    routes = _jd_routes()
    routes["backend-engineer-1"] = (200, _jd_ad(slug="some-other-slug"))  # slug differs from the URL
    routes["data-analyst-2"] = (200, _jd_ad(title="Data Analyst", slug="data-analyst-2", company={"name": "Beta"}))
    assert [j["title"] for j in m3.fetch_japandev([], _http(routes), {}, days=BIG, countries=["*"])] == ["Data Analyst"]
    for page in ("<html>no data</html>", '<script id="__NUXT_DATA__">{broken</script>',
                 '<script id="__NUXT_DATA__">[{"job": 99}]</script>', _jd_ad(raw_content="<p><br></p>"),
                 _jd_ad(company={"name": ""}), _jd_ad(title="")):
        assert m3.parse_japandev_ad(page, f"{JD}/jobs/acme/backend-engineer-1") is None


def test_japandev_refusal_off_host_leaf_and_bad_urls():
    routes = _jd_routes()
    routes["backend-engineer-1"] = (403, None)
    http = _http(routes)
    assert m3.fetch_japandev([], http, {}, days=BIG, countries=["*"]) == []
    assert not any("data-analyst" in c["url"] for c in http.calls)
    routes = _jd_routes()
    routes["japan-dev.com/sitemap.xml"] = (200, _index("https://evil.example/sitemaps/jobs.xml"))
    http = _http(routes)
    assert m3.fetch_japandev([], http, {}, days=BIG, countries=["*"]) == [] and _hosts(http) == {"japan-dev.com"}
    routes = _jd_routes()
    routes["sitemaps/jobs.xml"] = (200, _urlset(
        ("http://japan-dev.com/jobs/a/plain-1", _day(1)), ("https://evil.example/jobs/a/off-2", _day(1)),
        ("https://japan-dev.com/jobs/a", _day(1)), ("https://japan-dev.com/jobs/a/b/c", _day(1)),
        ("https://japan-dev.com/jobs/a/q-3?x=1", _day(1)), (f"{JD}/jobs/acme/backend-engineer-1", _day(1))))
    http = _http(routes)
    jobs = m3.fetch_japandev([], http, {}, days=BIG, countries=["*"])
    assert [urlsplit(j["url"]).hostname for j in jobs] == ["japan-dev.com"] and _hosts(http) == {"japan-dev.com"}
    assert {urlsplit(c["url"]).scheme for c in http.calls} == {"https"}


# ------------------------------------------------------------------------------------- hostile input
def test_hostile_markup_is_linear_and_harmless():
    page = _page(_ld(title="Dev", description="x", hiringOrganization={"name": "A"}) + "<script " * 20000
                 + "<a " * 20000 + "<!-- " * 20000)
    start = time.perf_counter()
    m3.parse_echojobs_ad(page, f"{ECHO}/job/dev-1")
    m3.parse_torre_ad(page, f"{TORRE}/post/Aa1-dev")
    m3.parse_japandev_ad("<script id='__NUXT_DATA__'>" + "[" * 5000 + "</script>" * 5000, f"{JD}/jobs/a/b")
    assert time.perf_counter() - start < 5
    xml_bomb = ("<?xml version='1.0'?><!DOCTYPE x [<!ENTITY a 'aaaa'><!ENTITY b '&a;&a;&a;&a;'>]>"
                "<urlset><url><loc>&b;</loc></url></urlset>")
    assert m3.echojobs_slugs(xml_bomb, datetime.now(timezone.utc)) == ([], False)
    assert m3.torre_posts(xml_bomb) == [] and m3.japandev_posts(xml_bomb) == []
    for fn in (m3.parse_echojobs_ad, m3.parse_torre_ad, m3.parse_japandev_ad):
        assert fn(None, "https://x.example/") is None and fn("", "https://x.example/") is None


def test_a_page_that_raises_is_dropped_by_the_reader_not_the_board():
    def boom(page, url):
        raise RuntimeError("bad posting")

    assert m3._safe(boom)("x", "https://echojobs.io/job/a") is None


def test_all_four_feeds_never_raise_and_return_nothing_when_unreachable():
    for fn in m3.FEEDS.values():
        assert fn([], _http({}), {}, days=BIG, countries=["*"]) == []
        assert fn([], _http({"": (0, None)}), {}, days=BIG, countries=["*"]) == []


# ------------------------------------------------------------------------------------- registry
def _hunt_kit_module():
    spec = importlib.util.spec_from_file_location("hunt_kit_markets3_check", _KIT / "hunt_kit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hunt_kit_markets3_check"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_feeds_are_registered_and_the_host_rule_is_exact():
    hk = _hunt_kit_module()
    assert set(m3.FEEDS) == {"echojobs", "torre", "tecla", "japandev"}
    assert set(m3.FEEDS) <= set(hk.FEED_SOURCES) and "kit_markets3" in hk.FEED_MODULES
    assert set(m3.FEEDS) <= set(hk.select_sources("default"))
    assert set(m3.FEEDS) <= set(hk._merged("FEEDS", hk.FEED_MODULES))
    hosts = dict(hk._FEED_HOSTS)
    read = {"echojobs": ["echojobs.io"], "torre": ["torre.ai"], "tecla": ["app.tecla.io", "api.tecla.io"],
            "japandev": ["japan-dev.com"]}
    other = {"echojobs": ["www.echojobs.io", "echojobs.io.evil.example"], "torre": ["www.torre.ai", "torre.co"],
             "tecla": ["tecla.io", "blog.tecla.io"], "japandev": ["www.japan-dev.com", "japan-dev.com.evil.example"]}
    for feed, names in read.items():
        assert all(hosts[feed].search(h) for h in names)
    for feed, names in other.items():
        assert not any(hosts[feed].search(h) for h in names)
    from src.api.routes.hunt_kit import KIT_FILES
    assert "kit_markets3.py" in KIT_FILES


def test_gradcracker_is_reported_as_unsupported_and_the_other_hosts_are_not():
    hk = _hunt_kit_module()
    assert hk.board_from_job_url("https://www.gradcracker.com/hub/1/jobs/2")["kind"] == "unsupported"
    assert hk._UNSUPPORTED_HOSTS.search("gradcracker.com") and not hk._UNSUPPORTED_HOSTS.search("echojobs.io")
    src = (_KIT / "kit_markets3.py").read_text(encoding="utf-8")
    assert "Gradcracker" in src and "Chrome TLS" in src
    assert "Gradcracker" in (_KIT / "README.md").read_text(encoding="utf-8")
    assert "kit_markets3.py" in (_KIT / "NOTICE.md").read_text(encoding="utf-8")


def test_every_http_call_uses_only_the_real_signature():
    import ast
    import inspect
    hk = _hunt_kit_module()
    allowed = set(inspect.signature(hk.http).parameters)
    tree = ast.parse((_KIT / "kit_markets3.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_fetch", "_read"):
            for kw in node.keywords:
                assert kw.arg is None or kw.arg in allowed | {"pause", "ad", "check_block"}, kw.arg
