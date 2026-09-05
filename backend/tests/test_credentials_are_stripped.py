"""A credential with a stray newline is a DIFFERENT credential.

Secrets are pasted into a dashboard by hand, so trailing whitespace rides along
more often than anyone expects. It is invisible in every UI, and fatal wherever
the value is concatenated rather than form-encoded — a key in a URL path
or a header becomes a DIFFERENT key.

Measured on production 2026-08-24 by fingerprinting each Railway service's
environment: two live keys both carried surrounding whitespace, while the
source using one was returning HTTP 401 — which reads as "your key is wrong"
rather than "your key has a space on the end".

TWO RULES THIS FILE OBEYS, both learned the hard way when the first draft printed
real key fragments into a terminal:

1. `load_dotenv` is neutralised before every reload. `core/settings.py` calls it
   at import, and it walks UP from the working directory — from a git worktree it
   finds the developer's real `.env` at the repository root. Deleting an
   environment variable and reloading therefore does not give you an unset
   credential; it gives you the developer's live one.

2. No assertion compares a credential value directly. A bare `assert value == ""`
   makes pytest print the actual value in the failure diff. Every check here
   reduces to a boolean first and reports through `pytest.fail` with a message
   that contains no secret.
"""

import importlib

import pytest

# Slice 5 (#483) deleted the nine job-board keys this file used to sweep, with
# the sources that read them. `_secret()` itself survives — `GITHUB_TOKEN` is
# its remaining caller — so the guard now tests the HELPER directly, under a
# name no scanner and no real environment can collide with. Testing the
# function rather than one constant is the stronger shape anyway: the next
# credential added through `_secret()` is covered without editing this file.
_PROBE = "JOB360_TEST_ONLY_CREDENTIAL"
CREDENTIALS = [_PROBE]


@pytest.fixture
def reload_settings(monkeypatch):
    """Reload settings with dotenv disabled, so the test controls the environment."""

    def _reload():
        import dotenv

        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
        import src.core.settings as settings_mod

        monkeypatch.setattr(settings_mod, "load_dotenv", lambda *a, **k: False, raising=False)
        return importlib.reload(settings_mod)

    return _reload


@pytest.mark.parametrize("name", CREDENTIALS)
def test_surrounding_whitespace_is_stripped(name, monkeypatch, reload_settings):
    monkeypatch.setenv(name, "  abc123\n")
    value = reload_settings()._secret(name)
    if value != "abc123":
        pytest.fail(
            f"{name} was not stripped (got {len(value)} chars, expected 6) — "
            "concatenated into a URL or header this is a different credential, "
            "and the upstream answers 401/403"
        )


@pytest.mark.parametrize("name", CREDENTIALS)
def test_a_whitespace_only_value_reads_as_absent(name, monkeypatch, reload_settings):
    """"   " must mean "no key", not "a key one space long".

    Otherwise `is_configured` returns True, the source runs, and the upstream
    rejects it — the loud failure path instead of the honest self-skip.
    """
    monkeypatch.setenv(name, "   \n")
    value = reload_settings()._secret(name)
    if value != "":
        pytest.fail(f"{name} read a whitespace-only value as configured ({len(value)} chars)")


@pytest.mark.parametrize("name", CREDENTIALS)
def test_an_unset_credential_is_still_empty_string(name, monkeypatch, reload_settings):
    """Absence must stay an empty string, never None — every caller does bool()."""
    monkeypatch.delenv(name, raising=False)
    value = reload_settings()._secret(name)
    if not isinstance(value, str):
        pytest.fail(f"{name} is {type(value).__name__}, not str — bool() callers would break")
    if value != "":
        pytest.fail(
            f"{name} came back non-empty ({len(value)} chars) with the variable "
            "unset — dotenv is repopulating it from a real .env"
        )


# A VALUE NO SCANNER CAN MISTAKE FOR A SECRET.
# This fixture used to be a sixteen-character lowercase-hex run, which is the
# exact shape gitleaks' `generic-api-key` rule exists to catch — so CI went red
# over a value that never held anything real.
#
# The fix is NOT an allowlist entry: teaching the scanner to ignore a pattern
# blinds it to the next REAL key of that shape. Make the value unmistakably
# fake instead. The test only needs a string that survives a round trip
# unchanged, so the string can say what it is.
#
# AND THE OLD VALUE IS DESCRIBED, NOT REPRODUCED. The first version of this
# comment quoted it verbatim to explain the fix — and gitleaks flagged the
# COMMENT, at this line, because a scanner reads the whole file and cannot tell
# an example from a use. A note about a secret-shaped string must not contain
# one. (Measured: run 32872148944, "File: ...test_credentials_are_stripped.py,
# Line: 101" — the line that was explaining the fix.)
_CLEAN_FIXTURE_VALUE = "not-a-real-key-only-a-test-fixture"


def test_a_clean_credential_is_untouched(monkeypatch, reload_settings):
    monkeypatch.setenv(_PROBE, _CLEAN_FIXTURE_VALUE)
    if reload_settings()._secret(_PROBE) != _CLEAN_FIXTURE_VALUE:
        pytest.fail("a clean credential must pass through unchanged")


def test_the_helper_is_actually_wired_to_a_real_setting(reload_settings):
    """Non-vacuity: `_secret` is only worth testing while something reads it.
    `GITHUB_TOKEN` is its one remaining caller after slice 5 (#483)."""
    import inspect

    settings = reload_settings()
    assert isinstance(settings.GITHUB_TOKEN, str)
    assert "GITHUB_TOKEN = _secret(" in inspect.getsource(settings), (
        "GITHUB_TOKEN no longer goes through _secret() — this file now tests "
        "a helper nothing uses"
    )
