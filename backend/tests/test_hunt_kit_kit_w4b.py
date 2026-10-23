"""Offline tests for hunt_kit/kit_w4b.py (GeekJob, ApplicantPro, Potok, Huntflow).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/geekjob_test.go, isolvedhire_test.go,
huntflow_test.go and testdata/potok_*.html (MIT, bed5ec9) and written fresh. Nothing touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
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


w4 = _load("kit_w4b_under_test", "kit_w4b.py")
hk = _load("hunt_kit_w4b_under_test", "hunt_kit.py")
F = w4.ATS_FETCHERS
NAMES = ("geekjob", "applicantpro", "potok", "huntflow")
ID1, ID2, ID3 = "6a5880f9186d0bddab0010e9", "6a589c8bda741b31f007fe90", "69b1d48a1121b4005a5cce59"


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers, data)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False, max_bytes=None):
        calls.append((url, method, headers or {}, data, once))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers or {}, data) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    return F[name](row, http, stats), stats


def _row(ats: str, token: str, **kw) -> dict:
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def _hosts(http) -> set:
    return {urlsplit(c[0]).hostname for c in http.calls}


# ------------------------------------------------------------------ registry
def test_every_reader_is_registered_and_wired():
    assert set(F) == set(NAMES)
    assert "kit_w4b" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_w4b.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_w4b.py"' in served


@pytest.mark.parametrize("url,ats,token", [
    ("https://geekjob.ru/vacancy/" + ID1, "geekjob", ""),
    ("https://acme.applicantpro.com/jobs/1792515", "applicantpro", "acme"),
    ("https://www.applicantpro.com/jobs/1", "applicantpro", ""),
    ("https://skyengteam.potok.io/open/jobs/2718", "potok", "skyengteam"),
    ("https://app.potok.io/open/jobs/2718", "potok", ""),
    ("https://tripster.huntflow.io/vacancy/senior-backend-developer-2", "huntflow", "tripster"),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token) or (token == "" and info["kind"] == "other")
    for host in ("geekjob.ru", "applicantpro.com", "potok.io", "huntflow.io"):
        assert not hk._UNSUPPORTED_HOSTS.search(host)


def test_parse_extra_reads_the_new_tokens():
    rows = hk.parse_extra("geekjob:all,applicantpro:acme,potok:skyengteam,huntflow:banki")
    assert [(r["ats"], r["token"]) for r in rows] == [("geekjob", "all"), ("applicantpro", "acme"), ("potok", "skyengteam"), ("huntflow", "banki")]


def test_companies_rows_are_valid_and_techstars_is_a_getro_network():
    rows = [r for r in json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8")) if r["ats"] in NAMES]
    assert {(r["ats"], r["board"]) for r in rows} >= {("geekjob", "all"), ("potok", "skyengteam"), ("huntflow", "banki")}
    assert all(r["source"] == "freehire" for r in rows)
    ks = _load("kit_startups_w4b_under_test", "kit_startups.py")
    techstars = [n for n in ks.GETRO_NETWORKS if n[0] == "Techstars"]
    assert techstars and urlsplit(techstars[0][2]).hostname == "jobs.techstars.com" and urlsplit(techstars[0][2]).scheme == "https"


# ------------------------------------------------------------------ GeekJob
def _gj_listing(*ids, extra=""):
    cards = "".join(f'<li><a href="/vacancy/{i}" class="title">Role {i[:4]}</a><a href="/vacancy/{i}">A Company</a></li>' for i in ids)
    return f'<html><body><ul>{cards}{extra}<a href="/vacancies">All</a></ul></body></html>'


def _gj_detail(title="Backend Dev", company="Acme", chips="Удаленная работа", extra_desc=""):
    ld = {"@context": "https://schema.org/", "@type": "JobPosting", "datePosted": "2026-07-16", "title": title,
          "hiringOrganization": {"@type": "Organization", "name": company},
          "description": "<p>Build things.</p><script>alert(1)</script>" + extra_desc,
          "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Бишкек", "addressCountry": "Кыргызстан"}}}
    return f'<html><body><div class="jobinfo">{chips} Опыт</div><script type="application/ld+json">{json.dumps(ld).replace("</", "<" + chr(92) + "/")}</script></body></html>'


def test_geekjob_happy_path_paging_and_dedup():
    http = _stub({"/vacancies/1": (200, _gj_listing(ID1, ID2)), "/vacancies/2": (200, _gj_listing(ID3)),
                  "/vacancies/3": (200, _gj_listing()), f"/vacancy/{ID1}": (200, _gj_detail()),
                  f"/vacancy/{ID2}": (200, _gj_detail("Frontend Dev", "Beta", "Работа в офисе")),
                  f"/vacancy/{ID3}": (200, _gj_detail("Data Dev", "Gamma", ""))})
    jobs, stats = _run("geekjob", _row("geekjob", "all"), http)
    assert [j["title"] for j in jobs] == ["Backend Dev", "Frontend Dev", "Data Dev"]
    assert [j["company"] for j in jobs] == ["Acme", "Beta", "Gamma"]
    assert [j["remote"] for j in jobs] == [True, False, None]
    assert jobs[0]["location"] == "Бишкек, Кыргызстан" and jobs[0]["posted"] == "2026-07-16"
    assert jobs[0]["url"] == f"https://geekjob.ru/vacancy/{ID1}" and "alert" not in jobs[0]["description"]
    assert _hosts(http) == {"geekjob.ru"} and stats["ok"] == 1


def test_geekjob_bad_posting_and_refusal_ends_detail_requests():
    http = _stub({"/vacancies/1": (200, _gj_listing(ID1, ID2, ID3)), "/vacancies/2": (200, _gj_listing()),
                  f"/vacancy/{ID1}": (200, "<html>no ld</html>"), f"/vacancy/{ID2}": (404, None),
                  f"/vacancy/{ID3}": (200, _gj_detail())})
    jobs, _ = _run("geekjob", _row("geekjob", "all"), http)
    assert [j["title"] for j in jobs] == ["Backend Dev"]  # a page without ld+json and a 404 cost only their postings
    http = _stub({"/vacancies/1": (200, _gj_listing(ID1, ID2)), "/vacancies/2": (200, _gj_listing()),
                  f"/vacancy/{ID1}": (429, None), f"/vacancy/{ID2}": (200, _gj_detail())})
    jobs, _ = _run("geekjob", _row("geekjob", "all"), http)
    assert jobs == [] and not any(ID2 in c[0] for c in http.calls)  # the first refusal ended the board's detail requests
    jobs, stats = _run("geekjob", _row("geekjob", "all"), _stub({"/vacancies/1": (403, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0


def test_geekjob_off_host_and_non_https_links_are_never_fetched():
    evil = ('<a href="https://evil.example/vacancy/%s">x</a><a href="http://geekjob.ru/vacancy/%s">y</a>'
            '<a href="//cdn.example/vacancy/%s">z</a><a href="http://[bad/vacancy/%s">w</a>' % (ID2, ID2, ID2, ID2))
    http = _stub({"/vacancies/1": (200, _gj_listing(ID1, extra=evil)), "/vacancies/2": (200, _gj_listing()),
                  f"/vacancy/{ID1}": (200, _gj_detail())})
    jobs, _ = _run("geekjob", _row("geekjob", "all"), http)
    assert len(jobs) == 1 and _hosts(http) == {"geekjob.ru"}
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"geekjob.ru"} and all(urlsplit(j["url"]).scheme == "https" for j in jobs)
    assert not any(c[0].startswith("http://") for c in http.calls)


def test_geekjob_hostile_markup_is_fast_and_title_filter_limits_requests():
    page = _gj_listing(ID1, extra="<div>" * 5000 + "<a href=/vacancy/" * 3000 + "<p>" * 5000)
    http = _stub({"/vacancies/1": (200, page), "/vacancies/2": (200, _gj_listing()), f"/vacancy/{ID1}": (200, _gj_detail())})
    t0 = time.perf_counter()
    jobs, _ = _run("geekjob", _row("geekjob", "all"), http)
    assert time.perf_counter() - t0 < 1.0 and len(jobs) == 1
    http = _stub({"/vacancies/1": (200, _gj_listing(ID1)), "/vacancies/2": (200, _gj_listing())})
    jobs, _ = _run("geekjob", _row("geekjob", "all", title_ok=lambda t: False), http)
    assert jobs == [] and not any("/vacancy/" in c[0] for c in http.calls)


# ------------------------------------------------------------------ ApplicantPro
def _ap_sitemap(host, *ids, extra=""):
    locs = "".join(f"<url><loc>https://{host}/jobs/{i}</loc></url><url><loc>https://{host}/jobs/{i}.html</loc></url>" for i in ids)
    return f'<?xml version="1.0"?><urlset>{locs}<url><loc>https://{host}/about</loc></url>{extra}</urlset>'


def _ap_detail(title="Cashier", posted="2026-07-10 08:30:00", company="Acme Stores"):
    ld = {"@type": "JobPosting", "title": title, "datePosted": posted, "hiringOrganization": {"name": company},
          "description": "&lt;p&gt;Ring up sales.&lt;/p&gt;",
          "jobLocation": {"address": {"addressLocality": "Austin", "addressRegion": "TX", "addressCountry": "US"}}}
    return f'<html><script type="application/ld+json">{json.dumps(ld)}</script></html>'


def test_applicantpro_happy_path_dedup_and_company():
    host = "acme.applicantpro.com"
    http = _stub({"/sitemap.xml": (200, _ap_sitemap(host, "1792515", "1792516")), "/jobs/1792515": (200, _ap_detail()),
                  "/jobs/1792516": (200, _ap_detail("Manager"))})
    jobs, _ = _run("applicantpro", _row("applicantpro", "acme", name="Acme"), http)
    assert [j["title"] for j in jobs] == ["Cashier", "Manager"]  # /jobs/<id> and /jobs/<id>.html are one posting
    assert jobs[0]["url"] == f"https://{host}/jobs/1792515" and jobs[0]["posted"] == "2026-07-10"
    assert jobs[0]["company"] == "Acme" and jobs[0]["location"] == "Austin, TX, US" and "Ring up sales" in jobs[0]["description"]
    assert _hosts(http) == {host}


def test_applicantpro_gone_board_bad_posting_and_refusal():
    host = "acme.applicantpro.com"
    for notice in ("You typed the URL for this website incorrectly.", "This career site has been disabled."):
        jobs, stats = _run("applicantpro", _row("applicantpro", "acme"), _stub({"/sitemap.xml": (200, f"<html>{notice}</html>")}))
        assert jobs == [] and stats.get("ok", 0) == 0
    http = _stub({"/sitemap.xml": (200, _ap_sitemap(host, "1", "2", "3")), "/jobs/1": (200, "<html></html>"),
                  "/jobs/2": (404, None), "/jobs/3": (200, _ap_detail())})
    assert [j["title"] for j in _run("applicantpro", _row("applicantpro", "acme"), http)[0]] == ["Cashier"]
    http = _stub({"/sitemap.xml": (200, _ap_sitemap(host, "1", "2")), "/jobs/1": (403, None), "/jobs/2": (200, _ap_detail())})
    assert _run("applicantpro", _row("applicantpro", "acme"), http)[0] == [] and not any("/jobs/2" in c[0] for c in http.calls)


def test_applicantpro_off_host_sitemap_entries_are_never_followed_and_bad_label_refused():
    host = "acme.applicantpro.com"
    sm = _ap_sitemap(host, "1", extra="<url><loc>https://evil.example/jobs/99</loc></url><url><loc>http://[bad/jobs/98</loc></url>")
    http = _stub({"/sitemap.xml": (200, sm), "/jobs/1": (200, _ap_detail()), "/jobs/99": (200, _ap_detail("Evil")), "/jobs/98": (200, _ap_detail("Bad"))})
    jobs, _ = _run("applicantpro", _row("applicantpro", "acme"), http)
    assert _hosts(http) == {host} and {urlsplit(j["url"]).hostname for j in jobs} == {host}
    assert all(urlsplit(j["url"]).scheme == "https" for j in jobs)
    http = _stub({})
    assert _run("applicantpro", _row("applicantpro", "evil.example/x"), http)[0] == [] and http.calls == []


def test_applicantpro_hostile_sitemap_is_fast():
    host = "acme.applicantpro.com"
    sm = _ap_sitemap(host, *[str(n) for n in range(2000)], extra="<loc>" * 50000)
    http = _stub({"/sitemap.xml": (200, sm), "/jobs/": (404, None)})
    t0 = time.perf_counter()
    jobs, _ = _run("applicantpro", _row("applicantpro", "acme"), http)
    assert time.perf_counter() - t0 < 1.0 and jobs == [] and len(http.calls) <= 1 + w4.ID_ONLY_CAP


# ------------------------------------------------------------------ Potok
def _pk_listing(rows, team="Отдел", wrapper=True):
    cards = "".join(
        f'<div class="posting"><a class="posting-title" href="{href}"><h5>{title}</h5><div class="posting-categories">'
        f'<span class="sort-by-location posting-category">{loc}</span></div></a></div>' for href, title, loc in rows)
    inner = f'<div class="postings-group"><div class="posting-category-title">{team}</div>{cards}</div>'
    return f'<html><body>{"<div class=postings-wrapper>" + inner + "</div>" if wrapper else inner}</body></html>'


def _pk_detail(schedule="Удаленная работа", body="<p>Teach English.</p><script>x()</script>"):
    return ('<html><div class="posting-headline"><div class="sort-by-time posting-category">Москва</div>'
            f'<div class="sort-by-time posting-category">{schedule}</div><div class="sort-by-team posting-category">T</div></div>'
            f'<div class="plain-list">{body}</div></html>')


def test_potok_happy_path_and_bad_rows():
    rows = [("/open/jobs/2718", "Teacher", "Москва"), ("/open/jobs/2719", "Dev", "Казань"), ("/open/jobs/2718", "Dup", "x"),
            ("/open/jobs/abc", "Bad id", "x"), ("/open/jobs/2720", "", "x"), ("http://[bad/open/jobs/5", "Bad href", "x")]
    http = _stub({"/open/jobs/2718": (200, _pk_detail()), "/open/jobs/2719": (200, _pk_detail("Полный день")),
                  "/open/jobs/": (200, _pk_listing(rows))})
    jobs, _ = _run("potok", _row("potok", "skyengteam", name="Skyeng"), http)
    assert [(j["title"], j["remote"]) for j in jobs] == [("Teacher", True), ("Dev", None)]
    assert jobs[0]["url"] == "https://skyengteam.potok.io/open/jobs/2718" and jobs[0]["company"] == "Skyeng"
    assert "Teach English" in jobs[0]["description"] and "x()" not in jobs[0]["description"]
    assert _hosts(http) == {"skyengteam.potok.io"}


def test_potok_empty_board_not_the_career_page_and_refusal():
    assert _run("potok", _row("potok", "acme"), _stub({"/open/jobs/": (200, _pk_listing([]))}))[0] == []
    jobs, stats = _run("potok", _row("potok", "acme"), _stub({"/open/jobs/": (200, _pk_listing([("/open/jobs/1", "A", "")], wrapper=False))}))
    assert jobs == [] and stats.get("ok", 0) == 0  # a 200 without the postings wrapper fails the board
    jobs, stats = _run("potok", _row("potok", "acme"), _stub({"/open/jobs/": (403, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    rows = [("/open/jobs/1", "A", ""), ("/open/jobs/2", "B", "")]
    http = _stub({"/open/jobs/1": (429, None), "/open/jobs/2": (200, _pk_detail()), "/open/jobs/": (200, _pk_listing(rows))})
    jobs, _ = _run("potok", _row("potok", "acme"), http)
    assert [j["title"] for j in jobs] == ["A", "B"] and not any(c[0].endswith("/open/jobs/2") for c in http.calls)  # listed, not opened


def test_potok_off_host_links_are_never_fetched_and_markup_is_hostile():
    rows = [("https://evil.example/open/jobs/9", "Evil", ""), ("/open/jobs/1", "A", "")]
    http = _stub({"/open/jobs/1": (200, _pk_detail()), "/open/jobs/": (200, _pk_listing(rows))})
    jobs, _ = _run("potok", _row("potok", "acme"), http)
    assert _hosts(http) == {"acme.potok.io"} and all(urlsplit(j["url"]).scheme == "https" for j in jobs)
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"acme.potok.io"}
    deep = _pk_listing([("/open/jobs/1", "A", "")]).replace("</body>", "<div class=posting>" * 3000 + "</body>")
    t0 = time.perf_counter()
    jobs, _ = _run("potok", _row("potok", "acme"), _stub({"/open/jobs/": (200, deep)}))
    assert time.perf_counter() - t0 < 1.0 and [j["title"] for j in jobs] == ["A"]


# ------------------------------------------------------------------ Huntflow
def _hf_list(items):
    """A devalue array: index 0 the root, objects/arrays hold indices, scalars are stored once."""
    raw: list = [{"data": 1}, {"vacancies": 2}, {"items": 3}, []]
    for it in items:
        idx = []
        obj: dict = {}
        for k, v in it.items():
            raw.append(v)
            obj[k] = len(raw) - 1
        raw.append(obj)
        idx.append(len(raw) - 1)
        raw[3].append(idx[0])
    return raw


def _hf_detail(position="Senior Backend Developer", city="Москва", money="от 300 000", body="<p>Code.</p><script>z()</script>"):
    return [{"data": 1}, ["ShallowReactive", 2], {"vacancy": 3}, {"position": 4, "city": 5, "money": 6, "intro": 7, "body": 8,
                                                                "requirements": 9, "conditions": 7},
            position, city, money, "<p>Intro.</p>", body, "<ul><li>Python</li></ul>"]


def test_huntflow_unflatten_resolves_references_wrappers_and_cycles():
    raw = [{"data": 1}, {"vacancies": 2}, {"items": 3}, [4], {"id": 5, "slug": 6, "position": 7, "city": 8}, 26610, "slug", "Dev", None]
    assert w4._unflatten(raw) == {"data": {"vacancies": {"items": [{"id": 26610, "slug": "slug", "position": "Dev", "city": None}]}}}
    assert w4._unflatten([{"data": 1}, ["ShallowReactive", 2], "ok"]) == {"data": "ok"}
    cyc = w4._unflatten([{"a": 1}, {"b": 0, "c": 2}, "ok"])  # a reference back to the root must not recurse forever
    assert cyc == {"a": {"b": None, "c": "ok"}}
    chain = [{"n": i + 1} for i in range(5000)] + ["end"]
    assert w4._unflatten(chain) is not None  # a hostile deep chain is cut, not a RecursionError
    shared = [{"a": 1, "b": 1}] + [{"a": i + 2, "b": i + 2} for i in range(60)] + ["leaf"]
    t0 = time.perf_counter()
    w4._find_vacancy(w4._unflatten(shared))  # a diamond DAG of 2^60 paths is walked once per node
    assert time.perf_counter() - t0 < 1.0


def test_huntflow_happy_path_archived_and_detail_fields():
    items = [{"id": 26610, "slug": "senior-backend-developer-2", "position": "Senior Backend Developer", "archived_at": None},
             {"id": 2, "slug": "closed", "position": "Closed", "archived_at": "2026-01-01"},
             {"id": 3, "slug": "../evil", "position": "Bad slug", "archived_at": None},
             {"id": 4, "slug": "no-detail", "position": "No Detail", "archived_at": None, "city": "Казань"}]
    http = _stub({"/vacancy/senior-backend-developer-2/_payload.json": (200, _hf_detail()), "/vacancy/no-detail/": (404, None),
                  "/_payload.json": (200, _hf_list(items))})
    jobs, _ = _run("huntflow", _row("huntflow", "tripster", name="Tripster"), http)
    assert [j["title"] for j in jobs] == ["Senior Backend Developer", "No Detail"]
    j = jobs[0]
    assert j["url"] == "https://tripster.huntflow.io/vacancy/senior-backend-developer-2" and j["company"] == "Tripster"
    assert j["location"] == "Москва" and "300 000" in j["description"] and "Python" in j["description"] and "z()" not in j["description"]
    assert jobs[1]["location"] == "Казань" and jobs[1]["description"] == ""
    assert _hosts(http) == {"tripster.huntflow.io"}


def test_huntflow_refusal_bad_payload_and_title_filter():
    items = [{"id": 1, "slug": "a", "position": "A", "archived_at": None}, {"id": 2, "slug": "b", "position": "B", "archived_at": None}]
    http = _stub({"/vacancy/a/": (403, None), "/vacancy/b/": (200, _hf_detail()), "/_payload.json": (200, _hf_list(items))})
    jobs, _ = _run("huntflow", _row("huntflow", "banki"), http)
    assert [j["title"] for j in jobs] == ["A", "B"] and not any("/vacancy/b/" in c[0] for c in http.calls)  # refusal ends the details
    http = _stub({"/vacancy/a/": (200, [{"x": 1}, "junk"]), "/vacancy/b/": (200, _hf_detail("B2")), "/_payload.json": (200, _hf_list(items))})
    jobs, _ = _run("huntflow", _row("huntflow", "banki"), http)
    assert [j["title"] for j in jobs] == ["A", "B"] and "Code" in jobs[1]["description"]  # one bad payload costs only its description
    for bad in ((200, {"data": {}}), (200, "[]"), (500, None), (403, None)):
        jobs, stats = _run("huntflow", _row("huntflow", "banki"), _stub({"/_payload.json": bad}))
        assert jobs == [] and stats.get("ok", 0) == 0
    http = _stub({"/_payload.json": (200, _hf_list(items))})
    assert [j["title"] for j in _run("huntflow", _row("huntflow", "banki", title_ok=lambda t: False), http)[0]] == ["A", "B"]
    assert not any("/vacancy/" in c[0] for c in http.calls)


def test_huntflow_host_rule_and_hostile_payload():
    http = _stub({})
    assert _run("huntflow", _row("huntflow", "evil.example/x"), http)[0] == [] and http.calls == []
    hostile = [{"data": 1}, {"vacancies": 2}, {"items": 3}, [4, 3], {"id": 5, "slug": 6, "position": 6, "archived_at": 0}, 1, "x/y"]
    jobs, _ = _run("huntflow", _row("huntflow", "banki"), _stub({"/_payload.json": (200, hostile)}))
    assert jobs == []  # a cyclic payload and a bad slug cost only their own postings
    assert {urlsplit(j["url"]).hostname for j in _run("huntflow", _row("huntflow", "banki"), _stub({
        "/_payload.json": (200, _hf_list([{"id": 1, "slug": "a", "position": "A", "archived_at": None}]))}))[0]} == {"banki.huntflow.io"}
