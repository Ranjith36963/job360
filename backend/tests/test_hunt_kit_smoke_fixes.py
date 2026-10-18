"""Regression tests for the 2026-10-18 live smoke test of the hunt kit (offline, inline fixtures)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load(name: str, fname: str):
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / fname)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load("hunt_kit_smoke_under_test", "hunt_kit.py")
kn = _load("kit_niche_smoke_under_test", "kit_niche.py")
ka = _load("kit_ats_smoke_under_test", "kit_ats.py")

NOFLUFF = {"postings": [
    {"url": "backend-engineer-remote", "title": "Backend Engineer", "name": "Acme", "fullyRemote": True,
     "location": {"fullyRemote": True, "places": []}, "posted": 1760000000000},
    {"url": "backend-engineer-wro", "title": "Backend Engineer", "name": "Beta", "fullyRemote": False,
     "location": {"places": [{"city": "Wroclaw"}]}, "posted": 1760000000000},
]}


# ---- 2. --any-location keeps every title match
def test_any_location_keeps_plain_remote_job() -> None:
    p = hk.make_prefs(None, any_row=True)
    assert hk.classify_location("Remote", "", p) == "any"  # was None: remote with no region is a dead end
    p_gb = hk.make_prefs("GB", any_row=True)
    assert hk.classify_location("Remote - US only", "Must be located in the United States.", p_gb) == "any"


def test_any_location_still_honours_explicit_only_and_exclude() -> None:
    assert hk.classify_location("Berlin", "", hk.make_prefs("DE", exclude="DE", any_row=True)) is None
    assert hk.classify_location("Lisbon", "", hk.make_prefs(None, only="PT", any_row=True)) == "any"
    assert hk.classify_location("Berlin", "", hk.make_prefs(None, only="PT", any_row=True)) is None


def test_poland_keeps_all_when_star_passed() -> None:
    jobs = kn.parse_nofluffjobs(NOFLUFF)[0]
    assert len(kn._poland(jobs, ["*"])) == 2  # was 1 (remote only)
    assert len(kn._poland(jobs, ["GB"])) == 1


def test_nofluffjobs_with_any_location_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake(url, params=None, data=None, retries=0, max_bytes=0, method=None, headers=None):
        return 200, url, json.dumps(NOFLUFF).encode()

    monkeypatch.setattr(hk, "_http_request", fake)
    out = tmp_path / "o.jsonl"
    rc = hk.main(["--sources", "nofluffjobs", "--titles", "engineer", "--any-location", "--history", "none",
                  "--companies", "none", "--no-universe", "--run-log", "none", "--out", str(out),
                  "--stream", str(tmp_path / "s.jsonl"), "--days", "36500", "--stats"])
    assert rc == 0
    text = out.read_text()
    assert "backend-engineer-remote" in text and "backend-engineer-wro" in text


# ---- 4. --companies none
def test_companies_none_means_no_company_list() -> None:
    assert hk.load_companies(Path("none")) == []
    assert hk.load_companies(Path("NONE")) == []


# ---- 3. Taleo portal from the JS config block (freehire taleo.go:72-74)
def test_taleo_portal_from_portalno_config_block() -> None:
    page = '<input type="hidden" name="portal" id="portal" value=""><script>var c = { portalNo: \'101430233\', portalCode: 2 };</script>'
    assert ka._taleo_portal(page) == "101430233"
    assert ka._taleo_portal('<input type="hidden" name="portal" id="portal" value="">') == ""


# ---- 5. Eightfold sends the reference's browser-like User-Agent (career-ops eightfold.mjs:392)
def test_eightfold_sends_browser_like_user_agent() -> None:
    seen: list[dict] = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        seen.append(headers or {})
        return 200, {"positions": [], "count": 0}

    ka.fetch_eightfold({"name": "Q", "ats": "eightfold", "tenant": "qualcomm"}, http, {})
    assert seen and seen[0]["User-Agent"].startswith("Mozilla/5.0")


# ---- 6. SolidJobs needs ?campaign= (freehire solidjobs.go:17,36)
def test_solidjobs_sends_campaign_param_and_reads_inline_fields() -> None:
    calls: list[str] = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True):
        calls.append(url)
        return 200, {"jobs": [{"title": "Dev", "company": "Acme", "url": "https://solid.jobs/o/k/job360",
                               "locations": ["Warszawa"], "isRemote": True, "validFrom": "2026-10-01T00:00:00Z",
                               "description": "<p>5 years</p>"}]}

    jobs = kn.FEEDS["solidjobs"]([], http, {}, days=36500, countries=["PL"])
    assert "campaign=" in calls[0]
    assert jobs[0]["remote"] is True and jobs[0]["posted"] == "2026-10-01" and "5 years" in jobs[0]["description"]
