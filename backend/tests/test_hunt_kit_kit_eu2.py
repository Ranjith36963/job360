"""Offline tests for hunt_kit/kit_eu2.py (StepStone Germany, jobs.ch / jobup.ch, itjobs.pt, Magnet.me).

The fixtures under tests/fixtures/hunt_kit/eu2/ are trimmed copies of the real answers fetched on 2026-11-13. Nothing touches
the network. Make it in Germany (Radware bot page) and gradireland.com (search POST body unknown) are not read.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlsplit

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
FIX = Path(__file__).resolve().parent / "fixtures" / "hunt_kit" / "eu2"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kb = _load("kit_eu2_under_test", "kit_eu2.py")
hk = _load("hunt_kit_eu2_under_test", "hunt_kit.py")
F = kb.ATS_FETCHERS
NAMES = ("stepstone", "swissjobs", "itjobspt", "magnetme")
ALLOWED_KW = {"method", "data", "headers", "as_json", "once", "max_bytes"}


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _stub(routes: dict):
    calls: list = []
    kws: list = []

    def http(url, **kw):
        calls.append(url)
        kws.append(kw)
        for frag, resp in routes.items():
            if frag in url:
                return resp(url) if callable(resp) else resp
        return 404, None

    http.calls = calls
    http.kws = kws
    return http


def _run(name, token, http, **extra):
    stats: dict = {}
    jobs = F[name]({"name": name.title() + " Co", "ats": name, "token": token, **extra}, http, stats)
    assert all(set(k) <= ALLOWED_KW for k in http.kws)
    assert all(k.get("once") for k in http.kws)  # a refusal is never repeated
    return jobs, stats


def _hosts(items):
    return {urlsplit(u).hostname for u in items}


def test_wiring_and_host_rules():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_eu2" in hk.ATS_MODULES and all(a in hk._merged("ATS_FETCHERS", hk.ATS_MODULES) for a in NAMES)
    assert '"kit_eu2.py"' in (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert {r["ats"] for r in hk.load_companies(KIT_DIR / "companies.json")} >= set(NAMES)
    for url, ats in (("https://www.stepstone.de/stellenangebote--X-1-inline.html", "stepstone"),
                     ("https://www.jobs.ch/en/vacancies/detail/abc/", "swissjobs"),
                     ("https://www.jobup.ch/en/jobs/detail/abc/", "swissjobs"),
                     ("https://www.itjobs.pt/oferta/1/x", "itjobspt"),
                     ("https://magnet.me/en/opportunity/1/x", "magnetme")):
        r = hk.board_from_job_url(url)
        assert (r["kind"], r["ats"], r["token"]) == ("ats", ats, "")
    assert hk.board_from_job_url("https://www.stepstone.at/jobs/x")["kind"] == "aggregator"  # only the German site is read


def test_stepstone_cards_one_request_and_own_host():
    http = _stub({"stepstone.de/jobs/machine-learning-engineer": (200, _fx("stepstone.html"))})
    jobs, _ = _run("stepstone", "machine-learning-engineer", http)
    assert http.calls == ["https://www.stepstone.de/jobs/machine-learning-engineer"]  # robots.txt disallows ?-queries: never paged
    assert len(jobs) == 3 and _hosts(j["url"] for j in jobs) == {"www.stepstone.de"}
    assert (jobs[0]["company"], jobs[0]["location"]) == ("Lease a Bike Deutschland GmbH", "Cloppenburg")
    assert jobs[0]["extra"]["work_mode"] == "Teilweise Home-Office" and "?" not in jobs[0]["url"]


def test_stepstone_bad_token_refusal_and_changed_page():
    for tok in ("../x", "a/b", "Machine Learning", ""):
        http = _stub({})
        assert _run("stepstone", tok, http)[0] == [] and http.calls == []
    jobs, stats = _run("stepstone", "ai-engineer", _stub({"stepstone.de": (403, None)}))
    assert jobs == [] and not stats.get("ok")
    jobs, stats = _run("stepstone", "ai-engineer", _stub({"stepstone.de": (200, "<html>Access denied</html>")}))
    assert jobs == [] and not stats.get("ok")
    swapped = _fx("stepstone.html").replace("https://www.stepstone.de/stellenangebote--Senior-Machine", "https://evil.example/stellenangebote--Senior-Machine")
    assert len(_run("stepstone", "ai-engineer", _stub({"stepstone.de": (200, swapped)}))[0]) == 1  # two of the three links left the site


def test_swissjobs_both_sites_paging_and_refusal():
    http = _stub({"jobs.ch/en/vacancies/": (200, _fx("jobsch.html"))})
    jobs, _ = _run("swissjobs", "jobs.ch/machine-learning", http)
    assert len(jobs) == 3 and _hosts(j["url"] for j in jobs) == {"www.jobs.ch"}
    assert http.calls[0] == "https://www.jobs.ch/en/vacancies/?term=machine%20learning" and http.calls[1].endswith("&page=2")
    assert jobs[0]["company"] and jobs[0]["posted"] and jobs[0]["extra"]["employment_type"]
    http = _stub({"jobup.ch/en/jobs/": (200, _fx("jobup.html"))})
    jobs, _ = _run("swissjobs", "jobup.ch/machine-learning", http)
    assert len(jobs) == 3 and _hosts(j["url"] for j in jobs) == {"www.jobup.ch"}
    http = _stub({"page=2": (403, None), "jobs.ch/en/vacancies/": (200, _fx("jobsch.html"))})
    assert len(_run("swissjobs", "jobs.ch/ai", http)[0]) == 3 and len(http.calls) == 2  # the refusal ends the board, earlier pages stay
    assert _run("swissjobs", "jobs.ch/ai", _stub({"jobs.ch": (403, None)}))[0] == []


def test_swissjobs_bad_tokens_and_foreign_links():
    for tok in ("evil.example/ai", "jobs.ch", "jobs.ch/a b", "jobs.ch/../x", "jobs.ch/"):
        http = _stub({})
        assert _run("swissjobs", tok, http)[0] == [] and http.calls == []
    swapped = _fx("jobsch.html").replace("https://www.jobs.ch/en/vacancies/detail/", "https://evil.example/en/vacancies/detail/", 1)
    assert len(_run("swissjobs", "jobs.ch/ai", _stub({"jobs.ch/en/vacancies/": (200, swapped), "page=2": (404, None)}))[0]) == 2


def test_itjobspt_cards_modes_and_paging():
    http = _stub({"itjobs.pt/emprego": (200, _fx("itjobs.html"))})
    jobs, _ = _run("itjobspt", "all", http)
    assert len(jobs) == 3 and _hosts(j["url"] for j in jobs) == {"www.itjobs.pt"} and http.calls[1].endswith("?page=2")
    first = jobs[0]
    assert (first["company"], first["location"], first["extra"]["work_mode"]) == ("bOut Consulting", "Setúbal", "Presencial")
    http = _stub({"page=2": (429, None), "itjobs.pt/emprego": (200, _fx("itjobs.html"))})
    assert len(_run("itjobspt", "all", http)[0]) == 3 and len(http.calls) == 2
    assert _run("itjobspt", "all", _stub({"itjobs.pt": (200, "<html>nothing</html>")}))[0] == []


def test_itjobspt_remote_flag():
    page = _fx("itjobs.html").replace("Presencial", "Remoto", 1)
    jobs, _ = _run("itjobspt", "all", _stub({"itjobs.pt/emprego": (200, page), "page=2": (404, None)}))
    assert jobs[0]["remote"] is True and jobs[0]["location"] == "Setúbal"


def test_magnetme_newest_first_detail_cap_and_title_test():
    page = _fx("magnet_detail.html")
    http = _stub({"en-fresh.xml": (200, _fx("magnet_sitemap.xml")), "/opportunity/1018545/": (200, page)})
    jobs, _ = _run("magnetme", "all", http)
    assert [urlsplit(u).path.split("/")[3] for u in http.calls[1:]] == ["1029157", "1018545", "998069", "994767"]  # highest id first
    assert len(jobs) == 1 and (jobs[0]["company"], jobs[0]["location"], jobs[0]["posted"]) == ("KPMG", "Amstelveen, NL", "2026-11-12")
    assert jobs[0]["extra"]["employment_type"] == "FULL_TIME" and _hosts(http.calls) == {"magnet.me"}
    assert http.kws[0]["headers"] == {"Accept": "application/xml, text/xml, application/rss+xml"}  # the sitemap answers 406 to text/html
    http = _stub({"en-fresh.xml": (200, _fx("magnet_sitemap.xml")), "/opportunity/1018545/": (200, page)})
    only = {"title_ok": lambda t: "consultant" in t}
    assert len(_run("magnetme", "all", http, **only)[0]) == 1 and len(http.calls) == 2  # only one slug passes the title test


def test_magnetme_cap_and_first_refusal_and_sitemap_failure():
    locs = "".join(f"<url><loc>https://magnet.me/en/opportunity/{i}/job-{i}</loc></url>" for i in range(2000, 2040))
    sitemap = f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'
    http = _stub({"en-fresh.xml": (200, sitemap), "/opportunity/": (404, None)})
    _run("magnetme", "all", http)
    assert len(http.calls) == 1 + kb.MAGNET_DETAIL_CAP
    http = _stub({"en-fresh.xml": (200, sitemap), "/opportunity/": (429, None)})
    _run("magnetme", "all", http)
    assert len(http.calls) == 2  # the first refusal ends the posting pages
    jobs, stats = _run("magnetme", "all", _stub({"en-fresh.xml": (406, None)}))
    assert jobs == [] and not stats.get("ok")
