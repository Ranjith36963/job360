"""Offline tests for hunt_kit/kit_places.py using the committed places.json."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load():
    spec = importlib.util.spec_from_file_location("kit_places", KIT / "kit_places.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kit_places"] = mod  # dataclasses needs the module registered
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def places():
    return _load().load_places(KIT / "places.json")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Remote - US only", {"US"}),
        ("Remote in the US", {"US"}),
        ("London, UK", {"GB"}),
        ("London", {"GB"}),
        ("London, Canada", {"CA"}),
        ("São Paulo, Brazil", {"BR"}),
        ("Sao Paulo", {"BR"}),
        ("Bengaluru", {"IN"}),
        ("Bangalore", {"IN"}),
        ("Dubai, UAE", {"AE"}),
        ("Kuala Lumpur", {"MY"}),
        ("Berlin or Munich", {"DE"}),
        ("München", {"DE"}),
        ("Washington", {"US"}),
        ("Washington, DC", {"US"}),
        ("Remote - DE", {"DE"}),
        ("Portland, OR", {"US"}),
        ("Papua New Guinea", {"PG"}),
        ("New Mexico", {"US"}),
        ("Tokyo, Japan", {"JP"}),
        ("Singapore", {"SG"}),
        ("Türkiye", {"TR"}),
        ("Turkey", {"TR"}),
        ("Reading the docs", set()),
        ("Nice to meet you", set()),
        ("CONTACT US", set()),
        ("Senior engineer, join us in it", set()),
        ("", set()),
    ],
)
def test_countries_in(places, text, expected):
    assert places.countries_in(text) == expected


def test_regions_expand(places):
    assert {"DE", "AE"} <= places.countries_in("Remote (EMEA)")
    assert {"IN", "SG", "AU"} <= places.countries_in("APAC")
    assert "BR" in places.region_members("LATAM")
    assert {"DE", "FR"} <= places.region_members("EU")
    assert "US" not in places.region_members("EMEA")
    assert places.region_members("DACH") == {"DE", "AT", "CH"}
    assert {"SA", "AE"} <= places.region_members("GCC")
    assert {"US", "CA"} <= places.region_members("North America")


def test_worldwide_is_all(places):
    allc = set(places.countries)
    for t in ("Worldwide", "Remote, Global", "Work from anywhere"):
        assert places.countries_in(t) == allc
    assert len(allc) > 240


def test_name(places):
    assert places.name("US") == "United States of America"
    assert places.name("DE") == "Germany"
    assert places.name("ZZ") == "ZZ"


def test_data_size_and_attribution():
    p = KIT / "places.json"
    assert p.stat().st_size < 1_500_000
    d = json.loads(p.read_text(encoding="utf-8"))
    assert len(d["countries"]) >= 240 and len(d["cities"]) > 5000
    assert any("CC BY 4.0" in s for s in d["meta"]["sources"])
