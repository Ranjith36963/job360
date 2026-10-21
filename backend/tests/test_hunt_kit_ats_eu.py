"""Offline tests for hunt_kit/kit_ats_eu.py.

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) for each ATS here
(Emply: the job.paychex.eu / molslinjen captures; Factorial: factorial_test.go:81-92; Jobylon: jobylon_test.go:11-47;
Manatal: manatal_test.go:35-44; PeopleForce: peopleforce_test.go:23-109; TalentAdore: talentadore_test.go:19-47;
TalentHR: talenthr_test.go:13-35; TalentLyft: talentlyft_test.go:13-36; Traffit: traffit_test.go:34-52;
Trakstar: trakstar_test.go:18-42). Nothing touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


eu = _load("kit_ats_eu_under_test", "kit_ats_eu.py")
hk = _load("hunt_kit_ats_eu_under_test", "hunt_kit.py")
F = eu.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}
NAMES = ("emply", "factorial", "jobylon", "manatal", "peopleforce", "talentadore", "talenthr", "talentlyft", "traffit", "trakstar")


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, method, body)."""
    calls: list = []
    once_flags: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
        once_flags.append((url, once))
        body = json.loads(data) if data else None
        calls.append((url, method, body, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, method, body) if callable(resp) else resp
        return 404, None

    http.calls = calls
    http.once = once_flags
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    jobs = F[name](row, http, stats)
    return jobs, stats


def _row(ats: str, token: str, **kw) -> dict:
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def test_every_reader_is_registered():
    assert set(F) == set(NAMES)


def test_hunt_kit_wiring():
    assert "kit_ats_eu" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    src = (KIT_DIR / "kit_ats_eu.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire") >= len(F)  # every reader names the reference file and line it follows
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_ats_eu.py"' in served


@pytest.mark.parametrize("url,ats,token", [
    ("https://acme.career.emply.com/ad/engineer/abc123", "emply", "acme"),
    ("https://acme.traffit.com/public/an/list/", "traffit", "acme"),
    ("https://acme.hire.trakstar.com/jobs/xyz", "trakstar", "acme"),
    ("https://www.careers-page.com/acme/job/ABC123", "manatal", "acme"),
    ("https://acme.careers-page.com/jobs/1", "manatal", "acme"),
    ("https://davidjoseph-co.careers-page.com/jobs/1", "manatal", "davidjoseph-co"),
    ("https://careers-page.com/acme/job/ABC123", "manatal", "acme"),
    ("https://acme.peopleforce.io/careers/v/12-engineer", "peopleforce", "acme"),
    ("https://acme.talentlyft.com/jobs/engineer-abc", "talentlyft", "acme"),
    ("https://jobs.talenthr.io/acme/engineer/12", "talenthr", "acme"),
    ("https://muffin.factorial.it/job_posting/engineer-12", "factorial", "muffin.factorial.it"),
    ("https://highbras.factorialhr.com.br/job_posting/engineer-12", "factorial", "highbras.factorialhr.com.br"),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)


# --------------------------------------------------------------------------- Emply
def _emply_vac(short, title, *, talent=False, loc="Østerbro, København", published="2026-08-21T07:00:00Z", trs=None, ext=None, slug=None):
    return {"id": "id-" + short, "shortId": short, "title": title, "titleAsUrl": slug or title.lower().replace(" ", "-"),
            "talentPool": talent, "location": loc, "published": published, "externalCseAdLink": ext,
            "translations": trs if trs is not None else [{"title": title, "content": "<p>Body of " + title + "</p>", "languageKey": "en-GB"}]}


EMPLY_P0 = {"count": 6, "vacancies": [
    _emply_vac("moxy5t", "Unsolicited applications", talent=True, loc=""),
    _emply_vac("hmds58", "Lead Integration Developer", slug="lead-integration-developer"),
    _emply_vac("t8sgwl", "Account Executive", trs=[
        {"title": "Account Manager", "content": "<p>Deine Aufgaben und mehr</p>", "languageKey": "de-DE"},
        {"title": "Account Executive", "content": "<p>Your tasks</p>", "languageKey": "en-GB"}]),
    _emply_vac("tzfpvg", "Nurse", loc=""),
]}
EMPLY_P1 = {"count": 6, "vacancies": [
    _emply_vac("2exc7k", "Soger kok", trs=[{"title": "ØRESUNDSLINJEN søger kok", "content": "<p>Kok</p>", "languageKey": "da-DK"}], slug="oresundslinjen-soger-kok"),
    _emply_vac("ext001", "External role", ext="https://careers.example.com/role/1"),
]}


def _emply_http(pages=None, status=200):
    pages = pages if pages is not None else {0: EMPLY_P0, 4: EMPLY_P1}

    def handler(url, method, body):
        return (status, pages.get(body["offset"], {"count": 6, "vacancies": []})) if status == 200 else (status, None)
    return _stub({"/api/integration/vacancy/get-page": handler})


def test_emply_happy_path_pages_by_offset_and_maps():
    http = _emply_http()
    jobs, stats = _run("emply", _row("emply", "job.paychex.eu", name="Paychex"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 5}
    assert [c[2]["offset"] for c in http.calls] == [0, 4]
    assert all(c[1] == "POST" and c[2]["filters"] == [] and c[2]["count"] == 100 for c in http.calls)
    assert all(c[0] == "https://job.paychex.eu/api/integration/vacancy/get-page" for c in http.calls)
    by = {j["extra"]["short_id"]: j for j in jobs}
    assert "moxy5t" not in by  # the talent-pool entry is not a vacancy
    assert set(by) == {"hmds58", "t8sgwl", "tzfpvg", "2exc7k", "ext001"}
    lead = by["hmds58"]
    assert set(lead) == JOB_KEYS and lead["source"] == "emply" and lead["company"] == "Paychex"
    assert lead["url"] == "https://job.paychex.eu/ad/lead-integration-developer/hmds58/en"
    assert lead["posted"] == "2026-08-21" and "Body of Lead" in lead["description"] and lead["location"] == "Østerbro, København"
    assert by["t8sgwl"]["title"] == "Account Executive" and "Your tasks" in by["t8sgwl"]["description"]  # English text wins
    assert by["t8sgwl"]["url"].endswith("/t8sgwl/en")
    assert by["tzfpvg"]["location"] == ""
    assert by["2exc7k"]["url"] == "https://job.paychex.eu/ad/oresundslinjen-soger-kok/2exc7k/da"  # first language when no English
    assert by["ext001"]["url"] == "https://careers.example.com/role/1"  # the vacancy's own external ad link


def test_emply_slug_token_builds_the_career_emply_host_and_bad_tokens_send_nothing():
    http = _emply_http()
    _run("emply", _row("emply", "molslinjen"), http)
    assert http.calls[0][0] == "https://molslinjen.career.emply.com/api/integration/vacancy/get-page"
    for bad in ("", "-x", "a b", "https://x.dk", "x/../y"):
        h = _emply_http()
        jobs, stats = _run("emply", _row("emply", bad), h)
        assert jobs == [] and stats["ok"] == 0 if "ok" in stats else True
        assert h.calls == []


def test_emply_empty_non200_gone_tenant_and_later_page_failure():
    jobs, stats = _run("emply", _row("emply", "x"), _emply_http({0: {"count": 0, "vacancies": []}}))
    assert jobs == [] and stats["ok"] == 1  # an empty board is a successful board
    jobs, stats = _run("emply", _row("emply", "x"), _emply_http(status=500))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("emply", _row("emply", "x"), _stub({"get-page": (0, None)}))  # a gone tenant answers the marketing page (not JSON)
    assert jobs == [] and stats.get("ok", 0) == 0

    def flaky(url, method, body):
        return (200, EMPLY_P0) if body["offset"] == 0 else (503, None)
    jobs, stats = _run("emply", _row("emply", "x"), _stub({"get-page": flaky}))
    assert len(jobs) == 3 and stats["ok"] == 1  # a hiccup after page 0 keeps what we have


# --------------------------------------------------------------------------- Factorial
FACTORIAL_LIST = """<html><body><ul>
<li class='job-offer-item'><a href="/job_posting/backend-engineer-100">Backend Engineer</a></li>
<li class='job-offer-item'><a href="/job_posting/backend-engineer-100">Apply</a></li>
<li class='job-offer-item'><a href="/job_posting/data-analyst-200">Data Analyst</a></li>
<li><a href="/job_posting/no-id-here">No id</a></li>
<li><a href="https://evil.example.com/job_posting/x-300">Off host</a></li>
</ul></body></html>"""


def _factorial_detail(title, salary, location, description):
    return ('<html><body><ul><li class="flex border-b border-gray-50">A tempo indeterminato</li>'
            f'<li class="flex border-b border-gray-50">{salary}</li><li class="flex border-b border-gray-50">{location}</li></ul>'
            f'<h1 class="font-bold">{title}</h1><div class=\'styledText\'>{description}</div></body></html>')


def test_factorial_happy_path():
    http = _stub({"/job_posting/backend-engineer-100": (200, _factorial_detail("Backend Engineer", "€40,000", "Ibrido (Milano, Italia)", "<p>Build things</p><script>x()</script>")),
                  "/job_posting/data-analyst-200": (200, _factorial_detail("Data Analyst", "€30,000", "Remoto (Madrid, España)", "<p>Analyze data</p>")),
                  "https://acme.factorial.it/": (200, FACTORIAL_LIST)})
    jobs, stats = _run("factorial", _row("factorial", "acme.factorial.it", name="Acme"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert [j["extra"]["job_id"] for j in jobs] == ["100", "200"]  # the duplicate link is merged; no-id and off-host links dropped
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Acme" and a["source"] == "factorial"
    assert a["title"] == "Backend Engineer" and a["location"] == "Milano, Italia" and a["extra"]["work_mode"] == "hybrid"
    assert "Build things" in a["description"] and "x()" not in a["description"]
    assert a["url"] == "https://acme.factorial.it/job_posting/backend-engineer-100"
    assert b["remote"] is True and b["extra"]["work_mode"] == "remote"
    assert len(http.calls) == 3


def test_factorial_location_rows():
    f = eu._factorial_location
    assert f(["Clt", "Período integral", "R$1.937,6 Mensal", "RIO DAS OSTRAS, RJ, Brasil", "QUALIDADE"]) == ("RIO DAS OSTRAS, RJ, Brasil", "")
    assert f(["Full time", "TRIVENETO", "S - Partnerships (PAR)"]) == ("", "")
    assert f(["Remoto (Madrid, España)"]) == ("Madrid, España", "remote")


def test_factorial_title_ok_gates_detail_pages_and_cap_and_refusal_stop():
    http = _stub({"data-analyst-200": (200, _factorial_detail("Data Analyst", "€1", "Roma, Italia", "<p>d</p>")),
                  "https://acme.factorial.it/": (200, FACTORIAL_LIST)})
    row = _row("factorial", "acme.factorial.it", title_ok=lambda t: "data" in t.lower())
    jobs, _ = _run("factorial", row, http)
    assert len(http.calls) == 2  # the list plus ONE detail page: the backend card is not a title the user searches
    by = {j["extra"]["job_id"]: j for j in jobs}
    assert by["100"]["description"] == "" and by["100"]["title"] == "Backend Engineer"  # listing title kept, no description
    assert by["200"]["location"] == "Roma, Italia"
    http = _stub({"/job_posting/": (429, None), "https://acme.factorial.it/": (200, FACTORIAL_LIST)})
    jobs, _ = _run("factorial", _row("factorial", "acme.factorial.it"), http)
    assert len(http.calls) == 2 and len(jobs) == 2  # the first refusal ends the detail requests; the cards stay


def test_factorial_empty_non200_and_bad_host():
    jobs, stats = _run("factorial", _row("factorial", "acme.factorial.it"), _stub({"factorial": (200, "<html></html>")}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("factorial", _row("factorial", "acme.factorial.it"), _stub({"factorial": (503, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    h = _stub({})
    for bad in ("evil.example.com", "factorial.it", "https://x.factorial.it", ""):
        assert _run("factorial", _row("factorial", bad), h)[0] == []
    assert h.calls == []


# --------------------------------------------------------------------------- Jobylon
JOBYLON_INDEX = '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://emp.jobylon.com/sitemap-jobs.xml</loc></sitemap></sitemapindex>'
JOBYLON_JOBS = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://emp.jobylon.com/jobs/111-globex-designer/</loc></url>'
                '<url><loc>https://emp.jobylon.com/jobs/369523-acme-engineer/</loc></url>'
                '<url><loc>https://emp.jobylon.com/companies/2451-acme/</loc></url>'
                '<url><loc>https://evil.example.com/jobs/999-x/</loc></url></urlset>')
JOBYLON_ACME = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Engineer &amp; Lead","employmentType":["CONTRACTOR"],
"description":"<p>Build things.</p>","datePosted":"2026-07-17T13:26:14+00:00",
"hiringOrganization":{"@type":"Organization","name":"Acme &amp; Co"},
"jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Kista","addressCountry":"Sweden"}},
{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Oslo","addressCountry":"Norway"}}]}
</script></head><body></body></html>"""
JOBYLON_NOCO = """<html><head><script type="application/ld+json">
{"@type":"JobPosting","title":"Designer","description":"<p>Design.</p>","hiringOrganization":{"name":""},
"jobLocation":{"@type":"Place","address":{"addressLocality":"Berlin"}}}</script></head></html>"""


def test_jobylon_happy_path_company_is_the_employer_and_empty_company_dropped():
    http = _stub({"/jobs/369523-": (200, JOBYLON_ACME), "/jobs/111-": (200, JOBYLON_NOCO),
                  "sitemap-jobs": (200, JOBYLON_JOBS), "sitemap.xml": (200, JOBYLON_INDEX)})
    jobs, stats = _run("jobylon", _row("jobylon", "all", name="Jobylon"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert set(j) == JOB_KEYS and j["source"] == "jobylon"
    assert j["title"] == "Engineer & Lead" and j["company"] == "Acme & Co"  # never the board
    assert j["location"] == "Kista, Sweden; Oslo, Norway" and j["posted"] == "2026-07-17"
    assert j["url"] == "https://emp.jobylon.com/jobs/369523-acme-engineer/" and j["extra"]["job_id"] == "369523"
    urls = [c[0] for c in http.calls]
    assert urls[:2] == ["https://emp.jobylon.com/sitemap.xml", "https://emp.jobylon.com/sitemap-jobs.xml"]
    assert urls[2].endswith("/jobs/369523-acme-engineer/")  # newest id first
    assert {urlsplit(u).hostname for u in urls} == {"emp.jobylon.com"}  # the off-site sitemap entry is never fetched
    assert not any("/companies/" in u for u in urls)


def test_jobylon_title_ok_cap_empty_and_failures():
    http = _stub({"/jobs/369523-": (200, JOBYLON_ACME), "sitemap-jobs": (200, JOBYLON_JOBS), "sitemap.xml": (200, JOBYLON_INDEX)})
    jobs, _ = _run("jobylon", _row("jobylon", "all", title_ok=lambda t: "engineer" in t), http)
    assert len(jobs) == 1 and len(http.calls) == 3  # two sitemaps + the one wanted page
    many = "".join(f"<url><loc>https://emp.jobylon.com/jobs/{i}-role/</loc></url>" for i in range(1, 80))
    http = _stub({"/jobs/": (200, JOBYLON_ACME), "sitemap-jobs": (200, f"<urlset>{many}</urlset>"), "sitemap.xml": (200, JOBYLON_INDEX)})
    _run("jobylon", _row("jobylon", "all"), http)
    assert len(http.calls) == 2 + eu.JOBYLON_DETAIL_CAP
    jobs, stats = _run("jobylon", _row("jobylon", "all"), _stub({"sitemap-jobs": (200, "<urlset></urlset>"), "sitemap.xml": (200, JOBYLON_INDEX)}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("jobylon", _row("jobylon", "all"), _stub({"sitemap.xml": (503, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("jobylon", _row("jobylon", "all"), _stub({"sitemap.xml": (200, '<!DOCTYPE x [<!ENTITY a "b">]><sitemapindex/>')}))
    assert jobs == [] and stats.get("ok", 0) == 0


# --------------------------------------------------------------------------- Manatal
MANATAL_P1 = {"count": 3, "next": "https://open.api.manatal.com/open/v3/career-page/acme/jobs/?page=2", "previous": None, "results": [
    {"id": 1, "hash": "AB12CD34", "organization_name": "Engineering", "position_name": "Backend Engineer", "description": "<p>Build &amp; ship.</p>",
     "country": "Thailand", "state": "Bangkok", "city": "Bangkok", "location_display": "Bangkok, Thailand", "is_remote": True, "contract_details": "full_time"},
    {"id": 2, "hash": "", "position_name": "skip me", "description": "x", "location_display": "Remote", "is_remote": None}]}
MANATAL_P2 = {"count": 3, "next": None, "previous": "x", "results": [
    {"id": 3, "hash": "ZZ99", "position_name": "QA Intern", "description": "<p>Test.</p>", "country": "Germany", "state": "", "city": "Berlin",
     "location_display": "", "is_remote": None, "contract_details": "internship"}]}


def test_manatal_follows_next_and_maps():
    http = _stub({"page=2": (200, MANATAL_P2), "career-page/acme/jobs": (200, MANATAL_P1)})
    jobs, stats = _run("manatal", _row("manatal", "acme", name="Acme"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert [c[0] for c in http.calls][0] == "https://open.api.manatal.com/open/v3/career-page/acme/jobs/"
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Acme" and a["source"] == "manatal"  # organization_name is a department, never the employer
    assert a["url"] == "https://www.careers-page.com/acme/job/AB12CD34" and a["description"] == "Build & ship."
    assert a["remote"] is True and a["location"] == "Bangkok, Thailand" and a["extra"]["employment_type"] == "full_time"
    assert b["location"] == "Berlin, Germany" and b["remote"] is None and b["posted"] == ""


def test_manatal_next_link_is_host_locked_and_failures():
    page = dict(MANATAL_P1, next="https://evil.example.com/steal?page=2")
    http = _stub({"career-page/acme/jobs": (200, page)})
    jobs, _ = _run("manatal", _row("manatal", "acme"), http)
    assert len(jobs) == 1 and len(http.calls) == 1
    jobs, stats = _run("manatal", _row("manatal", "acme"), _stub({"career-page": (200, {"count": 0, "next": None, "results": []})}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("manatal", _row("manatal", "acme"), _stub({"career-page": (404, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    loop = {"count": 1, "next": "https://open.api.manatal.com/open/v3/career-page/acme/jobs/?page=2", "results": []}
    http = _stub({"career-page": (200, loop)})
    _run("manatal", _row("manatal", "acme"), http)
    assert len(http.calls) == eu.MAX_PAGES  # a feed that never ends is capped


# --------------------------------------------------------------------------- PeopleForce
def _pf_list(page, total, *cards):
    cs = "".join(f'<div class="col-12"><h4><a class="stretched-link" href="/careers/v/{c[0]}">{c[1]}</a></h4></div>' for c in cards)
    nav = ""
    if total > 1:
        nav = "<nav><ul>" + "".join(f'<li><a href="/careers?page={p}">{p}</a></li>' for p in range(1, total + 1) if p != page) + "</ul></nav>"
    return f'<html><body><div class="row">{cs}</div>{nav}</body></html>'


PF_DETAIL = """<html><body><h1>Work at Acme</h1><div class="row"><div class="col-lg-8 col-12"><h2>About the role</h2><p>Build things.</p><script>alert(1)</script></div>
<div class="col-lg-4"><dl><dt>Work type</dt><dd>Full-time</dd><dt>Department</dt><dd>Sales</dd><dt>Location</dt><dd>Kyiv, Remote</dd></dl></div></div></body></html>"""
PF_TAILWIND = '<html><body><div class="tw-col-lg-8"><p>New theme body</p></div><dl><dt>Location</dt><dd>Lviv</dd></dl></body></html>'


def test_peopleforce_pages_until_nav_ends_then_details():
    http = _stub({"page=1": (200, _pf_list(1, 2, ("100-backend", "Backend Dev"))),
                  "page=2": (200, _pf_list(2, 2, ("200-designer", "Designer"))),
                  "/careers/v/100-backend": (200, PF_DETAIL), "/careers/v/200-designer": (200, PF_TAILWIND)})
    jobs, stats = _run("peopleforce", _row("peopleforce", "acme", name="Acme"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert [c[0] for c in http.calls][:2] == ["https://acme.peopleforce.io/careers?page=1", "https://acme.peopleforce.io/careers?page=2"]
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Acme" and a["title"] == "Backend Dev"  # title from the anchor, not the generic h1
    assert a["url"] == "https://acme.peopleforce.io/careers/v/100-backend" and a["extra"] == {"job_id": "100", "work_type": "Full-time"}
    assert a["location"] == "Kyiv, Remote" and a["remote"] is True and "Build things" in a["description"] and "alert" not in a["description"]
    assert b["location"] == "Lviv" and "New theme body" in b["description"]


def test_peopleforce_single_page_no_nav_stops_and_title_ok_gate():
    http = _stub({"page=1": (200, _pf_list(1, 1, ("100-backend", "Backend Dev"), ("101-sales", "Sales Rep"))), "/careers/v/100": (200, PF_DETAIL)})
    jobs, _ = _run("peopleforce", _row("peopleforce", "acme", title_ok=lambda t: "backend" in t.lower()), http)
    assert len(http.calls) == 2  # one listing page (no nav = last page) + one wanted detail
    assert {j["title"] for j in jobs} == {"Backend Dev", "Sales Rep"}
    assert [j["description"] != "" for j in jobs] == [True, False]


def test_peopleforce_empty_non200_429_stop_and_bad_tenant():
    jobs, stats = _run("peopleforce", _row("peopleforce", "acme"), _stub({"page=1": (200, "<html><body></body></html>")}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("peopleforce", _row("peopleforce", "acme"), _stub({"page=1": (500, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    http = _stub({"page=1": (200, _pf_list(1, 1, ("1-a", "A role"), ("2-b", "B role"))), "/careers/v/": (429, None)})
    jobs, _ = _run("peopleforce", _row("peopleforce", "acme"), http)
    assert len(http.calls) == 2 and len(jobs) == 2  # the first 429 ends the detail requests; the cards stay
    http = _stub({})
    assert _run("peopleforce", _row("peopleforce", "a.b/c"), http)[0] == [] and http.calls == []
    http = _stub({"page=": (200, _pf_list(1, 99, ("1-a", "A role")))})  # a nav that never ends is capped
    _run("peopleforce", _row("peopleforce", "acme"), http)
    assert len([c for c in http.calls if "?page=" in c[0]]) == eu.MAX_PAGES


# --------------------------------------------------------------------------- TalentAdore
TA_FEED = {"company": "Acme", "jobs": [
    {"id": "yjMOo", "job_token": "mWXEgr", "name": "AI Engineer", "link": "https://ats.talentadore.com/apply/ai-engineer/mWXEgr",
     "description_html": "<p>Build models.</p>", "description_text": "Build models.", "start_date": "2026-07-10T12:16:20Z",
     "updated": "2026-07-11T09:00:00Z", "city": "Helsinki", "county": "", "country": "Finland", "location": "Pasilankatu 2 A"},
    {"id": "RyGxO", "job_token": "m5n9e1", "name": "Remote Designer", "link": "https://ats.talentadore.com/apply/remote-designer/m5n9e1",
     "description_html": "", "description_text": "Design things.", "start_date": "", "updated": "2026-07-09T08:00:00Z", "city": "", "county": "", "country": "Remote"},
    {"id": "x", "job_token": "", "name": "No token", "link": "https://ats.talentadore.com/apply/x"}]}


def test_talentadore_happy_path():
    http = _stub({"/positions/9wmfASE/json": (200, TA_FEED)})
    jobs, stats = _run("talentadore", _row("talentadore", "9wmfASE", name="Acme Oy"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert http.calls[0][0] == "https://ats.talentadore.com/positions/9wmfASE/json?v=2"  # v=2 keeps the dates RFC3339
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Acme Oy" and a["location"] == "Helsinki, Finland" and a["posted"] == "2026-07-10"
    assert a["url"] == "https://ats.talentadore.com/apply/ai-engineer/mWXEgr" and a["extra"]["job_token"] == "mWXEgr" and a["description"] == "Build models."
    assert b["description"] == "Design things." and b["remote"] is True and b["posted"] == "2026-07-09"


def test_talentadore_row_without_a_real_name_takes_the_feed_company_and_failures():
    jobs, _ = _run("talentadore", {"name": "9wmfASE", "ats": "talentadore", "token": "9wmfASE"}, _stub({"positions": (200, TA_FEED)}))
    assert {j["company"] for j in jobs} == {"Acme"}
    jobs, stats = _run("talentadore", _row("talentadore", "t"), _stub({"positions": (200, {"jobs": []})}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("talentadore", _row("talentadore", "t"), _stub({"positions": (500, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0


# --------------------------------------------------------------------------- TalentHR
def _th_list(*items):
    pos = ",".join('{"id":%s,"slug":"%s"}' % it for it in items)
    return '<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"positions":[' + pos + ']}}}</script></html>'


def _th_detail(title, etype, status, city="", country="CH"):
    data = {"props": {"pageProps": {"data": {"job_position_title": title, "job_description": "<p>Own the backend.</p><script>alert(1)</script>",
                                             "is_remote": False, "employment_type": etype, "employment_status_name": status, "city": city,
                                             "region": "", "country": country, "location_name": "CH", "publish_date": "2026-07-17 10:33:19",
                                             "created_at": "2026-07-17 10:33:18"}}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script></html>"


def test_talenthr_listing_then_detail():
    http = _stub({"/dnext/senior-backend-developer-2/22": (200, _th_detail("Senior Backend Developer", "Remote", "Full-Time")),
                  "jobs.talenthr.io/dnext": (200, _th_list((22, "senior-backend-developer-2"), (0, "skip"), (7, "")))})
    jobs, stats = _run("talenthr", _row("talenthr", "dnext", name="Dnext Intelligence"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert set(j) == JOB_KEYS and j["company"] == "Dnext Intelligence" and j["title"] == "Senior Backend Developer"
    assert j["url"] == "https://jobs.talenthr.io/dnext/senior-backend-developer-2/22" and j["location"] == "CH"
    assert j["remote"] is True and j["posted"] == "2026-07-17" and "Own the backend" in j["description"] and "alert" not in j["description"]
    assert j["extra"] == {"job_id": "22", "work_mode": "remote", "employment_status": "Full-Time"}
    assert len(http.calls) == 2


def test_talenthr_title_ok_gate_empty_and_failures():
    http = _stub({"/dnext/live-role/10": (200, _th_detail("Live Role", "Onsite", "Full-Time", city="Zurich")),
                  "jobs.talenthr.io/dnext": (200, _th_list((10, "live-role"), (11, "gone-role")))})
    jobs, _ = _run("talenthr", _row("talenthr", "dnext", title_ok=lambda t: "live" in t), http)
    assert len(jobs) == 1 and len(http.calls) == 2 and jobs[0]["remote"] is None and jobs[0]["location"] == "Zurich, CH"
    jobs, stats = _run("talenthr", _row("talenthr", "dnext"), _stub({"jobs.talenthr.io/dnext": (200, _th_list())}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("talenthr", _row("talenthr", "dnext"), _stub({"talenthr": (200, "<html>no data</html>")}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("talenthr", _row("talenthr", "dnext"), _stub({"talenthr": (500, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("talenthr", _row("talenthr", "dnext"), _stub({"/dnext/gone-role/11": (404, None), "jobs.talenthr.io/dnext": (200, _th_list((11, "gone-role")))}))
    assert jobs == [] and stats["ok"] == 1  # a detail that fails costs only that job


# --------------------------------------------------------------------------- TalentLyft
def _tl_sitemap(host, *paths):
    return '<?xml version="1.0"?><urlset>' + "".join(f"<url><loc>https://{host}{p}</loc></url>" for p in paths) + f"<url><loc>https://{host}</loc></url></urlset>"


def _tl_detail(title):
    return ('<html><body><script type="application/ld+json">{"@context":"https://schema.org/","@type":"JobPosting","title":"' + title + '",'
            '"description":"<p>Build eddy current instruments.</p>","datePosted":"2026-06-23","employmentType":"FULL_TIME",'
            '"hiringOrganization":{"@type":"Organization","name":"INETEC"},"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",'
            '"addressLocality":"Donji Stupnik","addressCountry":"Croatia"}}}</script></body></html>')


def test_talentlyft_sitemap_then_ld_json():
    host = "inetec.talentlyft.com"
    http = _stub({"/jobs/senior-electronics-engineer-cjVz": (200, _tl_detail("Senior Electronics Engineer")),
                  "sitemap.xml": (200, _tl_sitemap(host, "/jobs/senior-electronics-engineer-cjVz", "/about"))})
    jobs, stats = _run("talentlyft", _row("talentlyft", "inetec", name="INETEC d.o.o."), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 1}
    assert http.calls[0][0] == "https://inetec.talentlyft.com/sitemap.xml" and len(http.calls) == 2  # the root and /about carry no code
    j = jobs[0]
    assert set(j) == JOB_KEYS and j["company"] == "INETEC d.o.o."  # the configured company, as the reference does
    assert j["title"] == "Senior Electronics Engineer" and j["location"] == "Donji Stupnik, Croatia" and j["posted"] == "2026-06-23"
    assert j["url"] == "https://inetec.talentlyft.com/jobs/senior-electronics-engineer-cjVz" and "eddy current" in j["description"]
    assert j["extra"]["employment_type"] == "FULL_TIME"


def test_talentlyft_code_pattern_title_ok_empty_and_failures():
    host = "inetec.talentlyft.com"
    for path, ok in (("/jobs/senior-electronics-engineer-cjVz", True), ("/jobs/role-cj4t?utm=x", True), ("/jobs/ab", False), ("/matchmatch", False)):
        http = _stub({"/jobs/": (200, _tl_detail("T")), "sitemap.xml": (200, _tl_sitemap(host, path))})
        _run("talentlyft", _row("talentlyft", "inetec"), http)
        assert (len(http.calls) == 2) is ok, path
    http = _stub({"/jobs/": (200, _tl_detail("T")), "sitemap.xml": (200, _tl_sitemap(host, "/jobs/nurse-aaaa", "/jobs/welder-bbbb"))})
    _run("talentlyft", _row("talentlyft", "inetec", title_ok=lambda t: "welder" in t), http)
    assert len(http.calls) == 2
    http = _stub({"sitemap.xml": (200, _tl_sitemap("evil.example.com", "/jobs/nurse-aaaa"))})
    assert _run("talentlyft", _row("talentlyft", "inetec"), http)[0] == [] and len(http.calls) == 1  # off-host URLs are never opened
    jobs, stats = _run("talentlyft", _row("talentlyft", "inetec"), _stub({"sitemap.xml": (200, "<urlset></urlset>")}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("talentlyft", _row("talentlyft", "inetec"), _stub({"sitemap.xml": (404, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("talentlyft", _row("talentlyft", "inetec"), _stub({"/jobs/": (200, "<html>no ld+json</html>"), "sitemap.xml": (200, _tl_sitemap(host, "/jobs/nurse-aaaa"))}))
    assert jobs == [] and stats["ok"] == 1


# --------------------------------------------------------------------------- Traffit
def _tr_page(start, n, count):
    return {"count": count, "items": [{"advertId": start + i, "title": "Role", "name": "Role", "url": f"https://acme.traffit.com/public/an/x{start + i}"} for i in range(n)]}


TR_LAST = {"count": 103, "items": [
    {"advertId": 2001, "title": "  Senior Java Engineer  ", "name": "Senior Java Engineer", "url": "https://acme.traffit.com/public/an/abc", "validStart": 1770284174,
     "description": "<p>Build things</p>", "geolocation": '{"iso":"pl","locality":"Warszawa","region1":"Mazowieckie","country":"Polska"}'},
    {"advertId": 2002, "title": "", "name": "Remote Fallback Role", "url": "https://acme.traffit.com/public/an/def", "geolocation": None},
    {"advertId": 0, "advertPublishId": 0, "title": "No Id", "url": "https://acme.traffit.com/public/an/ghi"}]}


def test_traffit_pages_until_count_and_maps():
    http = _stub({"offset=0": (200, _tr_page(1000, 100, 103)), "offset=100": (200, TR_LAST)})
    jobs, stats = _run("traffit", _row("traffit", "acme", name="Acme"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 102}  # 100 + 2; the id-less posting is dropped
    assert [c[0] for c in http.calls] == ["https://acme.traffit.com/public/an/list/?limit=100&offset=0", "https://acme.traffit.com/public/an/list/?limit=100&offset=100"]
    j = jobs[100]
    assert set(j) == JOB_KEYS and j["title"] == "Senior Java Engineer" and j["company"] == "Acme"
    assert j["location"] == "Warszawa, Mazowieckie, Polska" and j["posted"] == "2026-02-05" and j["description"] == "Build things"
    assert j["url"] == "https://acme.traffit.com/public/an/abc" and j["extra"]["advert_id"] == "2001"
    assert jobs[101]["title"] == "Remote Fallback Role" and jobs[101]["location"] == ""  # name when the title is blank; null geolocation


def test_traffit_empty_non200_garbage_geolocation():
    jobs, stats = _run("traffit", _row("traffit", "empty"), _stub({"traffit": (200, {"count": 0, "items": []})}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("traffit", _row("traffit", "x"), _stub({"traffit": (500, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    page = {"count": 1, "items": [{"advertId": 5, "title": "T", "url": "https://x.traffit.com/public/an/t", "geolocation": "{not json", "validStart": "soon"}]}
    jobs, _ = _run("traffit", _row("traffit", "x"), _stub({"traffit": (200, page)}))
    assert len(jobs) == 1 and jobs[0]["location"] == "" and jobs[0]["posted"] == ""
    http = _stub({"traffit": (200, _tr_page(1, 100, 10**6))})  # a feed that never reaches count is capped
    _run("traffit", _row("traffit", "x"), http)
    assert len(http.calls) == eu.MAX_PAGES


# --------------------------------------------------------------------------- Trakstar
TK_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:job="https://recruiterbox.com/rss/job/"><channel><title>Cheesecake Labs Jobs</title>
<item><title>Senior Go Engineer</title><link>http://cheesecakelabs.hire.trakstar.com/jobs/fk0zjzb</link>
<guid>http://cheesecakelabs.hire.trakstar.com/jobs/fk0zjzb</guid><pubDate>Thu, 18 Jun 2026 00:00:00 +0530</pubDate>
<description>&lt;p&gt;Build the &lt;strong&gt;pipeline&lt;/strong&gt;.&lt;/p&gt;</description>
<job:locationCity>Florian&#243;polis</job:locationCity><job:locationState>SC</job:locationState><job:locationCountry>Brazil</job:locationCountry></item>
<item><title>Remote Platform Engineer</title><link>http://cheesecakelabs.hire.trakstar.com/jobs/ab1cdef</link>
<pubDate>Thu, 18 Jun 2026 00:00:00 +0530</pubDate><description>&lt;p&gt;Work from anywhere.&lt;/p&gt;</description>
<job:locationCity>Remote</job:locationCity><job:locationCountry>Brazil</job:locationCountry></item>
<item><title></title><link>http://cheesecakelabs.hire.trakstar.com/jobs/zzz</link></item></channel></rss>"""


def test_trakstar_rss_happy_path():
    http = _stub({"/jobfeeds/cheesecakelabs": (200, TK_FEED)})
    jobs, stats = _run("trakstar", _row("trakstar", "cheesecakelabs", name="Cheesecake Labs"), http)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert http.calls[0][0] == "https://cheesecakelabs.hire.trakstar.com/jobfeeds/cheesecakelabs"
    a, b = jobs
    assert set(a) == JOB_KEYS and a["company"] == "Cheesecake Labs" and a["title"] == "Senior Go Engineer"
    assert a["url"] == "https://cheesecakelabs.hire.trakstar.com/jobs/fk0zjzb" and a["extra"]["job_id"] == "fk0zjzb"  # http upgraded to https
    assert a["location"] == "Florianópolis, SC, Brazil" and a["posted"] == "2026-06-18" and a["remote"] is None
    assert "Build the" in a["description"] and "pipeline" in a["description"] and "<p>" not in a["description"]
    assert b["remote"] is True and b["location"] == "Remote, Brazil"


def test_trakstar_empty_non200_and_garbage():
    empty = '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
    jobs, stats = _run("trakstar", _row("trakstar", "way2"), _stub({"jobfeeds": (200, empty)}))
    assert jobs == [] and stats["ok"] == 1
    for resp in ((404, None), (200, "<html>not rss</html>"), (200, "<rss><channel><item>"), (200, '<!DOCTYPE r [<!ENTITY a "b">]><rss/>'), (200, "")):
        jobs, stats = _run("trakstar", _row("trakstar", "way2"), _stub({"jobfeeds": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0, resp


# --------------------------------------------------------------------------- all readers
GARBAGE = [(0, None), (200, None), (200, []), (200, {}), (200, "text"), (200, 7), (200, [1, None, "x"]), (200, {"jobs": "x", "items": {}, "results": 3, "vacancies": [None]}),
           (500, None), (403, None), (429, None)]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("resp", GARBAGE, ids=lambda r: str(r)[:30])
def test_never_raises_on_garbage(name, resp):
    jobs, stats = _run(name, _row(name, "acme.factorial.it" if name == "factorial" else "acme"), _stub({"": resp}))
    assert isinstance(jobs, list) and stats["tried"] == 1


@pytest.mark.parametrize("name", NAMES)
def test_http_that_raises_is_survived(name):
    def boom(*a, **k):
        raise RuntimeError("network down")
    jobs, stats = _run(name, _row(name, "acme.factorial.it" if name == "factorial" else "acme"), boom)
    assert jobs == [] and stats["tried"] == 1 and stats.get("ok", 0) == 0


@pytest.mark.parametrize("name", [n for n in NAMES if n != "jobylon"])
@pytest.mark.parametrize("token", ["", "../x", "a b", "x?y=1"])
def test_bad_tokens_send_no_request(name, token):
    http = _stub({})
    assert _run(name, _row(name, token), http)[0] == []
    assert http.calls == []


def test_companies_json_rows_for_these_boards_are_valid():
    raw = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
    rows = [r for r in raw if r["ats"] in NAMES]
    assert rows and all(set(r) == {"name", "ats", "board", "source"} for r in rows)
    assert {r["ats"] for r in rows} == set(NAMES) - {"jobylon"}  # jobylon has one global sitemap, no per-company board
    assert len({(r["ats"], r["board"].lower()) for r in rows}) == len(rows)
    assert all(r["name"].strip() and r["board"].strip() for r in rows)


# --------------------------------------------------------------------------- review findings
def test_body_strip_is_linear_and_still_strips_pairs():
    import time
    evil = "<script>" * 50_000  # 400KB, never closed: the old lazy regex was quadratic on this
    t0 = time.perf_counter()
    assert eu._body(evil) == ""
    assert eu._body("<style>" * 50_000 + "x") == ""
    assert time.perf_counter() - t0 < 1.0
    assert eu._body("<p>Hello</p><script type='x'>alert(1)</script><p>World</p>").split() == ["Hello", "World"]
    assert eu._body("<style>p{}</style>Keep<SCRIPT>bad()</SCRIPT >me<scripts>kept</scripts>").split() == ["Keepmekept"]
    assert eu._body("Before<script>never closed " + "a" * 50) == "Before"
    assert eu._body("") == "" and eu._body(None) == ""


def test_body_strip_is_linear_on_fake_tags_and_bare_angle_brackets():
    import time
    t0 = time.perf_counter()
    for evil in ("<styles" * 28_000, "<scripts" * 25_000, "<" * 200_000, "<script " * 8_000 + "</script" * 8_000):
        eu._body(evil)
    assert time.perf_counter() - t0 < 1.5
    assert eu._body("a<styles>b</styles>c<b") == "abc"
    assert eu._body("keep<scripts>x</scripts>") == "keepx"


def test_trakstar_uses_the_shared_safe_xml_parser():
    assert eu.safe_fromstring.__module__.endswith("kit_xml") and not hasattr(eu, "_xml")


def test_manatal_host_is_read_not_unsupported():
    assert hk.board_from_job_url("https://careers-page.com/acme/job/X")["kind"] == "ats"


def test_feed_job_links_must_sit_on_the_boards_own_host():
    foreign = "https://evil.example/apply"
    page = {"count": 1, "items": [{"advertId": 7, "title": "T", "url": foreign}]}
    jobs, _ = _run("traffit", _row("traffit", "acme"), _stub({"traffit": (200, page)}))
    assert jobs == []
    ta = {"company": "A", "jobs": [{"job_token": "t1", "name": "N", "link": foreign}]}
    jobs, _ = _run("talentadore", _row("talentadore", "tok"), _stub({"positions": (200, ta)}))
    assert jobs == []
    rss = f"<rss><channel><item><title>T</title><link>{foreign}</link></item></channel></rss>"
    jobs, _ = _run("trakstar", _row("trakstar", "acme"), _stub({"jobfeeds": (200, rss)}))
    assert jobs == []


@pytest.mark.parametrize("host", ["www.factorial.io", "jobs.factorial.co", "x.factorial.xyz", "acme.factorial.com", "acme.factorialhr.io"])
def test_factorial_lookalike_hosts_are_not_factorial(host):
    assert not eu._FACTORIAL_HOST.fullmatch(host)
    info = hk.board_from_job_url(f"https://{host}/job_posting/engineer-12")
    assert info.get("ats") != "factorial"
    h = _stub({})
    assert _run("factorial", _row("factorial", host), h)[0] == [] and h.calls == []


@pytest.mark.parametrize("host", ["muffin.factorial.it", "highbras.factorialhr.com.br", "4farma.factorialhr.pt", "careers.factorialhr.com"])
def test_factorial_real_hosts_are_still_factorial(host):
    assert eu._FACTORIAL_HOST.fullmatch(host)
    info = hk.board_from_job_url(f"https://{host}/job_posting/engineer-12")
    assert (info["kind"], info["ats"], info["token"]) == ("ats", "factorial", host)


def test_a_404_posting_is_skipped_but_other_refusals_stop_the_board():
    for gone in (404, 410):
        http = _stub({"backend-engineer-100": (gone, None),  # the FIRST card's page is gone ...
                      "data-analyst-200": (200, _factorial_detail("Data Analyst", "€1", "Roma, Italia", "<p>d</p>")),
                      "https://acme.factorial.it/": (200, FACTORIAL_LIST)})
        jobs, _ = _run("factorial", _row("factorial", "acme.factorial.it"), http)
        assert len(http.calls) == 3 and len(jobs) == 2  # ... and the second card's page is still opened
        assert {j["extra"]["job_id"]: j["location"] for j in jobs}["200"] == "Roma, Italia"
    for refused in (429, 403, 0, 500):
        http = _stub({"/job_posting/": (refused, None), "https://acme.factorial.it/": (200, FACTORIAL_LIST)})
        jobs, _ = _run("factorial", _row("factorial", "acme.factorial.it"), http)
        assert len(http.calls) == 2 and len(jobs) == 2, refused  # list + ONE refused detail, then stop; cards stay


# --------------------------------------------------------------------------- final review (part 2)
def test_factorial_paren_row_is_linear_on_a_hostile_row():
    import time
    t0 = time.perf_counter()
    assert len(eu._factorial_location(["a(" + "," * 60000])[0]) <= 300
    assert eu._factorial_location(["a" + " " * 60000 + "x"]) == ("", "")
    assert time.perf_counter() - t0 < 0.5


def test_factorial_location_still_reads_nested_and_plain_rows():
    f = eu._factorial_location
    assert f(["Hybrid (Berlin, Germany)"]) == ("Berlin, Germany", "hybrid")
    assert f(["Remote (A, B) (Rome, Italy)"]) == ("Rome, Italy", "")
    assert f(["Rome, Italy"]) == ("Rome, Italy", "")


def test_html_tree_depth_is_capped_so_unclosed_tags_cannot_recurse_or_stall():
    import time
    t0 = time.perf_counter()
    root = eu._tree("<select>" + "<option>x" * 5000 + "</select>")
    assert len(list(eu._walk(root))) >= 5000
    assert "x" in root.text() and root.inner_html()
    eu._tree("<div>" * 20000 + "</b>" * 24000)
    assert time.perf_counter() - t0 < 2.0


def test_jobylon_style_page_with_thousands_of_unclosed_tags_does_not_lose_the_board():
    page = "<ul>" + "<li><a href='/job_posting/x-1'>X" * 3000 + "</ul>"
    assert eu._links(eu._tree(page), "https://acme.factorial.it/", "acme.factorial.it", lambda h: "/job_posting/" in h)


@pytest.mark.parametrize("host", ["a.factorialhr.es", "a.factorialhr.it", "a.factorialhr.mx", "a.factorialhr.com.mx"])
def test_unverified_factorial_country_domains_are_not_read(host):
    assert not eu._FACTORIAL_HOST.fullmatch(host)
    assert hk.board_from_job_url(f"https://{host}/job_posting/engineer-12").get("ats") != "factorial"


def test_detail_pages_and_peopleforce_listing_are_requested_once():
    http = _stub({"/job_posting/": (429, None), "https://acme.factorial.it/": (200, FACTORIAL_LIST)})
    _run("factorial", _row("factorial", "acme.factorial.it"), http)
    assert [o for u, o in http.once if "/job_posting/" in u] == [True]  # the refused detail page is sent once, no backoff
    http = _stub({"page=1": (200, _pf_list(1, 1, ("1-a", "A role"))), "/careers/v/": (429, None)})
    _run("peopleforce", _row("peopleforce", "acme"), http)
    assert all(o for _, o in http.once)


def test_read_kit_hosts_are_not_listed_as_unsupported():
    assert not hk._UNSUPPORTED_HOSTS.search("factorialhr.com") and not hk._UNSUPPORTED_HOSTS.search("trakstar.com")


_DEEP_LD = '<html><script type="application/ld+json">' + "[" * 100000 + "</script></html>"
_DEEP_NEXT = '<html><script id="__NEXT_DATA__" type="application/json">' + '{"a":' * 60000 + "1" + "}" * 60000 + "</script></html>"


def test_one_deeply_nested_page_costs_only_that_page():
    http = _stub({"/jobs/369523-": (200, JOBYLON_ACME), "/jobs/111-": (200, _DEEP_LD), "sitemap-jobs": (200, JOBYLON_JOBS), "sitemap.xml": (200, JOBYLON_INDEX)})
    assert [j["title"] for j in _run("jobylon", _row("jobylon", "all"), http)[0]] == ["Engineer & Lead"]
    http = _stub({"/jobs/good-role-abc12": (200, _tl_detail("Good Role")), "/jobs/bad-role-xyz34": (200, _DEEP_LD),
                  "sitemap.xml": (200, _tl_sitemap("a.talentlyft.com", "/jobs/bad-role-xyz34", "/jobs/good-role-abc12"))})
    assert [j["title"] for j in _run("talentlyft", _row("talentlyft", "a"), http)[0]] == ["Good Role"]
    http = _stub({"/dnext/good-role/10": (200, _th_detail("Good Role", "Onsite", "Full-Time")), "/dnext/bad-role/11": (200, _DEEP_NEXT),
                  "jobs.talenthr.io/dnext": (200, _th_list((10, "good-role"), (11, "bad-role")))})
    assert [j["title"] for j in _run("talenthr", _row("talenthr", "dnext"), http)[0]] == ["Good Role"] and eu._next_data(_DEEP_NEXT) is None
