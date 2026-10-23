"""Offline tests for hunt_kit/kit_market5.py (GeekHunter, Likeit, Strider, micro1, Alignerr).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT) and written fresh.
Nothing touches the network. Energy Jobline is not read (Googlebot-only pages): its link is reported as unsupported.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kb = _load("kit_market5_under_test", "kit_market5.py")
hk = _load("hunt_kit_market5_under_test", "hunt_kit.py")
F = kb.ATS_FETCHERS
NAMES = ("alignerr", "geekhunter", "likeit", "micro1", "onstrider")
ALLOWED_KW = {"method", "data", "headers", "as_json", "once", "max_bytes"}


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers)."""
    calls: list = []
    kws: list = []

    def http(url, **kw):
        calls.append((url, kw.get("method", "GET"), kw.get("headers") or {}))
        kws.append(kw)
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, kw.get("headers") or {}) if callable(resp) else resp
        return 404, None

    http.calls = calls
    http.kws = kws
    return http


def _run(name, row, http):
    stats: dict = {}
    jobs = F[name](row, http, stats)
    assert all(set(k) <= ALLOWED_KW for k in http.kws)  # a keyword the real http() lacks is a run-time TypeError
    return jobs, stats


def _row(ats, token="all", **kw):
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def _hosts(http):
    return {urlsplit(u).hostname for u, *_ in http.calls}


def _job_hosts(jobs):
    return {urlsplit(j["url"]).hostname for j in jobs}


def _ld(**kw):
    return '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", **kw}).replace("</", "<\\/") + "</script>"


def test_wiring():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_market5" in hk.ATS_MODULES and set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_market5.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_market5.py"' in served
    assert all(ats in hk._merged("ATS_FETCHERS", hk.ATS_MODULES) for ats in NAMES)


def test_company_rows_exist_for_every_type():
    rows = hk.load_companies(KIT_DIR / "companies.json")
    assert {r["ats"] for r in rows} >= set(NAMES)


# ----------------------------------------------------------------------------- host rule
def test_host_rule_reads_each_link_and_reports_energyjobline_as_unsupported():
    b = hk.board_from_job_url
    assert b("https://www.geekhunter.com/pt/omega-gestao-educacional-ltda/jobs/dev-123")["token"] == "omega-gestao-educacional-ltda"
    assert b("https://www.geekhunter.com/pt/jobs")["token"] == ""
    assert b("https://rekrymesta.likeit.fi/jsp/duuni/AdvertShow.jsp?id=8074439&guest=1")["token"] == "rekrymesta"
    assert b("https://www.likeit.fi/")["ats"] == "" or b("https://www.likeit.fi/")["token"] == ""
    for url, ats in (("https://www.onstrider.com/jobs/dev-12ab34cd", "onstrider"),
                     ("https://jobs.micro1.ai/post/0f0f0f0f-0f0f-0f0f-0f0f-0f0f0f0f0f0f", "micro1"),
                     ("https://www.alignerr.com/jobs/abc123", "alignerr")):
        r = b(url)
        assert (r["kind"], r["ats"], r["token"]) == ("ats", ats, "")
    assert b("https://www.energyjobline.com/job/rig-engineer-12345")["kind"] == "unsupported"
    assert b("https://evil.example/geekhunter.com/pt/x/jobs/y")["kind"] == "other"


# ----------------------------------------------------------------------------- GeekHunter
_GH_LIST = ('<html><script id="itemList" type="application/ld+json">' + json.dumps({"itemListElement": [
    {"url": "https://www.geekhunter.com/pt/acme/jobs/dev-backend"},
    {"url": "https://www.geekhunter.com/pt/acme/jobs/dev-remoto"},
    {"url": "https://evil.example/pt/acme/jobs/offhost"},
    {"url": "http://www.geekhunter.com/pt/acme/jobs/plain-http"},
    {"url": "https://www.geekhunter.com/pt/acme/jobs/broken"},
    {"url": "https://www.geekhunter.com/pt/acme/jobs/dev-backend"},
    {"url": "https://user@www.geekhunter.com/pt/acme/jobs/userinfo"},
    "nonsense", {"url": None},
]}) + "</script></html>")


def _gh_http(**over):
    routes = {
        "/pt/acme/jobs/dev-backend": (200, "<html>" + _ld(
            title="Dev Backend", description="&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;script&gt;steal()&lt;/script&gt;",
            datePosted="2026-04-02", employmentType=["FULL_TIME"], hiringOrganization={"name": "Acme SA"},
            jobLocation=[{"address": {"addressLocality": "Sao Paulo", "addressRegion": "SP", "addressCountry": "BR"}}]) + "</html>"),
        "/pt/acme/jobs/dev-remoto": (200, "<html>" + _ld(title="Dev Remoto", description="Home office", datePosted="2026-04-03") + "</html>"),
        "/pt/acme/jobs/broken": (200, '<script type="application/ld+json">{not json</script>'),
        "/pt/acme/jobs": (200, _GH_LIST),
    }
    routes.update(over)
    return _stub(routes)


def test_geekhunter_happy_path_one_bad_posting_and_only_the_boards_host():
    http = _gh_http()
    jobs, stats = _run("geekhunter", _row("geekhunter", "acme"), http)
    assert [j["title"] for j in jobs] == ["Dev Backend", "Dev Remoto"]
    a, b = jobs
    assert (a["company"], a["location"], a["posted"], a["remote"]) == ("Acme SA", "Sao Paulo, SP, BR", "2026-04-02", None)
    assert "steal" not in a["description"] and "<" not in a["description"] and "Build" in a["description"]
    assert b["remote"] is True and b["company"] == "Geekhunter Co"
    assert _hosts(http) == {"www.geekhunter.com"} and _job_hosts(jobs) == {"www.geekhunter.com"}
    assert all(u.startswith("https://") for u in (j["url"] for j in jobs))
    assert (stats["tried"], stats["ok"], stats["jobs"]) == (1, 1, 2)


def test_geekhunter_failed_listing_fails_the_board_and_refusal_ends_the_details():
    jobs, stats = _run("geekhunter", _row("geekhunter", "acme"), _stub({"/jobs": (403, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    http = _gh_http(**{"/pt/acme/jobs/dev-backend": (429, None)})
    jobs, _ = _run("geekhunter", _row("geekhunter", "acme"), http)
    assert jobs == [] and len([u for u, *_ in http.calls if "/jobs/" in u]) == 1  # the first refusal ends the board
    assert not F["geekhunter"](_row("geekhunter", "../x"), http, {})


def test_geekhunter_empty_or_garbage_item_list_is_an_empty_board_not_a_crash():
    for page in ("<html></html>", '<script id="itemList">{broken</script>', '<script id="itemList">[]</script>'):
        jobs, _ = _run("geekhunter", _row("geekhunter", "acme"), _stub({"/jobs": (200, page)}))
        assert jobs == []


def test_geekhunter_hostile_markup_stays_fast():
    page = "<div>" * 150 + "<b/>" * 100_000 + "<!-- x " * 1000 + "-->" + _GH_LIST
    t = time.time()
    jobs, _ = _run("geekhunter", _row("geekhunter", "acme"), _gh_http(**{"/pt/acme/jobs": (200, page)}))
    assert time.time() - t < 10 and len(jobs) == 2


# ----------------------------------------------------------------------------- Likeit
def _item(title, link, desc="", cat="", pub="Tue, 03 Mar 2026 10:00:00 +0000", dc=""):
    return (f"<item><title>{title}</title><link>{link}</link><description>{desc}</description><category>{cat}</category>"
            f"<pubDate>{pub}</pubDate>{dc}</item>")


def _feed(*items):
    return ('<?xml version="1.0"?><rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>Board</title>'
            "<link>https://rekrymesta.likeit.fi/jsp/duuni/AdvertList.jsp?guest=1</link>" + "".join(items) + "</channel></rss>")


def test_likeit_happy_path_skips_bad_items_and_foreign_links():
    base = "https://rekrymesta.likeit.fi/jsp/duuni/AdvertShow.jsp"
    xml = _feed(
        _item("Nurse", f"{base}?id=8074439&amp;guest=1", "&lt;p&gt;Care for people&lt;/p&gt;&lt;script&gt;x()&lt;/script&gt;", "Uusimaa, Helsinki",
              dc="<dc:date>2026-03-04T08:00:00Z</dc:date>"),
        _item("Cook", f"{base}?guest=1&amp;id=2145755", "Kitchen", "Remote"),
        _item("No id", "https://rekrymesta.likeit.fi/jsp/duuni/AdvertList.jsp?guest=1"),
        _item("Off host", "https://evil.example/jsp/duuni/AdvertShow.jsp?id=9"),
        _item("Plain http", "http://rekrymesta.likeit.fi/jsp/duuni/AdvertShow.jsp?id=10"),
        _item("Other tenant", "https://other.likeit.fi/jsp/duuni/AdvertShow.jsp?id=11"),
        _item("Dup", f"{base}?id=8074439"),
        _item("", f"{base}?id=12"))
    http = _stub({"AdvertRss.jsp": (200, xml)})
    jobs, stats = _run("likeit", _row("likeit", "rekrymesta"), http)
    assert [j["title"] for j in jobs] == ["Nurse", "Cook"]
    assert (jobs[0]["posted"], jobs[0]["location"], jobs[0]["extra"]["job_id"]) == ("2026-03-04", "Uusimaa, Helsinki", "8074439")
    assert jobs[1]["posted"] == "2026-03-03" and jobs[1]["remote"] is True and jobs[1]["extra"]["job_id"] == "2145755"
    assert "x()" not in jobs[0]["description"] and "Care for people" in jobs[0]["description"]
    assert _hosts(http) == {"rekrymesta.likeit.fi"} and _job_hosts(jobs) == {"rekrymesta.likeit.fi"}
    assert len(http.calls) == 1 and stats["ok"] == 1


def test_likeit_refusal_doctype_and_bad_token_return_nothing():
    for resp in ((403, None), (200, '<!DOCTYPE x [<!ENTITY a "b">]><rss/>'), (200, "<rss"), (200, 5)):
        jobs, stats = _run("likeit", _row("likeit", "rekrymesta"), _stub({"AdvertRss": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0
    http = _stub({"AdvertRss": (200, _feed())})
    assert F["likeit"](_row("likeit", "a.evil.example/x"), http, {}) == [] and not http.calls


# ----------------------------------------------------------------------------- Strider (onstrider)
_SM = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
       + "".join(f"<url><loc>{u}</loc></url>" for u in (
           "https://www.onstrider.com/jobs/senior-dev-12ab34cd", "https://www.onstrider.com/jobs/closed-dev-ffffffff",
           "https://www.onstrider.com/jobs/noattr-dev-00000001", "https://www.onstrider.com/pt/jobs/senior-dev-12ab34cd",
           "https://www.onstrider.com/jobs/preview-slug-abc", "https://www.onstrider.com/jobs", "https://evil.example/jobs/x-12345678",
           "http://www.onstrider.com/jobs/plain-http-12345678", "https://www.onstrider.com/jobs/bad-json-0badbad0",
           "https://www.onstrider.com/jobs/senior-dev-12ab34cd")) + "</urlset>")


def _os_page(open_="1", **kw):
    body = {"title": "Senior Dev", "description": "<p>Great &amp; fun</p><script>x()</script>", "datePosted": "2026-05-06",
            "jobLocationType": "TELECOMMUTE", "employmentType": "FULL_TIME", "identifier": {"value": "uuid-1"},
            "applicantLocationRequirements": [{"name": "CO"}, {"name": "AR"}, {"name": "Chile"}, {}]}
    body.update(kw)
    attr = f' data-position-open="{open_}"' if open_ is not None else ""
    return f"<html><div class=hero{attr}></div>{_ld(**body)}</html>"


def _os_http(**over):
    routes = {
        "closed-dev-ffffffff": (200, _os_page("0")),
        "noattr-dev-00000001": (200, _os_page(None)),
        "bad-json-0badbad0": (200, '<div data-position-open="1"></div><script type="application/ld+json">{x</script>'),
        "senior-dev-12ab34cd": (200, _os_page()),
        "sitemap.xml": (200, _SM),
    }
    routes.update(over)
    return _stub(routes)


def test_onstrider_reads_open_vacancies_only_on_the_canonical_host():
    http = _os_http()
    jobs, stats = _run("onstrider", _row("onstrider"), http)
    assert len(jobs) == 1
    j = jobs[0]
    assert (j["title"], j["url"], j["posted"], j["remote"]) == ("Senior Dev", "https://www.onstrider.com/jobs/senior-dev-12ab34cd", "2026-05-06", True)
    assert j["location"] == "Colombia, Argentina, Chile" and j["extra"]["job_id"] == "uuid-1" and "x()" not in j["description"]
    fetched = [u for u, *_ in http.calls]
    assert _hosts(http) == {"www.onstrider.com"} and _job_hosts(jobs) == {"www.onstrider.com"}
    assert not any(x in " ".join(fetched) for x in ("/pt/", "preview-slug", "evil.example", "plain-http"))
    assert sum(u.endswith("senior-dev-12ab34cd") for u in fetched) == 1 and stats["ok"] == 1


def test_onstrider_refusal_ends_the_board_and_a_bad_sitemap_fails_it():
    http = _os_http(**{"closed-dev-ffffffff": (429, None), "senior-dev-12ab34cd": (429, None)})
    jobs, _ = _run("onstrider", _row("onstrider"), http)
    assert jobs == [] and len([u for u, *_ in http.calls if "/jobs/" in u]) == 1
    for resp in ((403, None), (200, '<!DOCTYPE x [<!ENTITY a "b">]><urlset/>'), (200, "<urlset")):
        jobs, stats = _run("onstrider", _row("onstrider"), _os_http(**{"sitemap.xml": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0


def test_onstrider_posting_without_identifier_or_with_list_employment_type():
    http = _os_http(**{"senior-dev-12ab34cd": (200, _os_page(identifier={}))})
    assert _run("onstrider", _row("onstrider"), http)[0] == []
    http = _os_http(**{"senior-dev-12ab34cd": (200, _os_page(employmentType=["PART_TIME", "CONTRACTOR"], jobLocationType=""))})
    jobs, _ = _run("onstrider", _row("onstrider"), http)
    assert jobs[0]["extra"]["employment_type"] == "PART_TIME" and jobs[0]["remote"] is None


# ----------------------------------------------------------------------------- micro1
_UU = "0f0f0f0f-0f0f-0f0f-0f0f-0f0f0f0f0f0f"
_UU2 = "1a1a1a1a-1a1a-1a1a-1a1a-1a1a1a1a1a1a"
_UU3 = "2b2b2b2b-2b2b-2b2b-2b2b-2b2b2b2b2b2b"
_UU4 = "3c3c3c3c-3c3c-3c3c-3c3c-3c3c3c3c3c3c"


def _m1_page(status="open", desc_ref=True, **kw):
    desc = "<p>Label data &amp; more</p><script>x()</script>"
    d = {"client_job_id": "job-1", "job_role_name": "AI Trainer", "job_description": "$b" if desc_ref else desc, "job_status": status,
         "create_datetime": "2026-06-07 08:09:10", "required_skills": ["Python"], "location_type": "remote", "location_name": "Worldwide"}
    d.update(kw)
    flight = '0:["$","div"]\n' + 'a:{"props":{"data":' + json.dumps(d) + "}}\n" + f"b:T{len(desc.encode()):x},{desc}\n"
    return "<html><script>self.__next_f.push([1," + json.dumps(flight) + "])</script></html>"


def _m1_http(**over):
    sm = ('<?xml version="1.0"?><urlset>' + "".join(f"<url><loc>{u}</loc></url>" for u in (
        f"https://jobs.micro1.ai/post/{_UU}", f"https://jobs.micro1.ai/post/{_UU2}", f"https://jobs.micro1.ai/post/{_UU3}",
        f"https://jobs.micro1.ai/post/{_UU4}", "https://jobs.micro1.ai/post/not-a-uuid", "https://jobs.micro1.ai/",
        f"https://evil.example/post/{_UU}", f"http://jobs.micro1.ai/post/{_UU}", f"https://req.micro1.ai/post/{_UU}",
        f"https://jobs.micro1.ai/post/{_UU}/extra")) + "</urlset>")
    routes = {
        _UU2: (200, _m1_page("closed")),
        _UU3: (200, '<script>self.__next_f.push([1,"a:{\\"data\\":{\\"client_job_id\\":\\"x\\" broken"])</script>'),
        _UU4: (200, "<html>no flight here</html>"),
        _UU: (200, _m1_page()),
        "sitemap.xml": (200, sm),
    }
    routes.update(over)
    return _stub(routes)


def test_micro1_reads_open_posts_resolves_the_description_row_and_skips_bad_ones():
    http = _m1_http()
    jobs, stats = _run("micro1", _row("micro1"), http)
    assert len(jobs) == 1
    j = jobs[0]
    assert (j["title"], j["url"], j["posted"], j["remote"], j["location"]) == (
        "AI Trainer", f"https://jobs.micro1.ai/post/{_UU}", "2026-06-07", True, "Worldwide")
    assert j["description"] == "Label data & more" and j["extra"]["job_id"] == "job-1" and j["extra"]["work_mode"] == "remote"
    fetched = " ".join(u for u, *_ in http.calls)
    assert _hosts(http) == {"jobs.micro1.ai"} and _job_hosts(jobs) == {"jobs.micro1.ai"}
    assert not any(x in fetched for x in ("not-a-uuid", "evil.example", "http://", "req.micro1.ai", "/extra"))
    assert stats["ok"] == 1


def test_micro1_inline_description_and_refusals():
    jobs, _ = _run("micro1", _row("micro1"), _m1_http(**{_UU: (200, _m1_page(desc_ref=False, location_type="onsite", location_name="Paris"))}))
    assert jobs[0]["description"] == "Label data & more" and jobs[0]["remote"] is None and jobs[0]["extra"]["work_mode"] == "onsite"
    http = _m1_http(**{_UU: (403, None)})
    jobs, _ = _run("micro1", _row("micro1"), http)
    assert jobs == [] and len([u for u, *_ in http.calls if "/post/" in u]) == 1
    for resp in ((403, None), (200, '<!DOCTYPE x [<!ENTITY a "b">]><urlset/>')):
        assert _run("micro1", _row("micro1"), _m1_http(**{"sitemap.xml": resp}))[1].get("ok", 0) == 0


def test_micro1_flight_row_with_a_declared_length_past_the_page_is_ignored():
    flight = 'a:{"props":{"data":{"client_job_id":"j","job_role_name":"T","job_description":"$b","job_status":"open"}}}\nb:Tfffffff,short'
    page = "<script>self.__next_f.push([1," + json.dumps(flight) + "])</script>"
    jobs, _ = _run("micro1", _row("micro1"), _m1_http(**{_UU: (200, page)}))
    assert len(jobs) == 1 and jobs[0]["description"] == ""


# ----------------------------------------------------------------------------- Alignerr
def _next(page_props):
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": page_props}}) + "</script></html>"


def _al_http(**over):
    listing = _next({"initialJobs": [
        {"id": "job1", "title": "Code Reviewer", "location": "Remote"}, {"id": "gone1", "title": "Closed Role", "location": "Remote"},
        {"id": "../evil", "title": "Bad id"}, {"id": "", "title": "No id"}, "junk", {"id": "job1", "title": "Dup"},
        {"id": "job2", "title": "Math Expert", "location": "Remote"}]})
    routes = {
        "/jobs/job1": (200, _next({"job": {"name": "Code Reviewer II", "htmlLongDescription": "<p>Review &amp; grade</p><script>x()</script>",
                                           "isActive": True, "firstPostDate": "2026-07-08T01:02:03Z", "jobType": "CONTRACTOR", "location": "US"}})),
        "/jobs/gone1": (200, _next({"job": {"name": "Closed Role", "isActive": False}})),
        "/jobs/job2": (200, _next({"job": {"name": "Math Expert", "shortDescription": "<b>Short</b>", "isActive": True,
                                           "createdAt": "2026-07-09", "jobType": "FULL_TIME"}})),
        "alignerr.com/jobs": (200, listing),
    }
    routes.update(over)
    return _stub(routes)


def test_alignerr_happy_path_drops_closed_and_bad_ids_and_stays_on_the_site():
    http = _al_http()
    jobs, stats = _run("alignerr", _row("alignerr"), http)
    assert [j["title"] for j in jobs] == ["Code Reviewer II", "Math Expert"]
    a, b = jobs
    assert (a["url"], a["posted"], a["location"], a["remote"], a["extra"]["employment_type"]) == (
        "https://www.alignerr.com/jobs/job1", "2026-07-08", "Remote", True, "contract")
    assert a["description"] == "Review & grade" and b["description"] == "Short" and b["posted"] == "2026-07-09"
    assert _hosts(http) == {"www.alignerr.com"} and _job_hosts(jobs) == {"www.alignerr.com"}
    assert not any("evil" in u for u, *_ in http.calls) and stats["ok"] == 1


def test_alignerr_refusal_ends_the_details_but_keeps_the_listed_cards():
    http = _al_http(**{"/jobs/job1": (429, None)})
    jobs, _ = _run("alignerr", _row("alignerr"), http)
    assert [j["title"] for j in jobs] == ["Code Reviewer", "Closed Role", "Math Expert"]
    assert all(j["description"] == "" for j in jobs) and len([u for u, *_ in http.calls if "/jobs/" in u]) == 1


def test_alignerr_missing_next_data_or_listing_fails_the_board():
    for resp in ((403, None), (200, "<html>nothing</html>"), (200, _next({"initialJobs": "x"})), (200, '<script id="__NEXT_DATA__">{"props":')):
        jobs, stats = _run("alignerr", _row("alignerr"), _al_http(**{"alignerr.com/jobs": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0
    jobs, _ = _run("alignerr", _row("alignerr"), _al_http(**{"/jobs/job1": (200, "<html>no data</html>")}))
    assert "Code Reviewer" not in [j["title"] for j in jobs]  # a page that was opened and holds no job is not listed


def test_title_ok_gates_detail_requests():
    http = _al_http()
    jobs, _ = _run("alignerr", _row("alignerr", title_ok=lambda t: "math" in t.lower()), http)
    assert [u for u, *_ in http.calls if "/jobs/" in u] == ["https://www.alignerr.com/jobs/job2"]
    assert [j["title"] for j in jobs] == ["Code Reviewer", "Closed Role", "Math Expert"]
