"""preflight.cv_review: the queue says which CVs still need a review (production 2026-11-09, application 339).

The owner's Bulk apply run read application 339, whose newest CV had a stored ``cv_reviewed`` from 2026-11-06: one
of the 70 legacy reviews stored weak with ``not_current_cv`` (sha256 empty or wrong) before #1539 refused them.
From the queue list the run could not tell that broken review from a real one, so it reviewed nothing. Each queued
row now carries ``preflight.cv_review`` (none | weak | strong) for its NEWEST CV from the newest VALID review of
that exact id + sha; get_application's ``review.state`` is the same value. A legacy review never counts toward
``retry_limit``. Real doors (HTTP + MCP), values asserted (rule #21).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.services.applications import spine
from tests.test_cv_review import CV, _event, _review, _save_cv, _seed, _setup, _stored, sha


def _legacy(cv_id: int, sha256: str) -> dict[str, Any]:
    """A pre-#1539 review as production stored it: weak, downgraded for naming a non-current CV."""
    return {"artifact_id": cv_id, "sha256": sha256, "claimed": "strong", "confidence": "weak",
            "downgraded": ["not_current_cv", "line_uncovered"], "lines": 2, "content_lines": 0, "covered": 0,
            "reviewer": "fresh helper", "by": "claude-code"}


async def _insert_legacy(user_id: str, app_id: int, payload: dict[str, Any]) -> None:
    from src.api import dependencies as api_deps

    await spine.append_event(await api_deps.get_db(), user_id=user_id, application_id=app_id,
                             event_type="cv_reviewed", payload=payload, recorded_by="claude-code",
                             occurred_at=datetime.now(timezone.utc).isoformat())


async def _queued_states(client: Any, user_id: str) -> tuple[dict[int, str], dict[int, str]]:
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    full = (await client.get("/api/applications", params={"queued": True})).json()["applications"]
    tool = m.build_server()._tool_manager.get_tool("list_applications")
    token = m._current_user.set(
        CurrentUser(id=user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc"))
    try:
        short = (await tool.fn(queued=True, compact=True))["applications"]
    finally:
        m._current_user.reset(token)
    return ({r["id"]: r["preflight"]["cv_review"] for r in full},
            {r["id"]: r["preflight"]["cv_review"] for r in short})


@pytest.mark.asyncio
async def test_each_queued_row_says_whether_its_newest_cv_has_a_valid_review(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        apps: dict[str, tuple[int, int, str]] = {}
        for i, name in enumerate(("none", "legacy", "strong", "weak", "older")):
            text = CV + "\n" * (i + 1)  # same lines, its own sha: one application each
            app_id, cv_id = await _setup(client, cv=text)
            assert (await _event(client, app_id, "queued", {})).status_code == 201
            apps[name] = (app_id, cv_id, text)
        app_id, cv_id, text = apps["legacy"]
        for bad_sha in ("", sha("an older text"), sha(text)):  # 339's shape, and not_current_cv on the right sha
            await _insert_legacy(fixture_user_id, app_id, _legacy(cv_id, bad_sha))
        app_id, cv_id, text = apps["strong"]
        assert (await _stored(client, app_id, _review(cv_id, text)))["confidence"] == "strong"
        app_id, cv_id, text = apps["weak"]
        assert (await _stored(client, app_id, _review(cv_id, text, confidence="weak")))["confidence"] == "weak"
        app_id, cv_id, text = apps["older"]
        assert (await _stored(client, app_id, _review(cv_id, text)))["confidence"] == "strong"
        await _save_cv(client, app_id, text + "\n\n\n\n\n\n\n")  # a newer version: the old review is not its review

        full, short = await _queued_states(client, fixture_user_id)
        want = {apps[n][0]: s for n, s in (("none", "none"), ("legacy", "none"), ("strong", "strong"),
                                           ("weak", "weak"), ("older", "none"))}
        assert {a: full[a] for a in want} == want
        assert {a: short[a] for a in want} == want
        for app_id, state in want.items():
            review = (await client.get(f"/api/applications/{app_id}")).json()["review"]
            assert review["state"] == state


@pytest.mark.asyncio
async def test_legacy_not_current_reviews_never_count_toward_retry_limit(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, v1_id = await _setup(client)
        v2 = CV.replace("- Data engineer", "* Data engineer")
        v2_id = await _save_cv(client, app_id, v2)
        await _insert_legacy(fixture_user_id, app_id, _legacy(v1_id, ""))
        await _insert_legacy(fixture_user_id, app_id, _legacy(v2_id, sha("an older text")))
        v3 = CV.replace("- Built", "* Built")
        v3_id = await _save_cv(client, app_id, v3)
        got = await _stored(client, app_id, _review(v3_id, v3))
        assert (got["confidence"], got["downgraded"]) == ("strong", [])
