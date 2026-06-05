"""Tests for ATS company-slug discovery (copy_roadmap.md Tier-1 #1).

Distinct from test_discovery.py (which covers Step-3 Lane-Discovery API
endpoints). This module covers src/services/company_discovery.py — probing
public ATS endpoints to validate candidate company slugs before they are
added to core/companies.py.

All HTTP is mocked with aioresponses — no live network (CLAUDE.md rule #4).
Pure helpers (normalize_slug, merge_slugs) are tested directly; the async
probes use an injected aiohttp session.
"""
import asyncio

import aiohttp
import pytest
from aioresponses import aioresponses

from src.services.company_discovery import (
    ATS_PROBES,
    discover,
    merge_slugs,
    normalize_slug,
    probe_slug,
)


def _run(coro):
    return asyncio.run(coro)


# ----------------------------- pure helpers --------------------------------

def test_normalize_slug_lowercases_and_trims():
    assert normalize_slug("  DeepMind  ") == "deepmind"
    assert normalize_slug("Octopus Energy") == "octopus-energy"
    assert normalize_slug("acme_co-2") == "acme_co-2"
    assert normalize_slug("") == ""
    assert normalize_slug(None) == ""  # type: ignore[arg-type]


def test_merge_slugs_returns_only_new_sorted_dedup_case_insensitive():
    existing = ["deepmind", "Monzo", "stripe"]
    discovered = ["STRIPE", "wise", "monzo", "Anthropic", "wise"]
    # stripe + monzo already present (case-insensitive); wise dedup'd; new sorted
    assert merge_slugs(existing, discovered) == ["anthropic", "wise"]


def test_merge_slugs_drops_empty():
    assert merge_slugs(["a"], ["", "  ", "b"]) == ["b"]


# ----------------------------- probe_slug ----------------------------------

def test_probe_slug_greenhouse_valid_counts_jobs():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                    payload={"jobs": [{"id": 1}, {"id": 2}], "meta": {"total": 2}},
                )
                assert await probe_slug(session, "greenhouse", "acme") == 2
        finally:
            await session.close()
    _run(_t())


def test_probe_slug_greenhouse_empty_is_zero():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://boards-api.greenhouse.io/v1/boards/ghost/jobs",
                    payload={"jobs": []},
                )
                assert await probe_slug(session, "greenhouse", "ghost") == 0
        finally:
            await session.close()
    _run(_t())


def test_probe_slug_404_is_zero():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://boards-api.greenhouse.io/v1/boards/nope/jobs",
                    status=404,
                )
                assert await probe_slug(session, "greenhouse", "nope") == 0
        finally:
            await session.close()
    _run(_t())


def test_probe_slug_lever_list_payload():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://api.lever.co/v0/postings/acme?mode=json",
                    payload=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
                )
                assert await probe_slug(session, "lever", "acme") == 3
        finally:
            await session.close()
    _run(_t())


def test_probe_slug_smartrecruiters_uses_total_found():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://api.smartrecruiters.com/v1/companies/acme/postings",
                    payload={"totalFound": 7, "content": [{"id": "x"}]},
                )
                assert await probe_slug(session, "smartrecruiters", "acme") == 7
        finally:
            await session.close()
    _run(_t())


def test_probe_slug_normalizes_before_request():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://boards-api.greenhouse.io/v1/boards/octopus-energy/jobs",
                    payload={"jobs": [{"id": 1}]},
                )
                # raw input with spaces/caps must hit the normalized URL
                assert await probe_slug(session, "greenhouse", "Octopus Energy") == 1
        finally:
            await session.close()
    _run(_t())


def test_probe_slug_malformed_json_is_zero():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://boards-api.greenhouse.io/v1/boards/weird/jobs",
                    body="not json at all",
                    content_type="text/html",
                )
                assert await probe_slug(session, "greenhouse", "weird") == 0
        finally:
            await session.close()
    _run(_t())


def test_probe_slug_unknown_ats_raises():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with pytest.raises(ValueError):
                await probe_slug(session, "bamboozle", "acme")
        finally:
            await session.close()
    _run(_t())


# ----------------------------- discover ------------------------------------

def test_discover_returns_only_valid_slugs_with_counts():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://boards-api.greenhouse.io/v1/boards/good1/jobs",
                    payload={"jobs": [{"id": 1}, {"id": 2}]},
                )
                m.get(
                    "https://boards-api.greenhouse.io/v1/boards/good2/jobs",
                    payload={"jobs": [{"id": 9}]},
                )
                m.get(
                    "https://boards-api.greenhouse.io/v1/boards/empty/jobs",
                    payload={"jobs": []},
                )
                found = await discover(
                    session, "greenhouse", ["good1", "GOOD1", "good2", "empty"]
                )
                assert found == {"good1": 2, "good2": 1}
        finally:
            await session.close()
    _run(_t())


def test_discover_respects_min_jobs_threshold():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://api.lever.co/v0/postings/big?mode=json",
                    payload=[{"id": "1"}, {"id": "2"}, {"id": "3"}],
                )
                m.get(
                    "https://api.lever.co/v0/postings/small?mode=json",
                    payload=[{"id": "1"}],
                )
                found = await discover(
                    session, "lever", ["big", "small"], min_jobs=2
                )
                assert found == {"big": 3}
        finally:
            await session.close()
    _run(_t())


def test_discover_unknown_ats_raises():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with pytest.raises(ValueError):
                await discover(session, "nope", ["a"])
        finally:
            await session.close()
    _run(_t())


def test_known_ats_probes_cover_core_systems():
    # Guards against accidental registry shrinkage.
    for name in ("greenhouse", "lever", "workable", "ashby", "smartrecruiters", "recruitee"):
        assert name in ATS_PROBES
        assert "{slug}" in ATS_PROBES[name].url_template
