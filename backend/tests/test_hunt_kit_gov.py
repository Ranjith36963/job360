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


def _http(routes):
    """Fake http: the first route whose needle is in the url answers; a callable gets (url, data)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
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
         ("trudvsem", ["UA"]), ("jobstreet", ["GB", "PH", "TH"])]


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
    assert body["publicationPeriod"] == "LAST_THREE_DAYS" and body["keywords"] == []
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


def test_a_failing_service_never_raises():
    def boom(url, *, method="GET", data=None, headers=None, as_json=True):
        raise RuntimeError("down")

    for name, countries in (("eures", ["DE"]), ("vdab", ["BE"]), ("jobstreet", ["AU"])):
        stats: dict = {}
        assert kg.FEEDS[name](["x"], boom, stats, days=30, countries=countries) == []
        assert stats["jobs"] == 0
