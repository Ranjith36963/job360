"""A credential with a stray newline is a DIFFERENT credential.

Secrets are pasted into a dashboard by hand, so trailing whitespace rides along
more often than anyone expects. It is invisible in every UI, and fatal wherever
the value is concatenated rather than form-encoded: jooble builds
`https://jooble.org/api/{key}`, so a trailing newline makes a different URL.

Measured on production 2026-08-24 by fingerprinting each Railway service's
environment: SERPAPI_KEY and JSEARCH_API_KEY both carried surrounding whitespace
on the worker service, while google_jobs was returning HTTP 401 — which reads as
"your key is wrong" rather than "your key has a space on the end".

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

CREDENTIALS = [
    "REED_API_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "JSEARCH_API_KEY",
    "JOOBLE_API_KEY",
    "SERPAPI_KEY",
    "CAREERJET_AFFID",
    "FINDWORK_API_KEY",
    "DFE_APPRENTICESHIPS_API_KEY",
]


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
    value = getattr(reload_settings(), name)
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
    value = getattr(reload_settings(), name)
    if value != "":
        pytest.fail(f"{name} read a whitespace-only value as configured ({len(value)} chars)")


@pytest.mark.parametrize("name", CREDENTIALS)
def test_an_unset_credential_is_still_empty_string(name, monkeypatch, reload_settings):
    """Absence must stay an empty string, never None — every caller does bool()."""
    monkeypatch.delenv(name, raising=False)
    value = getattr(reload_settings(), name)
    if not isinstance(value, str):
        pytest.fail(f"{name} is {type(value).__name__}, not str — bool() callers would break")
    if value != "":
        pytest.fail(
            f"{name} came back non-empty ({len(value)} chars) with the variable "
            "unset — dotenv is repopulating it from a real .env"
        )


def test_a_clean_credential_is_untouched(monkeypatch, reload_settings):
    monkeypatch.setenv("SERPAPI_KEY", "abcdef0123456789")
    if reload_settings().SERPAPI_KEY != "abcdef0123456789":
        pytest.fail("a clean credential must pass through unchanged")
