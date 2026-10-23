"""Offline tests for fetch_jobvite (fixtures follow the XML shape in career-ops providers/jobvite.mjs header)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit" / "kit_ats.py"
_spec = importlib.util.spec_from_file_location("kit_ats", KIT)
kit_ats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit_ats)

BOARD = "<script>var x = { companyEId: 'q6NaVfwI', foo: 1 };</script>"
FEED = """<?xml version="1.0"?>
<result>
<job><id>oAb1</id><title>Senior Engineer &amp; Lead</title><category>Engineering</category>
<location>Remote, US</location><date>3/7/2026</date>
<detail-url><![CDATA[http://jobs.jobvite.com/tylertech/job/oAb1]]></detail-url>
<apply-url><![CDATA[http://jobs.jobvite.com/tylertech/job/oAb1/apply]]></apply-url>
<description><![CDATA[<p>Build things.</p><ul><li>Python</li></ul>]]></description></job>
<job><id>x2</id><title>Analyst</title><location>Plano, TX</location><date>bad</date>
<detail-url>not a url</detail-url><apply-url><![CDATA[https://careers.acme.com/apply/2]]></apply-url></job>
<job><id>x3</id><title></title><detail-url>https://a.example/3</detail-url></job>
<job><id>x4</id><title>No url</title></job>
<job><id>x5</id><title>Unterminated
</result>"""
ROW = {"name": "Tyler", "ats": "jobvite", "token": "tylertech"}


def _stub(routes):
    calls = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, max_bytes=None):
        calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, None

    http.calls = calls
    return http


def test_discovers_eid_then_parses():
    http = _stub({"jobs.jobvite.com/tylertech": (200, BOARD), "Xml.aspx?c=q6NaVfwI": (200, FEED)})
    stats = {}
    jobs = kit_ats.fetch_jobvite(ROW, http, stats)
    assert [j["title"] for j in jobs] == ["Senior Engineer & Lead", "Analyst"]
    a, b = jobs
    assert a["url"] == "https://jobs.jobvite.com/tylertech/job/oAb1"  # detail-url, http upgraded
    assert a["posted"] == "2026-03-07" and a["remote"] is True and a["source"] == "jobvite"
    assert a["description"] == "Build things.\nPython" and a["extra"]["department"] == "Engineering"
    assert b["url"] == "https://careers.acme.com/apply/2" and b["posted"] == ""  # falls back to apply-url
    assert stats == {"tried": 1, "ok": 1, "jobs": 2}
    assert "fr=true&nl=1" in http.calls[0]


def test_explicit_eid_skips_board():
    http = _stub({"Xml.aspx?c=ABC123": (200, FEED)})
    jobs = kit_ats.fetch_jobvite({**ROW, "company_eid": "ABC123"}, http, {})
    assert len(jobs) == 2 and len(http.calls) == 1


def test_failures_never_raise():
    for routes in ({}, {"jobs.jobvite.com": (200, "<html>no id</html>")},
                   {"jobs.jobvite.com": (200, BOARD), "Xml.aspx": (200, "<html>NoJobs</html>")},
                   {"jobs.jobvite.com": (200, BOARD), "Xml.aspx": (500, None)}):
        stats = {}
        assert kit_ats.fetch_jobvite(ROW, _stub(routes), stats) == []
        assert stats["ok"] if "ok" in stats else True
        assert stats.get("ok", 0) == 0 and stats["tried"] == 1

    def boom(*a, **k):
        raise RuntimeError("x")

    assert kit_ats.fetch_jobvite(ROW, boom, {}) == []
    assert kit_ats.fetch_jobvite({"name": "x"}, _stub({}), {}) == []


def test_empty_result_is_ok_and_registered():
    stats = {}
    http = _stub({"jobs.jobvite.com": (200, BOARD), "Xml.aspx": (200, "<result></result>")})
    assert kit_ats.fetch_jobvite(ROW, http, stats) == []
    assert stats["ok"] == 1
    assert kit_ats.ATS_FETCHERS["jobvite"] is kit_ats.fetch_jobvite


def test_cap():
    many = "<result>" + "".join(f"<job><title>T{i}</title><detail-url>https://a.example/{i}</detail-url></job>" for i in range(600)) + "</result>"
    http = _stub({"jobs.jobvite.com": (200, BOARD), "Xml.aspx": (200, many)})
    assert len(kit_ats.fetch_jobvite(ROW, http, {})) == kit_ats.JOBVITE_MAX_JOBS


def test_bad_token_rejected_without_request():
    for t in ("x?c=evil#", "a/../b", "a b", "../x", "x@evil.com"):
        http = _stub({})
        stats = {}
        assert kit_ats.fetch_jobvite({"name": "x", "token": t}, http, stats) == []
        assert http.calls == [] and stats["tried"] == 1
