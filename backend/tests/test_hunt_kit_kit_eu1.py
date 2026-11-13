"""Offline tests for hunt_kit/kit_eu1.py (Relocate.me, SwissDevJobs / GermanTechJobs, IamExpat Jobs, ictjob, Berlin Startup Jobs).

The fixtures under tests/fixtures/hunt_kit/eu1/ are trimmed copies of the real answers fetched on 2026-11-13. Nothing touches
the network. DEVjobs.at (Cloudflare challenge) is not read.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlsplit

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
FIX = Path(__file__).resolve().parent / "fixtures" / "hunt_kit" / "eu1"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kb = _load("kit_eu1_under_test", "kit_eu1.py")
hk = _load("hunt_kit_eu1_under_test", "hunt_kit.py")
F = kb.ATS_FETCHERS
NAMES = ("relocateme", "techjobs", "iamexpat", "ictjob", "berlinstartup")
ALLOWED_KW = {"method", "data", "headers", "as_json", "once", "max_bytes"}


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets the url."""
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


def _run(name, token, http):
    stats: dict = {}
    jobs = F[name]({"name": name.title() + " Co", "ats": name, "token": token}, http, stats)
    assert all(set(k) <= ALLOWED_KW for k in http.kws)
    assert all(k.get("once") for k in http.kws)  # a refusal is never repeated
    return jobs, stats


def _hosts(items):
    return {urlsplit(u).hostname for u in items}


# ----------------------------------------------------------------------------- wiring and host rules
def test_wiring():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_eu1" in hk.ATS_MODULES and all(a in hk._merged("ATS_FETCHERS", hk.ATS_MODULES) for a in NAMES)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_eu1.py"' in served
    assert {r["ats"] for r in hk.load_companies(KIT_DIR / "companies.json")} >= set(NAMES)


def test_host_rules_and_devjobs_stays_unread():
    for url, ats in (("https://relocate.me/japan/tokyo/hennge/x-1", "relocateme"),
                     ("https://swissdevjobs.ch/jobs/Acme-Dev", "techjobs"),
                     ("https://germantechjobs.de/jobs/Acme-Dev", "techjobs"),
                     ("https://www.iamexpat.nl/career/jobs-netherlands/a/b/c", "iamexpat"),
                     ("https://www.ictjob.be/en/job/x/1-2", "ictjob"),
                     ("https://www.ictjob.lu/en/job/x/1-2", "ictjob"),
                     ("https://berlinstartupjobs.com/engineering/x/", "berlinstartup")):
        r = hk.board_from_job_url(url)
        assert (r["kind"], r["ats"], r["token"]) == ("ats", ats, "")
    assert hk.board_from_job_url("https://evil.example/relocate.me/x")["kind"] == "other"
    assert hk.board_from_job_url("https://devjobs.at/jobs/x")["ats"] != "techjobs"


# ----------------------------------------------------------------------------- Relocate.me
def test_relocate_cards_fields_and_own_host():
    http = _stub({"relocate.me/international-jobs": (200, _fx("relocate.html"))})
    jobs, stats = _run("relocateme", "all", http)
    assert len(jobs) == 3 and stats["ok"] == 1
    assert {(j["company"], j["location"]) for j in jobs} >= {("HENNGE", "Tokyo, Japan"), ("PayPay", "Tokyo, Japan")}
    assert _hosts(j["url"] for j in jobs) == {"relocate.me"} and all(j["extra"]["relocation"] for j in jobs)
    assert http.calls[1].endswith("?page=2")  # a page of 3 still asks for the next, and the empty answer (404) ends it


def test_relocate_refusal_keeps_earlier_pages_and_first_page_refusal_fails():
    page = _fx("relocate.html")
    http = _stub({"page=2": (403, None), "relocate.me/international-jobs": (200, page)})
    jobs, _ = _run("relocateme", "all", http)
    assert len(jobs) == 3 and len(http.calls) == 2
    jobs, stats = _run("relocateme", "all", _stub({"relocate.me": (403, None)}))
    assert jobs == [] and not stats.get("ok")


def test_relocate_foreign_link_is_dropped():
    bad = _fx("relocate.html").replace('href="/japan/tokyo/hennge/', 'href="https://evil.example/japan/tokyo/hennge/')
    jobs, _ = _run("relocateme", "all", _stub({"relocate.me/international-jobs": (200, bad)}))
    assert len(jobs) == 2 and _hosts(j["url"] for j in jobs) == {"relocate.me"}


# ----------------------------------------------------------------------------- SwissDevJobs / GermanTechJobs
def test_techjobs_listing_and_detail_for_both_hosts():
    http = _stub({"/jobs/Machine-Learning/all": (200, _fx("sdj_list.html")),
                  "/jobs/Scalera-AG-Senior-ALML-Engineer": (200, _fx("sdj_detail.html"))})
    jobs, _ = _run("techjobs", "swissdevjobs.ch/Machine-Learning", http)
    by = {j["url"].rsplit("/", 1)[1]: j for j in jobs}
    d = by["Scalera-AG-Senior-ALML-Engineer"]
    assert (d["company"], d["posted"], d["extra"]["salary"]["currency"]) == ("Scalera AG", "2026-11-03", "CHF")
    assert by["OnlineDoctor-AG-AI-Enablement--Automation-Engineer-80-100-fmd"]["location"] == "St. Gallen"  # card only: its page was a 404
    assert _hosts(j["url"] for j in jobs) == {"swissdevjobs.ch"} and _hosts(http.calls) == {"swissdevjobs.ch"}
    http = _stub({"/jobs/Machine-Learning/all": (200, _fx("gtj_list.html")), "/jobs/Bellmer": (200, _fx("gtj_detail.html"))})
    jobs, _ = _run("techjobs", "germantechjobs.de/Machine-Learning", http)
    assert jobs and _hosts(j["url"] for j in jobs) == {"germantechjobs.de"}


def test_techjobs_detail_cap_title_test_and_first_refusal():
    cards = "".join(f'<a title="Role {i} job in Bern" href="/jobs/Acme-Role-{i:03d}">x</a>' for i in range(40))
    page = f"<html><body>{cards}</body></html>"
    http = _stub({"/all": (200, page), "/jobs/Acme": (404, None)})
    jobs, _ = _run("techjobs", "swissdevjobs.ch/Data", http)
    assert len(jobs) == 40 and len(http.calls) == 1 + kb.TECHJOBS_DETAIL_CAP <= 26
    http = _stub({"/all": (200, page), "/jobs/Acme": (429, None)})
    jobs, _ = _run("techjobs", "swissdevjobs.ch/Data", http)
    assert len(jobs) == 40 and len(http.calls) == 2  # the first refusal ends the detail pages
    http = _stub({"/all": (200, page)})
    stats: dict = {}
    only7 = {"name": "x", "token": "swissdevjobs.ch/Data", "title_ok": lambda t: t.endswith("7")}
    assert len(F["techjobs"](only7, http, stats)) == 40 and len(http.calls) == 1 + 4  # Role 7, 17, 27, 37


def test_techjobs_bad_tokens_and_changed_page_make_no_trouble():
    for tok in ("evil.example/Data", "swissdevjobs.ch", "swissdevjobs.ch/all", "swissdevjobs.ch/a/b", "swissdevjobs.ch/../x"):
        http = _stub({})
        jobs, stats = _run("techjobs", tok, http)
        assert jobs == [] and http.calls == [] and not stats.get("ok")
    jobs, stats = _run("techjobs", "swissdevjobs.ch/Data", _stub({"/all": (200, "<html>shell</html>")}))
    assert jobs == [] and not stats.get("ok")


# ----------------------------------------------------------------------------- IamExpat Jobs
def test_iamexpat_cards():
    http = _stub({"iamexpat.nl/career/jobs-netherlands": (200, _fx("iamexpat.html"))})
    jobs, _ = _run("iamexpat", "all", http)
    assert len(jobs) == 3 and _hosts(j["url"] for j in jobs) == {"www.iamexpat.nl"}
    j = jobs[0]
    assert (j["title"], j["location"], j["posted"], j["company"]) == ("Solutions Engineer", "Utrecht", "2026-10-29", "Zonos")
    assert all(k["max_bytes"] >= 5_000_000 for k in http.kws)  # a page is ~2.7 MB, over the 2 MB default
    assert len(http.calls) <= kb.IAMEXPAT_PAGES


def test_iamexpat_changed_markup_fails_the_board():
    jobs, stats = _run("iamexpat", "all", _stub({"iamexpat.nl": (200, "<html><body><a href='/x'>y</a></body></html>")}))
    assert jobs == [] and not stats.get("ok")


# ----------------------------------------------------------------------------- ictjob
def test_ictjob_feed_and_search_and_luxembourg():
    http = _stub({"/en/rss": (200, _fx("ictjob_rss.xml"))})
    jobs, _ = _run("ictjob", "be", http)
    assert [(j["title"], j["company"], j["location"], j["posted"]) for j in jobs][0] == ("1st/2nd Line Support Engineer", "Kwery", "Izegem", "2026-11-13")
    assert _hosts(j["url"] for j in jobs) == {"www.ictjob.be"} and http.calls == ["https://www.ictjob.be/en/rss"]
    http = _stub({"search-it-jobs": (200, _fx("ictjob_search.html"))})
    jobs, _ = _run("ictjob", "be/machine-learning", http)
    assert len(jobs) == 3 and "keywords=machine%20learning" in http.calls[0] and jobs[0]["company"] == "Kwery"
    http = _stub({"/en/rss": (200, _fx("ictjob_rss.xml").replace("ictjob.be", "ictjob.lu"))})
    jobs, _ = _run("ictjob", "lu", http)
    assert jobs and _hosts(j["url"] for j in jobs) == {"www.ictjob.lu"}


def test_ictjob_bad_token_and_foreign_link():
    http = _stub({})
    assert _run("ictjob", "fr", http)[0] == [] and http.calls == []
    swapped = _fx("ictjob_rss.xml").replace("https://www.ictjob.be/en/job/windows", "https://evil.example/en/job/windows")
    jobs, _ = _run("ictjob", "be", _stub({"/en/rss": (200, swapped)}))
    assert len(jobs) == 2


# ----------------------------------------------------------------------------- Berlin Startup Jobs
def test_berlinstartup_cards_and_paging():
    http = _stub({"/engineering/": (200, _fx("bsj.html"))})
    jobs, _ = _run("berlinstartup", "all", http)
    assert len(jobs) == 3 and jobs[0]["company"] == "Promethion Industries" and jobs[0]["location"] == "Berlin, Germany"
    assert _hosts(j["url"] for j in jobs) == {"berlinstartupjobs.com"} and http.calls[1].endswith("/engineering/page/2/")
    http = _stub({"/page/2/": (403, None), "/engineering/": (200, _fx("bsj.html"))})
    assert len(_run("berlinstartup", "all", http)[0]) == 3 and len(http.calls) == 2
