"""--every-title (owner, 2026-11-18): the assistant judges titles against the profile, never a word list.

In that mode the kit drops no job by --titles phrases, --exclude-title-words or --max-years; it only attaches a
``title_hint`` (a signal). The hunt recipe tells the assistant to read the titles and decide.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load():
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_every_title", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load()

TITLES = [
    "Machine Learning Engineer", "AI Software Engineer", "Engineer, Applied AI", "GenAI Engineer", "LLM Engineer",
    "Senior AI Engineer", "AI Sales Manager",
]


def _collector(*argv: str):
    args = hk.build_parser().parse_args(["--titles", "AI Engineer", "--exclude-title-words", "senior,sales",
                                         "--max-years", "3", "--work-countries", "GB", "--days", "3650", *argv])
    return hk.Collector(args)


def _offer(col, titles):
    for i, t in enumerate(titles):
        col.offer_job("test", {"title": t, "company": "Acme", "url": "https://jobs.example/%d" % i,
                               "location": "London, UK", "description": "You have 8+ years of experience.",
                               "source": "test", "board": "acme", "extra": {}})
    return {r["title"]: r for r in col.results}


def test_old_flags_still_drop_without_every_title():
    assert _offer(_collector(), TITLES) == {}  # 8 years >= 3 and phrase/exclude rules: unchanged default flow


def test_every_title_keeps_every_title_with_a_hint_only():
    col = _collector("--every-title")
    kept = _offer(col, TITLES)
    assert sorted(kept) == sorted(TITLES)
    assert col.matcher("Accountant")  # reader detail gates pass every title too
    senior = kept["Senior AI Engineer"]["title_hint"]
    assert senior == {"matched_target_phrase": True, "level_words": ["senior"], "guessed_min_years": 8}


def test_a_row_with_every_hint_negative_is_still_written():
    kept = _offer(_collector("--every-title"), ["Head of Sales"])
    hint = kept["Head of Sales"]["title_hint"]
    assert hint["matched_target_phrase"] is False and hint["guessed_min_years"] == 8


def test_recipe_says_the_assistant_judges_titles_and_names_no_word_list():
    from src.api.routes.recipes import load_recipe

    flat = " ".join(load_recipe("hunt").text.lower().split())
    assert "--every-title" in flat
    assert "you read the titles and decide which fit the profile's targets and level" in flat
    assert "then read the kept ads in full" in flat
    assert "title_hint" in flat and "only a signal" in flat
    assert "--exclude-title-words" not in flat and "--max-years" not in flat
