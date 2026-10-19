"""Offline tests for scripts/harvest_hunt_boards.py (grows hunt_kit/companies.json)."""
import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "harvest_hunt_boards.py"
_spec = importlib.util.spec_from_file_location("harvest_hunt_boards", _PATH)
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

FIX = json.dumps([
    {"company_name": "Acme", "active": True, "url": "https://boards.greenhouse.io/acme/jobs/123"},
    {"company_name": "Dead", "active": False, "url": "https://jobs.lever.co/deadco/abc"},
    {"company_name": "Wd", "active": True, "url": "https://logitech.wd5.myworkdayjobs.com/en-US/Logitech/job/X_1"},
    {"company_name": "Ash", "active": True, "url": "https://jobs.ashbyhq.com/Ashco/uuid?utm=1"},
    {"company_name": "Li", "active": True, "url": "https://apply.workable.com/linkedin-x/j/1"},
    {"company_name": "Sr", "active": True, "url": "https://careers.smartrecruiters.com/Foo/123"},
    {"company_name": "Rc", "active": True, "url": "https://bar.recruitee.com/o/x"},
    {"company_name": "Old", "active": True, "url": "https://job-boards.greenhouse.io/embed/job_app?for=embedco"},
    {"company_name": "Have", "active": True, "url": "https://jobs.lever.co/HaveCo/1"},
])


def test_extract_and_active():
    f = h.harvest_text(FIX)
    assert ("greenhouse", "acme") in f and f[("greenhouse", "acme")]["name"] == "Acme"
    assert ("lever", "deadco") not in f
    assert ("workday", "logitech|wd5|logitech") in f
    w = f[("workday", "logitech|wd5|logitech")]
    assert (w["tenant"], w["wd"], w["site"], w["board"]) == ("logitech", "wd5", "Logitech", "logitech|wd5|Logitech")
    assert ("ashby", "ashco") in f and ("smartrecruiters", "foo") in f and ("recruitee", "bar") in f
    assert ("greenhouse", "embedco") in f
    assert not any("linkedin" in k[1] for k in f)
    assert all(r["source"] == "harvest" for r in f.values())


def test_no_active_field_sweeps_everything():
    f = h.harvest_text('{"x": "see https://jobs.lever.co/plainco/1"}')
    assert ("lever", "plainco") in f


def test_merge_keeps_order_dedupes_sorted():
    ex = [{"name": "Z", "ats": "lever", "board": "HaveCo", "source": "jobsync"},
          {"name": "A", "ats": "ashby", "board": "zzz", "source": "x"}]
    f = h.harvest_text(FIX)
    m = h.merge(ex, f)
    assert m[:2] == ex
    new = m[2:]
    assert ("lever", "haveco") not in [h._key(r) for r in new]
    assert [h._key(r) for r in new] == sorted(h._key(r) for r in new)


def test_run_end_to_end(tmp_path):
    c = tmp_path / "c.json"
    c.write_text('[\n{"name":"Z","ats":"lever","board":"HaveCo","source":"jobsync"}\n]\n')
    o = tmp_path / "o.json"
    res = h.run(str(c), str(o), ["u1", "u2"], fetcher=lambda u: FIX.encode() if u == "u1" else None)
    out = json.loads(o.read_text())
    assert out[0]["board"] == "HaveCo" and len(out) == res["total"]
    assert res["new"]["greenhouse"] == 2 and res["existing"] == {"lever": 1}


def test_validate_blocked_returns_false():
    assert h.validate_row({"ats": "nope"}) is False


def test_fetch_rejects_non_http(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("[]")
    assert h.fetch(f.as_uri()) is None and h.fetch("ftp://x/y") is None


def test_escaped_slashes_and_uppercase_workday():
    f = h.harvest_text('["https:\\/\\/jobs.lever.co\\/esc\\/1", "HTTPS://A.WD1.MYWORKDAYJOBS.COM/S/job/1"]')
    assert ("lever", "esc") in f and ("workday", "a|wd1|s") in f


def test_main_bad_companies_file(tmp_path):
    assert h.main(["--companies", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o.json"), "--sources", "x"]) == 1


def test_new_ats_hosts():
    text = json.dumps([
        "https://careers-acme.icims.com/jobs/1/x/job",
        "https://bayer.eightfold.ai/careers?pid=1",
        "https://jobs.jobvite.com/tylertech/job/o1",
        "https://acme.taleo.net/careersection/2/jobdetail.ftl?job=1",
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1002/job/9",
        "https://tre.taleo.net/careersection/x/jobsearch.ftl",
    ])
    f = h.harvest_text(text)
    assert ("icims", "careers-acme") in f and ("eightfold", "bayer") in f and ("jobvite", "tylertech") in f
    t = f[("taleo", "acme/2")]
    assert (t["tenant"], t["section"], t["board"]) == ("acme", "2", "acme/2")
    o = f[("oraclecloud", "jpmc.fa.oraclecloud.com/cx_1002")]
    assert (o["host"], o["siteNumber"]) == ("jpmc.fa.oraclecloud.com", "CX_1002")
    assert not any(k[0] == "taleo" and k[1].startswith("tre/") for k in f)
