"""One hunt-kit response is capped: a body over the cap is a failed request (status 0), never a half-parsed page.

Pages (HTML/XML/RSS) default to 2 MB, JSON APIs to 20 MB, sitemaps ask for 10 MB, RSS feeds for 5 MB.
Nothing touches the network: ``hunt_kit._open`` is replaced by a fake opener.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.hunt_kit import hunt_kit as hk

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


eu = _load("kit_ats_eu_cap_under_test", "kit_ats_eu.py")
m1 = _load("kit_ats_misc1_cap_under_test", "kit_ats_misc1.py")


class _Resp:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def geturl(self) -> str:
        return "https://example.com/x"

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]


def _serve(monkeypatch: pytest.MonkeyPatch, routes: dict[str, bytes]) -> list[str]:
    """Fake opener: the first route whose key is in the URL answers; anything else is empty."""
    seen: list[str] = []

    def fake_open(req: Any, timeout: int) -> Any:
        seen.append(req.full_url)
        for frag, body in routes.items():
            if frag in req.full_url:
                return _Resp(body)
        return _Resp(b"")

    monkeypatch.setattr(hk, "_open", fake_open)
    monkeypatch.setattr(hk, "check_url", lambda u: True)
    monkeypatch.setattr(hk.time, "sleep", lambda s: None)
    return seen


def test_constants_are_the_documented_caps():
    assert (hk.PAGE_MAX_BYTES, hk.API_MAX_BYTES) == (2_000_000, 20_000_000)
    assert (hk.SITEMAP_MAX_BYTES, hk.FEED_MAX_BYTES) == (10_000_000, 5_000_000)


def test_http_request_oversize_is_status_0_and_exact_cap_passes(monkeypatch):
    _serve(monkeypatch, {"/big": b"x" * 2_000_001, "/exact": b"y" * 2_000_000})
    assert hk._http_request("https://example.com/big", max_bytes=2_000_000) == (0, "https://example.com/big", b"")
    st, _u, body = hk._http_request("https://example.com/exact", max_bytes=2_000_000)
    assert st == 200 and body == b"y" * 2_000_000


def test_http_html_over_2mb_is_a_failed_request(monkeypatch):
    _serve(monkeypatch, {"/page": b"<p>" + b"a" * 2_500_000})
    assert hk.http("https://example.com/page", as_json=False) == (0, None)


def test_http_html_can_ask_for_a_bigger_cap(monkeypatch):
    _serve(monkeypatch, {"/page": b"<p>" + b"a" * 2_500_000})
    st, text = hk.http("https://example.com/page", as_json=False, max_bytes=hk.SITEMAP_MAX_BYTES)
    assert st == 200 and len(text) == 2_500_003


def test_http_json_3mb_still_parses(monkeypatch):
    rows = [{"id": i, "title": "t" * 40} for i in range(60_000)]
    payload = json.dumps(rows).encode()
    assert 2_000_000 < len(payload) < 20_000_000
    _serve(monkeypatch, {"/api": payload})
    st, data = hk.http("https://example.com/api")
    assert st == 200 and len(data) == 60_000


def test_a_page_reader_returns_no_jobs_for_an_oversize_page_and_does_not_raise(monkeypatch):
    _serve(monkeypatch, {"betterteam.com": b"<html>" + b"<a href='/x-1'>x</a>" * 150_000 + b"</html>"})
    stats: dict = {}
    jobs = m1.ATS_FETCHERS["betterteam"]({"name": "Acme", "ats": "betterteam", "token": "acme"}, hk.http, stats)
    assert not jobs


def test_a_sitemap_reader_still_reads_a_3mb_sitemap(monkeypatch):
    host = "inetec.talentlyft.com"
    pad = "".join(f"<url><loc>https://{host}/about-{i}</loc></url>" for i in range(60_000))
    sitemap = ('<?xml version="1.0"?><urlset>' + pad
               + f"<url><loc>https://{host}/jobs/senior-electronics-engineer-cjVz</loc></url></urlset>").encode()
    assert 2_000_000 < len(sitemap) < 10_000_000
    detail = (b'<html><script type="application/ld+json">{"@context":"https://schema.org/","@type":"JobPosting",'
              b'"title":"Senior Electronics Engineer","description":"<p>Build things.</p>","datePosted":"2026-06-23",'
              b'"hiringOrganization":{"@type":"Organization","name":"INETEC"},"jobLocation":{"@type":"Place",'
              b'"address":{"@type":"PostalAddress","addressLocality":"Donji Stupnik","addressCountry":"Croatia"}}}'
              b"</script></html>")
    seen = _serve(monkeypatch, {"/jobs/senior-electronics-engineer-cjVz": detail, "sitemap.xml": sitemap})
    stats: dict = {}
    jobs = eu.ATS_FETCHERS["talentlyft"]({"name": "INETEC", "ats": "talentlyft", "token": "inetec"}, hk.http, stats)
    assert seen[0] == f"https://{host}/sitemap.xml"
    assert [j["title"] for j in jobs] == ["Senior Electronics Engineer"]
