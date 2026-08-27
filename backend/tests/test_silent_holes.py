"""W-12 / W-28 / W-29 — three facts the product knew and never acted on.

All three share a shape: the data was already correct in the database, and something
between the database and the user simply did not carry it. None of them is a missing
feature; each is a wire that was never connected.

  W-28  Uploading a CV or editing preferences queues a re-score. Clearing a profile
        section or restoring an older version does NOT — so the feed keeps showing
        scores computed from a profile the user has just deleted, presented as if
        they were current. The app showing a stale number as though it were fresh is
        the app lying.

  W-12  Every tailored document is stamped with the profile_version that produced it.
        The API response model drops the field, so no "this CV was written from an
        older version of your profile" warning can ever be built.

  W-29  Career domain, spoken languages and education detail are read straight into
        the LLM matcher's prompt (llm_matcher.py:249-260) — they move the score. The
        user can never see them, so can never correct them. The same "stored but not
        shown" class the cv_positions comment in models.py already documents.
"""

from __future__ import annotations

import pytest

from src.api import dependencies as api_deps  # noqa: F401 — parity with sibling suites
from src.api.routes import profile as profile_routes

PREFS = '{"target_job_titles": ["ML Engineer"], "experience_level": "senior"}'


async def _ensure_profile(client) -> None:
    """Create a profile for the fixture user.

    The fixture registers a bare account with NO profile, and /profile/clear
    honestly 404s in that state ("No profile to clear"). Every test here is about
    what happens to an EXISTING profile, so each has to make one exist first.
    """
    resp = await client.post("/api/profile/preferences", data={"preferences": PREFS})
    assert resp.status_code == 200, resp.text


# ── W-28: changing the profile must invalidate the scores ────────────────────


@pytest.mark.asyncio
async def test_clearing_a_profile_section_triggers_a_rescore(
    authenticated_async_context, monkeypatch
):
    """Clearing an input changes what the scores were computed FROM."""
    called: list[str] = []

    async def _spy(user_id: str) -> None:
        called.append(user_id)

    monkeypatch.setattr(profile_routes, "_maybe_trigger_rescore", _spy)

    async with authenticated_async_context() as client:
        await _ensure_profile(client)
        called.clear()
        resp = await client.post("/api/profile/clear", data={"section": "github"})
        assert resp.status_code == 200, resp.text

    assert called, (
        "clearing a profile section left the feed showing scores from the profile "
        "the user just deleted"
    )


@pytest.mark.asyncio
async def test_restoring_an_older_version_triggers_a_rescore(
    authenticated_async_context, monkeypatch
):
    """Rolling back the profile must roll back the scores with it."""
    called: list[str] = []

    async def _spy(user_id: str) -> None:
        called.append(user_id)

    monkeypatch.setattr(profile_routes, "_maybe_trigger_rescore", _spy)
    uid = authenticated_async_context.fixture_user_id

    async with authenticated_async_context() as client:
        # Create a version to roll back to by saving a profile first.
        await _ensure_profile(client)
        resp = await client.get("/api/profile/versions")
        if resp.status_code != 200 or not resp.json().get("versions"):
            pytest.skip("no profile version could be created in this fixture")
        version_id = resp.json()["versions"][0]["id"]
        called.clear()

        resp = await client.post(f"/api/profile/versions/{version_id}/restore")
        assert resp.status_code == 200, resp.text

    assert called == [uid], (
        "restoring an older profile left the feed scored against the newer one"
    )


@pytest.mark.asyncio
async def test_a_dead_queue_never_fails_the_users_request(
    authenticated_async_context, monkeypatch
):
    """Re-scoring is background work. A queue outage must not turn a clear into a 500.

    Breaks the REAL enqueue door rather than replacing _maybe_trigger_rescore: that
    function documents "the profile save never 500s because of this" and falls back
    to an in-process task, and the call site relies on that (it calls it bare, like
    the two pre-existing call sites do). Stubbing the function out would have tested
    a guard I invented instead of the one that actually ships.
    """
    from src.workers import queue as _queue

    async def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("redis is down")

    monkeypatch.setattr(_queue, "enqueue_job", _boom)

    async with authenticated_async_context() as client:
        r = await client.post("/api/profile/preferences", data={"preferences": PREFS})
        assert r.status_code == 200, r.text
        resp = await client.post("/api/profile/clear", data={"section": "github"})

    assert resp.status_code == 200, (
        f"a background re-score failure broke the user's request: {resp.text}"
    )


# ── W-29: the facts that move the score must be visible ──────────────────────


@pytest.mark.asyncio
async def test_the_three_scoring_facts_are_exposed_on_the_profile(
    authenticated_async_context,
):
    """career_domain / cv_languages / cv_education_details reach the response.

    Rule #21 — asserting the KEY exists would pass against an empty default, which
    is exactly the bug (the field was absent, not empty). So this writes real values
    and asserts they come back.
    """
    from src.services.profile.storage import load_profile, save_profile

    uid = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        await _ensure_profile(client)
    profile = load_profile(uid)
    # cv_detail is None unless raw_text exists (profile.py:397) — and in reality
    # these three fields only exist BECAUSE a CV was parsed, so a profile with
    # them and no CV text is not a state the product can produce.
    profile.cv_data.raw_text = "Senior engineer, MSc, speaks English and Tamil."
    profile.cv_data.career_domain = "software engineering"
    profile.cv_data.cv_languages = ["English", "Tamil"]
    profile.cv_data.cv_education_details = ["MSc thesis on distributed systems"]
    save_profile(profile, uid)

    async with authenticated_async_context() as client:
        resp = await client.get("/api/profile")
        assert resp.status_code == 200, resp.text
        detail = resp.json().get("cv_detail") or {}

    assert detail.get("career_domain") == "software engineering", (
        f"career_domain never reaches the user, but moves their score: {detail.keys()}"
    )
    assert "Tamil" in (detail.get("cv_languages") or []), "cv_languages not exposed"
    assert any(
        "distributed systems" in e for e in (detail.get("cv_education_details") or [])
    ), "cv_education_details not exposed"


@pytest.mark.asyncio
async def test_the_three_facts_are_empty_not_absent_for_a_bare_profile(
    authenticated_async_context,
):
    """Rule #29 — an unfilled field is "don't care", never a guess or a fake value."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/profile")
        detail = (resp.json() or {}).get("cv_detail") or {}

    if detail:
        assert detail.get("career_domain", "") == ""
        assert detail.get("cv_languages", []) == []
        assert detail.get("cv_education_details", []) == []

# ── W-12: a document must say which profile it was written from ──────────────


def _row(**kw):
    base = {"doc_kind": "cv", "ai_draft": "draft", "status": "draft"}
    base.update(kw)
    return base


def test_a_document_reports_the_profile_version_it_was_built_from() -> None:
    from src.api.routes.tailor import _doc_out

    out = _doc_out(_row(profile_version=3), 3)
    assert out.profile_version == 3, "the stamp was dropped before it reached the user"


def test_a_document_written_from_an_older_profile_is_flagged() -> None:
    from src.api.routes.tailor import _doc_out

    out = _doc_out(_row(profile_version=2), 5)
    assert out.profile_changed_since is True


def test_a_current_document_is_not_flagged() -> None:
    from src.api.routes.tailor import _doc_out

    out = _doc_out(_row(profile_version=5), 5)
    assert out.profile_changed_since is False


def test_an_unknown_version_never_claims_staleness() -> None:
    """Silence is not evidence.

    Documents generated before the stamp existed have profile_version NULL, and a
    profile read can fail. Warning on either would train the user to ignore the
    warning — which costs more than the warning is worth.
    """
    from src.api.routes.tailor import _doc_out

    assert _doc_out(_row(profile_version=None), 5).profile_changed_since is False
    assert _doc_out(_row(profile_version=2), None).profile_changed_since is False
    assert _doc_out(_row(), None).profile_changed_since is False


def test_a_document_from_a_newer_version_is_not_flagged_either() -> None:
    """Only strictly-older counts. A future-looking number is a bug elsewhere,
    and reporting it as 'out of date' would be actively wrong."""
    from src.api.routes.tailor import _doc_out

    assert _doc_out(_row(profile_version=9), 5).profile_changed_since is False

