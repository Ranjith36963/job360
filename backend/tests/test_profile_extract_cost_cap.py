"""The profile re-extraction cost cap must actually bite.

Why this exists
---------------
Every profile change re-runs the FULL two-pass extraction — 4+ paid LLM calls
over CV / LinkedIn / GitHub / about_me — from stored data. Five routes reach the
same code path (`_extract_save_trigger`), and nothing bounded how often a user
could trigger it.

That was harmless while it was free. It stopped being free on 2026-07-19: the
`openai` dependency had never actually been installed in production (the import
raised, a broad `except` swallowed it, and every parse quietly fell back to a
free tier), so declaring it turned the primary PAID provider on for the first
time. The uncapped loop that previously cost nothing now bills.

These tests assert BEHAVIOUR (the 13th call is refused, the extraction does not
run) rather than "the setting exists" — a config-presence test would pass against
a cap that was never wired in (rule #21).
"""

import pytest

import src.api.routes.profile as profile_routes
from src.services.auth import rate_limit as auth_rate_limit


@pytest.fixture(autouse=True)
def _clear_limiter():
    """Each test starts with an empty window — the limiter is process-global."""
    auth_rate_limit.reset_for_tests()
    yield
    auth_rate_limit.reset_for_tests()


@pytest.mark.asyncio
async def test_cap_allows_normal_use_then_refuses(monkeypatch):
    """12 extractions allowed in the hour; the 13th is refused with 429."""
    from fastapi import HTTPException

    calls = {"n": 0}

    async def _fake_extract(profile):
        calls["n"] += 1
        return profile

    monkeypatch.setattr(profile_routes, "run_two_pass_extraction", _fake_extract)
    monkeypatch.setattr(profile_routes, "save_profile", lambda *a, **k: None)

    monkeypatch.setattr(profile_routes, "PROFILE_EXTRACT_MAX_PER_HOUR", 12)

    for i in range(12):
        await profile_routes._extract_save_trigger(object(), "user-cap")
        assert calls["n"] == i + 1, "a within-limit call must run the extraction"

    with pytest.raises(HTTPException) as exc:
        await profile_routes._extract_save_trigger(object(), "user-cap")

    assert exc.value.status_code == 429
    assert calls["n"] == 12, (
        "the refused call must NOT have run the extraction — otherwise the cap "
        "costs money while pretending to save it"
    )


@pytest.mark.asyncio
async def test_cap_is_per_user(monkeypatch):
    """One user hitting the cap must not lock out everyone else."""
    from fastapi import HTTPException

    async def _fake_extract(profile):
        return profile

    monkeypatch.setattr(profile_routes, "run_two_pass_extraction", _fake_extract)
    monkeypatch.setattr(profile_routes, "save_profile", lambda *a, **k: None)

    monkeypatch.setattr(profile_routes, "PROFILE_EXTRACT_MAX_PER_HOUR", 2)

    for _ in range(2):
        await profile_routes._extract_save_trigger(object(), "alice")
    with pytest.raises(HTTPException):
        await profile_routes._extract_save_trigger(object(), "alice")

    # bob is untouched by alice's spending
    await profile_routes._extract_save_trigger(object(), "bob")


@pytest.mark.asyncio
async def test_cap_can_be_disabled_with_zero(monkeypatch):
    """PROFILE_EXTRACT_MAX_PER_HOUR=0 disables the cap (documented escape hatch)."""
    calls = {"n": 0}

    async def _fake_extract(profile):
        calls["n"] += 1
        return profile

    monkeypatch.setattr(profile_routes, "run_two_pass_extraction", _fake_extract)
    monkeypatch.setattr(profile_routes, "save_profile", lambda *a, **k: None)

    monkeypatch.setattr(profile_routes, "PROFILE_EXTRACT_MAX_PER_HOUR", 0)

    for _ in range(25):
        await profile_routes._extract_save_trigger(object(), "unlimited")
    assert calls["n"] == 25
