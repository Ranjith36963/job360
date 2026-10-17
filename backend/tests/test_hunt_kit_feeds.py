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

    def __call__(self, url, *, method="GET", data=None, headers=None, as_json=True):
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
     "PrairieLearn", "Full-Stack Software Engineer", True, True, "https://www.prairielearn.com"),
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
                             "himalayas", "remotive", "remoteok", "adzuna", "jooble", "githublists"}
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
