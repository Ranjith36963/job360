"""The search titles must be VISIBLE to the user who owns them.

WHY THIS FILE EXISTS. ``SearchConfig.search_titles`` is the only title a job
board ever sees (``sources/base.py::search_titles``). Until 2026-08-13 it never
left the engine — zero references in ``src/api/``, zero in ``frontend/src/``.
A user whose results were wrong therefore had exactly one conclusion available
to them: "this product does not understand me". That is unrecoverable. The
recoverable conclusion — "wrong setting" — needs the guess on screen, next to
the way to change it.

``GET /api/profile`` now carries ``search_titles``. These tests pin VALUE
presence, not schema presence (rule #21): a ``search_titles: list[str] = []``
default would satisfy ``assert "search_titles" in body`` while the serializer
never read the engine at all, so every assertion below runs a real profile
through the real generator and checks the real strings.

Auth: the route is ``GET /api/profile`` with ``Depends(require_user)`` and no
``user_id`` parameter anywhere in its signature (rules #12/#25). One test below
proves a ``?user_id=`` on the URL is ignored rather than honoured.
"""

from __future__ import annotations

import json

import pytest


async def _save_titles(client, titles: list[str]) -> dict:
    """POST the given target job titles and return the response body."""
    resp = await client.post(
        "/api/profile",
        data={"preferences": json.dumps({"target_job_titles": titles})},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_profile_response_carries_the_real_board_queries(authenticated_async_context):
    """VALUE presence: the field holds what the boards were actually asked.

    "Machine Learning Engineer Intern" is not a query we send — the rank word
    is stripped before it reaches a board, because an AND-matching board
    returns ~0 rows for it (measured live 2026-08-13: Reed 486 -> 0, Adzuna
    806 -> 1). So the honest thing to show the user is the DERANKED string.
    Asserting the exact list proves the route read the engine, not a default.
    """
    async with authenticated_async_context() as client:
        body = await _save_titles(client, ["Machine Learning Engineer Intern"])

    assert body["search_titles"] == ["Machine Learning Engineer"]


@pytest.mark.asyncio
async def test_get_profile_shows_the_same_titles_as_the_save(authenticated_async_context):
    """The GET the dashboard actually calls carries the same list as the POST.

    The dashboard reads ``GET /api/profile``; the profile page reads the POST
    response. If only one of the two carried the field the line would appear
    and vanish depending on which page you arrived from.
    """
    async with authenticated_async_context() as client:
        saved = await _save_titles(client, ["Data Scientist", "AI/ML Engineer"])
        resp = await client.get("/api/profile")

    assert resp.status_code == 200
    body = resp.json()
    assert body["search_titles"] == saved["search_titles"]
    # And it is a real, non-empty answer — a slash-split emits two live queries
    # where "AI/ML Engineer" alone is one dead one on an AND-matching board.
    assert "Data Scientist" in body["search_titles"]
    assert body["search_titles"] != []


@pytest.mark.asyncio
async def test_a_user_id_on_the_url_is_ignored(authenticated_async_context):
    """Rules #12/#25 — the titles come from the SESSION, never from the URL.

    ``GET /api/profile`` takes no ``user_id``; FastAPI drops the unknown query
    param and the response is the caller's own. If someone ever "helpfully"
    added a ``user_id`` parameter to this route, this test goes red.
    """
    async with authenticated_async_context() as client:
        mine = await _save_titles(client, ["Backend Engineer"])
        resp = await client.get("/api/profile?user_id=00000000-0000-0000-0000-000000000009")

    assert resp.status_code == 200
    assert resp.json()["search_titles"] == mine["search_titles"]
    assert resp.json()["search_titles"] == ["Backend Engineer"]


@pytest.mark.asyncio
async def test_no_usable_title_yields_an_empty_list_not_an_error(authenticated_async_context):
    """Degrade #1 — a profile with nothing to search for returns [] and a 200.

    The UI contract is "render NOTHING when the list is empty". That only
    works if the empty case is a normal 200 with an empty list, never a 500
    and never a placeholder string.
    """
    async with authenticated_async_context() as client:
        body = await _save_titles(client, [])

    assert body["search_titles"] == []


@pytest.mark.asyncio
async def test_a_broken_generator_costs_the_line_not_the_page(
    authenticated_async_context, monkeypatch
):
    """Degrade #2 — if the keyword generator raises, the profile still loads.

    This line is a nicety; the profile page is not. The route swallows the
    failure, logs it, and returns an empty list. Patching the module attribute
    works because the route imports it lazily at call time (rule #16).
    """
    from src.services.profile import keyword_generator

    async with authenticated_async_context() as client:
        await _save_titles(client, ["Product Manager"])

        def _boom(_profile):
            raise RuntimeError("ESCO vocabulary unavailable")

        monkeypatch.setattr(keyword_generator, "generate_search_config", _boom)
        resp = await client.get("/api/profile")

    assert resp.status_code == 200
    assert resp.json()["search_titles"] == []
    # The rest of the page is untouched — the failure is scoped to this field.
    assert resp.json()["summary"]["is_complete"] in (True, False)
