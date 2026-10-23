"""Offline tests for the RippleHire reader (kit_ats_more.py) and its companies.json rows.

Fixtures in tests/fixtures/hunt_kit/ripplehire_*.json are trimmed copies of live responses (2026-10-20):
only the fields the reader reads. Nothing touches the network: every request goes to a stub.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote

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


more = _load("kit_ats_more_ripplehire_under_test", "kit_ats_more.py")
hk = _load("hunt_kit_ripplehire_under_test", "hunt_kit.py")
TOK = "AbCdEfGhIjKlMnOpQr12"  # a fake 20-character career-site token


def _fix(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _stub(list_pages: list, detail=None, list_status: int = 200):
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        calls.append((method, url, data, headers))
        if "candidatejobsearch" in url:
            n = sum(1 for c in calls if "candidatejobsearch" in c[1]) - 1
            return (list_status, list_pages[n] if n < len(list_pages) else {"jobVoList": []}) if list_status == 200 else (list_status, None)
        if "candidatejobdetail" in url:
            return (200, detail) if detail is not None else (500, None)
        return 404, None

    http.calls = calls
    return http


def _run(row: dict, http):
    stats: dict = {}
    jobs = more.ATS_FETCHERS["ripplehire"]({"name": "Mphasis", "ats": "ripplehire", **row}, http, stats)
    return jobs, stats


def test_registered_and_wired():
    assert "ripplehire" in more.ATS_FETCHERS
    assert "ripplehire" in hk.BOARD_SOURCES


@pytest.mark.parametrize("token,ok", [
    (f"mphasis/{TOK}", ("mphasis", TOK)),
    (f"Mphasis/{TOK}", ("mphasis", TOK)),         # tenant is a host label: lowercased; the token keeps its case
    (f"app/{TOK}", None),                         # shared sandbox host answers any token with another company's jobs
    (f"www/{TOK}", None),
    ("mphasis", None),                            # no token
    ("mphasis/short", None),
    (f"mphasis/{TOK}x", None),
    (f"-bad/{TOK}", None),
    (f"a.b/{TOK}", None),                         # one DNS label only: no host injection
    (f"mphasis/{TOK[:-1]}/", None),
])
def test_board_parts(token: str, ok):
    assert more.ripplehire_board(token) == ok


def test_list_then_detail_for_title_matches_only():
    detail = _fix("ripplehire_mphasis_detail.json")
    http = _stub([_fix("ripplehire_mphasis_list.json")], detail)
    row = {"token": f"mphasis/{TOK}", "title_ok": lambda t: "Data" in t}
    jobs, stats = _run(row, http)
    assert stats["ok"] == 1 and len(jobs) == 3
    method, url, data, headers = http.calls[0]
    assert method == "POST" and url == "https://mphasis.ripplehire.com/candidate/candidatejobsearch"
    form = parse_qs(data.decode())
    assert form["lang"] == ["en"]
    assert json.loads(unquote(form["careerSiteUrlParams"][0])) == {"page": 0, "search": "*:*", "token": TOK, "pagesize": 100}
    assert headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    details = [c for c in http.calls if "candidatejobdetail" in c[1]]
    assert [c[1] for c in details] == [f"https://mphasis.ripplehire.com/candidate/candidatejobdetail?token={TOK}&jobSeq=917235"]
    first = jobs[0]
    assert first["title"] == "Senior Data Engineering" and first["location"] == "Pune"
    assert first["url"] == f"https://mphasis.ripplehire.com/candidate/?token={TOK}&source=CAREERSITE#detail/job/917235"
    assert first["posted"] == "2026-10-19"  # publishDetails.CAREER_SITE
    assert first["description"].startswith("Job Description for Data Engineer")
    assert first["extra"]["years_min"] == 4  # list jobMinExp is in YEARS (the detail's 48 is months)
    assert all(j["description"] == "" for j in jobs[1:])  # no detail spent on non-matching titles


def test_pages_until_short_page_or_total():
    page = _fix("ripplehire_hdfcbank_list.json")
    full = dict(page, totalJobCount=250, jobVoList=[dict(page["jobVoList"][0], jobSeq=str(1000 + i)) for i in range(100)])
    http = _stub([full, dict(full, jobVoList=[dict(j, jobSeq=str(2000 + i)) for i, j in enumerate(full["jobVoList"])]), page])
    jobs, _ = _run({"token": f"hdfcbank/{TOK}", "title_ok": lambda t: False}, http)
    pages = [json.loads(unquote(parse_qs(c[2].decode())["careerSiteUrlParams"][0]))["page"] for c in http.calls]
    assert pages == [0, 1, 2]
    assert len(jobs) == 203


def test_bad_token_counts_as_failed_and_makes_no_detail_call():
    http = _stub([], list_status=500)
    jobs, stats = _run({"token": f"mphasis/{TOK}"}, http)
    assert jobs == [] and stats.get("ok", 0) == 0
    assert len(http.calls) == 1


def test_malformed_board_sends_nothing():
    http = _stub([])
    jobs, stats = _run({"token": f"app/{TOK}"}, http)
    assert jobs == [] and stats.get("ok", 0) == 0 and http.calls == []


def test_failed_detail_keeps_the_list_job():
    http = _stub([_fix("ripplehire_mphasis_list.json")], detail=None)
    jobs, _ = _run({"token": f"mphasis/{TOK}"}, http)
    assert len(jobs) == 3 and jobs[0]["description"] == "" and jobs[0]["posted"] == ""


def test_detail_cap():
    page = _fix("ripplehire_hdfcbank_list.json")
    many = dict(page, totalJobCount=60, jobVoList=[dict(page["jobVoList"][0], jobSeq=str(i)) for i in range(1, 61)])
    http = _stub([many], _fix("ripplehire_mphasis_detail.json"))
    _run({"token": f"hdfcbank/{TOK}"}, http)
    assert sum(1 for c in http.calls if "candidatejobdetail" in c[1]) == more.RIPPLEHIRE_DETAIL_CAP


def test_posted_falls_back_to_posting_date_and_reads_a_string_publish_block():
    assert more._ripplehire_posted({"jobPostingDate": "19-Oct-2026"}) == "2026-10-19"
    assert more._ripplehire_posted({"publishDetails": '{"CAREER_SITE": "2026-10-18T01:00:00Z"}'}) == "2026-10-18"
    assert more._ripplehire_posted({"publishDetails": "not json", "jobPostingDate": "bad"}) == ""


def test_remote_from_title_or_place_and_control_chars_dropped():
    items = [{"jobSeq": "1", "jobTitle": "AI Engineer (Remote)", "locations": "Pune"},
             {"jobSeq": "2", "jobTitle": "AI Engineer", "locations": "Remote WFH, Blue Bell - Pennsylvania - USA"},
             {"jobSeq": "3", "jobTitle": "AI Engineer", "locations": "Pune"},
             {"jobSeq": "x", "jobTitle": "bad id", "locations": "Pune"},
             {"jobSeq": "3", "jobTitle": "duplicate", "locations": "Pune"}]
    jobs = more.ripplehire_jobs({"name": "X"}, "x", TOK, items, {"3": {"jobDesc": "<p>a\x00b</p>"}})
    assert [j["remote"] for j in jobs] == [True, True, None]
    assert jobs[2]["description"] == "ab"


@pytest.mark.parametrize("url,token", [
    (f"https://hdfcbank.ripplehire.com/candidate/?token={TOK}&source=CAREERSITE#list", f"hdfcbank/{TOK}"),
    (f"https://axisbank.ripplehire.com/candidate/?token={TOK}&lang=en&source=CAREERSITE#detail/job/120919", f"axisbank/{TOK}"),
    ("https://hdfcbank.ripplehire.com/candidate/careers", ""),
])
def test_board_from_job_url(url: str, token: str):
    got = hk.board_from_job_url(url)
    assert got["kind"] == "ats" and got["ats"] == "ripplehire" and got["token"] == token


def test_hostile_description_and_publish_block_stay_fast_and_keep_the_board():
    """Security review 2026-10-20: '<p ' * n was quadratic in _text; a nested publishDetails emptied the board."""
    import time
    t0 = time.perf_counter()
    for bad in ("<p " * 100_000, "</p" * 100_000, "<" * 100_000):
        more._text(bad)
    assert time.perf_counter() - t0 < 2.0
    assert more._text("<p>a</p><div class='x'>b</div><b>c</b> d") == "a\nb\nc d"
    items = [{"jobSeq": "1", "jobTitle": "AI Engineer", "locations": "Pune"},
             {"jobSeq": "2", "jobTitle": "AI Engineer", "locations": "Pune"}]
    jobs = more.ripplehire_jobs({"name": "X"}, "x", TOK, items, {"1": {"publishDetails": "[" * 200_000 + "]" * 200_000}})
    assert len(jobs) == 2 and jobs[0]["posted"] == ""


def test_unicode_digit_job_ids_are_skipped():
    jobs = more.ripplehire_jobs({"name": "X"}, "x", TOK, [{"jobSeq": "١٢", "jobTitle": "AI", "locations": "Pune"}], {})
    assert jobs == []


def test_companies_extra_accepts_tenant_and_token_with_case():
    """Review 2026-10-20: the one-slug branch had no '/', so the form README and --why-missed advise was dropped."""
    assert hk.parse_extra(f"ripplehire:mphasis/{TOK}") == [
        {"name": "mphasis", "ats": "ripplehire", "token": f"mphasis/{TOK}", "source": "user"}]


def test_why_missed_advice_round_trips_through_parse_extra():
    url = f"https://hdfcbank.ripplehire.com/candidate/?token={TOK}&source=CAREERSITE#detail/job/1"
    b = hk.board_from_job_url(url)
    assert hk.parse_extra(f"{b['ats']}:{b['token']}")[0]["token"] == f"hdfcbank/{TOK}"


def test_shared_sandbox_host_is_not_a_board():
    assert hk.board_from_job_url(f"https://app.ripplehire.com/candidate/?token={TOK}")["ats"] != "ripplehire"


def test_companies_rows_are_well_formed_and_unique():
    rows = [r for r in json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8")) if r.get("ats") == "ripplehire"]
    assert len(rows) >= 40
    assert all(more.ripplehire_board(r["board"]) for r in rows)
    tenants = [r["board"].split("/")[0] for r in rows]
    assert len(tenants) == len(set(tenants))
