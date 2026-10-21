"""Offline tests for fetch_eightfold (fixtures built from career-ops' documented response shapes)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit" / "kit_ats.py"
_spec = importlib.util.spec_from_file_location("kit_ats", KIT)
kit_ats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit_ats)

ROW = {"name": "Bayer", "ats": "eightfold", "tenant": "bayer", "domain": "bayer.com"}


def _stub(routes):
    calls = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, None

    http.calls = calls
    return http


def _pos(i, **kw):
    p = {"id": 1000 + i, "name": f"Role {i}", "location": "Berlin, Germany", "locations": ["Berlin, Germany", "Remote"],
         "department": "R&D", "t_create": 1735689600, "canonicalPositionUrl": f"https://talent.bayer.com/job/{i}",
         "ats_job_id": f"REQ{i}"}
    p.update(kw)
    return p


def test_v2_parses_fields():
    http = _stub({"/api/apply/v2/jobs": (200, {"count": 2, "positions": [_pos(1), _pos(2, canonicalPositionUrl="", t_create=0, t_update=1735776000)]})})
    st = {}
    jobs = kit_ats.fetch_eightfold(ROW, http, st)
    assert [j["title"] for j in jobs] == ["Role 1", "Role 2"]
    assert jobs[0]["url"] == "https://talent.bayer.com/job/1"
    assert jobs[0]["posted"] == "2025-01-01" and jobs[0]["remote"] is True
    assert jobs[0]["location"] == "Berlin, Germany · Remote"
    assert jobs[1]["url"] == "https://bayer.eightfold.ai/careers?pid=1002&domain=bayer.com"
    assert jobs[1]["posted"] == "2025-01-02"
    assert jobs[0]["source"] == "eightfold" and st == {"tried": 1, "ok": 1, "jobs": 2}
    assert "domain=bayer.com" in http.calls[0] and "start=0" in http.calls[0]


def test_paging_stops_on_count_and_cap():
    page = [_pos(i) for i in range(10)]
    http = _stub({"/api/apply/v2/jobs": (200, {"count": 20, "positions": page})})
    jobs = kit_ats.fetch_eightfold(ROW, http, {})
    assert len(http.calls) == 2 and len(jobs) == 10  # same urls dedupe; stopped at count
    http2 = _stub({"/api/apply/v2/jobs": (200, {"positions": page})})  # no count: capped by MAX_PAGES
    kit_ats.fetch_eightfold(ROW, http2, {})
    assert len(http2.calls) == kit_ats.MAX_PAGES


def test_pcsx_fallback_on_403():
    p = {"id": 7, "name": "Eng", "locations": ["Redmond"], "postedTs": 1735689600, "positionUrl": "/x", "atsJobId": "A1"}
    http = _stub({"/api/apply/v2/jobs": (403, {"message": "Not authorized for PCSX"}),
                  "/api/pcsx/search": (200, {"status": 200, "data": {"count": 1, "positions": [p]}})})
    jobs = kit_ats.fetch_eightfold({"name": "MS", "tenant": "msft"}, http, {})
    assert len(jobs) == 1 and jobs[0]["posted"] == "2025-01-01"
    assert jobs[0]["url"] == "https://msft.eightfold.ai/careers?pid=7"
    assert jobs[0]["extra"]["api"] == "pcsx" and jobs[0]["extra"]["requisition_id"] == "A1"
    assert "domain=" not in http.calls[0]


def test_failures_never_raise():
    st = {}
    assert kit_ats.fetch_eightfold(ROW, _stub({"eightfold.ai": (403, None)}), st) == []
    assert kit_ats.fetch_eightfold(ROW, _stub({"eightfold.ai": (200, {"oops": 1})}), st) == []
    assert kit_ats.fetch_eightfold({"name": "x", "tenant": "evil.com/x"}, _stub({}), st) == []

    def boom(*a, **k):
        raise RuntimeError("net")

    assert kit_ats.fetch_eightfold(ROW, boom, st) == []
    assert st["tried"] == 4 and st.get("ok", 0) == 0


def test_drops_untitled_and_non_https_without_id():
    http = _stub({"/api/apply/v2/jobs": (200, {"positions": [{"id": 1}, {"name": "A", "canonicalPositionUrl": "http://x"}, "junk", _pos(3)]})})
    jobs = kit_ats.fetch_eightfold(ROW, http, {})
    assert [j["title"] for j in jobs] == ["Role 3"]


def test_registered():
    assert kit_ats.ATS_FETCHERS["eightfold"] is kit_ats.fetch_eightfold


def test_fallback_url_is_quoted_and_title_must_be_str():
    row = {"name": "X", "tenant": "bayer", "domain": "a.com&x=1 y"}
    pos = [{"id": "a&b c", "name": "T"}, {"id": 5, "name": {"x": 1}, "posting_name": "Fallback"}]
    jobs = kit_ats.fetch_eightfold(row, _stub({"/api/apply/v2/jobs": (200, {"positions": pos})}), {})
    assert jobs[0]["url"] == "https://bayer.eightfold.ai/careers?pid=a%26b%20c&domain=a.com%26x%3D1%20y"
    assert jobs[1]["title"] == "Fallback"


def test_midwalk_failure_keeps_pages():
    page = [_pos(i) for i in range(10)]
    seq = iter([(200, {"positions": page}), (429, None)])
    jobs = kit_ats.fetch_eightfold(ROW, lambda *a, **k: next(seq), {})
    assert len(jobs) == 10
