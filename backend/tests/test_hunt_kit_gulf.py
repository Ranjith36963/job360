"""Offline checks for the Gulf company boards in companies.json (source "gulf-research", 2026-10-20).

Each row was verified by one real request that listed at least one job; these tests only guard the
shape, so a later edit cannot leave a row the kit silently drops or reads with the wrong token.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import re
import sys
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))
_spec = importlib.util.spec_from_file_location("hunt_kit_gulf_under_test", KIT_DIR / "hunt_kit.py")
hk = importlib.util.module_from_spec(_spec)
sys.modules["hunt_kit_gulf_under_test"] = hk
_spec.loader.exec_module(hk)

GULF = {"AE", "SA", "QA", "BH", "KW", "OM"}
RAW = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
ROWS = [r for r in RAW if r.get("source") == "gulf-research"]


def test_gulf_rows_present_and_in_a_gulf_country():
    assert len(ROWS) >= 100
    assert {r["country"] for r in ROWS} <= GULF


def test_every_gulf_row_survives_the_loader(tmp_path: Path):
    p = tmp_path / "companies.json"
    p.write_text(json.dumps(ROWS), encoding="utf-8")
    loaded = hk.load_companies(p)
    assert len(loaded) == len(ROWS)
    assert all(r["token"] == r["board"] for r in loaded)


def test_no_board_listed_twice_in_the_whole_file():
    keys = collections.Counter((r["ats"], str(r["board"]).lower()) for r in RAW)
    gulf_keys = {(r["ats"], str(r["board"]).lower()) for r in ROWS}
    assert not [k for k in gulf_keys if keys[k] > 1]


def test_compound_boards_match_their_parts():
    host_rule = dict(hk._HOST_RULES)["oraclecloud"]
    for r in ROWS:
        if r["ats"] == "oraclecloud":
            assert host_rule.search(r["host"]), r
            assert r["board"] == f"{r['host']}/{r['siteNumber']}"
        elif r["ats"] == "workday":
            assert r["board"] == f"{r['tenant']}|{r['wd']}|{r['site']}"
        elif r["ats"] == "taleo":
            assert r["board"] == f"{r['tenant']}/{r['section']}"
        elif r["ats"] == "successfactors":
            assert re.fullmatch(r"https://[a-z0-9.-]+", r["board"]), r
        elif r["ats"] == "phenom":
            assert re.fullmatch(r"[a-z0-9.-]+", r["board"]) and r.get("url_prefix"), r
        else:
            assert re.fullmatch(r"[A-Za-z0-9-]+", r["board"]), r
