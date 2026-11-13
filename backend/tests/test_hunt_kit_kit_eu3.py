"""Offline tests for hunt_kit/kit_eu3.py (freelancermap and Free-Work: freelance and contract boards).

The fixtures under tests/fixtures/hunt_kit/eu3/ are trimmed copies of the real answers fetched on 2026-11-13. Nothing touches
the network. Malt answers 403 with a Cloudflare challenge on every endpoint tried and is not read.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlsplit

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
FIX = Path(__file__).resolve().parent / "fixtures" / "hunt_kit" / "eu3"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kb = _load("kit_eu3_under_test", "kit_eu3.py")
hk = _load("hunt_kit_eu3_under_test", "hunt_kit.py")
F = kb.ATS_FETCHERS
NAMES = ("freelancermap", "freework")
ALLOWED_KW = {"method", "data", "headers", "as_json", "once", "max_bytes"}


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _stub(routes: dict):
    calls: list = []
    kws: list = []

    def http(url, **kw):
        calls.append(url)
        kws.append(kw)
        for frag, resp in routes.items():
            if frag in url:
                return resp(url) if callable(resp) else resp
        return 404, None

    http.calls = calls
    http.kws = kws
    return http


def _run(name, token, http):
    stats: dict = {}
    jobs = F[name]({"name": name.title() + " Co", "ats": name, "token": token}, http, stats)
    assert all(set(k) <= ALLOWED_KW for k in http.kws)
    assert all(k.get("once") for k in http.kws)  # a refusal is never repeated
    return jobs, stats


def _hosts(items):
    return {urlsplit(u).hostname for u in items}


def test_wiring_and_host_rules():
    assert set(F) == set(NAMES) and set(F) <= set(hk.BOARD_SOURCES)
    assert "kit_eu3" in hk.ATS_MODULES and all(a in hk._merged("ATS_FETCHERS", hk.ATS_MODULES) for a in NAMES)
    assert '"kit_eu3.py"' in (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert {r["ats"] for r in hk.load_companies(KIT_DIR / "companies.json")} >= set(NAMES)
    for url, ats in (("https://www.freelancermap.com/project/sap-ewm-lead-3073122", "freelancermap"),
                     ("https://www.free-work.com/en-gb/tech-it/job-mission/x/y", "freework")):
        r = hk.board_from_job_url(url)
        assert (r["kind"], r["ats"], r["token"]) == ("ats", ats, "")
    assert hk.board_from_job_url("https://www.malt.com/profile/x")["kind"] == "other"  # Malt is not read


def test_freelancermap_cards_type_and_permanent_dropped(monkeypatch):
    monkeypatch.setattr(kb, "_today", lambda: "2026-11-13")  # the card's "16:28" means today: the date comes from this clock
    http = _stub({"freelancermap.com/projects": (200, _fx("freelancermap.html"))})
    jobs, _ = _run("freelancermap", "machine-learning", http)
    assert http.calls == ["https://www.freelancermap.com/projects?sort=1&query=machine%20learning"]
    assert [j["title"] for j in jobs] == ["DevOps Engineer CGEMJP", "SAP EWM Lead", "Senior Project Manager"]  # the Permanent card is dropped
    assert _hosts(j["url"] for j in jobs) == {"www.freelancermap.com"}
    assert [j["extra"]["employment_type"] for j in jobs] == ["contract", "freelance", "contract"]
    assert jobs[1]["company"] == "Conexus" and jobs[1]["location"] == "Lancashire, United Kingdom" and jobs[1]["posted"] == "2026-11-13"
    assert all(j["extra"]["employment_type"] in ("freelance", "contract") and j["extra"]["contract_label"] for j in jobs)


def test_freelancermap_dated_card_bad_tokens_and_refusal():
    page = _fx("freelancermap.html").replace(">16:28<", ">12.11.2026<", 1)
    jobs, _ = _run("freelancermap", "all", _stub({"freelancermap.com/projects": (200, page)}))
    assert jobs[0]["posted"] == "2026-11-12"
    for tok in ("../x", "a b", "a/b", ""):
        http = _stub({})
        assert _run("freelancermap", tok, http)[0] == [] and http.calls == []
    jobs, stats = _run("freelancermap", "all", _stub({"freelancermap.com": (403, None)}))
    assert jobs == [] and not stats.get("ok")
    jobs, stats = _run("freelancermap", "all", _stub({"freelancermap.com": (200, "<html>moved</html>")}))
    assert jobs == [] and not stats.get("ok")
    swapped = _fx("freelancermap.html").replace('href="/project/sap-ewm-lead-3073122"', 'href="https://evil.example/project/x"')
    assert len(_run("freelancermap", "all", _stub({"freelancermap.com/projects": (200, swapped)}))[0]) == 2


def test_freework_cards_fields_paging_and_own_host():
    http = _stub({"free-work.com/en-gb/tech-it/jobs": (200, _fx("freework.html"))})
    jobs, _ = _run("freework", "machine-learning", http)
    assert http.calls[0] == "https://www.free-work.com/en-gb/tech-it/jobs?contracts=contractor&query=machine%20learning"
    assert http.calls[1].endswith("&page=2")
    assert len(jobs) == 3 and _hosts(j["url"] for j in jobs) == {"www.free-work.com"}  # pages 2 and 3 repeat the stub: deduped
    j = jobs[0]
    assert (j["title"], j["posted"], j["extra"]["employment_type"], j["extra"]["contract_label"]) == ("BCDR & Safety Manager - DV Cleared", "2026-11-12", "contract", "Contractor")
    assert j["extra"]["rate"] and j["extra"]["duration"] and "England" in j["location"] and j["company"]
    assert len(http.calls) == kb.FREEWORK_PAGES


def test_freework_refusal_empty_search_and_changed_page():
    http = _stub({"page=2": (429, None), "free-work.com/en-gb": (200, _fx("freework.html"))})
    assert len(_run("freework", "all", http)[0]) == 3 and len(http.calls) == 2  # the refusal ends the board, page 1 stays
    empty = "<html><head><title>IT Jobs for Freelancers | Free-Work</title></head><body></body></html>"
    jobs, stats = _run("freework", "ai", _stub({"free-work.com": (200, empty)}))
    assert jobs == [] and stats["ok"] == 1  # an alive board with no match is not a failure
    jobs, stats = _run("freework", "ai", _stub({"free-work.com": (200, "<html><title>Moved</title></html>")}))
    assert jobs == [] and not stats.get("ok")
    assert _run("freework", "ai", _stub({"free-work.com": (403, None)}))[0] == []
    http = _stub({})
    assert _run("freework", "../x", http)[0] == [] and http.calls == []


def test_freework_foreign_link_is_dropped():
    swapped = _fx("freework.html").replace('href="/en-gb/tech-it/job-mission/assistant-project-manager', 'href="https://evil.example/en-gb/tech-it/job-mission/assistant-project-manager', 1)
    jobs, _ = _run("freework", "all", _stub({"free-work.com/en-gb": (200, swapped), "page=2": (404, None)}))
    assert len(jobs) == 2 and _hosts(j["url"] for j in jobs) == {"www.free-work.com"}
