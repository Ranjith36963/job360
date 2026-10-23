"""Offline tests for src/hunt_kit/kit_gov.py (fixtures only, no network).

Fixture origins: EURES, Jobnet, Trudvsem and Job Market Finland are the JSON shapes in freehire's own
*_test.go files (eures_test.go:129-141, jobnet_test.go:49-54, trudvsem_test.go:42-62,
tyomarkkinatori_test.go:63-70 plus the listItem struct tags). VDAB and Jobstreet have no
fixture in their references, so those are built from the fields the reference code reads
(vdab.mjs:126-145, jobstreet.mjs:119-131 doc comment).
The hosts are unreachable from the build container: nothing here was checked against a live response.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("kit_gov_under_test", _KIT / "kit_gov.py")
kg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kg)  # type: ignore[union-attr]

BIG = 36500
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}


@pytest.fixture(autouse=True)
def _no_nav_pause(monkeypatch):
    monkeypatch.setattr(kg, "NAV_PAUSE_S", 0)


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets (url, data)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        calls.append({"url": url, "method": method, "data": data, "headers": headers or {}})
        for needle, resp in routes.items():
            if needle in url:
                return resp(url, data) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _check(jobs, name):
    assert jobs
    for j in jobs:
        assert set(j) == JOB_KEYS and j["source"] == "feed:" + name and j["board"] == name
        assert j["title"] and j["url"].startswith("http") and j["extra"]["attribution"]
        assert ("title_only" in j["extra"]) == (not j["description"])


# ----------------------------------------------------------------------------------- fixtures
EURES = {"numberRecords": 1, "jvs": [{
    "id": "MTAwMDEtMTAwMTEzOTMxMS1TIDE", "title": "Software Engineer", "description": "<p>We build things.</p>",
    "employer": {"name": "SUSS MicroTec Solutions GmbH & Co. KG"}, "creationDate": 1739403609768,
    "locationMap": {"DE": ["DE12B"], "EL": ["EL30"]}, "positionOfferingCode": "directhire",
    "positionScheduleCodes": ["fulltime"]}]}
VDAB = {"resultaten": [
    {"id": {"id": "a1b2c3"}, "vacaturefunctie": {"naam": "Data Engineer"}, "vacatureBedrijfsnaam": "Delhaize",
     "tewerkstellingsLocatieRegioOfAdres": "Gent", "eerstePublicatieDatum": "2026-10-10T08:00:00.000+0200"},
    {"id": {}, "vacaturefunctie": {"naam": "no id"}}]}
JOBNET = {"totalJobAdCount": 3, "jobAds": [
    {"jobAdId": "c1dc33fa-9c57-4592-a050-36ecda5bdfc1", "title": "IT-supportelev", "hiringOrgName": "ipnordic A/S",
     "postalDistrictName": "Gråsten", "country": "Danmark", "publicationDate": "2026-07-07T00:00:00+02:00",
     "jobAdUrl": "", "isExternal": False, "description": "<p>Er du nysgerrig</p><script>x()</script>"},
    {"jobAdId": "abc", "title": "Dev", "hiringOrgName": "Acme", "municipality": "Aarhus", "isExternal": True,
     "jobAdUrl": "https://acme.example/jobs/1", "description": ""},
    {"jobAdId": "", "title": "No id", "hiringOrgName": "Acme"}]}
TMT = {"lastPage": 0, "content": [{
    "id": "a1", "title": {"fi": "Ohjelmistokehittäjä", "en": "Software Developer"},
    "employer": {"ownerName": {"fi": "Bambu Food Oy"}, "ownerOfficeName": ""},
    "location": {"foreignCountry": False, "municipalities": [{"label": {"fi": "Helsinki", "en": "Helsinki"}}]},
    "publishDate": "2026-07-15T17:55:01.597Z"}]}
TRUDVSEM = {"status": "200", "meta": {"total": 2}, "results": {"vacancies": [
    {"vacancy": {"id": "AA-1", "region": {"region_code": "7700000000000", "name": "Город Москва"},
                 "company": {"name": "ООО Рога и Копыта"}, "creation-date": "2026-07-15", "job-name": "Программист",
                 "vac_url": "https://trudvsem.ru/vacancy/card/123/AA-1", "employment": "Полная занятость",
                 "requirements": "Опыт от 3 лет.", "duty": "Писать код."}},
    {"vacancy": {"id": "CC-3", "job-name": "  ", "region": {"name": "X"}, "company": {"name": "Y"}}}]}}
JOBSTREET = {"data": [{
    "id": "92996157", "title": "Facility Engineer", "advertiser": {"id": "1", "description": "PT YOFC International"},
    "companyName": "YOFC", "locations": [{"label": "Karawang, West Java", "countryCode": "ID"}],
    "listingDate": "2026-06-29T02:53:00Z", "teaser": "Run the <b>plant</b>.", "workTypes": ["Full time"]}]}


# -------------------------------------------------------------------------------------- parsers
def test_parse_eures():
    jobs = kg.parse_eures(EURES, "Germany")
    _check(jobs, "eures")
    j = jobs[0]
    assert j["url"] == "https://europa.eu/eures/portal/jv-se/jv-details/MTAwMDEtMTAwMTEzOTMxMS1TIDE?lang=en"
    assert j["company"].startswith("SUSS") and j["posted"] == "2025-02-12" and j["description"] == "We build things."
    assert j["location"] == "Germany, Greece"  # EL is EURES's code for Greece


def test_parse_vdab():
    jobs = kg.parse_vdab(VDAB)
    _check(jobs, "vdab")
    assert len(jobs) == 1 and jobs[0]["url"] == "https://www.vdab.be/vindeenjob/vacatures/a1b2c3"
    assert (jobs[0]["company"], jobs[0]["location"], jobs[0]["posted"]) == ("Delhaize", "Gent, Belgium", "2026-10-10")
    assert jobs[0]["extra"]["title_only"] is True


def test_parse_jobnet():
    jobs = kg.parse_jobnet(JOBNET)
    _check(jobs, "jobnet")
    assert len(jobs) == 2  # the ad without an id is dropped
    assert jobs[0]["url"] == "https://jobnet.dk/find-job/c1dc33fa-9c57-4592-a050-36ecda5bdfc1"
    assert jobs[0]["location"] == "Gråsten, Denmark" and jobs[0]["posted"] == "2026-07-07"
    assert jobs[0]["description"] == "Er du nysgerrig" and "title_only" not in jobs[0]["extra"]
    assert jobs[1]["url"] == "https://acme.example/jobs/1" and jobs[1]["extra"]["title_only"] is True


def test_parse_tyomarkkinatori():
    jobs = kg.parse_tyomarkkinatori(TMT)
    _check(jobs, "tyomarkkinatori")
    j = jobs[0]
    assert j["title"] == "Software Developer" and j["company"] == "Bambu Food Oy"  # English first
    assert j["location"] == "Helsinki, Finland" and j["posted"] == "2026-07-15"
    assert j["url"] == "https://tyomarkkinatori.fi/henkiloasiakkaat/avoimet-tyopaikat/a1/en"


def test_parse_trudvsem():
    jobs = kg.parse_trudvsem(TRUDVSEM)
    _check(jobs, "trudvsem")
    assert len(jobs) == 1  # blank title dropped
    assert jobs[0]["location"] == "Город Москва, Russia" and jobs[0]["posted"] == "2026-07-15"
    assert jobs[0]["description"] == "Обязанности:\nПисать код.\n\nТребования:\nОпыт от 3 лет."


def test_parse_jobstreet():
    jobs = kg.parse_jobstreet(JOBSTREET, "https://id.jobstreet.com", "Indonesia")
    _check(jobs, "jobstreet")
    j = jobs[0]
    assert j["url"] == "https://id.jobstreet.com/id/job/92996157"  # /id/job only on the Indonesian host
    assert j["company"] == "PT YOFC International" and j["location"] == "Karawang, West Java, Indonesia"
    assert j["posted"] == "2026-06-29" and j["description"] == "Run the plant." and j["extra"]["truncated"] is True
    au = kg.parse_jobstreet(JOBSTREET, "https://www.seek.com.au", "Australia")
    assert au[0]["url"] == "https://www.seek.com.au/job/92996157"


# ------------------------------------------------------------------------------ country gates
GATES = [("eures", ["GB", "US"]), ("vdab", ["NL"]), ("jobnet", ["SE"]), ("tyomarkkinatori", ["DK"]),
         ("trudvsem", ["UA"]), ("jobstreet", ["GB", "PH", "TH"]), ("nav", ["SE", "DK"])]


@pytest.mark.parametrize(("name", "countries"), GATES)
def test_runs_only_for_its_countries(name, countries):
    http = _http({"": (200, {})})
    stats: dict = {}
    assert kg.FEEDS[name](["engineer"], http, stats, days=30, countries=countries) == []
    assert http.calls == [] and stats["tried"] == 0 and stats["jobs"] == 0
    assert kg.FEEDS[name](["engineer"], http, {}, days=30, countries=[]) == [] and http.calls == []


def test_feeds_registry():
    assert set(kg.FEEDS) == {n for n, _ in GATES} and all(callable(f) for f in kg.FEEDS.values())
    sys.modules.pop("hunt_kit_under_test", None)
    spec = importlib.util.spec_from_file_location("hunt_kit_under_test", _KIT / "hunt_kit.py")
    hk = importlib.util.module_from_spec(spec)
    sys.modules["hunt_kit_under_test"] = hk
    spec.loader.exec_module(hk)  # type: ignore[union-attr]
    assert set(kg.FEEDS) <= set(hk.FEED_SOURCES) and "kit_gov" in hk.FEED_MODULES


# ---------------------------------------------------------------------- requests and paging
def test_eures_sends_the_users_countries_and_stops_on_a_short_page():
    full = {"numberRecords": 120, "jvs": [{"id": f"I{i}", "title": "Python Engineer", "employer": {"name": "Co"},
                                           "locationMap": {"DE": []}} for i in range(50)]}
    http = _http({"jv-search/search": (200, full)})
    # every page is full and reports 120 records: stops once 120 rows are read (page 3)
    jobs = kg.fetch_eures(["python"], http, {}, days=BIG, countries=["DE", "GR", "GB"])
    assert len(http.calls) == 3 and len(jobs) == 50  # same ids on every page are de-duplicated
    body = json.loads(http.calls[0]["data"])
    assert http.calls[0]["method"] == "POST" and body["locationCodes"] == ["de", "el"] and body["resultsPerPage"] == 50
    assert body["publicationPeriod"] == "LAST_THREE_DAYS" and body["keywords"] == [{"keyword": "python", "specificSearchCode": "TITLE"}]
    browse = _http({"jv-search/search": (200, EURES)})
    kg.fetch_eures([], browse, {}, days=BIG, countries=["DE"])  # no titles: the newest ads, unfiltered
    assert json.loads(browse.calls[0]["data"])["keywords"] == []
    short = _http({"jv-search/search": (200, EURES)})
    out = kg.fetch_eures(["software"], short, {}, days=BIG, countries=["DE"])
    assert len(short.calls) == 1 and len(out) == 1  # short page: no second request
    assert kg.fetch_eures(["cook"], _http({"jv-search/search": (200, EURES)}), {}, days=BIG, countries=["DE"]) == []


def test_vdab_pages_by_zero_based_index_with_the_public_key():
    page = {"resultaten": [{"id": {"id": f"V{p}-{i}"}, "vacaturefunctie": {"naam": "Dev"}} for p in range(1) for i in range(100)]}
    http = _http({"vacatureLight/zoek": (200, page)})
    kg.fetch_vdab(["python", "data"], http, {}, days=30, countries=["BE"])
    assert len(http.calls) == 2 * kg.NATIONAL_PAGES  # full pages: capped per query
    first = json.loads(http.calls[0]["data"])
    assert first["pagina"] == 0 and first["criteria"]["trefwoord"] == "python" and first["criteria"]["onlineSindsCode"] == "30"
    assert json.loads(http.calls[1]["data"])["pagina"] == 1
    assert http.calls[0]["headers"]["vej-key-monitor"] == kg.VDAB_FRONTEND_ID
    short = _http({"vacatureLight/zoek": (200, VDAB)})
    assert len(kg.fetch_vdab(["data"], short, {}, days=BIG, countries=["BE"])) == 1 and len(short.calls) == 1


def test_jobnet_sends_csrf_header_and_stops_at_the_total():
    http = _http({"FindJob/Search": (200, JOBNET)})
    jobs = kg.fetch_jobnet(["support"], http, {}, days=BIG, countries=["DK"])
    assert len(http.calls) == 1 and len(jobs) == 2  # 3 ads reported, 3 returned: no page 2
    c = http.calls[0]
    assert c["headers"] == {"x-csrf": "1"} and "searchString=support" in c["url"] and "pageNumber=1" in c["url"]
    more = dict(JOBNET, totalJobAdCount=999)
    again = _http({"FindJob/Search": (200, more)})
    kg.fetch_jobnet(["support"], again, {}, days=BIG, countries=["DK"])
    assert len(again.calls) == 2 and "pageNumber=2" in again.calls[1]["url"]


def test_tyomarkkinatori_posts_the_query_and_stops_at_last_page():
    http = _http({"search/v2/search": (200, TMT)})
    jobs = kg.fetch_tyomarkkinatori(["developer"], http, {}, days=BIG, countries=["FI"])
    assert len(jobs) == 1 and len(http.calls) == 1  # lastPage 0
    body = json.loads(http.calls[0]["data"])
    assert body["query"] == "developer" and body["paging"] == {"pageNumber": 0, "pageSize": 90}
    assert body["sorting"] == "LATEST"
    # more pages reported: the NATIONAL_PAGES cap applies
    n = {"c": 0}

    def pages(_u, _d):
        n["c"] += 1
        item = dict(TMT["content"][0], id=f"p{n['c']}")
        return 200, {"lastPage": 9, "content": [item]}

    capped = _http({"search/v2/search": pages})
    kg.fetch_tyomarkkinatori(["developer"], capped, {}, days=BIG, countries=["FI"])
    assert len(capped.calls) == kg.NATIONAL_PAGES


def test_trudvsem_pages_by_index_and_stops_at_the_total():
    http = _http({"vacancies/region/7700000000000": (200, TRUDVSEM)})
    jobs = kg.fetch_trudvsem(["Программист"], http, {}, days=BIG, countries=["RU"])
    assert len(jobs) == 1 and len(http.calls) == 1  # short page
    assert "limit=100" in http.calls[0]["url"] and "offset=0" in http.calls[0]["url"]
    assert kg.fetch_trudvsem(["python"], _http({"vacancies": (200, TRUDVSEM)}), {}, days=BIG, countries=["RU"]) == []
    big = {"meta": {"total": 250}, "results": {"vacancies": [
        {"vacancy": {"id": f"x{i}", "job-name": "Dev", "vac_url": f"https://trudvsem.ru/{i}"}} for i in range(100)]}}
    seq = _http({"vacancies/region": (200, big)})
    kg.fetch_trudvsem(["dev"], seq, {}, days=BIG, countries=["RU"])
    assert 1 < len(seq.calls) <= kg.LOCAL_PAGES


def test_jobstreet_uses_each_markets_host_and_site_key():
    http = _http({"seek.com.au": (200, JOBSTREET), "seek.co.nz": (200, JOBSTREET)})
    jobs = kg.fetch_jobstreet(["engineer"], http, {}, days=BIG, countries=["AU", "NZ", "PH"])
    assert [j["location"] for j in jobs] == ["Karawang, West Java, Australia", "Karawang, West Java, New Zealand"]
    hosts = [c["url"].split("/api/")[0] for c in http.calls]
    assert hosts == ["https://www.seek.com.au", "https://www.seek.co.nz"]  # short pages: one call each
    assert "siteKey=AU-Main" in http.calls[0]["url"] and "keywords=engineer" in http.calls[0]["url"]
    assert "pageSize=30" in http.calls[0]["url"]
    full = {"data": [{"id": str(i), "title": "Dev"} for i in range(30)]}
    paged = _http({"hk.jobsdb.com": (200, full)})
    kg.fetch_jobstreet(["dev"], paged, {}, days=BIG, countries=["HK"])
    assert len(paged.calls) == kg.NATIONAL_PAGES and "siteKey=HK-Main" in paged.calls[0]["url"]


# NAV arbeidsplassen: a trimmed REAL response (Data Scientist, sort=published, read live 2026-10-19).
NAV = json.loads((Path(__file__).resolve().parent / "fixtures" / "hunt_kit" / "nav_search.json")
                 .read_text(encoding="utf-8"))


def test_parse_nav():
    jobs = kg.parse_nav(NAV)
    _check(jobs, "nav")
    assert len(jobs) == 3
    j = jobs[0]
    assert j["url"] == "https://arbeidsplassen.nav.no/stillinger/stilling/d2560ea2-eef6-45e7-babe-f94f9f236b6b"
    assert j["company"] == "SLB Kristiansand" and j["posted"] == "2026-10-16"
    assert j["title"].endswith("Data Scientist - Industrial Data & Analytics")  # NAV's "\&" escape is undone
    assert j["location"] == "Kristiansand S, Norway" and j["remote"] is None
    assert j["description"].startswith("Data Scientist wanted") and "title_only" not in j["extra"]
    assert j["extra"]["truncated"] is True  # a one-sentence summary, not the ad: the assistant opens the link
    assert j["extra"]["work_language"] == ["Engelsk"] and j["extra"]["expires"] == "2026-10-29"
    assert jobs[1]["extra"]["work_language"] == ["Norsk"] and jobs[1]["location"] == "Oslo, Norway"
    # no summary -> title_only; no businessName -> employer name; several places are de-duplicated; junk rows dropped
    bare = {"hits": {"hits": [
        {"_source": {"uuid": "11111111-1111-1111-1111-111111111111", "title": "Dev", "employer": {"name": "ACME AS"},
                     "published": "bad",
                     "locationList": [{"city": "BERGEN", "country": "NORGE"}, {"city": "BERGEN"}, {"city": "OSLO"}]}},
        {"_source": {"uuid": "", "title": "no id"}},
        {"_source": {"uuid": "22222222-2222-2222-2222-222222222222", "title": " "}},
        {"_source": {"uuid": "..", "title": "path trick"}},  # not a UUID: never becomes a link (review 2026-10-19)
        "junk", {}]}}
    only = kg.parse_nav(bare)
    assert len(only) == 1 and only[0]["company"] == "ACME AS" and only[0]["posted"] == ""
    assert only[0]["location"] == "Bergen, Oslo, Norway" and only[0]["extra"]["title_only"] is True
    assert kg.parse_nav(None) == kg.parse_nav({}) == kg.parse_nav({"hits": {"hits": "x"}}) == []


def test_nav_searches_each_title_newest_first_and_stops_at_the_total():
    http = _http({"stillinger/api/search": (200, NAV)})
    jobs = kg.fetch_nav(["data scientist", "ml engineer"], http, {}, days=BIG, countries=["NO"])
    assert len(http.calls) == 2 * kg.NATIONAL_PAGES and len(jobs) == 3  # 3 of 29 read per call; same ids de-duplicated
    first = http.calls[0]["url"]
    assert first.startswith("https://arbeidsplassen.nav.no/stillinger/api/search?")
    assert "q=data+scientist" in first and "size=50" in first and "from=0" in first and "sort=published" in first
    assert "workLanguage" not in first  # never filtered by language by default


def test_nav_pages_by_rows_received_and_stops_on_empty_or_total():
    def row(i):
        return {"_source": {"uuid": f"00000000-0000-0000-0000-{i:012d}", "title": "Data Engineer", "businessName": "Co"}}

    def page(total, n):
        return 200, {"hits": {"total": {"value": total}, "hits": [row(i) for i in range(n)]}}

    full = _http({"stillinger/api/search": lambda _u, _d: page(60, 25)})
    kg.fetch_nav(["data"], full, {}, days=BIG, countries=["NO"])
    assert len(full.calls) == kg.NATIONAL_PAGES and "from=25" in full.calls[1]["url"]  # advances by rows received
    empty = _http({"stillinger/api/search": page(0, 0)})
    assert kg.fetch_nav(["data"], empty, {}, days=BIG, countries=["NO"]) == [] and len(empty.calls) == 1
    done = _http({"stillinger/api/search": page(3, 3)})
    assert len(kg.fetch_nav(["data"], done, {}, days=BIG, countries=["NO"])) == 3 and len(done.calls) == 1


def test_nav_non_200_counts_as_failed_and_star_runs_it():
    stats: dict = {}
    down = _http({"": (503, None)})
    assert kg.fetch_nav(["data"], down, stats, days=BIG, countries=["NO"]) == []
    assert (stats["tried"], stats["ok"], stats["jobs"]) == (1, 0, 0) and len(down.calls) == 1
    # a refusal (429) on the first title stops the whole service: three titles, still one request
    limited = _http({"": (429, None)})
    assert kg.fetch_nav(["a", "b", "c"], limited, {}, days=BIG, countries=["NO"]) == [] and len(limited.calls) == 1
    star = _http({"": (200, {"hits": {"total": {"value": 0}, "hits": []}})})
    kg.fetch_nav(["data"], star, {}, days=30, countries=["IN", "*"])  # --visa-onsite: runs everywhere
    assert len(star.calls) == 1


def test_a_failing_service_never_raises():
    def boom(url, *, method="GET", data=None, headers=None, as_json=True):
        raise RuntimeError("down")

    for name, countries in (("eures", ["DE"]), ("vdab", ["BE"]), ("jobstreet", ["AU"])):
        stats: dict = {}
        assert kg.FEEDS[name](["x"], boom, stats, days=30, countries=countries) == []
        assert stats["jobs"] == 0


def test_star_runs_every_market_for_visa_onsite():
    http = _http({"": (200, {"data": []})})
    kg.fetch_jobstreet(["dev"], http, {}, days=30, countries=["IN", "*"])
    hosts = {c["url"].split("/api/")[0] for c in http.calls}
    assert hosts == {"https://www.seek.com.au", "https://www.seek.co.nz", "https://my.jobstreet.com",
                     "https://sg.jobstreet.com", "https://id.jobstreet.com", "https://hk.jobsdb.com"}


def test_wanted_puts_the_users_own_markets_first():
    assert kg._wanted(["NZ", "*"], "AU", "NZ", "MY")[:1] == ["NZ"]
    assert kg._wanted(["NZ", "*"], "AU", "NZ", "MY") == ["NZ", "AU", "MY"]
    assert kg._wanted(["NZ"], "AU", "NZ", "MY") == ["NZ"]
