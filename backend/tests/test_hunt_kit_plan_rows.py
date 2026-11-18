"""The hunt kit searches every search-plan row (owner decision 2026-11-18, coverage audit of the 14:15, 16:13 and
17:11 runs: ~80% GB/IN on-site, nothing outside GB/IN brought).

Replayed gaps, each failing on the kit before this change:
* on-site abroad kept only an ad that STATED sponsorship (``--visa-onsite``): a silent or refusing Berlin ad was lost;
* "Remote - Germany" was dropped though Europe is a plan row;
* only the first 12 titles were ever queried (titles 13-23 never), exact phrases only (no ML / GenAI / FDE);
* freehire was asked for GB / IN only;
* the done file had no per-row count, and no result said what the ad says about a visa.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load() -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_plan_rows_under_test", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()

# The owner's 30 plan rows (whats_due hunt plan_rows) and his 23 target titles, as get_profile read them 2026-11-18.
PLACES = ("GB", "IN", "ANY", "NORTH_AMERICA", "EUROPE", "SOUTH_AMERICA", "ASIA", "AFRICA", "OCEANIA", "MIDDLE_EAST")
OWNER_ROWS = ",".join(f"{p}:{k}" for p in PLACES for k in ("remote", "onsite", "contract"))
OWNER_TITLES = [
    "Graduate AI Engineer", "Junior AI Engineer", "Entry Level AI Engineer", "Associate AI Engineer",
    "AI Graduate Programme", "Graduate Machine Learning Engineer", "Junior Machine Learning Engineer",
    "Junior Applied AI Engineer", "Junior Generative AI Engineer", "Junior Forward Deployed Engineer",
    "Graduate Forward Deployed Engineer", "AI/ML Engineer Intern", "Graduate Software Engineer AI", "AI Engineer",
    "Applied AI Engineer", "Forward Deployed Engineer", "Generative AI Engineer", "Gen AI Engineer",
    "AI Agentic Engineer", "Agentic Engineer", "AI Native Engineer", "AI Solutions Engineer", "AI/ML Engineer",
]
EUROPE_ONLY = "GB:onsite,IN:onsite,EUROPE:onsite,EUROPE:remote"
REFUSES = "We are unable to sponsor visas for this role. Build agents with Python."
SILENT = "Build agents with Python and ship them to customers."
STATES = "We sponsor visas for this role. Build agents with Python."


def _prefs(rows: str = EUROPE_ONLY, **kw: Any):
    return hk.make_prefs("GB,IN", plan_rows=rows, **kw)


# ── place: every ad in a plan place, whatever it says about a visa ──────────


@pytest.mark.parametrize("desc,row", [(REFUSES, "plan_onsite"), (SILENT, "plan_onsite"), (STATES, "visa_onsite")])
def test_on_site_in_a_plan_region_is_kept_whatever_the_ad_says_about_sponsorship(desc, row):
    assert hk.classify_location("Berlin, Germany", desc, _prefs()) == row
    # without the plan the old sponsor-only rule still applies (and drops the silent and refusing ads)
    old = hk.classify_location("Berlin, Germany", desc, hk.make_prefs("GB,IN"))
    assert old == ("visa_onsite" if desc == STATES else None)


def test_on_site_outside_every_plan_row_is_still_out_and_visa_onsite_is_a_no_op():
    assert hk.classify_location("Tokyo, Japan", STATES, _prefs()) is None, "Asia is not in this plan"
    assert _prefs(visa_onsite=True).visa_onsite is False
    assert hk.classify_location("Tokyo, Japan", REFUSES, _prefs(OWNER_ROWS)) == "plan_onsite", "ASIA / ANY rows"


def test_remote_locked_to_a_plan_country_is_kept():
    assert hk.classify_location("Remote - Germany", SILENT, hk.make_prefs("GB,IN")) is None, "the 3 runs' gap"
    assert hk.classify_location("Remote - Germany", SILENT, _prefs()) == "remote_place"
    assert hk.classify_location("Remote - Japan", SILENT, _prefs()) is None, "no Asia remote row"


def test_plan_rows_parse_most_specific_first_and_refuse_a_bad_row():
    rows = hk.parse_plan_rows(OWNER_ROWS)
    assert len(rows) == 30 and {r.id for r in rows} == set(OWNER_ROWS.split(","))
    assert [r.id for r in rows][:2] == ["GB:remote", "GB:onsite"] and rows[-1].members is None
    de = frozenset({"DE"})
    assert hk.plan_row_for(rows, de, "onsite") == "EUROPE:onsite"
    assert hk.plan_row_for(rows, frozenset({"GB"}), "contract") == "GB:contract"
    assert hk.plan_row_for(rows, frozenset({"BR"}), "remote") == "SOUTH_AMERICA:remote"
    assert hk.plan_row_for(rows, frozenset(), "remote") == "ANY:remote"
    for bad in ("GB:hybrid", "ATLANTIS:onsite", ":remote"):
        with pytest.raises(ValueError):
            hk.parse_plan_rows(bad)


# ── titles: all of them, rotated, with variants ─────────────────────────────


def test_every_target_title_is_queried_across_hourly_runs():
    titles = hk.title_variants(OWNER_TITLES)
    seen: set[str] = set()
    hours = -(-len(titles) // hk.QUERY_TITLES)  # ceil: runs needed to reach every title once
    for hour in range(hours):
        seen |= set(hk.rotate_titles(titles, hour / 24, hk.QUERY_TITLES)[:hk.QUERY_TITLES])
    assert seen == set(titles), "titles 13-23 were never queried before"
    assert {"AI Native Engineer", "AI/ML Engineer"} <= seen


def test_title_variants_cover_the_spellings_ads_use():
    got = {t.lower() for t in hk.title_variants(["Junior Machine Learning Engineer", "Generative AI Engineer",
                                                  "Forward Deployed Engineer", "AI/ML Engineer"])}
    assert {"junior ml engineer", "genai engineer", "gen ai engineer", "fde", "ai ml engineer"} <= got
    m = hk.make_title_matcher(hk.title_variants(["Junior Machine Learning Engineer", "Forward Deployed Engineer"]), "")
    assert m("Junior ML Engineer") and m("FDE - London") and not m("Junior Data Analyst")


# ── freehire and the done file ──────────────────────────────────────────────


def _collector(tmp_path: Path, *extra: str):
    args = hk.build_parser().parse_args(["--titles", "AI Engineer", "--work-countries", "GB,IN", *extra])
    return hk.Collector(args)


def test_freehire_is_asked_for_every_plan_place_not_just_gb_and_in(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake(url: str, params: dict[str, Any], *a: Any, **k: Any):
        calls.append(dict(params))
        return 200, {"data": []}

    monkeypatch.setattr(hk, "http_get_json", fake)
    hk._freehire(_collector(tmp_path), ["ai engineer"])
    before = [c for c in calls if "work_mode" not in c]
    assert {c.get("countries") for c in before} == {"GB", "IN"}
    calls.clear()
    hk._freehire(_collector(tmp_path, "--plan-rows", OWNER_ROWS), ["ai engineer"])
    no_mode = [c for c in calls if "work_mode" not in c]
    assert {c.get("countries") for c in no_mode} == {"GB", "IN", None}, "a region / ANY row asks every place"


def test_each_result_carries_its_visa_signal_and_plan_row_and_the_done_file_counts_rows(tmp_path):
    col = _collector(tmp_path, "--plan-rows", EUROPE_ONLY + ",EUROPE:contract")
    ads = [("Berlin, Germany", REFUSES, "AI Engineer"), ("Paris, France", SILENT, "AI Engineer"),
           ("Remote - Germany", STATES, "AI Engineer"), ("Madrid, Spain", SILENT, "AI Engineer (Contract)"),
           ("London, UK", SILENT, "AI Engineer")]
    for i, (loc, desc, title) in enumerate(ads):
        col.offer("freehire", {"title": title, "url": f"https://jobs.example.com/{i}", "loc": loc, "desc": desc,
                               "company": f"Co {i}", "posted": ""})
    by_loc = {r["location"]: r for r in col.results}
    assert [by_loc[x]["visa_signal"] for x in ("Berlin, Germany", "Paris, France", "Remote - Germany")] == [
        "refused", "silent", "stated"]
    assert by_loc["Berlin, Germany"]["plan_row"] == "EUROPE:onsite"
    assert by_loc["Remote - Germany"]["plan_row"] == "EUROPE:remote"
    assert by_loc["Madrid, Spain"]["contract"] is True and by_loc["Madrid, Spain"]["plan_row"] == "EUROPE:contract"
    assert by_loc["London, UK"]["plan_row"] == "GB:onsite"
    out = str(tmp_path / "found.jsonl")
    hk.write_done_file(out, col.results, col, None)
    done = json.loads(Path(out + ".done.json").read_text())
    assert done["plan_rows"] == {"GB:onsite": 1, "IN:onsite": 0, "EUROPE:onsite": 2, "EUROPE:remote": 1,
                                 "EUROPE:contract": 1}
    assert done["results"] == 5 and done["plan_rows_contract"] == 1
