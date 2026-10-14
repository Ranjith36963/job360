"""Offline tests for hunt_kit/kit_boards.py (fake jobspy module)."""
import importlib.util
import sys
import types
from pathlib import Path

_P = Path(__file__).resolve().parents[1] / "src" / "hunt_kit" / "kit_boards.py"
_spec = importlib.util.spec_from_file_location("kit_boards", _P)
kb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb)

FORBIDDEN = ("proxies", "cookies", "cookie", "password", "username", "user", "token",
             "headers", "auth", "ca_cert", "credentials")


class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


def _fake(monkeypatch, behaviour):
    calls = []
    mod = types.ModuleType("jobspy")

    def scrape_jobs(**kw):
        calls.append(kw)
        return behaviour(kw)

    mod.scrape_jobs = scrape_jobs
    monkeypatch.setitem(sys.modules, "jobspy", mod)
    return calls


def _row(site="indeed", **kw):
    base = {"site": site, "title": "AI Engineer", "company": "Acme",
            "job_url": "https://x/1", "job_url_direct": None, "location": "London, UK",
            "date_posted": "2026-10-10", "description": "desc", "is_remote": True,
            "id": "abc", "min_amount": float("nan")}
    base.update(kw)
    return base


def _run(**kw):
    stats = {}
    args = dict(sites=["indeed"], queries=["ai engineer"], days=3, remote=True,
                countries=["GB"], results_wanted=500, full_text=True, stats=stats)
    args.update(kw)
    return kb.run_boards(**args), stats


def test_mapping_and_kwargs(monkeypatch, capsys):
    calls = _fake(monkeypatch, lambda kw: _Frame([_row(job_url_direct="https://co/apply")]))
    jobs, stats = _run()
    assert len(jobs) == 1
    j = jobs[0]
    assert j["url"] == "https://co/apply"  # direct preferred
    assert j["source"] == "jobspy:indeed" and j["remote"] is True
    assert j["posted"] == "2026-10-10" and j["visa_flag"] is None
    assert j["extra"]["job_url"] == "https://x/1" and "min_amount" not in j["extra"]
    kw = calls[0]
    assert kw["hours_old"] == 72 and kw["country_indeed"] == "uk"
    assert kw["results_wanted"] == 200 and "is_remote" not in kw  # plain search; remote is a 2nd call
    assert calls[1]["is_remote"] is True
    assert kw["linkedin_fetch_description"] is True and kw["description_format"] == "markdown"
    assert kw["site_name"] == ["indeed"]
    assert stats["tried"] == 2 and stats["ok"] == 2 and stats["jobs"] == 1
    assert kb.WARNING in capsys.readouterr().err


def test_no_forbidden_kwargs(monkeypatch):
    calls = _fake(monkeypatch, lambda kw: [])
    _run(sites=["linkedin", "indeed", "glassdoor"])
    assert calls
    for kw in calls:
        assert not [k for k in kw if k.lower() in FORBIDDEN]


def test_zero_jobs_note(monkeypatch):
    _fake(monkeypatch, lambda kw: [])
    jobs, stats = _run(sites=["google"])
    assert jobs == []
    assert any("google" in n and "0 jobs" in n for n in stats["notes"])


def test_missing_library(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "jobspy", None)  # import raises ImportError
    jobs, _ = _run()
    err = capsys.readouterr().err
    assert jobs == [] and "pip install python-jobspy" in err and kb.WARNING not in err


def test_one_site_failing_does_not_stop_others(monkeypatch):
    def beh(kw):
        if kw["site_name"] == ["linkedin"]:
            raise RuntimeError("429")
        return [_row(site="naukri", job_url="https://n/1")]

    _fake(monkeypatch, beh)
    jobs, stats = _run(sites=["linkedin", "naukri"], countries=["IN"])
    assert [j["source"] for j in jobs] == ["jobspy:naukri"]
    assert stats["linkedin_ok"] == 0 and stats["naukri_jobs"] == 1
    assert any("linkedin" in n and "failed" in n for n in stats["notes"])


def test_dedup_and_skip_bad_rows(monkeypatch):
    _fake(monkeypatch, lambda kw: [_row(), _row(), _row(title=""), _row(job_url=None)])
    jobs, _ = _run()
    assert len(jobs) == 1


def test_unknown_site_noted(monkeypatch):
    _fake(monkeypatch, lambda kw: [])
    _, stats = _run(sites=["monster"])
    assert any("monster" in n for n in stats["notes"])
