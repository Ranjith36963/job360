"""The re-extraction sweep must never exceed the bound it was given.

WHY THIS FILE EXISTS. Every ``--apply`` re-extraction is a real, billed LLM
call across a user's four inputs, so ``--limit`` is not a convenience -- it is
the spend cap. The first version of the script wrote its guard as

    if apply and limit and reextracted + failed >= limit:

which reads fine and is wrong: ``limit`` is used as a truthiness test, so
``--limit 0`` is FALSY and the cap is skipped entirely. The one invocation the
script's own docstring promises "never makes an LLM call" would instead have
re-extracted every stale profile in the database, unbounded.

Nothing about that is visible in a dry run, and it would not surface until an
operator reached for the safest-looking flag during an incident. So the bound
is pinned here rather than left to review.

The whole DB and extraction layer is stubbed (rule #4) -- these tests must
never touch Postgres and must never make a real LLM call.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

import pytest

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "reextract_stale_profiles.py"
)


def _load_script():
    """Import the script by path -- backend/scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("_reextract_script", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_reextract_script"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Recorder:
    """Counts how many profiles were actually put through extraction."""

    def __init__(self) -> None:
        self.extracted: list[str] = []
        self.saved: list[str] = []


@pytest.fixture()
def sweep(monkeypatch):
    """The script with every external edge replaced, returning (run, rec).

    ``run(limit, apply)`` executes the sweep over 25 profiles that are ALL
    stale, so any failure to honour the bound shows up immediately as a count.
    """
    mod = _load_script()
    rec = _Recorder()

    ids = [f"user-{i:02d}" for i in range(25)]

    class _Profile:
        def __init__(self, uid: str) -> None:
            self.uid = uid

    import src.services.profile.storage as storage
    import src.services.profile.two_pass as two_pass

    monkeypatch.setattr(storage, "list_profile_user_ids", lambda: ids)
    monkeypatch.setattr(storage, "load_profile", lambda uid: _Profile(uid))
    monkeypatch.setattr(
        storage, "save_profile",
        lambda profile, uid, action: rec.saved.append(uid),
    )
    # EVERY profile is stale, so the bound is the only thing that can stop it.
    monkeypatch.setattr(two_pass, "stale_extraction_inputs", lambda p: ["cv"])

    async def _fake_extract(profile):
        rec.extracted.append(profile.uid)
        return profile

    monkeypatch.setattr(two_pass, "run_two_pass_extraction", _fake_extract)

    def run(limit: int, apply: bool) -> int:
        return asyncio.run(mod.main(None, limit, apply))

    return run, rec


class TestTheSpendCapIsHonoured:
    def test_limit_zero_extracts_nothing(self, sweep) -> None:
        """THE BUG. --limit 0 is the emergency stop, and it was the one value
        that disabled the cap."""
        run, rec = sweep
        run(0, True)
        assert rec.extracted == [], (
            f"--limit 0 re-extracted {len(rec.extracted)} profiles. Each is a "
            "billed LLM call, and the script's docstring promises none."
        )
        assert rec.saved == []

    @pytest.mark.parametrize("limit", [1, 3, 10])
    def test_it_never_exceeds_the_limit(self, sweep, limit: int) -> None:
        """The general guarantee, not just the zero case -- a cap that holds
        only at 0 would still overspend at every other value."""
        run, rec = sweep
        run(limit, True)
        assert len(rec.extracted) == limit

    def test_a_dry_run_never_extracts_at_any_limit(self, sweep) -> None:
        """Dry run is the default, so this is the path an operator hits first.
        It must report without spending anything."""
        run, rec = sweep
        run(10, False)
        assert rec.extracted == []
        assert rec.saved == []

    def test_the_bound_is_not_vacuous(self, sweep) -> None:
        """The control. Without it, a script that extracted NOTHING under any
        setting would pass every assertion above while being useless."""
        run, rec = sweep
        run(5, True)
        assert len(rec.extracted) == 5
        assert rec.saved == rec.extracted
