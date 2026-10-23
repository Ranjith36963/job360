"""Offline tests for src/hunt_kit/kit_feeds.py (fixtures only, no network)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_MOD = _HERE.parent / "src" / "hunt_kit" / "kit_feeds.py"
FIX = _HERE / "fixtures" / "hunt_kit" / "feeds"

_spec = importlib.util.spec_from_file_location("kit_feeds_under_test", _MOD)
kf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kf)  # type: ignore[union-attr]

JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source",
            "board", "remote", "visa_flag", "extra"}
BIG_DAYS = 36500


def _j(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _check(jobs: list[dict], source: str) -> None:
    assert jobs, source
    for j in jobs:
        assert set(j) == JOB_KEYS
        assert j["source"] == source
        assert j["title"] and j["url"].startswith("http")
        assert "<" not in j["description"] or "<" not in j["description"].split("\n")[0]
        assert isinstance(j["extra"], dict)


class FakeHttp:
    """Routes URL substrings to canned bodies and records every call."""

    def __init__(self, routes: dict[str, object], status: int = 200) -> None:
        self.routes, self.status, self.calls = routes, status, []

    def __call__(self, url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        self.calls.append({"url": url, "method": method, "data": data})
        for needle, body in self.routes.items():
            if needle in url:
                return self.status, body
        return 404, None


def _stats() -> dict:
    return {"tried": 0, "ok": 0, "jobs": 0}


# ----------------------------------------------------------------------- parsers
def test_parse_jobicy():
    jobs = kf.parse_jobicy(_j("jobicy.json"))
    _check(jobs, "feed:jobicy")
    assert jobs[0]["remote"] is True and jobs[0]["posted"] == "2026-10-13"
    assert jobs[0]["company"] == "Findev" and "Cyprus" in jobs[0]["location"]


def test_parse_workingnomads():
    jobs = kf.parse_workingnomads(_j("workingnomads.json"))
    _check(jobs, "feed:workingnomads")
    assert jobs[0]["company"] == "Lemon.io" and jobs[0]["extra"]["tags"][0] == "php"
    assert jobs[0]["posted"] == "2026-10-13"


def test_parse_wwr():
    jobs = kf.parse_wwr((FIX / "wwr.xml").read_text(encoding="utf-8"))
    _check(jobs, "feed:wwr")
    assert len(jobs) == 2
    assert jobs[0]["company"] == "bondex" and jobs[0]["title"].startswith("Fullstack")
    assert jobs[0]["location"] == "Remote - Americas" and jobs[0]["posted"] == "2026-09-29"
    assert "&lt;" not in jobs[0]["description"] and "<p>" not in jobs[0]["description"]


def test_parse_wwr_rejects_entities():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel><item/></channel></rss>'
    assert kf.parse_wwr(evil) == []
    assert kf.parse_wwr("not xml") == []


def test_parse_themuse():
    jobs = kf.parse_themuse(_j("themuse.json"))
    _check(jobs, "feed:themuse")
    assert jobs[0]["company"] == "DMC Sinai-Grace Hospital"
    assert jobs[0]["location"] == "Dearborn, MI" and jobs[0]["remote"] is None
    assert jobs[0]["url"].startswith("https://www.themuse.com/jobs/")


def test_parse_arbeitnow_maps_remote_and_visa():
    jobs = kf.parse_arbeitnow(_j("arbeitnow.json"))
    _check(jobs, "feed:arbeitnow")
    assert jobs[0]["remote"] is False and jobs[0]["visa_flag"] is None
    assert jobs[1]["visa_flag"] is True  # fixture row 2 has visa_sponsorship injected
    assert jobs[0]["location"] == "Amberg"


def test_parse_himalayas():
    jobs = kf.parse_himalayas(_j("himalayas.json"))
    _check(jobs, "feed:himalayas")
    assert jobs[0]["location"] == "South Africa" and jobs[0]["remote"] is True
    assert jobs[0]["company"] == "Pavago" and jobs[0]["posted"].startswith("2026-")


def test_parse_remotive():
    jobs = kf.parse_remotive(_j("remotive.json"))
    _check(jobs, "feed:remotive")
    assert jobs[0]["company"] == "Coalition Technologies"  # trailing space stripped
    assert jobs[0]["location"] == "Worldwide" and jobs[0]["posted"] == "2026-10-11"


def test_parse_remoteok_skips_legal_row():
    raw = _j("remoteok.json")
    assert "legal" in raw[0]
    jobs = kf.parse_remoteok(raw)
    _check(jobs, "feed:remoteok")
    assert len(jobs) == len(raw) - 1
    assert jobs[0]["extra"]["attribution"] == "Source: RemoteOK"


def test_parse_adzuna_and_jooble_shapes():
    az = {"results": [{"title": "Dev", "company": {"display_name": "Acme"},
                       "location": {"display_name": "London, UK"},
                       "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/1",
                       "created": "2026-10-10T08:00:00Z", "description": "<b>Build</b> things"}]}
    j = kf.parse_adzuna(az, "gb")[0]
    assert (j["company"], j["location"], j["posted"], j["board"]) == ("Acme", "London, UK", "2026-10-10", "gb")
    assert j["description"] == "Build things"
    jb = {"jobs": [{"title": "Eng", "company": "Beta", "location": "Berlin",
                    "link": "https://jooble.org/desc/9", "updated": "2026-10-11T00:00:00.0000000",
                    "snippet": "Do &amp; build"}]}
    k = kf.parse_jooble(jb)[0]
    assert (k["company"], k["posted"], k["description"]) == ("Beta", "2026-10-11", "Do & build")


# ------------------------------------------------------------------ HN comments
HN_SAMPLES = [
    # (html, expected company, title contains, remote, visa, url)
    ("Sidekick Labs | Senior SWE, R&amp;D Engineer | REMOTE (US Timezones) | Full-time | $170k<p>"
     "We build AI security. Apply: <a href=\"https:&#x2F;&#x2F;sidekick.example&#x2F;jobs\">jobs</a>",
     "Sidekick Labs", "Senior SWE", True, None, "https://sidekick.example/jobs"),
    ("Shepherd (Series B) | ONSITE | San Francisco, CA &amp; New York City, NY<p>Insurance. "
     "We cannot sponsor visas at this time.",
     "Shepherd (Series B)", "Shepherd", False, False,
     "https://news.ycombinator.com/item?id=222"),
    ("PrairieLearn (Remote US) — Full-Stack Software Engineer — TypeScript &#x2F; Postgres<p>"
     "Visa sponsorship available. <a href=\"https:&#x2F;&#x2F;www.prairielearn.com\">site</a>",
     # 2026-10-20 smoke test: a homepage link is not the posting, so the HN comment is the ad
     "PrairieLearn", "Full-Stack Software Engineer", True, True, "https://news.ycombinator.com/item?id=113"),
]


@pytest.mark.parametrize("n,sample", list(enumerate(HN_SAMPLES, start=111)))
def test_hn_comment_parsing(n, sample):
    html, company, title_part, remote, visa, url = sample
    cid = 222 if "Shepherd" in html else n
    j = kf.parse_hn_comment(html, cid, "2026-10-01T15:02:37.000Z")
    assert j["company"] == company
    assert title_part in j["title"]
    assert j["remote"] is remote
    assert j["visa_flag"] is visa
    assert j["url"] == url
    assert j["posted"] == "2026-10-01" and j["source"] == "feed:hn"
    assert j["extra"]["hn_url"] == f"https://news.ycombinator.com/item?id={cid}"
    assert "&#x2F;" not in j["description"]


def test_hn_remote_in_parens_goes_to_location():
    j = kf.parse_hn_comment(HN_SAMPLES[2][0], 5, "")
    assert "Remote US" in j["location"]


def test_hn_thread_fixture_and_fetch_flow():
    http = FakeHttp({"search_by_date": _j("hn_search.json"), "items/49922569": _j("hn_item.json")})
    st = _stats()
    jobs = kf.fetch_hn(["sidekick"], http, st, days=BIG_DAYS, countries=[])
    assert [j["company"] for j in jobs] == ["Sidekick Labs"]
    assert st["tried"] == 2 and st["ok"] == 2 and st["jobs"] == 1
    allj = kf.fetch_hn([], FakeHttp({"search_by_date": _j("hn_search.json"),
                                     "items/49922569": _j("hn_item.json")}), _stats(),
                       days=BIG_DAYS, countries=[])
    assert len(allj) == 3
    _check(allj, "feed:hn")


def test_hn_days_filter_drops_old():
    http = FakeHttp({"search_by_date": _j("hn_search.json"), "items/49922569": _j("hn_item.json")})
    assert kf.fetch_hn([], http, _stats(), days=7, countries=[]) == []  # fixture comments are dated 2026-10-01


# ------------------------------------------------------------------- fetchers
@pytest.mark.parametrize("fn,route,fixture,source", [
    ("fetch_jobicy", "jobicy.com", "jobicy.json", "feed:jobicy"),
    ("fetch_workingnomads", "workingnomads.com", "workingnomads.json", "feed:workingnomads"),
    ("fetch_themuse", "themuse.com", "themuse.json", "feed:themuse"),
    ("fetch_arbeitnow", "arbeitnow.com", "arbeitnow.json", "feed:arbeitnow"),
    ("fetch_himalayas", "himalayas.app", "himalayas.json", "feed:himalayas"),
    ("fetch_remotive", "remotive.com", "remotive.json", "feed:remotive"),
    ("fetch_remoteok", "remoteok.com", "remoteok.json", "feed:remoteok"),
])
def test_fetchers_on_fixtures(fn, route, fixture, source):
    http = FakeHttp({route: _j(fixture)})
    st = _stats()
    jobs = getattr(kf, fn)([], http, st, days=BIG_DAYS, countries=[])
    _check(jobs, source)
    assert st["jobs"] == len(jobs) and st["ok"] == st["tried"] >= 1
    assert st["tried"] <= kf.MAX_PAGES * 5  # page cap respected


def test_wwr_fetcher_four_categories_and_dedupe():
    xml = (FIX / "wwr.xml").read_text(encoding="utf-8")
    http = FakeHttp({"weworkremotely.com": xml})
    st = _stats()
    jobs = kf.fetch_wwr([], http, st, days=BIG_DAYS, countries=[])
    assert st["tried"] == 4 and len(jobs) == 2  # same items in every category -> deduped by url
    assert all(c["url"].endswith(".rss") for c in http.calls)


def test_query_keyword_match_is_local_and_not_ranking():
    http = FakeHttp({"workingnomads.com": _j("workingnomads.json")})
    assert kf.fetch_workingnomads(["zzzz-nomatch"], http, _stats(), days=BIG_DAYS, countries=[]) == []
    hit = kf.fetch_workingnomads(["php laravel"], http, _stats(), days=BIG_DAYS, countries=[])
    assert [j["company"] for j in hit] == ["Lemon.io"]


def test_pages_are_capped():
    page = {"data": [{"title": "T", "company_name": "C", "url": "https://x/1", "description": "d",
                      "remote": True, "location": "L", "created_at": 1791916248}]}
    http = FakeHttp({"arbeitnow.com": page})
    kf.fetch_arbeitnow([], http, _stats(), days=BIG_DAYS, countries=[])
    assert len(http.calls) == kf.MAX_PAGES


def test_failures_return_empty_never_raise():
    st = _stats()
    assert kf.fetch_jobicy(["x"], FakeHttp({}, status=0), st, days=7, countries=[]) == []
    assert st["tried"] >= 1 and st["ok"] == 0 and st["jobs"] == 0

    def boom(*a, **k):
        raise RuntimeError("net down")

    assert kf.fetch_remoteok([], boom, _stats(), days=7, countries=[]) == []
    assert kf.fetch_hn([], lambda *a, **k: (200, "garbage"), _stats(), days=7, countries=[]) == []


# ------------------------------------------------------------------ keyed feeds
def test_missing_env_keys_return_empty_without_requests(monkeypatch):
    for v in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_KEY"):
        monkeypatch.delenv(v, raising=False)
    http = FakeHttp({})
    st = _stats()
    assert kf.fetch_adzuna(["python"], http, st, days=7, countries=["GB"]) == []
    assert kf.fetch_jooble(["python"], http, st, days=7, countries=["GB"]) == []
    assert http.calls == [] and st["tried"] == 0
    monkeypatch.setenv("ADZUNA_APP_ID", "id")  # key half-set is still "missing"
    assert kf.fetch_adzuna(["python"], http, st, days=7, countries=["GB"]) == []
    assert http.calls == []


def test_adzuna_with_keys_only_supported_countries(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "AID")
    monkeypatch.setenv("ADZUNA_APP_KEY", "AKEY")
    body = {"results": [{"title": "Dev", "company": {"display_name": "Acme"},
                         "location": {"display_name": "London"},
                         "redirect_url": "https://www.adzuna.co.uk/x", "created": "2026-10-10T00:00:00Z",
                         "description": "d"}]}
    http = FakeHttp({"/jobs/gb/": body})
    jobs = kf.fetch_adzuna(["python"], http, _stats(), days=BIG_DAYS, countries=["GB", "JP"])
    assert jobs and jobs[0]["board"] == "gb"
    assert all("/jobs/jp/" not in c["url"] for c in http.calls)
    assert "what=python" in http.calls[0]["url"] and "max_days_old=" in http.calls[0]["url"]
    assert len(http.calls) <= kf.MAX_PAGES
    assert kf.fetch_adzuna(["python"], FakeHttp({}), _stats(), days=7, countries=[]) == []


def test_jooble_with_key_posts_json(monkeypatch):
    monkeypatch.setenv("JOOBLE_KEY", "KEY123")
    body = {"jobs": [{"title": "Eng", "company": "Beta", "location": "Berlin",
                      "link": "https://jooble.org/desc/9", "updated": "2026-10-11T00:00:00",
                      "snippet": "s"}]}
    http = FakeHttp({"jooble.org/api/KEY123": body})
    jobs = kf.fetch_jooble(["python dev"], http, _stats(), days=BIG_DAYS, countries=["DE"])
    assert jobs and jobs[0]["source"] == "feed:jooble"
    c = http.calls[0]
    assert c["method"] == "POST"
    sent = json.loads(c["data"])
    assert sent["keywords"] == "python dev" and sent["location"] == "Germany"


def test_feeds_registry_matches_contract():
    assert set(kf.FEEDS) == {"hn", "jobicy", "workingnomads", "wwr", "themuse", "arbeitnow",
                             "himalayas", "remotive", "remoteok", "adzuna", "jooble", "githublists",
                             "arbeitsagentur", "jobtech", "mycareersfuture", "jobbank"}
    assert all(callable(f) for f in kf.FEEDS.values())


# ---- GitHub job lists (SimplifyJobs listings.json), 2026-10-17
_GH_ROWS = [
    {"company_name": "Starling Bank", "title": "Machine Learning Engineer", "active": True, "is_visible": True,
     "url": "https://apply.workable.com/starling-bank/j/0A13F0BEE0/apply", "locations": ["London, UK"],
     "date_posted": 1792229415, "sponsorship": "Other", "category": "AI/ML"},
    {"company_name": "Old Co", "title": "Machine Learning Engineer", "active": False, "url": "https://x.example/1",
     "locations": ["NYC"], "date_posted": 1792229415},
    {"company_name": "Hidden Co", "title": "Machine Learning Engineer", "active": True, "is_visible": False,
     "url": "https://x.example/2", "locations": ["Remote in USA"], "date_posted": 1792229415},
    "not a row",
]


def test_githublists_keeps_only_active_visible_rows():
    jobs = kf.parse_githublists(_GH_ROWS, kf.GITHUB_LISTS[0])
    assert [j["company"] for j in jobs] == ["Starling Bank"]
    j = jobs[0]
    assert j["location"] == "London, UK" and j["posted"] and j["source"] == "feed:githublists"
    assert j["description"] == "" and j["extra"]["title_only"] is True
    assert j["extra"]["sponsorship"] == "Other" and j["extra"]["list"] == "New-Grad-Positions"
    assert kf.parse_githublists({"not": "a list"}) == []


def test_githublists_reads_both_lists_and_matches_titles():
    seen: list[str] = []

    def http(url, **kw):
        seen.append(url)
        return 200, _GH_ROWS

    stats: dict = {}
    jobs = kf.fetch_githublists(["machine learning engineer"], http, stats, days=36500)
    assert seen == list(kf.GITHUB_LISTS)
    assert len(jobs) == 2 and stats["jobs"] == 2  # one active row from each list
    assert kf.fetch_githublists(["chef"], http, {}, days=36500) == []


# ---- national job services (shapes follow career-ops' providers, MIT), 2026-10-17
def _rec_http(routes):
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        calls.append((url, method, data, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, None

    http.calls = calls
    return http


_BA = {"ergebnisliste": [
    {"referenznummer": "10001-1000123456-S", "stellenangebotsTitel": "Machine Learning Engineer (m/w/d)",
     "firma": "Acme GmbH", "stellenlokationen": [{"adresse": {"ort": "Berlin", "region": "BERLIN"}}]},
    {"stellenangebotsTitel": "no ref"},
]}


def test_arbeitsagentur_runs_only_for_germany_and_sends_the_public_client_id():
    http = _rec_http({"rest.arbeitsagentur.de": (200, _BA)})
    assert kf.fetch_arbeitsagentur(["machine learning engineer"], http, {}, days=30, countries=["GB"]) == []
    assert http.calls == []
    jobs = kf.fetch_arbeitsagentur(["machine learning engineer"], http, {}, days=30, countries=["DE", "GB"])
    assert [j["company"] for j in jobs] == ["Acme GmbH"]
    assert jobs[0]["url"] == "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1000123456-S"
    assert jobs[0]["location"] == "Berlin, Germany"
    url, _m, _d, hdrs = http.calls[0]
    assert "pc/v6/jobs?" in url and "was=machine+learning+engineer" in url and "angebotsart=1" in url
    assert hdrs.get("X-API-Key") == kf.ARBEITSAGENTUR_KEY


def test_jobtech_parses_hits_for_sweden_only():
    body = {"hits": [{"id": 29876543, "headline": "Machine Learning Engineer", "employer": {"name": "Spotify"},
                      "description": {"text": "Build ML."}, "publication_date": "2026-10-10T08:00:00",
                      "workplace_address": {"municipality": "Stockholm", "country": "Sverige"}}, {"headline": "no id"}]}
    http = _rec_http({"jobtechdev.se": (200, body)})
    assert kf.fetch_jobtech(["machine learning engineer"], http, {}, days=36500, countries=["NO"]) == []
    jobs = kf.fetch_jobtech(["machine learning engineer"], http, {}, days=36500, countries=["SE"])
    assert len(jobs) == 1 and jobs[0]["url"] == "https://arbetsformedlingen.se/platsbanken/annonser/29876543"
    assert jobs[0]["description"] == "Build ML." and jobs[0]["posted"] == "2026-10-10"
    assert "offset=0" in http.calls[0][0] and "limit=100" in http.calls[0][0]


def test_mycareersfuture_posts_and_pages_by_query_string():
    body = {"results": [{"title": "Machine Learning Engineer", "description": "<p>Build</p>",
                         "metadata": {"jobPostId": "abc", "jobDetailsUrl": "https://www.mycareersfuture.gov.sg/job/x-abc",
                                      "newPostingDate": "2026-10-12"},
                         "hiringCompany": {"name": "GovTech"}, "postedCompany": {"name": "Agency"},
                         "address": {"districts": [{"location": "Central"}]}}]}
    http = _rec_http({"mycareersfuture": (200, body)})
    assert kf.fetch_mycareersfuture(["machine learning engineer"], http, {}, days=36500, countries=["GB"]) == []
    assert http.calls == []
    jobs = kf.fetch_mycareersfuture(["machine learning engineer"], http, {}, days=36500, countries=["SG"])
    assert [j["company"] for j in jobs] == ["GovTech"] and jobs[0]["location"] == "Central, Singapore"
    url, method, data, _h = http.calls[0]
    assert method == "POST" and url.endswith("?limit=100&page=0")
    assert json.loads(data) == {"search": "machine learning engineer", "sortBy": ["new_posting_date"], "page": 0}


_JOBBANK_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Job Bank</title>
<entry><title>Machine learning engineer</title>
<link rel="alternate" type="text/html" href="https://www.jobbank.gc.ca/jobsearch/jobposting/45678901"/>
<updated>2026-10-11T10:00:00-04:00</updated>
<summary type="html">&lt;strong&gt;Employer:&lt;/strong&gt; Maple AI Inc.&lt;br&gt;&lt;strong&gt;Location:&lt;/strong&gt; Toronto (ON)&lt;br&gt;</summary>
</entry></feed>"""


def test_jobbank_parses_atom_for_canada_only():
    http = _rec_http({"jobbank.gc.ca": (200, _JOBBANK_ATOM)})
    assert kf.fetch_jobbank(["machine learning engineer"], http, {}, days=36500, countries=["US"]) == []
    jobs = kf.fetch_jobbank(["machine learning engineer"], http, {}, days=36500, countries=["CA"])
    assert len(jobs) == 1
    j = jobs[0]
    assert j["company"] == "Maple AI Inc." and j["location"] == "Toronto (ON), Canada"
    assert j["url"] == "https://www.jobbank.gc.ca/jobsearch/jobposting/45678901" and j["posted"] == "2026-10-11"
    assert kf.parse_jobbank("<!DOCTYPE x [<!ENTITY a 'b'>]><feed/>") == []  # entity feeds refused


def test_national_services_keep_local_language_titles_and_no_age_limit():
    """The services match the query themselves (in their own language): no English re-filter,
    and days<=0 sends no age window (review 2026-10-17)."""
    body = {"ergebnisliste": [{"referenznummer": "r1", "stellenangebotsTitel": "Softwareentwickler (m/w/d)",
                               "firma": "Acme GmbH"}]}
    http = _rec_http({"rest.arbeitsagentur.de": (200, body)})
    jobs = kf.fetch_arbeitsagentur(["software engineer"], http, {}, days=0, countries=["DE"])
    assert [j["title"] for j in jobs] == ["Softwareentwickler (m/w/d)"]
    assert "veroeffentlichtseit" not in http.calls[0][0]
    http2 = _rec_http({"rest.arbeitsagentur.de": (200, body)})
    kf.fetch_arbeitsagentur(["software engineer"], http2, {}, days=7, countries=["DE"])
    assert "veroeffentlichtseit=7" in http2.calls[0][0]


def test_himalayas_uses_its_search_api_per_query():
    page = _j("himalayas.json")
    http = FakeHttp({"himalayas.app": page})
    kf.fetch_himalayas(["AI Engineer"], http, _stats(), days=BIG_DAYS, countries=[])
    assert http.calls and all("/jobs/api/search?" in c["url"] and "q=AI+Engineer" in c["url"] for c in http.calls)
    assert "offset=" not in http.calls[0]["url"]  # offset paging is deprecated by Himalayas (2026-08-21)


def test_national_services_run_everywhere_for_visa_onsite():
    # the core adds "*" when on-site jobs abroad count; each service then runs, and the core
    # keeps only the ads that say they sponsor a visa
    http = FakeHttp({"": {}})
    kf.fetch_jobtech(["engineer"], http, _stats(), days=30, countries=["IN"])
    assert http.calls == []
    kf.fetch_jobtech(["engineer"], http, _stats(), days=30, countries=["IN", "*"])
    assert http.calls and "jobtechdev.se" in http.calls[0]["url"]



def test_hn_keeps_a_job_link_and_files_a_homepage_under_company_site():
    # Owner's PC smoke test 2026-10-20: Mastra linked its GitHub repo, Metcor its homepage.
    raw = ('Mastra | AI Engineer | Remote <a href="https:&#x2F;&#x2F;github.com&#x2F;mastra-ai">repo</a> '
           'apply <a href="https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;mastra&#x2F;123">here</a>')
    job = kf.parse_hn_comment(raw, 7)
    assert job is not None and job["url"] == "https://jobs.ashbyhq.com/mastra/123" and "company_site" not in job["extra"]
    home = kf.parse_hn_comment('Metcor | Data Engineer | London <a href="https:&#x2F;&#x2F;metcor.co.uk">site</a>', 8)
    assert home is not None and home["url"] == "https://news.ycombinator.com/item?id=8"
    assert home["extra"]["company_site"] == "https://metcor.co.uk"


# --------------------------------------------------------------------------- regex scaling (hostile markup)
def _grows_linearly(fn, make, n=6000):
    """Time fn(make(n)) and fn(make(4n)): linear stays within 8x (+50 ms of noise room), quadratic takes ~16x."""
    import time

    def run(k):
        s, best = make(k), 9e9
        for _ in range(2):
            t0 = time.perf_counter()
            fn(s)
            best = min(best, time.perf_counter() - t0)
        return best

    t1, t4 = run(n), run(4 * n)
    assert t4 < 8 * t1 + 0.05, (t1, t4)


def test_feed_html_scans_are_linear_on_unclosed_markup():
    assert kf.strip_html("<p>Hello</p><script>x</script><ul><li>a</li><li>b</li></ul><b>c</b>") == "Hello\n\n- a\n- b\nc"
    for evil in ("<script>", "<style>", "<p ", "<li", "<a ", "<"):
        _grows_linearly(kf.strip_html, lambda k, e=evil: e * k)
    assert kf._respace("a ,  b ,c", ",", ", ") == "a, b, c" and kf._respace(" x ", ",", ", ") == " x "
    _grows_linearly(lambda s: kf._respace(s, ",", ", "), lambda k: " " * (4 * k) + "x")
    _grows_linearly(lambda s: kf.parse_hn_comment(s, 1), lambda k: "Acme (" + "(" * (4 * k))
