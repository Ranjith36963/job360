"""An empty Sentry must be falsifiable.

WHY THIS EXISTS (found 2026-08-15, while trying to answer "what do the
extraction errors look like in Sentry?").

``init_sentry`` returns a bool saying whether error reporting actually turned
on. BOTH call sites -- ``api/main.py:98`` and ``workers/settings.py:107`` --
discarded it, and nothing was logged either way. So a production process whose
``SENTRY_DSN`` was unset, misspelled, or dropped during a redeploy would report
nothing, announce nothing, and look exactly like a healthy service.

That is not a hypothetical: this module's own docstring records that the WORKER
never initialised Sentry at all, so "Sentry looked healthy precisely where the
worst failures happened". The fix then was to call init from the worker too.
The gap that remained is that nobody can tell whether the call SUCCEEDED.

It is the same shape as every other defect in this batch: silence that reads as
health. A capability that is off must say it is off.

Three states, and an operator must be able to tell them apart from the logs
alone:
  * ON in prod        -> INFO, so an empty Sentry can be trusted
  * OFF in prod       -> ERROR, because it is a real operational hole
  * off outside prod  -> INFO, because that is correct and expected
"""
from __future__ import annotations

import logging

import pytest


@pytest.fixture()
def _prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)


def _messages(caplog, min_level: int) -> str:
    return " ".join(
        r.getMessage() for r in caplog.records if r.levelno >= min_level
    ).lower()


class TestItSaysWhichStateItIsIn:
    def test_production_without_a_dsn_is_an_error(self, monkeypatch, caplog, _prod) -> None:
        """The dangerous state. Anyone reading an empty Sentry needs to know it
        was never connected, and needs it at a level that gets noticed."""
        import src.core.settings as settings
        from src.core.observability import init_sentry

        monkeypatch.setattr(settings, "SENTRY_DSN", "", raising=False)

        with caplog.at_level(logging.INFO, logger="job360"):
            assert init_sentry(component="worker") is False

        text = _messages(caplog, logging.ERROR)
        assert text, "a production process with no DSN said nothing at ERROR"
        assert "sentry_dsn" in text, "does not name the missing setting"
        assert "worker" in text, "does not say WHICH component is unobserved"

    def test_outside_production_is_only_informational(self, monkeypatch, caplog) -> None:
        """The control. Local dev and the test suite are SUPPOSED to be off, so
        shouting here would train everyone to ignore the message that matters."""
        import src.core.settings as settings
        from src.core.observability import init_sentry

        monkeypatch.setattr(settings, "SENTRY_DSN", "a-dsn", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)

        with caplog.at_level(logging.INFO, logger="job360"):
            assert init_sentry(component="api") is False

        assert not _messages(caplog, logging.ERROR), (
            "being off outside production is correct and must not raise an ERROR"
        )
        assert _messages(caplog, logging.INFO), "said nothing at all"

    def test_a_working_setup_announces_itself(self, monkeypatch, caplog, _prod) -> None:
        """The positive half, and the one that makes an empty Sentry mean
        something. Without this line you cannot distinguish 'no errors' from
        'never connected'."""
        import src.core.settings as settings
        from src.core.observability import init_sentry

        monkeypatch.setattr(settings, "SENTRY_DSN", "https://k@example.test/1", raising=False)

        captured: dict = {}

        class _FakeSentry:
            def init(self, **kw):
                captured.update(kw)

            def set_tag(self, *a, **kw):
                pass

        import sys

        fake = _FakeSentry()
        monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
        monkeypatch.setitem(
            sys.modules,
            "sentry_sdk.integrations.logging",
            type("_M", (), {"ignore_logger": staticmethod(lambda name: None)})(),
        )

        with caplog.at_level(logging.INFO, logger="job360"):
            assert init_sentry(component="api") is True

        text = _messages(caplog, logging.INFO)
        assert "error reporting on" in text, (
            "a working Sentry setup is silent, so an empty dashboard stays "
            "unfalsifiable"
        )
        assert "api" in text

    def test_the_dsn_is_never_logged(self, monkeypatch, caplog, _prod) -> None:
        """A DSN is a credential. The whole point of this file is to log MORE
        about Sentry, which is exactly when a secret slips into a log line."""
        import src.core.settings as settings
        from src.core.observability import init_sentry

        secret = "https://super-secret-key@o123.ingest.sentry.io/456"
        monkeypatch.setattr(settings, "SENTRY_DSN", secret, raising=False)

        captured: dict = {}

        class _FakeSentry:
            def init(self, **kw):
                captured.update(kw)

            def set_tag(self, *a, **kw):
                pass

        import sys

        monkeypatch.setitem(sys.modules, "sentry_sdk", _FakeSentry())
        monkeypatch.setitem(
            sys.modules,
            "sentry_sdk.integrations.logging",
            type("_M", (), {"ignore_logger": staticmethod(lambda name: None)})(),
        )

        with caplog.at_level(logging.INFO, logger="job360"):
            init_sentry(component="api")

        everything = " ".join(r.getMessage() for r in caplog.records)
        assert "super-secret-key" not in everything
        assert secret not in everything
