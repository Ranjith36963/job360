"""Offline tests for hunt_kit/kit_co6.py (Agentic Engineering Jobs, Collage HR, Flowxtra, Generalist World).

Fixtures are shaped like the data described in the career-ops provider comments (MIT) and written fresh. Nothing touches
the network. Consider is not read (cookie session + CSRF token): its host is reported as unsupported.
"""
from __future__ import annotations

import importlib.util
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


kb = _load("kit_co6_under_test", "kit_co6.py")
hk = _load("hunt_kit_co6_under_test", "hunt_kit.py")
F = kb.ATS_FETCHERS
NAMES = ("agenticjobs", "collage", "flowxtra", "generalistworld")
ALLOWED_KW = {"method", "data", "headers", "as_json", "once", "max_bytes"}


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


def _run(name, row, http):
    stats: dict = {}
    jobs = F[name](row, http, stats)
    assert all(set(k) <= ALLOWED_KW for k in http.kws)  # a keyword the real http() lacks is a run-time TypeError
    return jobs, stats


def _row(ats, token="all"):
    return {"name": ats.title() + " Co", "ats": ats, "token": token}


def _hosts(http):
    return {urlsplit(u).hostname for u in http.calls}


def _job_hosts(jobs):
    return {urlsplit(j["url"]).hostname for j in jobs}


# ----------------------------------------------------------------------------- wiring and host rule
def test_wiring():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_co6" in hk.ATS_MODULES and all(a in hk._merged("ATS_FETCHERS", hk.ATS_MODULES) for a in NAMES)
    assert src_has_cites() >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_co6.py"' in served
    rows = hk.load_companies(KIT_DIR / "companies.json")
    assert {r["ats"] for r in rows} >= {"agenticjobs", "flowxtra", "generalistworld"}


def src_has_cites() -> int:
    return (KIT_DIR / "kit_co6.py").read_text(encoding="utf-8").count("Borrowed from")


def test_host_rule_and_consider_is_unsupported():
    b = hk.board_from_job_url
    for url, ats in (("https://agentic-engineering-jobs.com/jobs/ml-lead-1", "agenticjobs"),
                     ("https://flowxtra.com/jobs/abc", "flowxtra"),
                     ("https://generalist.world/jobs/chief-of-staff/", "generalistworld"),
                     ("https://www.generalist.world/jobs/chief-of-staff/", "generalistworld")):
        r = b(url)
        assert (r["kind"], r["ats"], r["token"]) == ("ats", ats, "")
    r = b("https://secure.collage.co/jobs/acme-careers/12345")
    assert (r["kind"], r["ats"], r["token"]) == ("ats", "collage", "acme-careers")
    assert b("https://secure.collage.co/pricing")["token"] == ""
    assert b("https://jobs.founderful.com/jobs")["kind"] == "other"
    assert b("https://app.getconsider.com/jobs")["kind"] == "unsupported"
    assert b("https://evil.example/agentic-engineering-jobs.com/jobs/x")["kind"] == "other"


# ----------------------------------------------------------------------------- Agentic Engineering Jobs
def _ag(i, **kw):
    return {"title": f"Agent Engineer {i}", "companyName": "Acme", "slug": f"agent-engineer-{i}", "location": "Remote (EU)",
            "description": "<p>Build &amp; ship</p><script>steal()</script>", "postedAt": "2026-05-01T10:00:00Z", **kw}


def test_agentic_happy_path_fields_and_one_bad_posting():
    rows = [_ag(1, salaryMin=100000, salaryMax=140000, salaryCurrency="eur"),
            {"title": "No company", "slug": "x"}, "junk", None,
            _ag(2, slug="../etc/passwd"), _ag(3, location="", geoRegion="Europe", description=None),
            _ag(4, title="Dup"), _ag(4, title="Dup again")]
    http = _stub({"/api/v1/jobs?page=1": (200, {"data": rows, "meta": {"total": 8, "per_page": 50}})})
    jobs, stats = _run("agenticjobs", _row("agenticjobs"), http)
    assert [j["url"] for j in jobs] == ["https://agentic-engineering-jobs.com/jobs/agent-engineer-1",
                                        "https://agentic-engineering-jobs.com/jobs/agent-engineer-3",
                                        "https://agentic-engineering-jobs.com/jobs/agent-engineer-4"]
    a, c, _ = jobs
    assert (a["company"], a["location"], a["posted"], a["remote"]) == ("Acme", "Remote (EU)", "2026-05-01", True)
    assert "steal" not in a["description"] and "<" not in a["description"] and "Build & ship" in a["description"]
    assert a["extra"]["salary"] == {"salaryMin": 100000, "salaryMax": 140000, "currency": "EUR"}
    assert c["location"] == "Europe" and c["description"] == "" and c["extra"] == {}
    assert _hosts(http) == {"agentic-engineering-jobs.com"} and _job_hosts(jobs) == {"agentic-engineering-jobs.com"}
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}


def test_agentic_paging_stops_on_short_page_and_total():
    full = [_ag(i) for i in range(50)]
    pages = {1: full, 2: [_ag(i) for i in range(50, 60)]}
    http = _stub({"/api/v1/jobs?page=": lambda u: (200, {"data": pages.get(int(u.rsplit("=", 1)[1]), []), "meta": {"total": 60, "per_page": 50}})})
    jobs, _ = _run("agenticjobs", _row("agenticjobs"), http)
    assert len(jobs) == 60 and len(http.calls) == 2 and all(k.get("once") for k in http.kws)


def test_agentic_page_cap_and_refusal_keeps_earlier_pages():
    endless = _stub({"/api/v1/jobs?page=": lambda u: (200, {"data": [_ag(int(u.rsplit("=", 1)[1]) * 100 + i) for i in range(50)], "meta": {}})})
    jobs, _ = _run("agenticjobs", _row("agenticjobs"), endless)
    assert len(endless.calls) == kb.AGENTIC_PAGES and len(jobs) == 50 * kb.AGENTIC_PAGES

    def two_then_429(u):
        return (200, {"data": [_ag(int(u.rsplit("=", 1)[1]) * 100 + i) for i in range(50)]}) if u.endswith("=1") else (429, None)
    refused = _stub({"/api/v1/jobs?page=": two_then_429})
    jobs, stats = _run("agenticjobs", _row("agenticjobs"), refused)
    assert len(jobs) == 50 and len(refused.calls) == 2 and stats["ok"] == 1


def test_agentic_first_page_refusal_or_wrong_shape_fails_the_board():
    for resp in ((429, None), (200, {"data": "nope"}), (200, [1, 2]), (0, None)):
        jobs, stats = _run("agenticjobs", _row("agenticjobs"), _stub({"/api/v1/jobs": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0


# ----------------------------------------------------------------------------- Collage HR
def _co(**kw):
    return {"title": "Nurse", "hostedUrl": "https://secure.collage.co/jobs/acme/1", "location": ["Leeds", " ", "Remote"],
            "descriptionPlain": "Care for people", "department": "Care", "commitment": "Full time",
            "employmentType": "Permanent", "createdDate": "2026-03-09T08:00:00Z", **kw}


def test_collage_happy_path_both_envelopes_and_bad_rows():
    rows = [_co(), _co(title="Off host", hostedUrl="https://evil.example/jobs/1"),
            _co(title="Plain http", hostedUrl="http://secure.collage.co/jobs/2", url=None, applyUrl=None),
            _co(title="Userinfo", hostedUrl="https://u:p@secure.collage.co/jobs/3", url="", applyUrl=""),
            _co(title="Lookalike", hostedUrl="https://notcollage.co/jobs/4"),
            _co(title="Falls back", hostedUrl="https://evil.example/x", url="https://secure.collage.co/jobs/5"),
            _co(title="", hostedUrl="https://secure.collage.co/jobs/6"), "junk", None, _co(hostedUrl="https://secure.collage.co/jobs/acme/1")]
    for body in (rows, {"positions": rows}):
        http = _stub({"api.collage.co/v1/positions/acme-careers": (200, body)})
        jobs, stats = _run("collage", _row("collage", "acme-careers"), http)
        assert [j["title"] for j in jobs] == ["Nurse", "Falls back"]
        a = jobs[0]
        assert (a["company"], a["location"], a["posted"]) == ("Collage Co", "Leeds; Remote", "2026-03-09")
        assert a["description"] == "Care for people\n\nDepartment: Care\nCommitment: Full time\nEmployment type: Permanent"
        assert _hosts(http) == {"api.collage.co"} and _job_hosts(jobs) == {"secure.collage.co"}
        assert stats == {"tried": 1, "ok": 1, "jobs": 2}


def test_collage_bad_tokens_never_reach_the_network_and_bad_answers_fail_the_board():
    for tok in ("", "a/b", "..", "a b", "x?y=1", "-x", "a" * 90):
        http = _stub({})
        jobs, stats = _run("collage", _row("collage", tok), http)
        assert jobs == [] and http.calls == [] and stats.get("ok", 0) == 0
    for resp in ((403, None), (200, {"nope": []}), (200, "text"), (0, None)):
        jobs, stats = _run("collage", _row("collage", "acme"), _stub({"api.collage.co": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0
    http = _stub({"api.collage.co": (200, [])})  # an empty board is alive, not failed
    assert _run("collage", _row("collage", "acme"), http)[1] == {"tried": 1, "ok": 1, "jobs": 0}


# ----------------------------------------------------------------------------- Flowxtra
def _fx(i, **kw):
    return {"title": f"Dev {i}", "urlJobApplay": f"https://flowxtra.com/apply/{i}", "name_company": "Beta GmbH", "city_company": "Berlin",
            "state_company": " ", "country_company": "DE", "workplace": "Remote", "date_share": "2026-06-01 09:00:00", **kw}


def _fx_page(rows, nxt="x"):
    return 200, {"success": True, "data": {"data": rows, "next_page_url": nxt}}


def test_flowxtra_happy_path_one_bad_posting_and_only_flowxtra_links():
    rows = [_fx(1), _fx(2, name_company="", workplace="Onsite"), "junk", None, {"title": ""},
            _fx(3, urlJobApplay="https://evil.example/apply/3"), _fx(4, urlJobApplay="http://flowxtra.com/apply/4"),
            _fx(5, urlJobApplay="https://flowxtra.com.evil.example/5"), _fx(6, urlJobApplay="https://u@flowxtra.com/6"),
            _fx(7, title=None), _fx(1, title="Dup")]
    http = _stub({"app.flowxtra.com/api/central/jobs?status=Live&per_page=100&page=1": _fx_page(rows, nxt=None)})
    jobs, stats = _run("flowxtra", _row("flowxtra"), http)
    assert [j["url"] for j in jobs] == ["https://flowxtra.com/apply/1", "https://flowxtra.com/apply/2"]
    a, b = jobs
    assert (a["company"], a["location"], a["posted"], a["remote"]) == ("Beta GmbH", "Berlin, DE, Remote", "2026-06-01", True)
    assert (b["company"], b["location"], b["remote"]) == ("Flowxtra", "Berlin, DE", None)
    assert _hosts(http) == {"app.flowxtra.com"} and _job_hosts(jobs) == {"flowxtra.com"}
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}


def test_flowxtra_paging_cap_and_refusal():
    full = lambda n: [_fx(n * 1000 + i) for i in range(100)]  # noqa: E731
    endless = _stub({"central/jobs": lambda u: _fx_page(full(int(u.rsplit("=", 1)[1])))})
    jobs, _ = _run("flowxtra", _row("flowxtra"), endless)
    assert len(endless.calls) == kb.FLOWXTRA_PAGES and len(jobs) == 100 * kb.FLOWXTRA_PAGES and all(k.get("once") for k in endless.kws)
    short = _stub({"&page=1": _fx_page(full(1)), "&page=2": _fx_page([_fx(9)])})
    assert len(_run("flowxtra", _row("flowxtra"), short)[0]) == 101 and len(short.calls) == 2
    refused = _stub({"&page=1": _fx_page(full(1)), "&page=2": (429, None)})
    assert len(_run("flowxtra", _row("flowxtra"), refused)[0]) == 100 and len(refused.calls) == 2
    for resp in ((403, None), (200, {"data": []}), (200, {"data": {"data": "x"}}), (200, None)):
        jobs, stats = _run("flowxtra", _row("flowxtra"), _stub({"central/jobs": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0


# ----------------------------------------------------------------------------- Generalist World
def _card(slug="/jobs/chief-of-staff/", *, title="Chief of Staff", company="ExampleCo", place="Remote (UK)", desc="Teaser <b>text</b>",
          region="remote", cls="gw-job-card"):
    return (f'<a class="{cls}" data-type="connector" data-region="{region}" href="{slug}">'
            f'<div class="gw-job-company">{company}</div><div class="gw-job-title">{title}</div>'
            f'<p class="gw-job-description">{desc}</p><span class="gw-job-meta-tag gw-salary">£70k</span>'
            f'<span class="gw-job-meta-tag gw-location">{place}</span></a>')


def _gw_page(*cards):
    return '<html><style>.gw-job-card{}</style><div class="gw-jobs-section" data-jobs-container>' + "".join(cards) + "</div></html>"


def test_generalistworld_happy_path_bad_cards_and_own_host_only():
    page = _gw_page(
        _card(), _card("/jobs/ops-lead/", title="Ops &amp; Lead", place="", region="uk", desc=""),
        _card("https://evil.example/jobs/x/"), _card("http://generalist.world/jobs/plain/"), _card("/jobs/../etc/"),
        _card("/jobs/q/?utm=1"), _card("https://u@generalist.world/jobs/ui/"), _card("/jobs/no-title/", title=""),
        _card("/jobs/no-company/", company=""), _card("/jobs/chief-of-staff/", title="Dup"),
        _card("https://www.generalist.world/jobs/abs/"), _card("/jobs/fine-extra/", cls="gw-job-card extra"),
        _card("/jobs/lookalike/", cls="gw-job-card-top"),
        "<template>" + _card("/jobs/in-template/") + "</template>", "<!-- " + _card("/jobs/in-comment/") + " -->",
        "<script>var s='" + _card("/jobs/in-script/") + "';</script>")
    http = _stub({"generalist.world/jobs/": (200, page)})
    jobs, stats = _run("generalistworld", _row("generalistworld"), http)
    assert [j["url"] for j in jobs] == ["https://generalist.world/jobs/chief-of-staff/", "https://generalist.world/jobs/ops-lead/",
                                        "https://generalist.world/jobs/abs/", "https://generalist.world/jobs/fine-extra/"]
    a, b, *_ = jobs
    assert (a["title"], a["company"], a["location"], a["description"], a["remote"]) == ("Chief of Staff", "ExampleCo", "Remote (UK)", "Teaser text", True)
    assert (b["title"], b["location"], b["description"]) == ("Ops & Lead", "UK", "")
    assert _hosts(http) == {"generalist.world"} and _job_hosts(jobs) == {"generalist.world"}
    assert http.calls == ["https://generalist.world/jobs/"] and stats == {"tried": 1, "ok": 1, "jobs": 4}


def test_generalistworld_empty_board_changed_markup_and_refusal():
    assert _run("generalistworld", _row("generalistworld"), _stub({"/jobs/": (200, _gw_page())}))[1] == {"tried": 1, "ok": 1, "jobs": 0}
    moved = _gw_page('<a class="gw-job-card" href="/jobs/x/"><span>new markup</span></a>')
    for resp in ((200, moved), (200, "<html>challenge page</html>"), (200, ""), (403, None), (0, None)):
        jobs, stats = _run("generalistworld", _row("generalistworld"), _stub({"/jobs/": resp}))
        assert jobs == [] and stats.get("ok", 0) == 0


def test_generalistworld_hostile_markup_is_safe():
    nested = _gw_page('<a class="gw-job-card" href="/jobs/n/">' * 20000 + _card("/jobs/real/"))
    unclosed = _gw_page("<div " * 30000, "<p " * 30000, '<div class="gw-job-title">' * 5000, _card("/jobs/after/"))
    for page in (nested, unclosed):
        t = time.perf_counter()
        jobs, _ = _run("generalistworld", _row("generalistworld"), _stub({"/jobs/": (200, page)}))
        assert time.perf_counter() - t < 2 and isinstance(jobs, list)


def test_generalistworld_cost_scales_linearly():
    best = {}
    for n in (500, 2000):  # a 4x page must cost about 4x; a quadratic reader would cost 16x
        page = _gw_page(*[_card(f"/jobs/job-{i}/", title=f"Role {i}") for i in range(n)])
        runs = []
        for _ in range(2):
            t = time.perf_counter()
            jobs, _ = _run("generalistworld", _row("generalistworld"), _stub({"/jobs/": (200, page)}))
            runs.append(time.perf_counter() - t)
            assert len(jobs) == n
        best[n] = min(runs)
    assert best[2000] < 8 * best[500] and best[2000] < 1
