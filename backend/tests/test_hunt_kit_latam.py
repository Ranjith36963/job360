"""Offline tests for hunt_kit/kit_latam.py (Gupy, HiringRoom, Recrutei, InHire, Solides, Vagas.com.br).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go and testdata/ (MIT, bed5ec9)
for each reader here (gupy: testdata/gupy list and detail JSON; hiringroom: testdata/hiringroom pager JSON and vacancy HTML;
recrutei: recrutei_test.go envelope and ld+json page; inhire: inhire_test.go; solides: solides_test.go page envelope;
vagas: vagas_test.go listing and ld+json page) and written fresh. Nothing touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


lt = _load("kit_latam_under_test", "kit_latam.py")
hk = _load("hunt_kit_latam_under_test", "hunt_kit.py")
F = lt.ATS_FETCHERS
NAMES = ("gupy", "hiringroom", "inhire", "recrutei", "solides", "vagas")
HEX = "66984666bb7169324800e116"
HEX2 = "6675a00fc0bd43544f2ce222"
HEX3 = "69b1d48a1121b4005a5cce59"


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers, data)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
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


def _https(jobs) -> bool:
    return all(urlsplit(j["url"]).scheme == "https" for j in jobs)


def test_every_reader_is_registered_and_wired():
    assert set(F) == set(NAMES)
    assert "kit_latam" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_latam.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_latam.py"' in served


@pytest.mark.parametrize("url,ats,token", [
    ("https://caju.gupy.io/jobs/12691526", "gupy", "caju"),
    ("https://portal.gupy.io/job-search/term=dev", "gupy", ""),
    ("https://stefaniniitsolutionsperu.hiringroom.com/jobs/get_vacancy/" + HEX, "hiringroom", "stefaniniitsolutionsperu"),
    ("https://jobs.recrutei.com.br/digisystem/vacancy/108736-analista", "recrutei", "digisystem"),
    ("https://contaazul.inhire.app/vagas/j1/senior-backend-engineer", "inhire", "contaazul"),
    ("https://api.inhire.app/job-posts/public/pages", "inhire", ""),
    ("https://dynamox.vagas.solides.com.br/vaga/2001", "solides", "dynamox"),
    ("https://www.vagas.com.br/vagas/v1234567/dev-backend", "vagas", ""),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)
    for host in ("gupy.io", "hiringroom.com", "recrutei.com.br", "inhire.app", "vagas.com.br"):
        assert not hk._UNSUPPORTED_HOSTS.search(host)


def test_parse_extra_reads_the_new_tokens():
    rows = hk.parse_extra("gupy:caju,hiringroom:stefaniniitsolutionsperu,recrutei:digisystem,inhire:contaazul,solides:dynamox,vagas:all")
    assert [r["ats"] for r in rows] == ["gupy", "hiringroom", "recrutei", "inhire", "solides", "vagas"]


# --------------------------------------------------------------------------- Gupy
GU_LIST = {"data": [
    {"id": 12691526, "name": "Pessoa Desenvolvedora FrontEnd Sênior", "addressCity": "", "addressStateShortName": "",
     "addressCountry": "Brasil", "remoteWorking": True, "workplaceType": "remote"},
    {"id": 12679978, "name": "Analista Sênior de Garantia da Receita", "addressCity": "São Paulo", "addressStateShortName": "SP",
     "addressCountry": "Brasil", "remoteWorking": False, "workplaceType": "hybrid"},
    {"id": 12679978, "name": "Duplicate", "addressCountry": "Brasil"}]}
GU_DETAIL = {"description": "<p>.</p>", "responsibilities": "<ul><li>Desenvolvimento de aplicações web</li></ul>",
             "prerequisites": "<p>&nbsp;</p>", "relevantExperiences": "<p>Benefícios: VR</p>", "publishedAt": "2026-10-16T17:28:19.254Z"}


def test_gupy_happy_path_maps_listing_and_detail():
    http = _stub({"subdomain=caju": (200, GU_LIST), "/jobs/12691526": (200, GU_DETAIL), "/jobs/12679978": (200, GU_DETAIL)})
    jobs, stats = _run("gupy", _row("gupy", "caju"), http)
    assert [j["url"] for j in jobs] == ["https://caju.gupy.io/jobs/12691526", "https://caju.gupy.io/jobs/12679978"]
    a, b = jobs
    assert (a["title"], a["location"], a["remote"], a["posted"]) == ("Pessoa Desenvolvedora FrontEnd Sênior", "Brasil", True, "2026-10-16")
    assert b["location"] == "São Paulo, SP, Brasil" and b["remote"] is None and b["extra"]["work_mode"] == "hybrid"
    assert "Desenvolvimento de aplicações web" in a["description"] and "Benefícios: VR" in a["description"] and "nbsp" not in a["description"]
    assert _https(jobs) and (stats["ok"], stats["jobs"]) == (1, 2)
    assert _hosts(http) == {"private-api.gupy.io"}


def test_gupy_detail_only_for_title_ok_cards_and_a_refusal_ends_details():
    http = _stub({"subdomain=caju": (200, GU_LIST), "/jobs/12691526": (200, GU_DETAIL)})
    jobs, _ = _run("gupy", _row("gupy", "caju", title_ok=lambda t: "Analista" not in t), http)
    assert len(jobs) == 2 and jobs[0]["description"] and jobs[1]["description"] == ""
    assert not any("12679978" in c[0] for c in http.calls)
    http = _stub({"subdomain=caju": (200, GU_LIST), "/jobs/12691526": (429, None)})
    jobs, _ = _run("gupy", _row("gupy", "caju"), http)
    assert len(jobs) == 2 and not any(j["description"] for j in jobs)
    assert [c[0] for c in http.calls if "/jobs/1" in c[0]] == ["https://private-api.gupy.io/job-publication/public/jobs/12691526"]
    assert all(c[4] for c in http.calls if "/jobs/1" in c[0])  # sent once, no 429 retry


def test_gupy_unknown_subdomain_and_bad_tokens_fail_the_board_and_one_bad_row_costs_one():
    jobs, stats = _run("gupy", _row("gupy", "gone"), _stub({"subdomain=gone": (404, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    for tok in ("", "a b", "x/y", "-x", "a.b"):
        http = _stub({})
        assert _run("gupy", _row("gupy", tok), http)[0] == [] and not http.calls
    rows = {"data": [None, {"id": True, "name": "x"}, {"id": 5, "name": "  "}, {"id": 6, "name": "Ok"}, "s"]}
    jobs, _ = _run("gupy", _row("gupy", "caju"), _stub({"subdomain=caju": (200, rows)}))
    assert [j["title"] for j in jobs] == ["Ok"]


# --------------------------------------------------------------------------- HiringRoom
def _hr_card(cid, title, loc="Lima, Perú", sched="Full-time", modality="Híbrido", href=None):
    href = href if href is not None else f"/jobs/get_vacancy/{cid}"
    return (f'<a href="{href}" class="text-decoration-none"><div class="card"><h4 class="font-black name__vacancy">\n  {title}\n</h4>'
            f'<p><span><i class="hr-Location-pin hrc-black"></i>\n {loc} </span></p>'
            f'<p><span><i class="hr-Work-area"></i> Tecnología, Sistemas y Telecomunicaciones / Tecnologia </span></p>'
            f'<p><span><i class="hr-Clock mr-1"></i> {sched} </span></p><p><span><i class="hrc-black hr-Company"></i> {modality} </span></p>'
            f'<p><span><i class="hr-Campus"></i> Senior </span></p></div></a>')


def _hr_page(*cards, total):
    return 200, {"result": "success", "data": {"htmlContent": "".join(cards), "total_vacancies": total}}


HR_VACANCY = ('<html><body><div class="main__description"><h6 class="x"><i class="hr-File"></i> Descripción del puesto </h6>'
              '<div class="job-description-content"><p>Buscamos <b>Ejecutivos</b>.</p></div>'
              '<h6>Requisitos</h6><div class="job-description-content"><ul><li>Experiencia B2B</li></ul></div></div></body></html>')
HR_BASE = "https://stefaniniitsolutionsperu.hiringroom.com/"


def _hr_http(pages, vacancy=HR_VACANCY, vac_status=200):
    def pager(url, headers, data):
        n = int(url.rsplit("/", 1)[1])
        assert parse_qs(data.decode()) == {"selectedPage": [str(n)], "typePortal": ["external"]}
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
        return pages[n - 1] if n <= len(pages) else (500, None)
    return _stub({"getVacanciesForPortal": pager, "/jobs/get_vacancy/": (vac_status, vacancy if vac_status == 200 else None)})


def test_hiringroom_pages_to_the_stated_total_and_reads_the_vacancy_sections():
    pages = [_hr_page(_hr_card(HEX, "Analista QA"), _hr_card(HEX2, "Dev Java", modality="Remoto", sched="Freelance"), total=3),
             _hr_page(_hr_card(HEX2, "Dev Java"), _hr_card(HEX3, "Account Executive (Hunter)"), total=3)]
    http = _hr_http(pages)
    jobs, stats = _run("hiringroom", _row("hiringroom", "stefaniniitsolutionsperu"), http)
    assert [j["url"] for j in jobs] == [HR_BASE + f"jobs/get_vacancy/{x}" for x in (HEX, HEX2, HEX3)]
    a, b, _c = jobs
    assert (a["title"], a["location"], a["remote"], a["posted"]) == ("Analista QA", "Lima, Perú", None, "")
    assert b["remote"] is True and b["extra"]["employment_type"] == "contract" and a["extra"]["employment_type"] == "full_time"
    assert a["extra"]["level"] == "Senior" and a["extra"]["area"].startswith("Tecnología")
    assert a["description"].index("Descripción del puesto") < a["description"].index("Requisitos") < a["description"].index("Experiencia B2B")
    assert [c[0] for c in http.calls if c[1] == "POST"] == [HR_BASE + "jobs/getVacanciesForPortal/1", HR_BASE + "jobs/getVacanciesForPortal/2"]
    assert _hosts(http) == {"stefaniniitsolutionsperu.hiringroom.com"} and _https(jobs) and stats["jobs"] == 3


def test_hiringroom_off_host_or_malformed_hrefs_are_not_cards_and_never_fetched():
    cards = [_hr_card(HEX, "Good"), _hr_card(HEX2, "Evil", href=f"https://evil.example/jobs/get_vacancy/{HEX2}"),
             _hr_card(HEX3, "Scheme", href=f"//evil.example/jobs/get_vacancy/{HEX3}"), _hr_card("zz", "Short", href="/jobs/get_vacancy/zz"),
             '<a href="/jobs/get_vacancy/' + "a" * 24 + '"><p>no title</p></a>']
    http = _hr_http([_hr_page(*cards, total=1)])
    jobs, _ = _run("hiringroom", _row("hiringroom", "stefaniniitsolutionsperu"), http)
    assert [j["title"] for j in jobs] == ["Good"]
    assert _hosts(http) == {"stefaniniitsolutionsperu.hiringroom.com"} and _job_hosts(jobs) == {"stefaniniitsolutionsperu.hiringroom.com"}


def _job_hosts(jobs) -> set:
    return {urlsplit(j["url"]).hostname for j in jobs}


def test_hiringroom_failures_a_repeat_page_and_refused_vacancy_pages():
    p1 = _hr_page(_hr_card(HEX, "A"), total=5)
    jobs, _ = _run("hiringroom", _row("hiringroom", "t"), _hr_http([p1, p1]))  # page 2 adds nothing: stop, keep page 1
    assert [j["title"] for j in jobs] == ["A"]
    jobs, _ = _run("hiringroom", _row("hiringroom", "t"), _hr_http([p1]))  # page 2 is a 500: keep page 1
    assert len(jobs) == 1
    for bad in ((500, None), (200, {"result": "error"}), (200, {"result": "success", "data": {"htmlContent": ""}}), (200, [])):
        jobs, stats = _run("hiringroom", _row("hiringroom", "t"), _hr_http([bad]))
        assert jobs == [] and stats.get("ok", 0) == 0
    http = _hr_http([_hr_page(_hr_card(HEX, "A"), _hr_card(HEX2, "B"), total=2)], vac_status=403)
    jobs, _ = _run("hiringroom", _row("hiringroom", "t"), http)
    assert len(jobs) == 2 and not any(j["description"] for j in jobs)
    assert len([c for c in http.calls if "get_vacancy" in c[0]]) == 1  # the first refusal ended the pages
    for tok in ("", "a.b", "x y", "-x", "stefanini.hiringroom.com"):
        http = _stub({})
        assert _run("hiringroom", _row("hiringroom", tok), http)[0] == [] and not http.calls


def test_hiringroom_deep_and_unclosed_markup_is_linear_and_loses_nothing():
    t0 = time.perf_counter()
    hostile = _hr_card(HEX, "Good") + ('<a href="/jobs/get_vacancy/%s"><h4 class="name__vacancy">Nested</h4>' % HEX2) * 3000 + "<div>" * 30000
    deep_page = '<div class="job-description-content">' + "<div>" * 30000 + "texto" + "</div>" * 30000
    jobs, _ = _run("hiringroom", _row("hiringroom", "t"), _hr_http([_hr_page(hostile, total=2)], vacancy=deep_page))
    assert [j["title"] for j in jobs][0] == "Good"
    assert time.perf_counter() - t0 < 10.0


# --------------------------------------------------------------------------- Recrutei
RC_BASE = "https://jobs.recrutei.com.br/digisystem/vacancy/"


def _rc_item(iid, title, loc, link, regime="CLT", company="Digisystem"):
    return {"id": iid, "title": title, "regime": regime, "company_name": company, "location": loc, "public_link": link, "slug": "s"}


def _rc_list(*items, total=None):
    return 200, {"message": "ok", "data": {"total": len(items) if total is None else total, "departments": 1,
                                           "vacancies": [{"department": "Geral", "total": len(items), "items": list(items)}]}}


def _rc_page(desc="Vaga <b>dev</b>", date="21/10/2026 09:30:00"):
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": "x", "description": desc, "datePosted": date,
          "employmentType": "FULL_TIME", "jobLocation": {"address": {"addressLocality": "undefined"}}}
    return f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body></body></html>'


def test_recrutei_happy_path_post_body_company_regime_and_date():
    items = [_rc_item(108736, "Analista", ["Brasília", "DF", "Brasil"], RC_BASE + "108736-analista"),
             _rc_item(157190, "Remote dev", "Não informado", RC_BASE + "157190-dev", regime="Pessoa Jurídica", company="Outra"),
             _rc_item(3, "Outra", "Remote, BR", RC_BASE + "3-x", regime="CLT ou PJ", company="")]
    seen = {}

    def lister(url, headers, data):
        seen.update(url=url, data=data, headers=headers)
        return _rc_list(*items)
    http = _stub({"per-departments": lister, "/vacancy/": (200, _rc_page())})
    jobs, _ = _run("recrutei", _row("recrutei", "digisystem"), http)
    assert seen["url"] == "https://api.recrutei.com.br/api/v2/vacancies/per-departments/digisystem" and json.loads(seen["data"]) == {"search": ""}
    a, b, c = jobs
    assert (a["location"], a["posted"], a["company"], a["description"]) == ("Brasília, DF, Brasil", "2026-10-21", "Digisystem", "Vaga dev")
    assert (b["location"], b["remote"], b["company"], b["extra"]["employment_type"]) == ("", True, "Outra", "contract")
    assert c["company"] == "Recrutei Co" and c["extra"]["employment_type"] == "" and c["remote"] is True
    assert _hosts(http) == {"api.recrutei.com.br", "jobs.recrutei.com.br"} and _https(jobs)


def test_recrutei_total_mismatch_fails_and_off_host_or_http_links_are_dropped():
    http = _stub({"per-departments": _rc_list(_rc_item(1, "A", [], RC_BASE + "1-a"), total=5)})
    jobs, stats = _run("recrutei", _row("recrutei", "digisystem"), http)
    assert jobs == [] and stats.get("ok", 0) == 0
    items = [_rc_item(1, "Good", [], RC_BASE + "1-a"), _rc_item(2, "Evil", [], "https://evil.example/digisystem/vacancy/2-x"),
             _rc_item(3, "Plain", [], "http://jobs.recrutei.com.br/digisystem/vacancy/3-x"), _rc_item(4, "Look", [], "https://jobs.recrutei.com.br.evil.example/x"),
             "junk", _rc_item(True, "Bool", [], RC_BASE + "5-x"), _rc_item(6, "  ", [], RC_BASE + "6-x")]
    http = _stub({"per-departments": _rc_list(*items), "/vacancy/": (200, _rc_page())})
    jobs, _ = _run("recrutei", _row("recrutei", "digisystem"), http)
    assert [j["title"] for j in jobs] == ["Good"]
    assert _hosts(http) == {"api.recrutei.com.br", "jobs.recrutei.com.br"}
    assert [c[0] for c in http.calls if c[1] == "GET"] == [RC_BASE + "1-a"]


def test_recrutei_refusal_ends_details_title_ok_and_bad_tokens():
    items = [_rc_item(i, f"Role {i}", [], RC_BASE + f"{i}-r") for i in (1, 2, 3)]
    http = _stub({"per-departments": _rc_list(*items), "/vacancy/": (403, None)})
    jobs, _ = _run("recrutei", _row("recrutei", "digisystem"), http)
    assert len(jobs) == 3 and len([c for c in http.calls if c[1] == "GET"]) == 1
    http = _stub({"per-departments": _rc_list(*items), "/vacancy/": (200, _rc_page())})
    jobs, _ = _run("recrutei", _row("recrutei", "digisystem", title_ok=lambda t: t.endswith("2")), http)
    assert [bool(j["description"]) for j in jobs] == [False, True, False]
    for tok in ("", "a/b", "a b", "a.b"):
        http = _stub({})
        assert _run("recrutei", _row("recrutei", tok), http)[0] == [] and not http.calls


def test_recrutei_deeply_nested_ld_page_costs_only_that_page():
    deep = '<script type="application/ld+json">' + '{"a":' * 100000 + "1" + "}" * 100000 + "</script>" + "<div>" * 30000
    items = [_rc_item(1, "Good", [], RC_BASE + "1-a"), _rc_item(2, "Deep", [], RC_BASE + "2-b")]
    http = _stub({"per-departments": _rc_list(*items), "/vacancy/1-a": (200, _rc_page()), "/vacancy/2-b": (200, deep)})
    jobs, _ = _run("recrutei", _row("recrutei", "digisystem"), http)
    assert [(j["title"], bool(j["description"])) for j in jobs] == [("Good", True), ("Deep", False)]


# --------------------------------------------------------------------------- InHire
IN_LIST = {"jobsPage": [
    {"jobId": "j1", "displayName": " Consultor(a) Sênior ", "workplaceType": "remote", "location": "Florianópolis, SC"},
    {"jobId": "../x", "displayName": "Traversal", "location": ""}, {"jobId": "j3", "displayName": "!!!", "workplaceType": "on-site"},
    {"jobId": "j4", "displayName": ""}, "junk"]}
IN_DETAIL = {"description": "&lt;p&gt;Vaga &amp;amp; benef&lt;/p&gt;", "publishedAt": "2026-10-01T10:00:00Z", "createdAt": "2026-09-01"}


def test_inhire_happy_path_sends_the_tenant_header_and_builds_the_slug_url():
    seen = []

    def route(url, headers, data):
        seen.append((url, headers.get("X-Tenant")))
        return 200, IN_LIST if url.endswith("/pages") else IN_DETAIL
    http = _stub({"api.inhire.app": route})
    jobs, _ = _run("inhire", _row("inhire", "contaazul"), http)
    assert [j["url"] for j in jobs] == ["https://contaazul.inhire.app/vagas/j1/consultor-a-senior", "https://contaazul.inhire.app/vagas/j3/vaga"]
    a, b = jobs
    assert (a["title"], a["location"], a["remote"], a["posted"]) == ("Consultor(a) Sênior", "Florianópolis, SC", True, "2026-10-01")
    assert "Vaga" in a["description"] and b["extra"]["work_mode"] == "onsite"
    assert {t for _u, t in seen} == {"contaazul"} and len(seen) == 3
    assert _hosts(http) == {"api.inhire.app"} and _https(jobs)


def test_inhire_refusal_ends_details_failed_list_fails_and_bad_tokens():
    http = _stub({"/pages/": (429, None), "/pages": (200, IN_LIST)})
    jobs, _ = _run("inhire", _row("inhire", "contaazul"), http)
    assert len(jobs) == 2 and len([c for c in http.calls if "/pages/" in c[0]]) == 1
    jobs, stats = _run("inhire", _row("inhire", "contaazul"), _stub({"/pages": (404, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    for tok in ("", "a b", "a.b", "x/y"):
        http = _stub({})
        assert _run("inhire", _row("inhire", tok), http)[0] == [] and not http.calls


# --------------------------------------------------------------------------- Solides
def _sol(pid, title, state="em_andamento", city="Florianópolis", code="SC", home=False, kind="hibrido", desc="<p>Build things</p><script>evil()</script>"):
    return {"id": pid, "title": title, "description": desc, "currentState": state, "city": {"name": city}, "state": {"name": "x", "code": code},
            "homeOffice": home, "jobType": kind, "createdAt": "2026-06-11"}


def _sol_page(pages, *rows):
    return 200, {"data": {"count": 3, "currentPage": 1, "totalPages": pages, "data": list(rows)}}


def test_solides_pages_to_total_pages_skips_closed_and_maps_fields():
    http = _stub({"page=1": _sol_page(2, _sol(2001, "  Backend Engineer  "), _sol(2002, "Closed", state="encerrada")),
                  "page=2": _sol_page(2, _sol(2003, "Remote SRE", city="", code="", home=True, kind="remoto"), _sol(2001, "Dup"))})
    jobs, stats = _run("solides", _row("solides", "dynamox"), http)
    assert [j["url"] for j in jobs] == ["https://dynamox.vagas.solides.com.br/vaga/2001", "https://dynamox.vagas.solides.com.br/vaga/2003"]
    a, b = jobs
    assert (a["title"], a["location"], a["posted"], a["remote"], a["extra"]["work_mode"]) == ("Backend Engineer", "Florianópolis, SC", "2026-06-11", None, "hybrid")
    assert a["description"] == "Build things" and b["remote"] is True and b["location"] == ""
    q = [parse_qs(urlsplit(c[0]).query) for c in http.calls]
    assert q == [{"slug": ["dynamox"], "take": ["25"], "page": ["1"]}, {"slug": ["dynamox"], "take": ["25"], "page": ["2"]}]
    assert _hosts(http) == {"apigw.solides.com.br"} and _https(jobs) and stats["jobs"] == 2


def test_solides_failures_and_one_bad_row():
    http = _stub({"page=1": _sol_page(3, _sol(1, "A")), "page=2": (500, None)})
    jobs, _ = _run("solides", _row("solides", "reivax"), http)
    assert [j["title"] for j in jobs] == ["A"] and len(http.calls) == 2
    jobs, stats = _run("solides", _row("solides", "reivax"), _stub({"page=1": (403, None)}))
    assert jobs == [] and stats.get("ok", 0) == 0
    jobs, _ = _run("solides", _row("solides", "reivax"), _stub({"page=1": _sol_page(1, None, {"id": "x"}, _sol(5, "Ok", city=None), "s")}))
    assert [j["title"] for j in jobs] == ["Ok"]
    endless = _stub({"page=": _sol_page(10 ** 6, _sol(1, "A"))})
    _run("solides", _row("solides", "reivax"), endless)
    assert len(endless.calls) <= lt.SOLIDES_PAGES
    for tok in ("", "a b", "a.b"):
        http = _stub({})
        assert _run("solides", _row("solides", tok), http)[0] == [] and not http.calls


# --------------------------------------------------------------------------- Vagas.com.br
VG = "https://www.vagas.com.br"


def _vg_listing(*hrefs):
    return "<html><body>" + "".join(f'<a href="{h}">Vaga</a>' for h in hrefs) + "</body></html>"


def _vg_page(title="Dev Backend", company="Acme", desc="<p>Flattened</p>", block="<p>Bloco <b>real</b></p>"):
    ld = {"@type": "JobPosting", "title": title, "description": desc, "datePosted": "2026-10-20",
          "hiringOrganization": {"@type": "Organization", "name": company},
          "jobLocation": {"address": {"addressLocality": "São Paulo", "addressRegion": "SP", "addressCountry": "BR"}}}
    inner = f'<div class="job-description__text">{block}</div>' if block else ""
    return f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body>{inner}</body></html>'


def _vg_http(listings: dict, pages: dict, default_listing=(200, _vg_listing())):
    routes: dict = {}
    for area, per_page in listings.items():
        for n, body in per_page.items():
            routes[f"/{area}?pagina={n}"] = (200, body) if isinstance(body, str) else body
    routes.update({f"/vagas/v{k}": (200, v) if isinstance(v, str) else v for k, v in pages.items()})
    routes["?pagina="] = default_listing
    return _stub(routes)


def test_vagas_lists_areas_dedups_across_them_and_reads_the_page_body():
    http = _vg_http({"vagas-de-tecnologia": {1: _vg_listing("/vagas/v111/dev-backend", "/vagas/v222/dev-frontend"), 2: _vg_listing("/vagas/v111/dev-backend")},
                     "vagas-de-programador": {1: _vg_listing("/vagas/v222/dev-frontend", VG + "/vagas/v333/analista?x=1#y")}},
                    {111: _vg_page(), 222: _vg_page("Dev Frontend", "Beta"), 333: _vg_page("Analista", "Gama")})
    jobs, stats = _run("vagas", _row("vagas", "all"), http)
    assert [j["url"] for j in jobs] == [VG + "/vagas/v111/dev-backend", VG + "/vagas/v222/dev-frontend", VG + "/vagas/v333/analista"]
    a = jobs[0]
    assert (a["title"], a["company"], a["location"], a["posted"], a["description"]) == ("Dev Backend", "Acme", "São Paulo, SP, BR", "2026-10-20", "Bloco real")
    assert [j["company"] for j in jobs] == ["Acme", "Beta", "Gama"] and _https(jobs) and stats["jobs"] == 3
    assert _hosts(http) == {"www.vagas.com.br"}
    assert len([c for c in http.calls if "/vagas/v111" in c[0]]) == 1


def test_vagas_off_host_links_are_never_fetched_and_the_ld_description_is_the_fallback():
    http = _vg_http({"vagas-de-tecnologia": {1: _vg_listing("https://evil.example/vagas/v999/x", "//evil.example/vagas/v998/x",
                                                            "http://www.vagas.com.br/vagas/v997/x", "/vagas/v111/dev")}},
                    {111: _vg_page(block="")})
    jobs, _ = _run("vagas", _row("vagas", "all"), http)
    assert [j["url"] for j in jobs] == [VG + "/vagas/v111/dev"] and jobs[0]["description"] == "Flattened"
    assert _hosts(http) == {"www.vagas.com.br"}


def test_vagas_title_ok_cap_refusal_and_incomplete_pages():
    hrefs = [f"/vagas/v{i}/dev-{i}" for i in range(1, 41)]
    http = _vg_http({"vagas-de-tecnologia": {1: _vg_listing(*hrefs)}}, {i: _vg_page(f"Dev {i}") for i in range(1, 41)})
    jobs, _ = _run("vagas", _row("vagas", "all"), http)
    assert len(jobs) == lt.DETAIL_CAP
    http = _vg_http({"vagas-de-tecnologia": {1: _vg_listing(*hrefs[:3])}}, {i: _vg_page(f"Dev {i}") for i in range(1, 4)})
    jobs, _ = _run("vagas", _row("vagas", "all", title_ok=lambda t: t.endswith("dev 2")), http)
    assert [j["title"] for j in jobs] == ["Dev 2"] and len([c for c in http.calls if "/vagas/v" in c[0]]) == 1
    http = _vg_http({"vagas-de-tecnologia": {1: _vg_listing(*hrefs[:3])}}, {1: (403, None), 2: _vg_page(), 3: _vg_page()})
    jobs, _ = _run("vagas", _row("vagas", "all"), http)
    assert jobs == [] and len([c for c in http.calls if "/vagas/v" in c[0]]) == 1  # the refusal ended the detail pages
    jobs, stats = _run("vagas", _row("vagas", "all"), _stub({"?pagina=": (403, None)}))  # first area, first page refused
    assert jobs == [] and stats.get("ok", 0) == 0
    http = _vg_http({"vagas-de-tecnologia": {1: _vg_listing("/vagas/v1/a")}}, {1: _vg_page(company="")})
    assert _run("vagas", _row("vagas", "all"), http)[0] == []  # no employer named in the ad: not listed


def test_vagas_a_refused_later_area_keeps_what_was_gathered_and_stops_listing():
    http = _vg_http({"vagas-de-tecnologia": {1: _vg_listing("/vagas/v1/a")}, "vagas-de-programador": {1: (429, None)}}, {1: _vg_page()})
    jobs, _ = _run("vagas", _row("vagas", "all"), http)
    assert len(jobs) == 1 and not any("vagas-de-desenvolvedor" in c[0] for c in http.calls)


def test_vagas_hostile_listing_and_deep_job_page_do_not_lose_the_board():
    t0 = time.perf_counter()
    listing = _vg_listing("/vagas/v1/good") + '<a href="/vagas/v2/deep">' * 3000 + "<div>" * 30000
    deep = '<script type="application/ld+json">' + '{"a":' * 100000 + "1" + "}" * 100000 + "</script>" + '<div class="job-description__text">' + "<div>" * 30000
    http = _vg_http({"vagas-de-tecnologia": {1: listing}}, {1: _vg_page(), 2: deep})
    jobs, _ = _run("vagas", _row("vagas", "all"), http)
    assert [j["title"] for j in jobs] == ["Dev Backend"]
    assert time.perf_counter() - t0 < 10.0
