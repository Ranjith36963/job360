"""Offline tests for hunt_kit/kit_ats_misc1.py (Betterteam, CATS, ClinchTalent, Crelate, eRecruiter, gr8people,
HR-Manager, iSmartRecruit, Loxo, Mindsight).

Fixtures are shaped like the test data in the MIT freehire adapters (internal/ingest/sources/<name>_test.go and
testdata/): same selectors, ids, and field names. Nothing touches the network.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


m1 = _load("kit_ats_misc1_under_test", "kit_ats_misc1.py")
F = m1.ATS_FETCHERS
JOB_KEYS = {"title", "company", "url", "location", "posted", "description", "source", "board", "remote", "visa_flag", "extra"}
ALL = ["betterteam", "catsone", "clinch", "crelate", "erecruiter", "gr8people", "hrmanager", "ismartrecruit", "loxo", "mindsight"]


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, method, body)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        body = json.loads(data) if data else None
        calls.append((url, method, body, headers or {}))
        for frag, resp in routes.items():
            if frag in url:
                st, payload = resp(url, method, body) if callable(resp) else resp
                if st != 200:
                    return st, None
                if as_json and isinstance(payload, str):
                    return st, json.loads(payload)
                if not as_json and not isinstance(payload, str):
                    return st, json.dumps(payload)
                return st, payload
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    jobs = F[name](dict(row, ats=name), http, stats)
    return jobs, stats


def _check_shape(jobs):
    for j in jobs:
        assert set(j) == JOB_KEYS
        assert j["title"] and j["url"].startswith("https://")


# ----------------------------------------------------------------------------- Betterteam
def _bt_detail(title, desc, posted, city, country):
    ld = {"@context": "https://schema.org/", "@type": "JobPosting", "title": title, "description": desc, "datePosted": posted,
          "hiringOrganization": {"@type": "Organization", "name": "110 Grill"},
          "jobLocation": {"@type": "Place", "address": {"addressLocality": city, "addressCountry": country}}}
    return '<html><head><script type="application/ld+json">%s</script></head></html>' % json.dumps(ld)


def test_betterteam_happy_path():
    listing = '<ul><li><a href="/bartender-21">A job</a></li><li><a href="/bartender-21">dup</a></li><li><a href="/about-us">About</a></li></ul>'
    http = _stub({"/bartender-21": (200, _bt_detail("Bartender", "&lt;div&gt;Pour drinks.&lt;/div&gt;", "2026-06-09T15:07:41.914471Z", "North Conway", "US")),
                  "110grill.betterteam.com": (200, listing)})
    jobs, stats = _run("betterteam", {"name": "110 Grill", "token": "110grill"}, http)
    assert len(jobs) == 1 and stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    _check_shape(jobs)
    assert j["url"] == "https://110grill.betterteam.com/bartender-21" and j["company"] == "110 Grill"
    assert j["location"] == "North Conway, US" and j["posted"] == "2026-06-09" and "Pour drinks" in j["description"]
    assert j["extra"]["id"] == "bartender-21" and j["remote"] is None
    assert len(http.calls) == 2  # listing + one detail (duplicate and /about-us never fetched)


def test_betterteam_empty_nonok_garbage():
    jobs, stats = _run("betterteam", {"name": "x", "token": "x"}, _stub({"betterteam.com": (200, "<ul></ul>")}))
    assert jobs == [] and stats["ok"] == 1
    http = _stub({"betterteam.com": (500, None)})
    jobs, stats = _run("betterteam", {"name": "x", "token": "x"}, http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1
    jobs, _ = _run("betterteam", {"name": "x", "token": "../bad host"}, _stub({}))
    assert jobs == []


# ----------------------------------------------------------------------------- CATS (catsone)
def _cats_listing(*rows):
    out = '<div class="jobs-table"><div class="table-header"><div class="header-cell">Job Title</div></div>'
    for slug, title, loc in rows:
        out += ('<a class="table-row" href="/careers/90438/jobs/%s"><div class="data-cell title-cell">%s</div>'
                '<div class="data-cell" data-label="Category">General</div><div class="data-cell" data-label="Location">%s</div></a>') % (slug, title, loc)
    return out + "</div>"


CATS_DETAIL = ('<div class="job-description-container"><a class="view-all-jobs" href="/careers/90438">View all jobs</a>'
               '<div><div class="job-header"><h2>Ignored Header</h2></div><div class="job-description"><p><strong>About</strong></p>'
               '<p>Sell things.</p><script>alert(1)</script></div></div></div>')


def test_catsone_matches_freehire_listing_then_detail():
    http = _stub({"/jobs/16831828": (200, CATS_DETAIL), "/jobs/16823910": (200, CATS_DETAIL),
                  "sphereinc.catsone.com/careers": (200, _cats_listing(("16831828-sdr", "Sales Development Representative (SDR)", "Brazil, Remote"),
                                                                        ("16823910-ai", "AI Engineer", "Kyiv"),
                                                                        ("16831828-sdr", "dup row", "x")))})
    jobs, stats = _run("catsone", {"name": "Sphere", "token": "sphereinc.catsone.com"}, http)
    _check_shape(jobs)
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    by = {j["extra"]["id"]: j for j in jobs}
    j = by["16831828"]
    assert j["url"] == "https://sphereinc.catsone.com/careers/90438/jobs/16831828-sdr"
    assert j["title"] == "Sales Development Representative (SDR)" and j["company"] == "Sphere"
    assert j["location"] == "Brazil, Remote" and j["remote"] is True
    assert "Sell things" in j["description"] and "alert" not in j["description"]
    assert "View all jobs" not in j["description"] and "Ignored Header" not in j["description"]
    assert http.calls[0][0] == "https://sphereinc.catsone.com/careers"  # GET /careers, nothing else first


def test_catsone_skips_rows_without_numeric_id_and_drops_failed_detail():
    listing = _cats_listing(("16831828-sdr", "Keep", "Kyiv")).replace("</div>", "", 0) + '<a class="table-row" href="/careers/90438"><div class="title-cell">No id</div></a>'
    http = _stub({"/jobs/16831828": (500, None), "acme.catsone.com/careers": (200, listing)})
    jobs, _ = _run("catsone", {"name": "A", "token": "acme.catsone.com"}, http)
    assert jobs == []  # catsone.go:55-57: a failed detail drops the posting; the id-less row never counts


def test_catsone_title_gate_keeps_listing_row_without_detail_request():
    http = _stub({"acme.catsone.com/careers": (200, _cats_listing(("1-a", "Cook", "Leeds")))})
    jobs, _ = _run("catsone", {"name": "A", "token": "acme.catsone.com", "title_ok": lambda t: False}, http)
    assert len(jobs) == 1 and jobs[0]["description"] == "" and len(http.calls) == 1


def test_catsone_empty_nonok_garbage():
    assert _run("catsone", {"name": "A", "token": "a.catsone.com"}, _stub({"catsone": (200, "<html></html>")}))[0] == []
    jobs, stats = _run("catsone", {"name": "A", "token": "a.catsone.com"}, _stub({"catsone": (403, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    assert _run("catsone", {"name": "A", "token": "a.catsone.com"}, _stub({"catsone": (200, "\x00<<a class='table-row' href=")}))[0] == []


# ----------------------------------------------------------------------------- ClinchTalent
def _sitemap(*locs):
    return '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>' % "".join(
        "<url><loc>%s</loc><lastmod>%s</lastmod></url>" % (u, "2026-07-01T10:00:00Z") for u in locs)


def test_clinch_happy_path_and_waf_latch():
    base = "https://careers.withwaymo.com"
    detail = '<div class="job-description"><p>Drive safely.</p><script>x()</script></div>'
    http = _stub({"/jobs/one-": (200, detail), "/jobs/two-": (202, None), "/jobs/three-": (200, detail),
                  "sitemap.xml": (200, _sitemap(base + "/", base + "/why-waymo", base + "/jobs/one-london-england-united-kingdom",
                                                base + "/jobs/two-warsaw-poland-0a1b2c3d-0000-1111-2222-333344445555",
                                                base + "/jobs/three-remote-germany"))})
    jobs, stats = _run("clinch", {"name": "Waymo", "token": "careers.withwaymo.com"}, http)
    _check_shape(jobs)
    assert [j["title"] for j in jobs] == ["One London England", "Two Warsaw", "Three Remote"]
    assert jobs[0]["location"] == "United Kingdom" and jobs[1]["location"] == "Poland" and jobs[0]["posted"] == "2026-07-01"
    assert jobs[1]["extra"]["id"] == "0a1b2c3d-0000-1111-2222-333344445555"
    assert "Drive safely" in jobs[0]["description"] and "x()" not in jobs[0]["description"]
    assert jobs[1]["description"] == "" and jobs[2]["description"] == ""  # a challenge latches detail fetching off
    assert len(http.calls) == 3 and jobs[2]["company"] == "Waymo"


def test_clinch_empty_nonok_garbage():
    assert _run("clinch", {"name": "W", "token": "c.example.com"}, _stub({"sitemap.xml": (200, _sitemap("https://c.example.com/about"))}))[0] == []
    jobs, stats = _run("clinch", {"name": "W", "token": "c.example.com"}, _stub({"sitemap.xml": (404, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("clinch", {"name": "W", "token": "c.example.com"}, _stub({"sitemap.xml": (200, "<<not xml")}))
    assert jobs == [] and stats.get("ok", 0) == 0


# ----------------------------------------------------------------------------- Crelate
CRELATE_BOARD = "careertree:ec546cba-84d5-4d8a-97e5-52e8ef47db08"


def test_crelate_happy_path_company_is_the_employer():
    feed = {"Jobs": [{"Id": "a1", "Title": "Home Health RN", "CompanyName": "Bayview Home Care", "Description": "<p>Home Health role.</p><script>alert(1)</script>",
                      "City": "Tampa", "State": "FL", "Country": "United States", "Url": "/not-a-real-job-code",
                      "JobCode": "not-a-real-job-code", "LastPostedOnDate": "2026-07-17T17:17:12.81Z"},
                     {"Id": "no-company-1", "Title": "Recruiter", "Description": "<p>Join us.</p>", "Country": "United States", "Url": "/abc123", "JobCode": "",
                      "LastPostedOnDate": "2026-07-01T00:00:00Z"},
                     {"Id": "", "Title": "No id", "JobCode": "zzz"}], "IsError": False, "ErrorMessage": None}
    http = _stub({"candidateportal/GetAllJobs": (200, feed)})
    jobs, stats = _run("crelate", {"name": "Career Tree Network", "token": CRELATE_BOARD}, http)
    _check_shape(jobs)
    assert stats["jobs"] == 2
    a, b = jobs
    assert a["company"] == "Bayview Home Care" and a["location"] == "Tampa, FL" and a["posted"] == "2026-07-17"
    assert a["url"] == "https://jobs.crelate.com/portal/careertree/job/not-a-real-job-code" and "alert" not in a["description"]
    assert b["company"] == "Career Tree Network" and b["location"] == "United States" and b["url"].endswith("/job/abc123")
    url = http.calls[0][0]
    assert "requestEnvelope=" in url and "ec546cba-84d5-4d8a-97e5-52e8ef47db08" in url and len(http.calls) == 1


def test_crelate_error_bad_board_garbage():
    assert _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (200, {"Jobs": [], "IsError": False})}))[0] == []
    jobs, stats = _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (200, {"Jobs": [], "IsError": True, "ErrorMessage": "no"})}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, stats = _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (500, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    for bad in ("", "nocolon", ":org", "slug:"):
        http = _stub({})
        assert _run("crelate", {"name": "x", "token": bad}, http)[0] == [] and http.calls == []
    assert _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (200, {"Jobs": ["junk", 3, None]})}))[0] == []


# ----------------------------------------------------------------------------- eRecruiter
CFG = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _jsonp(htm: str) -> str:
    return "(" + json.dumps({"htm": htm}) + ")"


def _er_detail(title, city):
    return ('<div class="job1"><div id="JobTitle">%s</div><div id="WorkPlace">Miejsce pracy: %s</div></div>'
            "<div id=\"t1\"><p><strong>Zakres</strong></p><ul><li>Rozmowy</li></ul><script>evil()</script></div>"
            '<div id="CompanyDescription"><p>Od 40 lat.</p></div>') % (title, city)


def test_erecruiter_happy_path_stops_at_total_and_keeps_multicity_variants():
    rows = ("<tr jobOfferId='1' offerId='4891462' externalJobOfferId='574507' externalJobOfferRegionId='309263' comId='20002054' skkResult='offer'>"
            "<td class='skk_positionName'>Staz</td><td>Wroclaw</td></tr>"
            "<tr jobOfferId='2' offerId='4882522' externalJobOfferId='570550' externalJobOfferRegionId='308328' comId='20002054' skkResult='offer'>"
            "<td class='skk_positionName'>Specjalista</td><td>Lodz</td></tr>"
            "<tr jobOfferId='2' offerId='4882523' externalJobOfferId='570550' externalJobOfferRegionId='308329' comId='20002054' skkResult='offer'>"
            "<td class='skk_positionName'>Specjalista</td><td>Krakow</td></tr>"
            "<tr tp='2' tr='3' pn='1' ps='15' style='display:none;'></tr>")
    http = _stub({"oid=4891462": (200, _er_detail("Staz w Dziale", "Wroclaw")), "oid=4882522": (200, _er_detail("Specjalista", "Lodz")),
                  "oid=4882523": (200, _er_detail("Specjalista", "Krakow")), "GetHtml.ashx": (200, _jsonp(rows))})
    jobs, stats = _run("erecruiter", {"name": "Echo Investment", "token": CFG}, http)
    _check_shape(jobs)
    assert len(jobs) == 3 and stats["jobs"] == 3
    assert sum("GetHtml.ashx" in c[0] for c in http.calls) == 1  # total reached after page 1
    by = {j["extra"]["id"]: j for j in jobs}
    j = by["4882523"]
    assert j["location"] == "Krakow" and j["company"] == "Echo Investment"
    assert j["url"] == "https://skk.erecruiter.pl/Offer.aspx?oid=4882523&cfg=%s&ejoId=570550&ejorId=308329&comId=20002054" % CFG
    assert "Rozmowy" in j["description"] and "Od 40 lat" in j["description"] and "evil" not in j["description"]
    assert by["4891462"]["title"] == "Staz w Dziale"


def test_erecruiter_detail_failure_and_bodyless_offer_are_dropped_marker_missing_walks_on():
    rows = ("<tr offerId='1' externalJobOfferId='1' externalJobOfferRegionId='1' comId='9' skkResult='offer'><td class='skk_positionName'>Dead</td><td>X</td></tr>"
            "<tr offerId='2' externalJobOfferId='1' externalJobOfferRegionId='1' comId='9' skkResult='offer'><td class='skk_positionName'>Empty</td><td>X</td></tr>"
            "<tr externalJobOfferId='1' skkResult='offer'><td class='skk_positionName'>No offer id</td></tr>")
    http = _stub({"oid=2": (200, "<div id='JobTitle'>Empty</div>"), "oid=1": (500, None), "pn=1": (200, _jsonp(rows)), "pn=2": (200, _jsonp(""))})
    jobs, _ = _run("erecruiter", {"name": "A", "token": CFG}, http)
    assert jobs == []
    assert any("pn=2" in c[0] for c in http.calls)  # no marker, so page 2 is read and found empty


def test_erecruiter_empty_nonok_garbage():
    assert _run("erecruiter", {"name": "A", "token": CFG}, _stub({"GetHtml.ashx": (200, "")}))[0] == []
    jobs, stats = _run("erecruiter", {"name": "A", "token": CFG}, _stub({"GetHtml.ashx": (503, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    assert _run("erecruiter", {"name": "A", "token": CFG}, _stub({"GetHtml.ashx": (200, "({broken")}))[1].get("ok", 0) == 0
    assert _run("erecruiter", {"name": "A", "token": "cfg"}, _stub({}))[0] == []


# ----------------------------------------------------------------------------- gr8people
HOME = '<script id="__NEXT_DATA__">{"props":{"session":{"token":"eyJnot-a-real-token"}}}</script>'


def _gr8_page(nodes, nxt=""):
    return {"data": {"searchJobs": {"results": {"nodes": nodes, "pageInfo": {"hasNextPage": bool(nxt), "endCursor": nxt}, "totalCount": 9}}}}


def test_gr8people_mints_token_pages_and_maps():
    n1 = {"key": "4709", "title": "FSR - Chicago, IL", "descriptionHTML": "<p>Serve customers.</p><script>x()</script>", "workplaceType": "ON_SITE",
          "postedOn": "2026-03-18T19:59:49.013Z", "positionType": {"name": "Full Time"}, "primaryPlace": {"name": "Chicago, IL, USA"},
          "places": {"nodes": [{"name": "Chicago, IL, USA"}, {"name": "Chicago, IL, USA"}, {"name": "Remote"}]}}
    n2 = {"key": "9", "title": "Analyst", "descriptionHTML": "", "workplaceType": "REMOTE", "positionType": None, "primaryPlace": {"name": "NYC"}, "places": {"nodes": []}}

    def gql(url, method, body):
        assert method == "POST" and body["variables"]["first"] == 50
        return (200, _gr8_page([n1], "c1")) if "after" not in body["variables"] else (200, _gr8_page([n2]))

    http = _stub({"/graphql": gql, "etrade.gr8people.com/jobs": (200, HOME)})
    jobs, stats = _run("gr8people", {"name": "Morgan Stanley E-TRADE", "token": "etrade.gr8people.com"}, http)
    _check_shape(jobs)
    assert len(jobs) == 2 and stats["jobs"] == 2
    a, b = jobs
    assert a["url"] == "https://etrade.gr8people.com/jobs/4709" and a["location"] == "Chicago, IL, USA; Remote" and a["remote"] is True
    assert a["extra"]["employment_type"] == "full_time" and a["posted"] == "2026-03-18" and "x()" not in a["description"]
    assert b["location"] == "NYC" and b["remote"] is True and b["company"] == "Morgan Stanley E-TRADE"
    posts = [c for c in http.calls if c[1] == "POST"]
    assert posts[0][3]["Authorization"] == "Bearer eyJnot-a-real-token" and "after" not in posts[0][2]["variables"]


def test_gr8people_empty_and_failures():
    http = _stub({"/graphql": (200, _gr8_page([])), "/jobs": (200, HOME)})
    assert _run("gr8people", {"name": "T", "token": "t.gr8people.com"}, http)[0] == []
    http = _stub({"/jobs": (200, "<html>no token</html>")})
    jobs, stats = _run("gr8people", {"name": "T", "token": "t.gr8people.com"}, http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1
    http = _stub({"/graphql": (500, None), "/jobs": (200, HOME)})
    assert _run("gr8people", {"name": "T", "token": "t.gr8people.com"}, http)[1].get("ok", 0) == 0
    # a later page failing keeps what was gathered
    n = {"key": "1", "title": "A", "primaryPlace": {"name": "X"}, "places": {"nodes": []}}
    http = _stub({"/graphql": lambda u, m, b: (200, _gr8_page([n], "c")) if "after" not in b["variables"] else (500, None), "/jobs": (200, HOME)})
    jobs, stats = _run("gr8people", {"name": "T", "token": "t.gr8people.com"}, http)
    assert len(jobs) == 1 and stats["ok"] == 1
    assert _run("gr8people", {"name": "T", "token": "t.gr8people.com"}, _stub({"/graphql": (200, {"data": None}), "/jobs": (200, HOME)}))[1].get("ok", 0) == 0


# ----------------------------------------------------------------------------- HR-Manager
def _hrm(items, total=None, in_list=None, alias="energinet", status=0):
    return {"CustomerAlias": alias, "TransactionStatus": {"StatusCode": status, "Description": "x"}, "Items": items,
            "PositionCountSearch": len(items) if total is None else total, "PositionCountList": len(items) if in_list is None else in_list}


HRM_ITEM = {"Id": 188952, "Name": "  Kontorchef  ", "ProjectType": "RecruitmentProject",
            "AdvertisementUrl": "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=188952&DepartmentId=9728&MediaId=5",
            "Published": "/Date(1791377991000+0200)/", "Created": "/Date(0)/", "WorkPlace": "Fredericia / Ballerup",
            "Department": {"City": "Fredericia", "Country": "Danmark"},
            "Advertisements": [{"Introduction": "<p>Intro.</p>", "Content": "<p>Energinet spiller en rolle.</p><script>a()</script>"}]}


def test_hrmanager_happy_path_skips_open_application():
    items = [HRM_ITEM, dict(HRM_ITEM, Id=2, ProjectType="OpenApplication", Name="Open")]
    http = _stub({"jobportal.svc/energinet/positionlist/json/": (200, _hrm(items))})
    jobs, stats = _run("hrmanager", {"name": "Energinet", "token": "energinet"}, http)
    _check_shape(jobs)
    assert len(jobs) == 1 and stats == {"tried": 1, "ok": 1, "jobs": 1}
    j = jobs[0]
    assert j["title"] == "Kontorchef" and j["company"] == "Energinet" and j["location"] == "Fredericia / Ballerup"
    assert j["url"].startswith("https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316") and j["posted"] == "2026-10-07"
    assert "Intro." in j["description"] and "Energinet spiller" in j["description"] and "a()" not in j["description"]
    assert j["extra"]["country"] == "Danmark"
    assert "incads=1" in http.calls[0][0] and "take=2000" in http.calls[0][0] and len(http.calls) == 1


@pytest.mark.parametrize("payload", [
    _hrm([HRM_ITEM], status=500), _hrm([HRM_ITEM], alias="other"), _hrm([HRM_ITEM], in_list=2),
    _hrm([HRM_ITEM], total=4), _hrm([HRM_ITEM, HRM_ITEM], total=2), _hrm([HRM_ITEM], total=5000),
])
def test_hrmanager_listing_that_cannot_prove_itself_whole_fails_the_board(payload):
    jobs, stats = _run("hrmanager", {"name": "E", "token": "energinet"}, _stub({"positionlist": (200, payload)}))
    assert jobs == [] and stats.get("ok", 0) == 0


def test_hrmanager_tolerates_two_hidden_and_garbage():
    assert len(_run("hrmanager", {"name": "E", "token": "energinet"}, _stub({"positionlist": (200, _hrm([HRM_ITEM], total=3))}))[0]) == 1
    assert _run("hrmanager", {"name": "E", "token": "energinet"}, _stub({"positionlist": (200, _hrm([]))}))[0] == []
    assert _run("hrmanager", {"name": "E", "token": "energinet"}, _stub({"positionlist": (400, None)}))[1].get("ok", 0) == 0
    assert _run("hrmanager", {"name": "E", "token": "energinet"}, _stub({"positionlist": (200, {"Items": "x"})}))[1].get("ok", 0) == 0
    http = _stub({})
    assert _run("hrmanager", {"name": "E", "token": "bad alias/!"}, http)[0] == [] and http.calls == []


# ----------------------------------------------------------------------------- iSmartRecruit
def _ism_token(raw: str) -> str:
    return "E7p" + base64.b64encode(raw.encode()).decode() + "Q9e"


def _ism_card(raw, title, loc):
    return ('<div class="jobListing_Data_in"><a href="https://app.ismartrecruit.com/jobDescription?x=%s&amp;view=grid" target="_blank" class="jobListing_Data">'
            '<div class="ui-panel"><div class="ui-panel-titlebar"><span class="ui-panel-title">%s</span></div><div class="ui-panel-content">'
            '<table class="ui-panelgrid"><tbody><tr><td class="ui-panelgrid-cell">%s</td></tr></tbody></table></div></div></a></div>') % (_ism_token(raw), title, loc)


def test_ismartrecruit_listing_url_wraps_the_board_like_the_widget():
    http = _stub({"openJobWebsite": (200, "<html></html>")})
    _run("ismartrecruit", {"name": "CA", "token": "example.com_notarealcode"}, http)
    assert http.calls[0][0] == ("https://app.ismartrecruit.com/openJobWebsite?tenantId=E7pZXhhbXBsZS5jb21fbm90YXJlYWxjb2RlQ9e&view=grid&col=2&lang=en")


def test_ismartrecruit_happy_path():
    listing = _ism_card("caglobalint.com_14208623_W_en", "On-Site Mining Engineer &#8211; Namibia", "Namibia") + _ism_card("caglobalint.com_14208625_W_en", "Specialist Advisor", "Namibia")
    listing += '<a class="jobListing_Data" href="https://app.ismartrecruit.com/jobDescription?x=notatoken">bad</a>'
    detail = '<html><head><meta property="og:title" content="t" /><meta property="og:description" content="&lt;p&gt;Reference 083&lt;/p&gt;&lt;script&gt;e()&lt;/script&gt;" /></head></html>'
    http = _stub({"jobDescription": (200, detail), "openJobWebsite": (200, listing)})
    jobs, stats = _run("ismartrecruit", {"name": "CA Global", "token": "example.com_notarealcode"}, http)
    _check_shape(jobs)
    assert stats["jobs"] == 2
    a = jobs[0]
    assert a["title"] == "On-Site Mining Engineer – Namibia" and a["location"] == "Namibia" and a["extra"]["id"] == "14208623"
    assert "Reference 083" in a["description"] and "e()" not in a["description"]
    assert a["url"].startswith("https://app.ismartrecruit.com/jobDescription?x=E7p")


def test_ismartrecruit_failed_detail_keeps_the_card_and_errors():
    listing = _ism_card("caglobalint.com_7_W_en", "Role", "Lagos")
    jobs, _ = _run("ismartrecruit", {"name": "CA", "token": "caglobalint.com_x"}, _stub({"jobDescription": (500, None), "openJobWebsite": (200, listing)}))
    assert len(jobs) == 1 and jobs[0]["description"] == ""
    assert _run("ismartrecruit", {"name": "CA", "token": "caglobalint.com_x"}, _stub({"openJobWebsite": (200, "<div></div>")}))[0] == []
    assert _run("ismartrecruit", {"name": "CA", "token": "caglobalint.com_x"}, _stub({"openJobWebsite": (429, None)}))[1].get("ok", 0) == 0
    assert _run("ismartrecruit", {"name": "CA", "token": "bad token!"}, _stub({}))[0] == []


# ----------------------------------------------------------------------------- Loxo
def _loxo_detail(title="Frontend Engineer (React + TypeScript)", location="Remote, US", desc="<p>Build UIs.</p><script>x()</script>"):
    return ('<html><head><title>%s | FitNext Co.</title><meta content="%s" property="og:title">'
            '<meta content="%sLocation: %sSalary: $100k" property="og:description"></head><body>'
            '<script type="application/json" id="public-job-description-json">%s</script></body></html>') % (
        title, title, title, location, json.dumps({"description": desc}).replace("<", "\\u003c"))


LOXO_TOKEN = base64.b64encode(b"42474-7tse3ikc642yzj0v").decode()


def test_loxo_happy_path(monkeypatch):
    naps: list[float] = []
    monkeypatch.setattr(m1, "_sleep", naps.append)
    listing = '<a href="/job/%s">Frontend</a><a href="/job/%s?t=1">dup</a><a href="/careers-in-nonprofits">x</a>' % (LOXO_TOKEN, LOXO_TOKEN)
    http = _stub({"/job/": (200, _loxo_detail()), "fitnext.app.loxo.co/fitnext": (200, listing)})
    jobs, stats = _run("loxo", {"name": "FitNext Co.", "token": "fitnext.app.loxo.co/fitnext"}, http)
    _check_shape(jobs)
    assert len(jobs) == 1 and stats["jobs"] == 1 and naps == []  # dedicated subdomain: no throttle
    j = jobs[0]
    assert j["url"] == "https://fitnext.app.loxo.co/job/" + LOXO_TOKEN and j["extra"]["id"] == "42474-7tse3ikc642yzj0v"
    assert j["title"] == "Frontend Engineer (React + TypeScript)" and j["location"] == "Remote, US" and j["remote"] is True
    assert "Build UIs" in j["description"] and "x()" not in j["description"] and j["company"] == "FitNext Co."


def test_loxo_shared_host_is_throttled_and_hub_splits_client(monkeypatch):
    naps: list[float] = []
    monkeypatch.setattr(m1, "_sleep", naps.append)
    listing = '<a href="/job/%s">x</a>' % LOXO_TOKEN
    detail = _loxo_detail(title="Platform Engineer — Acme Corp")
    http = _stub({"/job/": (200, detail), "app.loxo.co/agile": (200, listing)})
    jobs, _ = _run("loxo", {"name": "Agile Recruiter", "token": "app.loxo.co/agile", "hub": True}, http)
    assert naps == [m1.LOXO_SHARED_HOST_GAP] and jobs[0]["company"] == "Acme Corp" and jobs[0]["title"] == "Platform Engineer"
    jobs, _ = _run("loxo", {"name": "Agile Recruiter", "token": "app.loxo.co/agile"}, http)
    assert jobs[0]["company"] == "Agile Recruiter" and jobs[0]["title"].endswith("Acme Corp")  # hub only when asked


def test_loxo_empty_nonok_garbage():
    assert _run("loxo", {"name": "A", "token": "app.loxo.co/a"}, _stub({"loxo.co/a": (200, "<html></html>")}))[0] == []
    assert _run("loxo", {"name": "A", "token": "app.loxo.co/a"}, _stub({"loxo.co/a": (404, None)}))[1].get("ok", 0) == 0
    listing = '<a href="/job/%s">x</a>' % LOXO_TOKEN
    assert _run("loxo", {"name": "A", "token": "pod4.app.loxo.co/a"}, _stub({"/job/": (500, None), "loxo.co/a": (200, listing)}))[0] == []
    assert _run("loxo", {"name": "A", "token": "pod4.app.loxo.co/a"}, _stub({"/job/": (200, "<html><body>nothing</body></html>"), "loxo.co/a": (200, listing)}))[0] == []
    assert _run("loxo", {"name": "A", "token": "no-slash"}, _stub({}))[0] == []


# ----------------------------------------------------------------------------- Mindsight
def _next(obj):
    return '<html><body><script id="__NEXT_DATA__" type="application/json">%s</script></body></html>' % json.dumps(obj)


def test_mindsight_happy_path_drops_closed_posts():
    listing = _next({"props": {"pageProps": {"publicJobPostings": [
        {"id": 16, "name": "  Backend Engineer  ", "status": "IN_PROGRESS", "work_model": "REMOTE", "country": "BR", "state": "SP", "city": "Rio Claro",
         "external_publication_start_at": "2026-06-16T00:00:00Z", "created_at": "2026-06-10T00:00:00Z"},
        {"id": 99, "name": "Closed", "status": "FINISHED", "work_model": "IN_PERSON", "country": "BR", "state": "SP", "city": "Sao Paulo"},
        {"name": "No id", "status": "IN_PROGRESS"}]}}})
    detail = _next({"props": {"pageProps": {"jobPosting": {"id": 16, "description": "<p>Build APIs</p><script>evil()</script>"}}}})
    http = _stub({"/grupomngt/16": (200, detail), "/grupomngt": (200, listing)})
    jobs, stats = _run("mindsight", {"name": "Grupo MNGT", "token": "grupomngt"}, http)
    _check_shape(jobs)
    assert len(jobs) == 1 and stats["jobs"] == 1
    j = jobs[0]
    assert j["url"] == "https://oportunidades.mindsight.com.br/grupomngt/16" and j["title"] == "Backend Engineer"
    assert j["location"] == "Rio Claro, SP, BR" and j["remote"] is True and j["posted"] == "2026-06-16"
    assert "Build APIs" in j["description"] and "evil" not in j["description"] and j["extra"]["work_mode"] == "remote"


def test_mindsight_future_start_is_not_special_cased_but_failed_detail_keeps_job():
    listing = _next({"props": {"pageProps": {"publicJobPostings": [{"id": 7, "name": "Role", "work_model": "HYBRID", "city": "Sao Paulo", "created_at": "2026-06-10T00:00:00Z"}]}}})
    jobs, _ = _run("mindsight", {"name": "A", "token": "acme"}, _stub({"/acme/7": (500, None), "/acme": (200, listing)}))
    assert len(jobs) == 1 and jobs[0]["description"] == "" and jobs[0]["posted"] == "2026-06-10" and jobs[0]["remote"] is None


def test_mindsight_empty_nonok_garbage():
    assert _run("mindsight", {"name": "A", "token": "acme"}, _stub({"/acme": (200, _next({"props": {"pageProps": {"publicJobPostings": []}}}))}))[0] == []
    assert _run("mindsight", {"name": "A", "token": "acme"}, _stub({"/acme": (404, None)}))[1].get("ok", 0) == 0
    assert _run("mindsight", {"name": "A", "token": "acme"}, _stub({"/acme": (200, "<html>no data</html>")}))[1].get("ok", 0) == 0
    assert _run("mindsight", {"name": "A", "token": "acme"}, _stub({"/acme": (200, '<script id="__NEXT_DATA__">{broken')}))[1].get("ok", 0) == 0
    assert _run("mindsight", {"name": "A", "token": "a/../b"}, _stub({}))[0] == []


# ----------------------------------------------------------------------------- all readers
@pytest.mark.parametrize("name", ALL)
def test_every_reader_never_raises_on_garbage_and_http_exceptions(name):
    row = {"name": "X", "token": {"crelate": CRELATE_BOARD, "erecruiter": CFG, "loxo": "app.loxo.co/x", "ismartrecruit": "xxx"}.get(name, "x")}
    for payload in (None, 5, [], {}, "<<>>", "\x00�", {"Jobs": 1}):
        jobs, stats = _run(name, row, lambda url, p=payload, **kw: (200, p))
        assert isinstance(jobs, list) and stats["tried"] == 1

    def boom(url, **kw):
        raise OSError("down")

    jobs, stats = _run(name, row, boom)
    assert jobs == [] and stats["tried"] == 1 and stats.get("ok", 0) == 0


@pytest.mark.parametrize("name", ALL)
def test_registered_and_first_non200_stops_after_one_request(name):
    assert name in F
    row = {"name": "X", "token": {"crelate": CRELATE_BOARD, "erecruiter": CFG, "loxo": "app.loxo.co/x", "ismartrecruit": "xxx"}.get(name, "x")}
    http = _stub({"": (503, None)})
    assert _run(name, row, http)[0] == [] and len(http.calls) == 1


# ----------------------------------------------------------------------------- readers stay on the board host
OFF_HOST_HREFS = ("https://evil.example/x-123", "//evil2.example/y-9", "http://good.betterteam.com/ok-5")


def test_betterteam_never_fetches_or_emits_off_host_links():
    listing = "".join('<a href="%s">x</a>' % h for h in OFF_HOST_HREFS) + '<a href="/bartender-21">ok</a>'
    http = _stub({"good.betterteam.com/bartender-21": (200, _bt_detail("Bartender", "d", "2026-06-09T15:07:41Z", "Leeds", "GB")),
                  "good.betterteam.com": (200, listing)})
    jobs, _ = _run("betterteam", {"name": "G", "token": "good"}, http)
    assert [j["url"] for j in jobs] == ["https://good.betterteam.com/bartender-21"]
    assert not any("evil" in c[0] or c[0].startswith("http://") for c in http.calls)


def test_catsone_never_fetches_or_emits_off_host_links():
    row = '<a class="table-row" href="%s"><div class="title-cell">Evil</div></a>'
    listing = (row % "https://evil.example/careers/1/jobs/77-x") + (row % "//evil2.example/careers/1/jobs/78-y") \
        + (row % "http://good.catsone.com/careers/1/jobs/79-z") + _cats_listing(("16831828-sdr", "Ok", "Kyiv"))
    http = _stub({"/jobs/16831828": (200, CATS_DETAIL), "good.catsone.com/careers": (200, listing)})
    jobs, _ = _run("catsone", {"name": "G", "token": "good.catsone.com"}, http)
    assert [j["url"] for j in jobs] == ["https://good.catsone.com/careers/90438/jobs/16831828-sdr"]
    assert not any("evil" in c[0] or c[0].startswith("http://") for c in http.calls)


def test_loxo_never_fetches_or_emits_off_host_links(monkeypatch):
    monkeypatch.setattr(m1, "_sleep", lambda s: None)
    listing = ('<a href="https://evil.example/job/%s">x</a><a href="//evil2.example/job/%s">y</a><a href="/job/%s">ok</a>'
               % (LOXO_TOKEN, LOXO_TOKEN, LOXO_TOKEN))
    http = _stub({"/job/": (200, _loxo_detail()), "fitnext.app.loxo.co/fitnext": (200, listing)})
    jobs, _ = _run("loxo", {"name": "F", "token": "fitnext.app.loxo.co/fitnext"}, http)
    assert [j["url"] for j in jobs] == ["https://fitnext.app.loxo.co/job/" + LOXO_TOKEN]
    assert not any("evil" in c[0] for c in http.calls)


def test_clinch_never_fetches_or_emits_off_host_or_non_https_locs():
    base = "https://c.example.com"
    detail = '<div class="job-description"><p>Hi.</p></div>'
    http = _stub({"c.example.com/jobs/good-london": (200, detail),
                  "sitemap.xml": (200, _sitemap("https://evil.example/jobs/evil-one", "javascript:void(0)//jobs/x",
                                                "http://c.example.com/jobs/plain-http", "https://c.example.com.evil.example/jobs/lookalike",
                                                "//evil2.example/jobs/y", base + "/jobs/good-london"))})
    jobs, _ = _run("clinch", {"name": "C", "token": "c.example.com"}, http)
    assert [j["url"] for j in jobs] == [base + "/jobs/good-london"]
    assert [c[0] for c in http.calls] == [base + "/sitemap.xml", base + "/jobs/good-london"]


def test_clinch_sitemap_with_doctype_or_entity_returns_no_rows():
    bomb = ('<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY x "https://c.example.com/jobs/boom-1">]>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>&x;</loc></url>'
            '<url><loc>https://c.example.com/jobs/real-1</loc></url></urlset>')
    http = _stub({"sitemap.xml": (200, bomb)})
    jobs, stats = _run("clinch", {"name": "C", "token": "c.example.com"}, http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1


# ----------------------------------------------------------------------------- wiring
def test_misc1_is_wired_into_the_kit():
    hk = _load("hunt_kit_misc1_wiring", "hunt_kit.py")
    assert hk.ATS_MODULES == ("kit_ats", "kit_ats_more", "kit_ats_eu", "kit_ats_extra", "kit_ats_misc1", "kit_ats_misc2", "kit_ats_us", "kit_sites_a", "kit_pubsec", "kit_latam", "kit_ats_misc3", "kit_ats_misc5", "kit_ats_misc4", "kit_markets4", "kit_asia2", "kit_market5", "kit_co6", "kit_fh4", "kit_w4a")
    assert set(ALL) <= set(F) and set(ALL) <= set(hk.BOARD_SOURCES)
    from src.api.routes.hunt_kit import KIT_FILES
    assert "kit_ats_misc1.py" in KIT_FILES


def test_deeply_nested_html_keeps_the_board_and_does_not_stall():
    import time

    deep = "<p>line" * 3000
    jobs_in = [
        {"Id": "1", "JobCode": "a", "Title": "Nurse", "Description": "<p>ok"},
        {"Id": "2", "JobCode": "b", "Title": "Cook", "Description": deep},
    ]
    jobs, _ = _run("crelate", {"name": "x", "token": CRELATE_BOARD}, _stub({"GetAllJobs": (200, {"IsError": False, "Jobs": jobs_in})}))
    assert [j["title"] for j in jobs] == ["Nurse", "Cook"]
    assert "line" in jobs[1]["description"]
    t0 = time.monotonic()
    m1._parse("<b>" * 12500 + "</i>" * 12500)
    assert time.monotonic() - t0 < 3
    assert m1._own_text(m1._parse("<div>" * 3000 + "hi")) == "hi"


# ----------------------------------------------------------------------------- review round 2
def test_betterteam_deeply_nested_ldjson_drops_only_that_posting():
    bomb = '<script type="application/ld+json">' + "[" * 100000 + "]" * 100000 + "</script>"
    listing = '<a href="/bad-1">x</a><a href="/good-2">y</a>'
    http = _stub({"/bad-1": (200, "<html>" + bomb + "</html>"),
                  "/good-2": (200, _bt_detail("Cook", "Fry.", "2026-06-09", "Leeds", "GB")),
                  "bt.betterteam.com": (200, listing)})
    jobs, stats = _run("betterteam", {"name": "BT", "token": "bt"}, http)
    assert [j["title"] for j in jobs] == ["Cook"] and stats["ok"] == 1


def test_betterteam_cats_clinch_detail_parse_failure_drops_only_that_posting(monkeypatch):
    real = m1._parse

    def boom(text, *a, **k):
        if "BOOM" in str(text):
            raise RuntimeError("parser blew up")
        return real(text, *a, **k)

    monkeypatch.setattr(m1, "_parse", boom)
    http = _stub({"/bad-1": (200, "BOOM"), "/good-2": (200, _bt_detail("Cook", "Fry.", "2026-06-09", "Leeds", "GB")),
                  "bt.betterteam.com": (200, '<a href="/bad-1">x</a><a href="/good-2">y</a>')})
    jobs, stats = _run("betterteam", {"name": "BT", "token": "bt"}, http)
    assert [j["title"] for j in jobs] == ["Cook"] and stats["ok"] == 1

    http = _stub({"/jobs/1": (200, "BOOM"), "/jobs/2": (200, '<div class="job-description">Fine</div>'),
                  "c.example.com/careers": (200, _cats_listing(("1", "Nurse", "Leeds"), ("2", "Cook", "York")))})
    jobs, stats = _run("catsone", {"name": "C", "token": "c.example.com"}, http)
    assert [j["title"] for j in jobs] == ["Cook"] and stats["ok"] == 1

    http = _stub({"sitemap.xml": (200, _sitemap("https://k.example.com/jobs/nurse-canada", "https://k.example.com/jobs/cook-canada")),
                  "/jobs/nurse-canada": (200, "BOOM"), "/jobs/cook-canada": (200, '<div class="job-description">Fine</div>')})
    jobs, stats = _run("clinch", {"name": "K", "token": "k.example.com"}, http)
    assert sorted(j["title"] for j in jobs) == ["Cook", "Nurse"] and stats["ok"] == 1


def test_ld_posting_is_linear_on_a_huge_array_and_keeps_document_order():
    import time

    n = 200000
    blob = json.dumps([{"@type": "Thing"}] * n + [{"@type": "JobPosting", "title": "Last"}])
    root = m1._parse('<script type="application/ld+json">' + blob + "</script>")
    t0 = time.monotonic()
    assert m1._ld_posting(root)["title"] == "Last"
    assert time.monotonic() - t0 < 1
    first = m1._parse('<script type="application/ld+json">' + json.dumps(
        [{"@graph": [{"@type": "JobPosting", "title": "A"}]}, {"@type": "JobPosting", "title": "B"}]) + "</script>")
    assert m1._ld_posting(first)["title"] == "A"


# ----------------------------------------------------------------------------- one malformed href never loses the board
def test_betterteam_malformed_href_keeps_the_good_job():
    listing = '<a href="/eng-1">ok</a><a href="http://[bad/x-2">bad</a>'
    http = _stub({"/eng-1": (200, _bt_detail("Engineer", "d", "2026-06-09T15:07:41Z", "Leeds", "GB")),
                  "bt.betterteam.com": (200, listing)})
    jobs, stats = _run("betterteam", {"name": "BT", "token": "bt"}, http)
    assert [j["url"] for j in jobs] == ["https://bt.betterteam.com/eng-1"] and stats["ok"] == 1


def test_catsone_malformed_href_keeps_the_good_job():
    bad = '<a class="table-row" href="https://[x/careers/1/jobs/6"><div class="title-cell">Bad</div></a>'
    listing = _cats_listing(("16831828-sdr", "Ok", "Kyiv")) + bad
    http = _stub({"/jobs/16831828": (200, CATS_DETAIL), "good.catsone.com/careers": (200, listing)})
    jobs, stats = _run("catsone", {"name": "G", "token": "good.catsone.com"}, http)
    assert [j["url"] for j in jobs] == ["https://good.catsone.com/careers/90438/jobs/16831828-sdr"] and stats["ok"] == 1


def test_gr8people_odd_nested_field_shapes_cost_only_that_field():
    bad = {"key": "7", "title": "Odd", "descriptionHTML": "", "positionType": "Full Time", "primaryPlace": ["x"], "places": ["a"]}
    good = {"key": "8", "title": "Fine", "descriptionHTML": "", "positionType": {"name": "Full Time"}, "primaryPlace": {"name": "NYC"}, "places": {"nodes": []}}
    http = _stub({"/graphql": (200, _gr8_page([bad, good])), "t.gr8people.com/jobs": (200, HOME)})
    jobs, stats = _run("gr8people", {"name": "T", "token": "t.gr8people.com"}, http)
    assert [j["title"] for j in jobs] == ["Odd", "Fine"] and stats.get("ok") == 1


def test_hrmanager_out_of_range_date_costs_only_that_date():
    far = dict(HRM_ITEM, Id=3, Published="/Date(253402300800000)/", Created="/Date(253402300800000)/")
    http = _stub({"positionlist": (200, _hrm([HRM_ITEM, far]))})
    jobs, stats = _run("hrmanager", {"name": "E", "token": "energinet"}, http)
    assert len(jobs) == 2 and stats.get("ok") == 1 and jobs[1]["posted"] == ""
