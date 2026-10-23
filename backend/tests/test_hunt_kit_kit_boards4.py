"""Offline tests for hunt_kit/kit_markets4.py (Yourator, CareerViet, OCC Mundial).

Fixtures are shaped like the field lists and card markup in career-ops providers/yourator.mjs, careerviet.mjs and occ.mjs
(MIT, 8f48928) and written fresh. Nothing touches the network. AIJobs is not read: its host is reported as unsupported.
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


kb = _load("kit_markets4_under_test", "kit_markets4.py")
hk = _load("hunt_kit_markets4_under_test", "hunt_kit.py")
F = kb.ATS_FETCHERS
NAMES = ("yourator", "careerviet", "occ")


def _stub(routes: dict):
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
        calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                return resp(url) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name: str, token: str, http):
    stats: dict = {}
    return F[name]({"name": name.title(), "ats": name, "token": token}, http, stats), stats


def _hosts(http) -> set:
    return {urlsplit(u).hostname for u in http.calls}


# ----------------------------------------------------------------------- registry
def test_registered_and_wired():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_markets4" in hk.ATS_MODULES and set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_markets4.py"' in served
    assert (KIT_DIR / "kit_markets4.py").read_text(encoding="utf-8").count("Borrowed from career-ops") >= 3


def test_host_rules_and_unsupported_aijobs():
    for url, ats in (("https://www.yourator.co/companies/x/jobs/1", "yourator"),
                     ("https://careerviet.vn/vi/tim-viec-lam/a.1.html", "careerviet"),
                     ("https://www.occ.com.mx/empleo/oferta/1/", "occ")):
        info = hk.board_from_job_url(url)
        assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, "")
    assert hk.board_from_job_url("https://aijobs.net/job/1")["kind"] == "unsupported"
    assert hk.board_from_job_url("https://evil.example/yourator.co")["ats"] == ""


# ----------------------------------------------------------------------- Yourator
def _yj(i, **kw):
    return {"id": i, "name": f"Engineer {i}", "path": f"/companies/co{i}/jobs/{i}", "location": "臺北市",
            "company": {"brand": f"Co{i}"}, "thirdPartyUrl": f"https://evil.example/x/{i}?utm_source=yourator", **kw}


def _ypage(jobs, more):
    return 200, {"payload": {"hasMore": more, "jobs": jobs}}


def test_yourator_pages_and_stays_on_the_board():
    http = _stub({"page=1": _ypage([_yj(1), _yj(2)], True), "page=2": _ypage([_yj(2), _yj(3)], False)})
    jobs, stats = _run("yourator", "all", http)
    assert [j["title"] for j in jobs] == ["Engineer 1", "Engineer 2", "Engineer 3"]  # page 2's repeat of 2 is dropped
    assert jobs[0]["company"] == "Co1" and jobs[0]["location"] == "臺北市" and jobs[0]["posted"] == ""
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"www.yourator.co"} and all(j["url"].startswith("https://") for j in jobs)
    assert _hosts(http) == {"www.yourator.co"} and len(http.calls) == 2  # thirdPartyUrl on evil.example is never fetched or returned
    assert stats["ok"] == 1 and stats["jobs"] == 3


def test_yourator_bad_posting_and_hostile_paths():
    bad = [None, 5, {"name": "No path"}, _yj(7, path="//evil.example/x"), _yj(8, path="/\\evil.example"), _yj(9, path="http://evil.example/x"),
           _yj(10, name=""), _yj(11, company=None), _yj(12, name="<img src=x onerror=alert(1)> Dev")]
    jobs, _ = _run("yourator", "all", _stub({"page=1": _ypage(bad + [_yj(1)], False)}))
    assert [j["title"] for j in jobs] == ["Engineer 11", "<img src=x onerror=alert(1)> Dev", "Engineer 1"]
    assert jobs[0]["company"] == "?"  # never the board's own name
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"www.yourator.co"}


def test_yourator_refusal_ends_the_board():
    jobs, stats = _run("yourator", "all", _stub({"page=1": _ypage([_yj(1)], True), "page=2": (429, None)}))
    assert [j["title"] for j in jobs] == ["Engineer 1"] and stats["ok"] == 1  # earlier pages kept
    http = _stub({"page=1": (403, None)})
    jobs, stats = _run("yourator", "all", http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1
    _, stats = _run("yourator", "all", _stub({"page=1": (200, {"payload": {}})}))
    assert stats.get("ok", 0) == 0  # no jobs array: a failed page, not an empty board


# ----------------------------------------------------------------------- CareerViet
def _cv_card(i, href=None, title=None, company="ACME", loc="Hồ Chí Minh", upd="05-03-2026"):
    href = href if href is not None else f"https://careerviet.vn/vi/tim-viec-lam/dev-{i}.{i}.html"
    title = title if title is not None else f"Dev {i}"
    return (f'<div class="job-item" id="job-item-{i}"><div class="figcaption"><h2><a class="job_link" title="{title}" href="{href}">{title}</a></h2>'
            f'<a class="company-name" title="{company}">{company}</a><div class="location"><ul><li>{loc}</li></ul></div>'
            f'<div class="time"><ul><li><span>Hạn nộp</span><time>30-04-2026</time></li><li>Cập nhật<time>{upd}</time></li></ul></div>'
            f'<a class="job_link" title="{title}" href="{href}">again</a></div></div>')


def _cv_page(*cards):
    return 200, "<html><body>" + "".join(cards) + "</body></html>"


def test_careerviet_paging_fields_and_dedupe():
    http = _stub({"tat-ca-viec-lam-vi.html": _cv_page(_cv_card(1), _cv_card(2, company="Beta &amp; Co")),
                  "tat-ca-viec-lam-trang-2-vi.html": _cv_page(_cv_card(2), _cv_card(3, upd="31-02-2026")),
                  "tat-ca-viec-lam-trang-3-vi.html": _cv_page(_cv_card(3))})
    jobs, stats = _run("careerviet", "all", http)
    assert [j["title"] for j in jobs] == ["Dev 1", "Dev 2", "Dev 3"]
    assert jobs[0]["company"] == "ACME" and jobs[1]["company"] == "Beta & Co" and jobs[0]["location"] == "Hồ Chí Minh"
    assert jobs[0]["posted"] == "2026-03-05"  # the updated date, not the deadline
    assert jobs[2]["posted"] == ""  # an impossible date is not invented
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"careerviet.vn"} and _hosts(http) == {"careerviet.vn"}
    assert len(http.calls) == 3 and stats["jobs"] == 3  # page 3 adds nothing new: the walk ends


def test_careerviet_keyword_slug():
    http = _stub({"/viec-lam/ke-toan-k-vi.html": _cv_page(_cv_card(1))})
    jobs, _ = _run("careerviet", "Ke-Toan", http)
    assert len(jobs) == 1 and http.calls[0].endswith("/viec-lam/ke-toan-k-vi.html")
    http = _stub({})
    _, stats = _run("careerviet", "../x y", http)
    assert http.calls == [] and stats.get("ok", 0) == 0


def test_careerviet_off_host_and_bad_cards_dropped():
    cards = [_cv_card(1, href="https://evil.example/job/1"), _cv_card(2, href="http://careerviet.vn/x"), _cv_card(3, href="https://careerviet.vn.evil.example/x"),
             _cv_card(4, href="//evil.example/x"), _cv_card(5, href=""), _cv_card(6, title=""), _cv_card(7, href="/vi/tim-viec-lam/ok.7.html"),
             _cv_card(8, href="https://[bad/x"), _cv_card(9, title="&lt;script&gt;alert(1)&lt;/script&gt;")]
    http = _stub({"tat-ca-viec-lam-vi.html": _cv_page(*cards)})
    jobs, stats = _run("careerviet", "all", http)
    assert [j["title"] for j in jobs] == ["Dev 7", "<script>alert(1)</script>"]
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"careerviet.vn"} and all(j["url"].startswith("https://") for j in jobs)
    assert stats["ok"] == 1


def test_careerviet_refusal_and_changed_layout():
    jobs, stats = _run("careerviet", "all", _stub({"tat-ca-viec-lam-vi.html": _cv_page(_cv_card(1)), "trang-2": (429, None)}))
    assert len(jobs) == 1 and stats["ok"] == 1
    http = _stub({"tat-ca-viec-lam-vi.html": (403, None)})
    jobs, stats = _run("careerviet", "all", http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1
    _, stats = _run("careerviet", "all", _stub({"tat-ca-viec-lam-vi.html": (200, '<div id="job-item-5"><p>no link</p></div>')}))
    assert stats.get("ok", 0) == 0  # cards on the page, none readable: a failed board
    _, stats = _run("careerviet", "all", _stub({"tat-ca-viec-lam-vi.html": (200, "<html><body>nada</body></html>")}))
    assert stats["ok"] == 1 and stats["jobs"] == 0  # a real empty board


# ----------------------------------------------------------------------- OCC
def _occ_card(i, title=None, company="Acme SA", loc="MONTERREY, NUEVO LEON"):
    title = title if title is not None else f"Analista {i}"
    return (f'<div class="card-job-offer mb-4" data-id=\'{i}\' id="jobcard-{i}"><h2 class="x">{title}</h2><span class="mr-2">$ 40,000 Mensual</span>'
            f'<div class="h-[21px] flex items-center gap-1"><span class="c">{company}</span></div>'
            f'<div class="no-alter-loc-text mt-1"><span></span><p class="l">{loc}</p></div></div>')


def _occ_page(*cards):
    return 200, "<html><body>" + "".join(cards) + "</body></html>"


def test_occ_paging_fields_and_slug():
    http = _stub({"/empleos/de-automatizacion/": _occ_page(_occ_card(101), _occ_card(102, company="Empresa confidencial")),
                  "/empleos/de-automatizacion-pagina-2/": _occ_page(_occ_card(103)),
                  "/empleos/de-automatizacion-pagina-3/": _occ_page(_occ_card(101))})  # out of range: OCC repeats page 1
    jobs, stats = _run("occ", "Automatización", http)
    assert [j["title"] for j in jobs] == ["Analista 101", "Analista 102", "Analista 103"]
    assert jobs[0]["company"] == "Acme SA" and jobs[1]["company"] == "?" and jobs[0]["location"] == "MONTERREY, NUEVO LEON"
    assert [j["url"] for j in jobs][0] == "https://www.occ.com.mx/empleo/oferta/101/"
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"www.occ.com.mx"} and _hosts(http) == {"www.occ.com.mx"}
    assert len(http.calls) == 3 and stats["jobs"] == 3
    assert kb._occ_slug("  Robótica & IA!! ") == "robotica-ia"


def test_occ_token_required_and_bad_cards():
    http = _stub({})
    _, stats = _run("occ", "!!!", http)
    assert http.calls == [] and stats.get("ok", 0) == 0
    cards = [_occ_card(1, title=""), '<div class="card-job-offer" id="x"><h2>No id</h2></div>', _occ_card("../evil", title="Bad id"),
             _occ_card(5, title="<script>alert(1)</script>Analista"), _occ_card(6)]
    jobs, _ = _run("occ", "ia", _stub({"/empleos/de-ia/": _occ_page(*cards)}))
    assert [j["title"] for j in jobs] == ["Analista", "Analista 6"]  # the script body is never page text
    assert all(urlsplit(j["url"]).hostname == "www.occ.com.mx" and j["url"].startswith("https://") for j in jobs)
    assert not any("<" in j["title"] for j in jobs)


def test_occ_refusal_challenge_and_empty_search():
    http = _stub({"/empleos/de-ia/": (403, None)})
    jobs, stats = _run("occ", "ia", http)
    assert jobs == [] and stats.get("ok", 0) == 0 and len(http.calls) == 1  # Cloudflare challenge: the board fails
    _, stats = _run("occ", "ia", _stub({"/empleos/de-ia/": (200, "<html><body>Just a moment...</body></html>")}))
    assert stats.get("ok", 0) == 0  # 200 with neither cards nor OCC's empty copy: not a healthy empty board
    jobs, stats = _run("occ", "ia", _stub({"/empleos/de-ia/": (200, "<p>No hay empleos que coincidan con tu b&uacute;squeda</p>")}))
    assert jobs == [] and stats["ok"] == 1
    jobs, stats = _run("occ", "ia", _stub({"/empleos/de-ia/": _occ_page(_occ_card(1)), "pagina-2": (429, None)}))
    assert len(jobs) == 1 and stats["ok"] == 1


# ----------------------------------------------------------------------- hostile markup
def test_hostile_markup_is_linear():
    t0 = time.time()
    cv = "".join(f'<div id="job-item-{i}"><a class="job_link" title="t{i}" href="/vi/x.{i}.html"><div><div>' for i in range(3000))  # never closed
    jobs, _ = _run("careerviet", "all", _stub({"tat-ca-viec-lam-vi.html": (200, cv), "trang-2": (404, None)}))
    occ = "".join(f"<div class=\"card-job-offer\" data-id='{i}'><h2>T{i}<div><p>" for i in range(3000))
    jobs2, _ = _run("occ", "ia", _stub({"/empleos/de-ia/": (200, occ), "pagina-2": (404, None)}))
    assert time.time() - t0 < 10 and len(jobs) <= 3000 and len(jobs2) <= 3000
    junk = "<" * 200000 + "<!--" + "<div " * 50000
    for name, tok in (("careerviet", "all"), ("occ", "ia")):
        _, stats = _run(name, tok, _stub({"": (200, junk)}))
        assert stats["tried"] == 1
    assert time.time() - t0 < 10
