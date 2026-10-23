"""Offline tests for hunt_kit/kit_asia2.py (MokaHR, HRMOS, HERP, Quickin, PyjamaHR).

Fixtures are shaped like the sample data in freehire's internal/ingest/sources/<name>_test.go (MIT, bed5ec9) and career-ops
providers/mokahr.mjs (MIT, 8f48928) and written fresh. The MokaHR ciphertext was produced once with a reference AES library.
Nothing touches the network.
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


k = _load("kit_asia2_under_test", "kit_asia2.py")
hk = _load("hunt_kit_asia2_under_test", "hunt_kit.py")
F = k.ATS_FETCHERS
NAMES = ("mokahr", "hrmos", "herp", "quickin", "pyjamahr")
# json.dumps(body) of one open posting, AES-128-CBC under key "0123456789abcdef" and the fixed IV, base64
MOKA_CT = ("WqOqgv7irbsnFTu64Fraf4uwyxzaQURvYxoaoB/UWPuTmzqVeIPaqLdBfNAyXusi9dPQYOx/vVeiQ3XvEHOlKz6wV5SWjZGW8yiDH14CQw3VjXfnKLyMbW3Y/CWEzNf"
           "VZzCvXeQ7qFYjNa+F2bLt3Onwy78i7hBVRep9DfARxL5HtoD6C98Up7cLkXJCsiDejM4H+Q7Nf6/uR5kpN7MXQaw4sn8JCnne+XwCjantyrVrAQiQiWevc6Bd/H+doQVayc+70"
           "uyMmKAr1GdQbixcus3k8JqTf1JDu17fk9m35dW9RTZEZx2OLKtHOoEC9wrPvBUF1wuH88Zp8OkhIXcIW6h+Gkzmq3rpyxuYji6G0ENNdItJTtOwDOZTscsQF1mM7g63UjyN10rPYC"
           "3Tr9yRs9TJOhXIwK8r6DLk2Yi5ip/d6HKp/bms8dar3I+D1shOi5WFTrllT+C+5G+MYLoK7IlcPwn+lEUgsDVIft78wss=")


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


def _job_hosts(jobs) -> set:
    return {urlsplit(j["url"]).hostname for j in jobs}


def _https(jobs) -> bool:
    return all(urlsplit(j["url"]).scheme == "https" for j in jobs)


def _ld(title, desc="<p>Build things</p>", **extra) -> str:
    return ('<html><head><script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": title, "description": desc, **extra}).replace("</", "<\\/")
            + "</script></head><body></body></html>")


def test_every_reader_is_registered_and_wired():
    assert set(F) == set(NAMES)
    assert "kit_asia2" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_asia2.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from freehire") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_asia2.py"' in served
    for row in json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8")):
        if row["ats"] in NAMES:
            assert row["source"] in ("freehire", "career-ops")


@pytest.mark.parametrize("url,ats,token", [
    ("https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/abc", "mokahr", "high-flyer/140576"),
    ("https://app.mokahr.com/apply/moonshot/148506", "mokahr", "moonshot/148506"),
    ("https://hire-r1.mokahr.com/social-recruitment/redotpay/9001", "mokahr", "redotpay/9001/r1"),
    ("https://app.mokahr.com/login", "mokahr", ""),
    ("https://hrmos.co/pages/cyberagent-group/jobs/900100", "hrmos", "cyberagent-group"),
    ("https://herp.careers/v1/a244/GnoQonoXGBZi", "herp", "a244"),
    ("https://jobs.quickin.io/botcity/jobs/5f1", "quickin", "botcity"),
    ("https://jobs.pyjamahr.com/dodo-payments/senior-dev", "pyjamahr", "dodo-payments"),
])
def test_job_links_are_read_as_these_boards(url, ats, token):
    info = hk.board_from_job_url(url)
    assert (info["kind"], info["ats"], info["token"]) == ("ats", ats, token)
    for host in ("mokahr.com", "hrmos.co", "herp.careers", "quickin.io", "pyjamahr.com"):
        assert not hk._UNSUPPORTED_HOSTS.search(host)
    assert hk.board_from_job_url("https://hire-r1-ats.mokahr.com/x")["kind"] == "other"  # the static CDN is no board
    assert hk.board_from_job_url("https://www.herkey.com/jobs/data-analyst/1234")["kind"] == "unsupported"  # not built


def test_parse_extra_reads_the_new_tokens():
    rows = hk.parse_extra("mokahr:high-flyer/140576,mokahr:redotpay/9001/r1,hrmos:cyberagent-group,herp:a244,quickin:botcity,pyjamahr:dodo-payments")
    assert [r["ats"] for r in rows] == ["mokahr", "mokahr", "hrmos", "herp", "quickin", "pyjamahr"]
    assert rows[0]["name"] == "high-flyer" and rows[1]["token"] == "redotpay/9001/r1"


# --------------------------------------------------------------------------- AES / MokaHR
def test_aes_decrypt_matches_fips_197_and_rejects_bad_input():
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ct = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a9e978e6d16b086570ef794ef97984232")  # AES-128 C.1 plaintext, PKCS#7 padded
    assert k._aes_cbc_decrypt(ct, key, bytes(16)) == bytes.fromhex("00112233445566778899aabbccddeeff")
    assert k._aes_cbc_decrypt(ct, key[:15], bytes(16)) is None and k._aes_cbc_decrypt(ct[:-1], key, bytes(16)) is None
    assert k._aes_cbc_decrypt(ct, bytes(16), bytes(16)) is None  # wrong key: the padding check fails
    assert k._aes_cbc_decrypt(b"", key, bytes(16)) is None


def _plain_row(i, **kw):
    return {"id": f"j{i}", "title": f"Role {i}", "status": "open", "jobDescription": f"<p>Body {i}</p>", "commitment": "全职",
            "openedAt": "2026-10-01T09:00", "locations": [{"cityId": 310115, "cityName": "浦东新区", "provinceName": "上海市"}], **kw}


def _moka_ok(rows):
    return 200, {"success": True, "code": 0, "data": {"jobs": rows}}


def test_mokahr_decrypts_the_app_host_answer_and_maps_the_posting():
    http = _stub({"app.mokahr.com/api/outer/ats-apply/website/jobs/v2": (200, {"data": MOKA_CT, "necromancer": "0123456789abcdef"})})
    jobs, stats = _run("mokahr", _row("mokahr", "high-flyer/140576"), http)
    assert len(jobs) == 1 and (stats["ok"], stats["jobs"]) == (1, 1)
    j = jobs[0]
    assert j["url"] == "https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/a1b2" and j["title"] == "算法研究员 Researcher"
    assert (j["location"], j["posted"], j["extra"]["employment_type"]) == ("Beijing", "2026-09-30", "full_time") and "大模型" in j["description"]
    url, method, headers, data, _ = http.calls[0]
    assert method == "POST" and headers["Content-Type"] == "application/json"
    assert json.loads(data) == {"siteId": 140576, "orgId": "high-flyer", "locale": "zh-CN", "limit": 50, "offset": 0}
    assert _hosts(http) == {"app.mokahr.com"} and _job_hosts(jobs) == {"app.mokahr.com"}
    bad = _stub({"mokahr.com": (200, {"data": MOKA_CT, "necromancer": "fedcba9876543210"})})
    assert _run("mokahr", _row("mokahr", "high-flyer/140576"), bad)[0] == []  # a wrong key fails the board


def test_mokahr_pages_by_offset_on_the_international_host_until_a_short_page():
    first = [_plain_row(i) for i in range(50)]

    def page(url, headers, data):
        off = json.loads(data)["offset"]
        return _moka_ok(first if off == 0 else [_plain_row(60 + i) for i in range(3)])
    http = _stub({"/api/outer/ats-apply/website/jobs/v2": page})
    jobs, _ = _run("mokahr", _row("mokahr", "redotpay/9001/r1"), http)
    assert len(jobs) == 53 and [json.loads(c[3])["offset"] for c in http.calls] == [0, 50]
    assert _hosts(http) == {"hire-r1.mokahr.com"} and _job_hosts(jobs) == {"hire-r1.mokahr.com"} and _https(jobs)
    assert jobs[0]["location"] == "Shanghai"


def test_mokahr_one_bad_posting_costs_one_and_a_page_failure_after_page_one_keeps_the_rest():
    rows = [None, "x", {"id": "../x", "title": "t"}, _plain_row(1, status="paused"), _plain_row(2, jobDescription="  "), _plain_row(3, title=""),
            _plain_row(4, locations="oops"), _plain_row(5, id="j5"), _plain_row(6)]
    jobs, _ = _run("mokahr", _row("mokahr", "o/1"), _stub({"mokahr.com": _moka_ok(rows)}))
    assert [j["extra"]["job_id"] for j in jobs] == ["j5", "j6"]
    full = [_plain_row(i) for i in range(50)]
    http = _stub({"mokahr.com": lambda u, h, d: _moka_ok(full) if json.loads(d)["offset"] == 0 else (403, None)})
    jobs, stats = _run("mokahr", _row("mokahr", "o/1"), http)
    assert len(jobs) == 50 and len(http.calls) == 2 and stats["ok"] == 1


def test_mokahr_failed_first_page_robots_tenants_and_bad_tokens_fail_the_board():
    for body in ((500, None), (200, {"success": False, "code": 102, "msg": "x"}), (200, {"success": True, "data": {"jobs": []}}), (200, "html")):
        assert _run("mokahr", _row("mokahr", "o/1"), _stub({"mokahr.com": body}))[0] == []
    for tok in ("shopee/74378", "lingjuninvest/46355", "", "o", "o/0", "o/x", "../1", "o/1/r2", "o/1/r1/x", "a b/1"):
        http = _stub({})
        assert _run("mokahr", _row("mokahr", tok), http)[0] == [] and not http.calls


# --------------------------------------------------------------------------- HRMOS
def _hrmos_list(*hrefs, extra=""):
    return "<html><body>" + "".join(f'<a href="{h}">{t}</a>' for h, t in hrefs) + extra + "</body></html>"


def test_hrmos_pages_until_no_new_link_and_reads_the_posting_page():
    p1 = _hrmos_list(("/pages/acme/jobs/900100", "Backend Engineer"), ("https://hrmos.co/pages/acme/jobs/900101", "Designer"))
    p2 = _hrmos_list(("/pages/acme/jobs/900102", "Data Scientist"), ("/pages/acme/jobs/900100", "Backend Engineer"))
    page = {"900100": _ld("Backend Engineer (Tokyo)", "<p>Go &amp; SQL</p>", datePosted="2026-10-02T09:00:00+09:00", employmentType="FULL_TIME",
                          jobLocation=[{"address": {"addressLocality": "Shibuya", "addressRegion": "Tokyo", "addressCountry": "JP"}}])}
    http = _stub({"?page=1": (200, p1), "?page=2": (200, p2), "?page=3": (200, p2),
                  "/jobs/900100": (200, page["900100"]), "/jobs/900101": (404, None), "/jobs/900102": (200, "<html></html>")})
    jobs, stats = _run("hrmos", _row("hrmos", "acme"), http)
    assert [j["url"] for j in jobs] == [f"https://hrmos.co/pages/acme/jobs/{i}" for i in (900100, 900101, 900102)]
    a, b, c = jobs
    assert (a["title"], a["posted"], a["extra"]["employment_type"]) == ("Backend Engineer (Tokyo)", "2026-10-02", "full_time")
    assert "Go & SQL" in a["description"] and "Shibuya" in a["location"] and "Tokyo" in a["location"]
    assert (b["title"], b["description"], c["title"]) == ("Designer", "", "Data Scientist")  # a gone or empty page keeps the card
    assert [c_[0] for c_ in http.calls if "?page" in c_[0]] == [f"https://hrmos.co/pages/acme/jobs?page={n}" for n in (1, 2, 3)]
    assert _hosts(http) == {"hrmos.co"} and _job_hosts(jobs) == {"hrmos.co"} and _https(jobs) and stats["jobs"] == 3


def test_hrmos_off_host_other_board_and_navigation_links_are_never_cards_or_fetched():
    page = _hrmos_list(("/pages/acme/jobs/1", "Good"), ("https://evil.example/pages/acme/jobs/2", "Evil"), ("//evil.example/pages/acme/jobs/3", "Proto"),
                       ("http://hrmos.co/pages/acme/jobs/4", "Plain http"), ("/pages/other/jobs/5", "Other board"), ("/pages/acme/jobs", "Root"),
                       ("/pages/acme/jobs/6/entry", "Deeper"), ("/pages/acme/jobs/7/", "Slash"), ("/pages/acme/about", "About"),
                       ("https://hrmos.co.evil.example/pages/acme/jobs/8", "Lookalike"), ("javascript:alert(1)", "js"))
    http = _stub({"?page=1": (200, page), "?page=2": (200, "<html></html>"), "/jobs/1": (200, _ld("Good"))})
    jobs, _ = _run("hrmos", _row("hrmos", "acme"), http)
    assert [j["url"] for j in jobs] == ["https://hrmos.co/pages/acme/jobs/1"]
    assert _hosts(http) == {"hrmos.co"} and all(c[0].startswith("https://hrmos.co/pages/acme/jobs") for c in http.calls)


def test_hrmos_refusals_failed_first_page_title_ok_cap_and_bad_tokens():
    p1 = _hrmos_list(*[(f"/pages/acme/jobs/{i}", f"Job {i}") for i in range(1, 40)])
    http = _stub({"?page=1": (200, p1), "?page=2": (429, None), "/jobs/": (200, _ld("T"))})
    jobs, _ = _run("hrmos", _row("hrmos", "acme", title_ok=lambda t: t != "Job 2"), http)
    assert len(jobs) == 39 and sum("/jobs/" in c[0] and "?page" not in c[0] for c in http.calls) == k.DETAIL_CAP
    assert not any(c[0].endswith("/jobs/2") for c in http.calls)
    http = _stub({"?page=1": (200, p1), "?page=2": (403, None), "/jobs/1": (429, None)})
    jobs, _ = _run("hrmos", _row("hrmos", "acme"), http)
    assert len(jobs) == 39 and len([c for c in http.calls if "?page" not in c[0]]) == 1  # the first refusal ends the detail requests
    assert _run("hrmos", _row("hrmos", "acme"), _stub({"?page=1": (503, None)}))[0] == []
    for tok in ("", "..", ".x", "a/b", "a b"):
        h = _stub({})
        assert _run("hrmos", _row("hrmos", tok), h)[0] == [] and not h.calls


def test_hrmos_hostile_listing_and_deep_posting_do_not_lose_the_board_or_take_long():
    junk = "<div><p><li>" * 20000
    page = _hrmos_list(("/pages/acme/jobs/1", "A"), extra=junk) + "<a href='/pages/acme/jobs/2'>B</a>"
    deep = '<script type="application/ld+json">' + '{"a":' * 5000 + "1" + "}" * 5000 + "</script>"
    http = _stub({"?page=1": (200, page), "?page=2": (200, "<html></html>"), "/jobs/1": (200, deep), "/jobs/2": (200, _ld("<b>B</b>", "<script>x()</script><p>ok</p>"))})
    t = time.time()
    jobs, _ = _run("hrmos", _row("hrmos", "acme"), http)
    assert time.time() - t < 10 and [j["title"] for j in jobs] == ["A", "B"] and jobs[1]["description"] == "ok"


# --------------------------------------------------------------------------- HERP
def test_herp_reads_direct_links_and_requisition_groups_one_level_deep():
    root = ('<a href="/v1/a244/AAA111">Engineer</a><a href="/v1/a244/top">Top</a><a href="/v1/a244/AAA111/apply">Apply</a>'
            '<a href="/v1/a244/requisition-groups/11111111-aaaa">Group</a><a href="https://twitter.com/share?u=https://herp.careers/v1/a244/ZZZ">Tw</a>'
            '<a href="https://evil.example/v1/a244/EVIL">Evil</a><a href="/v1/other/BBB">Other</a>')
    group = '<a href="/v1/a244/BBB222">Designer</a><a href="/v1/a244/AAA111">Engineer</a><a href="/v1/a244/requisition-groups/22222222">Deeper</a>'
    http = _stub({"requisition-groups/11111111-aaaa": (200, group), "/v1/a244/AAA111": (200, _ld("Engineer", "<p>Hi</p>", datePosted="2026-10-03",
                                                                                                  jobLocation={"address": "Tokyo, Japan"})),
                  "/v1/a244/BBB222": (500, None), "/v1/a244": (200, root)})
    jobs, stats = _run("herp", _row("herp", "a244"), http)
    assert [j["url"] for j in jobs] == ["https://herp.careers/v1/a244/AAA111", "https://herp.careers/v1/a244/BBB222"]
    a, b = jobs
    assert (a["title"], a["location"], a["posted"], a["description"]) == ("Engineer", "Tokyo, Japan", "2026-10-03", "Hi")
    assert (b["title"], b["description"]) == ("Designer", "") and _https(jobs) and stats["jobs"] == 2
    assert _hosts(http) == {"herp.careers"} and _job_hosts(jobs) == {"herp.careers"}
    assert not any("22222222" in c[0] for c in http.calls)  # groups are read once, one level deep


def test_herp_refused_group_and_failed_board_and_bad_tokens():
    root = '<a href="/v1/a244/AAA">A</a><a href="/v1/a244/requisition-groups/g1">G</a><a href="/v1/a244/requisition-groups/g2">G2</a>'
    http = _stub({"requisition-groups/g1": (429, None), "/v1/a244/AAA": (200, _ld("A")), "/v1/a244": (200, root)})
    jobs, _ = _run("herp", _row("herp", "a244"), http)
    assert [j["title"] for j in jobs] == ["A"] and not any("g2" in c[0] for c in http.calls)  # a refusal ends the group pages
    assert _run("herp", _row("herp", "a244"), _stub({"/v1/a244": (403, None)}))[0] == []
    for tok in ("", "..", "a/b"):
        h = _stub({})
        assert _run("herp", _row("herp", tok), h)[0] == [] and not h.calls


def test_herp_hostile_markup_keeps_the_board():
    root = "<div>" * 30000 + '<a href="/v1/a244/AAA">A</a><a href="/v1/a244/%zz">bad</a><a href="http://[::1">broken</a>'
    t = time.time()
    jobs, _ = _run("herp", _row("herp", "a244"), _stub({"/v1/a244/AAA": (200, _ld("A")), "/v1/a244": (200, root)}))
    assert time.time() - t < 10 and [j["title"] for j in jobs] == ["A"]  # the card's text is lost past the depth cap, the opened page names it


# --------------------------------------------------------------------------- Quickin
def _q(i, **kw):
    return {"_id": f"id{i}", "publicate": "published", "title": f" Role {i} ", "description": "<p>Do things</p>", "requirements": "<ul><li>Python</li></ul>",
            "city": "São Paulo", "region": "SP", "country": "BR", "workplace_type": "remote", "created_at": "2026-10-04T10:00:00.000Z", **kw}


def test_quickin_resolves_the_account_pages_and_maps_published_postings():
    page1 = {"docs": [_q(i) for i in range(100)], "pages": 2}
    page2 = {"docs": [_q(100), _q(101, publicate="draft"), _q(102, career_url="https://evil.example/x"),
                      _q(103, career_url="https://jobs.quickin.io/botcity/jobs/id103?utm=1#top"), _q(104, career_url="http://jobs.quickin.io/botcity/jobs/id104")], "pages": 2}
    http = _stub({"/public/accounts/botcity": (200, {"_id": "acc1"}), "page=1&limit=100": (200, page1), "page=2&limit=100": (200, page2)})
    jobs, stats = _run("quickin", _row("quickin", "botcity"), http)
    assert len(jobs) == 104 and stats["jobs"] == 104
    first = jobs[0]
    assert first["url"] == "https://jobs.quickin.io/botcity/jobs/id0" and first["title"] == "Role 0" and first["remote"] is True
    assert (first["location"], first["posted"], first["extra"]["work_mode"]) == ("São Paulo, SP, BR", "2026-10-04", "remote")
    assert first["description"] == "Do things\n\nPython"
    by = {j["extra"]["job_id"]: j["url"] for j in jobs}
    assert by["id102"] == "https://jobs.quickin.io/botcity/jobs/id102" and by["id103"] == "https://jobs.quickin.io/botcity/jobs/id103"
    assert by["id104"] == "https://jobs.quickin.io/botcity/jobs/id104" and "id101" not in by
    assert _hosts(http) == {"api.quickin.io"} and _job_hosts(jobs) == {"jobs.quickin.io"} and _https(jobs)


def test_quickin_one_bad_posting_short_pages_failures_and_unsafe_ids():
    docs = {"docs": [None, "x", _q(1, _id="../x"), _q(2, title=" "), _q(3, description=None, requirements=None), _q(4), _q(4)]}
    http = _stub({"/public/accounts/botcity": (200, {"_id": "acc1"}), "page=1": (200, docs)})
    jobs, _ = _run("quickin", _row("quickin", "botcity"), http)
    assert [j["extra"]["job_id"] for j in jobs] == ["id3", "id4"] and len([c for c in http.calls if "page=" in c[0]]) == 1  # no "pages": a short page ends
    full = {"docs": [_q(i) for i in range(100)], "pages": 3}
    http = _stub({"/public/accounts/botcity": (200, {"_id": "acc1"}), "page=1": (200, full), "page=2": (429, None)})
    assert len(_run("quickin", _row("quickin", "botcity"), http)[0]) == 100  # a later failure keeps what was gathered
    for bad in ({"_id": "a/../b"}, {"_id": ""}, {}, {"_id": 5}):
        h = _stub({"/public/accounts/": (200, bad)})
        assert _run("quickin", _row("quickin", "botcity"), h)[0] == [] and len(h.calls) == 1
    assert _run("quickin", _row("quickin", "botcity"), _stub({"accounts": (200, {"_id": "acc1"}), "page=1": (500, None)}))[0] == []
    for tok in ("", "..", "a/b"):
        h = _stub({})
        assert _run("quickin", _row("quickin", tok), h)[0] == [] and not h.calls


# --------------------------------------------------------------------------- PyjamaHR
PJ = "https://api.pyjamahr.com/api/career/jobs/"


def test_pyjamahr_follows_its_own_next_url_and_reads_detail_json():
    p1 = {"next": PJ + "?company_slug=dodo-payments&page=2", "results": [{"id": 1, "slug": "senior-dev", "title": "Senior Dev", "location": "Bengaluru"}]}
    p2 = {"next": None, "results": [{"id": 2, "slug": "designer", "title": "Designer", "location": "Remote - India"}]}
    det = {"description": "<p>Ship &amp; learn</p>", "job_type": "FULLTIME", "workplace_type": "on_site", "remote": False, "created_at": "2026-10-05T08:00:00Z"}
    http = _stub({"page=1": (200, p1), "page=2": (200, p2), "/jobs/1/": (200, det), "/jobs/2/": (200, {**det, "remote": True, "workplace_type": "", "job_type": "intern"})})
    jobs, stats = _run("pyjamahr", _row("pyjamahr", "dodo-payments"), http)
    assert [j["url"] for j in jobs] == ["https://jobs.pyjamahr.com/dodo-payments/senior-dev", "https://jobs.pyjamahr.com/dodo-payments/designer"]
    a, b = jobs
    assert (a["title"], a["location"], a["posted"], a["description"]) == ("Senior Dev", "Bengaluru", "2026-10-05", "Ship & learn")
    assert (a["extra"]["work_mode"], a["extra"]["employment_type"], a["remote"]) == ("onsite", "full_time", None)
    assert (b["extra"]["work_mode"], b["extra"]["employment_type"], b["remote"]) == ("remote", "internship", True)
    assert _hosts(http) == {"api.pyjamahr.com"} and _job_hosts(jobs) == {"jobs.pyjamahr.com"} and stats["jobs"] == 2


def test_pyjamahr_off_host_http_or_repeated_next_urls_end_the_walk_and_never_fetch():
    for nxt in ("https://evil.example/api/career/jobs/?page=2", "http://api.pyjamahr.com/api/career/jobs/?page=2", "//evil.example/x",
                "https://api.pyjamahr.com.evil.example/x", PJ + "?company_slug=acme&page=1", "ftp://api.pyjamahr.com/x"):
        http = _stub({"page=1": (200, {"next": nxt, "results": [{"id": 1, "slug": "a", "title": "A"}]}), "/jobs/1/": (200, {"description": "d"})})
        jobs, _ = _run("pyjamahr", _row("pyjamahr", "acme"), http)
        assert [j["title"] for j in jobs] == ["A"]
        assert _hosts(http) == {"api.pyjamahr.com"} and all(c[0].startswith("https://") for c in http.calls)


def test_pyjamahr_one_bad_item_refusal_title_ok_failures_and_bad_tokens():
    items = [None, {"id": True, "slug": "x", "title": "x"}, {"id": 3, "slug": "../x", "title": "x"}, {"id": 4, "slug": "ok", "title": ""},
             {"id": 5, "slug": "a", "title": "Alpha"}, {"id": 5, "slug": "dup", "title": "Dup"}, {"id": 6, "slug": "b", "title": "Beta"}]
    http = _stub({"page=1": (200, {"next": "", "results": items}), "/jobs/5/": (429, None), "/jobs/6/": (200, {"description": "b"})})
    jobs, _ = _run("pyjamahr", _row("pyjamahr", "acme"), http)
    assert [j["title"] for j in jobs] == ["Alpha", "Beta"] and all(j["description"] == "" for j in jobs)
    assert not any("/jobs/6/" in c[0] for c in http.calls)  # the first refusal ended the detail requests
    http = _stub({"page=1": (200, {"next": "", "results": items}), "/jobs/": (200, {"description": "d"})})
    jobs, _ = _run("pyjamahr", _row("pyjamahr", "acme", title_ok=lambda t: t == "Beta"), http)
    assert [j["description"] for j in jobs] == ["", "d"]
    full = {"next": PJ + "?company_slug=acme&page=2", "results": [{"id": 1, "slug": "a", "title": "A"}]}
    assert len(_run("pyjamahr", _row("pyjamahr", "acme"), _stub({"page=1": (200, full), "page=2": (503, None)}))[0]) == 1
    assert _run("pyjamahr", _row("pyjamahr", "acme"), _stub({"page=1": (404, None)}))[0] == []
    for tok in ("", "..", "a/b", "a&b"):
        h = _stub({})
        assert _run("pyjamahr", _row("pyjamahr", tok), h)[0] == [] and not h.calls


def test_mokahr_stops_reading_encrypted_pages_once_the_board_has_spent_its_byte_budget(monkeypatch):
    """A host that keeps answering full encrypted pages cannot hold the decryptor for 100 pages."""
    monkeypatch.setattr(k, "MOKAHR_BOARD_MAX_BYTES", 2500)
    served = []

    def decrypted(data):
        served.append(1)
        return [_plain_row(len(served) * 100 + i) for i in range(50)]  # a full page: the walk would go on

    monkeypatch.setattr(k, "_mokahr_rows", decrypted)
    http = _stub({"/api/outer/ats-apply/website/jobs/v2": (200, {"data": "A" * 1000, "necromancer": "0123456789abcdef"})})
    jobs, _ = _run("mokahr", _row("mokahr", "acme/123"), http)
    assert len(http.calls) == 3 and len(served) == 2 and len(jobs) == 100  # the third answer tripped the budget unread
