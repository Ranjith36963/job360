"""Offline tests for the Keka Hire and Zoho Recruit readers (kit_ats_more.py) and build_india.py.

Fixtures in tests/fixtures/hunt_kit/ are trimmed copies of the live page shapes (2026-10-19). Nothing
touches the network: every request goes to a stub.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
FIX = Path(__file__).resolve().parent / "fixtures" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


more = _load("kit_ats_more_india_under_test", "kit_ats_more.py")
hk = _load("hunt_kit_india_under_test", "hunt_kit.py")
india = _load("build_india_under_test", "build_india.py")
F = more.ATS_FETCHERS
GUID = "2e810e5f-3702-4d48-a889-7edddd45a214"


def _fix(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _stub(routes: dict):
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    jobs = F[name]({"name": "Acme", "ats": name, **row}, http, stats)
    return jobs, stats


def test_both_registered_and_wired():
    assert {"keka", "zohorecruit"} <= set(F)
    assert {"keka", "zohorecruit"} <= set(hk.BOARD_SOURCES)


# ------------------------------------------------------------------ Keka
def test_keka_guid_from_careers_page():
    assert more.keka_guid(_fix("keka_careers.html")) == GUID
    assert more.keka_guid(_fix("keka_no_guid.html")) == ""
    assert more.keka_guid(None) == ""


def test_keka_two_calls_and_parse():
    http = _stub({"/careers/api/embedjobs/default/active/": (200, json.loads(_fix("keka_active.json"))),
                  "acme.keka.com/careers/": (200, _fix("keka_careers.html"))})
    jobs, stats = _run("keka", {"token": "Acme"}, http)
    assert http.calls == ["https://acme.keka.com/careers/", f"https://acme.keka.com/careers/api/embedjobs/default/active/{GUID}"]
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}
    by_title = {j["title"]: j for j in jobs}
    assert set(by_title) == {"ML Engineer", "Data Scientist", "LLM Engineer"}  # bad id, empty title and non-dict dropped
    ml = by_title["ML Engineer"]
    assert ml["url"] == "https://acme.keka.com/careers/jobdetails/144374" and ml["source"] == "keka" and ml["board"] == "Acme"
    assert ml["location"] == "Bangalore, Karnataka, India · Pune, Maharashtra, IN"
    assert ml["posted"] == "2026-10-14" and ml["remote"] is None  # a numeric jobType code is not read as remote
    assert ml["description"] == "About & us\nBuild models." and ml["extra"] == {"department": "AI", "experience": "3-6 years"}
    ds = by_title["Data Scientist"]
    assert ds["remote"] is True and ds["posted"] == "" and ds["location"] == "" and ds["description"] == ""
    assert by_title["LLM Engineer"]["remote"] is True and by_title["LLM Engineer"]["posted"] == "2026-10-01"


def test_keka_page_without_guid_counts_as_failed():
    http = _stub({"acme.keka.com/careers/": (200, _fix("keka_no_guid.html"))})
    jobs, stats = _run("keka", {"token": "acme"}, http)
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}
    assert len(http.calls) == 1  # never guesses the list URL


def test_keka_list_not_a_list_counts_as_failed():
    http = _stub({"/embedjobs/": (200, {"error": "x"}), "acme.keka.com/careers/": (200, _fix("keka_careers.html"))})
    assert _run("keka", {"token": "acme"}, http) == ([], {"tried": 1, "jobs": 0})


# ------------------------------------------------------------------ Zoho Recruit
def test_zoho_hidden_input_rows_with_missing_fields():
    http = _stub({"acme.zohorecruit.com/jobs/Careers": (200, _fix("zoho_careers.html"))})
    jobs, stats = _run("zohorecruit", {"token": "acme", "tld": "com"}, http)
    assert http.calls == ["https://acme.zohorecruit.com/jobs/Careers"]
    assert stats == {"tried": 1, "ok": 1, "jobs": 3}
    by_title = {j["title"]: j for j in jobs}
    assert set(by_title) == {"Prior Authorization Assistant", "AI Engineer", "Support Engineer"}  # unpublished, no-id, dup, not-hidden dropped
    pa = by_title["Prior Authorization Assistant"]
    assert pa["remote"] is True and pa["location"] == "Philippines" and pa["posted"] == "" and pa["description"] == ""
    assert pa["url"] == "https://acme.zohorecruit.com/jobs/Careers/655395000258975755"
    ai = by_title["AI Engineer"]
    assert ai["location"] == "Chennai, Tamil Nadu, India" and ai["posted"] == "2026-10-13" and ai["remote"] is None
    assert ai["description"] == "Work on LLM agents & tools."
    se = by_title["Support Engineer"]  # falls back to Job_Opening_Name; the literal "None" city is dropped
    assert se["location"] == "" and se["remote"] is None


def test_zoho_default_tries_in_then_com():
    http = _stub({"acme.zohorecruit.in/": (200, _fix("zoho_missing.html")),
                  "acme.zohorecruit.com/": (200, _fix("zoho_careers.html"))})
    jobs, stats = _run("zohorecruit", {"token": "acme"}, http)
    assert http.calls == ["https://acme.zohorecruit.in/jobs/Careers", "https://acme.zohorecruit.com/jobs/Careers"]
    assert len(jobs) == 3 and stats["ok"] == 1


def test_zoho_empty_site_is_ok_missing_site_is_failed():
    jobs, stats = _run("zohorecruit", {"token": "acme"}, _stub({"zohorecruit.in/": (200, _fix("zoho_empty.html"))}))
    assert jobs == [] and stats == {"tried": 1, "ok": 1, "jobs": 0}
    jobs, stats = _run("zohorecruit", {"token": "acme", "tld": "in"}, _stub({"zohorecruit.in/": (200, _fix("zoho_missing.html"))}))
    assert jobs == [] and stats == {"tried": 1, "jobs": 0}


# ------------------------------------------------------------------ tokens
@pytest.mark.parametrize("name", ["keka", "zohorecruit"])
@pytest.mark.parametrize("token", ["", "acme.evil.com", "a/b", "user@host", "-acme", "acme-", "a b"])
def test_token_must_be_one_dns_label(name: str, token: str):
    http = _stub({"": (200, _fix("keka_careers.html"))})
    jobs, stats = _run(name, {"token": token}, http)
    assert jobs == [] and stats == {"tried": 1, "jobs": 0} and http.calls == []


def test_zoho_unknown_tld_falls_back_to_known_domains_only():
    http = _stub({})
    _run("zohorecruit", {"token": "acme", "tld": "evil.com/x"}, http)
    assert http.calls == ["https://acme.zohorecruit.in/jobs/Careers", "https://acme.zohorecruit.com/jobs/Careers"]


@pytest.mark.parametrize(
    ("url", "ats", "token"),
    [
        ("https://lumel.keka.com/careers/jobdetails/144374", "keka", "lumel"),
        ("https://bruntwork.zohorecruit.com/jobs/Careers/655395000258975755", "zohorecruit", "bruntwork"),
        ("https://us-careers.zohorecruit.in/jobs/Careers", "zohorecruit", "us-careers"),
    ],
)
def test_board_from_job_url(url: str, ats: str, token: str):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)
    assert [r["token"] for r in hk.parse_extra(f"{ats}:{token}")] == [token]


# ------------------------------------------------------------------ build_india.py
def test_company_name_from_page():
    assert india.company_name(_fix("keka_careers.html"), "acme") == "Acme Labs"
    assert india.company_name("<title>Teachmint Careers | Join Our Team &amp; Grow</title>", "tm") == "Teachmint"
    assert india.company_name("<title>US Careers at Zoho Corporation</title>", "us-careers") == "Zoho Corporation"
    assert india.company_name(_fix("keka_no_guid.html"), "acme") == "acme"
    assert india.company_name(None, "acme") == "acme"


def test_read_seeds(tmp_path: Path):
    p = tmp_path / "seeds.txt"
    p.write_text("# c\nkeka:Lumel\nkeka:lumel\nzohorecruit:bruntwork.com\nzohorecruit:x.evil\nkeka:a.b.c\nbad:x\n\n", encoding="utf-8")
    assert india.read_seeds(p) == [("keka", "lumel", ""), ("zohorecruit", "bruntwork", "com")]
    shipped = india.read_seeds(KIT_DIR / "india_boards.txt")
    assert len(shipped) > 100 and all(a in ("keka", "zohorecruit") for a, _, _ in shipped)


def test_company_rows_for_india_boards():
    rows = [r for r in hk.load_companies(KIT_DIR / "companies.json")
            if r["ats"] in ("keka", "zohorecruit") and r.get("source") != "gulf-research"]  # Gulf rows: test_hunt_kit_gulf
    assert len(rows) > 100
    for r in rows:
        assert r["source"] == "commoncrawl" and r["token"] == r["board"] and r.get("country", "IN") == "IN"
        assert r.get("tld", "com") == "com"


def test_zoho_input_scan_is_linear_on_a_hostile_page():
    """Security review 2026-10-19: '<input ' * n with no '>' was quadratic (52 s at 560 KB)."""
    import time

    from src.hunt_kit import kit_ats_more

    page = "<input " * 300_000  # about 2 MB
    t = time.perf_counter()
    assert kit_ats_more._INPUT_TAG.findall(page) == []
    assert time.perf_counter() - t < 2.0


def test_company_name_takes_the_company_after_a_pipe_not_the_job_title():
    """Review 2026-10-19: GoKwik's careers page title made the company "Technical Program Manager"."""
    from src.hunt_kit.build_india import company_name

    gokwik = "<title>Technical Program Manager &#x2013; eCommerce &amp; Integrations | GoKwik</title>"
    assert company_name(gokwik, "gokwik") == "GoKwik"
    assert company_name("<title>Career at Fiftyfive Technologies</title>", "fiftyfive") == "Fiftyfive Technologies"
    assert company_name("<title>Acme - Careers</title>", "acme") == "Acme"
