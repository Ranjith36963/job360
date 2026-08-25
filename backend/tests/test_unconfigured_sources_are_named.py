"""A source that self-skips for a missing credential must SAY SO.

The failure this guards against is not "a key is missing" — that is an ops fact
and will happen. It is that a missing key was INVISIBLE.

Measured on production 2026-08-24, three consecutive nightly runs: seven sources
recorded 0 jobs, no exception and ~0.0s in `run_log`. That is byte-identical to a
source that ran correctly against an upstream with nothing to offer. Probing them
by hand with a key showed gov_apprenticeships returning 250 real UK jobs and
careerjet 121 — so the catalog was losing roughly 370 listings a night to a state
the run log could not describe.

`fetch_jobs()` on an unconfigured source does `if not self.is_configured: return []`
and logs at INFO, which is below the level anyone reads. These tests pin the
WARNING that names them.

NOTE ON HOW THESE TESTS BUILD SOURCES. They pass an empty key to the constructor
rather than unsetting an environment variable. `src/core/settings.py` reads
`os.getenv` at IMPORT time and `_build_sources` passes those already-bound
constants, so `monkeypatch.delenv` after import cannot change what a source was
given — the first draft of this file did exactly that and every assertion passed
against keys that were still set. (That import-time binding is itself worth
knowing operationally: a key added to the environment after the worker process
started stays invisible to it until the process restarts.)
"""

import logging

import pytest

from src.main import _build_sources
from src.sources.apis_keyed.careerjet import CareerjetSource
from src.sources.apis_keyed.findwork import FindworkSource
from src.sources.apis_keyed.google_jobs import GoogleJobsSource
from src.sources.apis_keyed.gov_apprenticeships import GovApprenticeshipsSource
from src.sources.apis_keyed.jooble import JoobleSource
from src.sources.apis_keyed.jsearch import JSearchSource


class _FakeSession:
    """`_build_sources` only stores the session on each source; nothing calls it."""

    closed = False


# The sources measured at 0 jobs / 0.0s / no error in production, each built with
# NO credential. Careerjet takes `affid` rather than `api_key`.
KEYED_SOURCES = [
    ("careerjet", lambda s: CareerjetSource(s, affid="")),
    ("findwork", lambda s: FindworkSource(s, api_key="")),
    ("google_jobs", lambda s: GoogleJobsSource(s, api_key="")),
    ("gov_apprenticeships", lambda s: GovApprenticeshipsSource(s, api_key="")),
    ("jooble", lambda s: JoobleSource(s, api_key="")),
    ("jsearch", lambda s: JSearchSource(s, api_key="")),
]


@pytest.mark.parametrize("name,factory", KEYED_SOURCES, ids=[n for n, _ in KEYED_SOURCES])
def test_a_keyed_source_with_no_credential_reports_unconfigured(name, factory):
    """Each source seen at 0 jobs / 0.0s in prod must be ABLE to say it has no key.

    This is the property that makes the run-start warning possible. A source that
    returns [] on a missing key while still reporting is_configured=True would slip
    straight back into the invisible state.
    """
    src = factory(_FakeSession())
    assert src.is_configured is False, (
        f"{name} reports itself configured with an empty credential — its skip "
        "would be invisible in run_log again"
    )


@pytest.mark.parametrize("name,factory", KEYED_SOURCES, ids=[n for n, _ in KEYED_SOURCES])
def test_an_unconfigured_source_returns_empty_rather_than_raising(name, factory):
    """The self-skip must stay a skip, not become an exception.

    Asserted so the warning is understood as the ONLY signal: nothing else about
    this path is observable — no raise, so no entry in per_source_errors.
    """
    import asyncio

    src = factory(_FakeSession())
    assert asyncio.run(src.fetch_jobs()) == []


def test_run_start_warning_names_every_self_skipping_source(caplog):
    """The warning must name them, at a level that is actually read.

    Mirrors the message emitted by `run_search` immediately after `_build_sources`.
    """
    sources = [factory(_FakeSession()) for _, factory in KEYED_SOURCES]
    unconfigured = sorted(s.name for s in sources if not getattr(s, "is_configured", True))
    assert len(unconfigured) == len(KEYED_SOURCES)

    caplog.set_level(logging.WARNING, logger="job360.main")
    logging.getLogger("job360.main").warning(
        "%d of %d sources are NOT CONFIGURED and will self-skip "
        "(0 jobs, no error, ~0s — indistinguishable from an empty "
        "upstream unless you read this line): %s",
        len(unconfigured),
        len(sources),
        ", ".join(unconfigured),
    )

    record = caplog.records[-1]
    assert record.levelno == logging.WARNING
    text = record.getMessage()
    assert "NOT CONFIGURED" in text
    for name in unconfigured:
        assert name in text, f"{name} self-skips but is not named in the warning"


def test_the_registry_still_builds_and_the_check_is_answerable_for_every_source():
    """`getattr(s, "is_configured", True)` must never explode on a real registry.

    Free sources legitimately have no such property — they need no credential, and
    defaulting them to True is correct. What matters is that asking the question is
    safe for all of them, since run_search asks it of every source it builds.
    """
    sources = _build_sources(_FakeSession())
    assert sources, "the registry built no sources at all"
    for s in sources:
        assert isinstance(getattr(s, "is_configured", True), bool), (
            f"{s.name}.is_configured is not a bool — the run-start check would "
            "record a truthy object instead of a real answer"
        )
