"""SITE_BASE_URL must never resolve to an empty origin.

Every job link in a delivered email is built from this value
(``workers/tasks.py`` → ``build_decision_card(..., site_base_url=SITE_BASE_URL)``).
If it is empty, the link becomes ``/jobs/147`` — a path with no host, which
resolves to nothing from someone's inbox and is unrecoverable once sent.

Two ways it has already been got wrong, both guarded below:

1. ``os.getenv("SITE_BASE_URL", "https://job360.uk")`` — a getenv default never
   fires for a variable that is SET BUT EMPTY, which is exactly what a blank
   Railway variable or a bare ``SITE_BASE_URL=`` line in .env produces.
2. ``os.getenv("SITE_BASE_URL") or "https://job360.uk"`` — closes case 1 but not
   whitespace: ``" "`` is truthy, so the fallback is skipped, and a later
   ``.strip()`` empties it again. (CodeRabbit, PR #381.)

The settings module reads the environment at import time, so each case is
exercised by re-importing it under a patched environ rather than by reassigning
the constant — reassignment would test nothing about how the value is derived.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def _restore_settings_module():
    """Reload settings under the ORIGINAL environ after every test in this file.

    ``importlib.reload`` mutates module-level state that outlives the test:
    monkeypatch restores the environment, but ``settings.SITE_BASE_URL`` keeps
    whatever the last reload computed. A later test — in this file or any other
    that imports the constant — could then read `https://staging.job360.uk` with
    no environment set, and which one it sees would depend on test ORDER.
    Reloading again on teardown, after monkeypatch has undone the environ,
    leaves the module exactly as it was found. (CodeRabbit, PR #381.)
    """
    yield
    import src.core.settings as settings

    importlib.reload(settings)


def _reload_site_base_url(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> str:
    """Re-import settings with SITE_BASE_URL set to ``raw`` and return the result."""
    if raw is None:
        monkeypatch.delenv("SITE_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("SITE_BASE_URL", raw)
    import src.core.settings as settings

    importlib.reload(settings)
    return str(settings.SITE_BASE_URL)


@pytest.mark.parametrize(
    "raw,label",
    [
        (None, "unset"),
        ("", "set but empty"),
        ("   ", "whitespace only"),
        ("\t\n", "tabs and newlines"),
        # Slash-only values are the third variant of this bug: truthy, so they
        # survive a fallback applied before normalisation, and then rstrip("/")
        # turns them into "". Normalise first, fall back last.
        ("/", "a single slash"),
        ("///", "several slashes"),
        ("  //  ", "slashes padded with whitespace"),
    ],
)
def test_absent_or_blank_env_falls_back_to_the_real_origin(monkeypatch, raw, label):
    value = _reload_site_base_url(monkeypatch, raw)
    assert value == "https://job360.uk", f"{label} must fall back, got {value!r}"


def test_a_real_value_is_honoured_and_trailing_slash_stripped():
    """A configured origin must win — this is a PARAMETER, not a hardcode.

    Staging and preview deploys need their own origin; a link that silently
    points at production from a staging send misleads a real person.
    """
    with pytest.MonkeyPatch.context() as mp:
        assert (
            _reload_site_base_url(mp, "https://staging.job360.uk/")
            == "https://staging.job360.uk"
        )


def test_surrounding_whitespace_is_trimmed_not_treated_as_absent():
    """``" https://x "`` is a real value that was merely typed untidily."""
    with pytest.MonkeyPatch.context() as mp:
        assert _reload_site_base_url(mp, "  https://staging.job360.uk  ") == (
            "https://staging.job360.uk"
        )
