"""The hunt kit download (routes/hunt_kit.py) — owner decision 2026-10-13.

The kit is static files the user's OWN assistant runs; Job360 serves them and
nothing else. Pinned here: anyone can fetch it (an assistant's fetch tool has no
Job360 credential), only allow-listed names resolve, the index hashes match the
bytes served, and the hunt recipe points the assistant at it.
"""
from __future__ import annotations

import hashlib

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_index_and_every_file_are_public_and_match_their_hash():
    from src.api.main import app
    from src.api.routes.hunt_kit import KIT_FILES

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        index = await anon.get("/api/hunt-kit")
        assert index.status_code == 200, index.text
        assert index.headers["access-control-allow-origin"] == "*"
        body = index.json()
        assert "never logs in" in body["how_to"].lower()
        names = [f["name"] for f in body["files"]]
        assert names == list(KIT_FILES)
        for f in body["files"]:
            got = await anon.get(f["url"])
            assert got.status_code == 200, f["name"]
            assert len(got.content) == f["bytes"] > 0
            assert hashlib.sha256(got.content).hexdigest() == f["sha256"]
            assert got.headers["x-content-type-options"] == "nosniff"
        script = await anon.get("/api/hunt-kit/hunt_kit.py")
        # Served as text to read and run, never as something a browser executes.
        assert script.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_unknown_names_404_without_reaching_the_filesystem():
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        for bad in ("nope.txt", "..%2F..%2Fcore%2Fsettings.py", "%2E%2E%2Fmain.py", "companies.json.bak"):
            assert (await anon.get(f"/api/hunt-kit/{bad}")).status_code == 404, bad


def test_hunt_recipe_sends_the_assistant_to_the_kit_and_keeps_the_rules():
    from src.api.routes.recipes import load_recipe

    hunt = " ".join(load_recipe("hunt").text.lower().split())
    assert "job360.uk/api/hunt-kit" in hunt
    assert "job360 never searches" in hunt
    # Owner 2026-10-16 (B+C, replaces the 2026-10-14 ban): the assistant itself
    # opens Indeed pages slowly and LinkedIn's logged-out pages at very low
    # volume; no login, no app spoofing, copied keys or proxies; a login wall,
    # CAPTCHA or bot check stops that site and is never solved or dodged.
    assert "open indeed search and job pages yourself, slowly" in hunt
    assert "logged-out job pages at very low volume" in hunt
    # Owner 2026-10-19: the hunt kit itself now reads LinkedIn, Glassdoor and Google Jobs at low volume;
    # the assistant still reads Indeed by hand, and reads LinkedIn only when it runs no kit.
    assert "the hunt kit now reads linkedin, glassdoor and google jobs itself at low volume" in hunt
    assert "only without the kit" in hunt
    assert "never log in, spoof an app, use copied keys or proxies" in hunt
    assert "captcha or bot check = stop that site, never solve or dodge it" in hunt
    assert "--boards" not in hunt and "big boards" not in hunt and "browser hunt" not in hunt
    assert "never log in for the user or type a password" in hunt
    # A relocation line is not a visa line (owner hunt 2026-10-13).
    assert '"relocation support" alone is not visa sponsorship' in hunt
