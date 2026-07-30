"""A read-only filesystem must never take down the pipeline.

Regression guard for the 2026-07-30 outage behind issue #170: the worker image
installs the package into site-packages, so LOGS_DIR resolved to
``/usr/local/lib/python3.12/site-packages/data/logs``. ``setup_logging`` did an
unguarded ``LOGS_DIR.mkdir()`` as the FIRST line of ``run_search`` — the 04:00
``refresh_catalog`` cron raised PermissionError before fetching a single job,
for three nights, silently. The catalog starved and the absence detector fired.

The rule these tests pin: log/export FILES are conveniences; stdout and
Postgres are the product. Disk failure downgrades output, never kills the run.
"""

import logging

import pytest

import src.utils.logger as logger_mod


class _ReadOnlyDir:
    """Stands in for LOGS_DIR on a read-only filesystem: mkdir always denied."""

    def mkdir(self, *a, **kw):
        raise PermissionError(13, "Permission denied", "/site-packages/data/logs")

    def __truediv__(self, other):  # pragma: no cover — reaching this IS the bug
        raise AssertionError(
            "file handler path was built even though mkdir failed — "
            "the fallback must skip file handlers entirely"
        )

    def __str__(self):
        return "/site-packages/data/logs (read-only stub)"


@pytest.fixture(autouse=True)
def _fresh_loggers():
    """Both setup functions are idempotent via `if logger.handlers` — strip
    handlers before AND after so each test exercises the real setup path and
    leaves nothing behind for the rest of the suite."""
    for name in ("job360", "job360.audit"):
        logging.getLogger(name).handlers.clear()
    yield
    for name in ("job360", "job360.audit"):
        logging.getLogger(name).handlers.clear()


def test_setup_logging_survives_readonly_fs(monkeypatch):
    monkeypatch.setattr(logger_mod, "LOGS_DIR", _ReadOnlyDir())

    log = logger_mod.setup_logging("INFO")  # must not raise — THE bug

    # Console-only fallback: exactly the stream handler, no file handlers.
    assert len(log.handlers) == 1
    assert isinstance(log.handlers[0], logging.StreamHandler)
    # And logging through it must work end to end.
    log.info("pipeline continues without file logs")


def test_setup_logging_console_keeps_injection_scrubber(monkeypatch):
    """The CRLF scrubber is a security control (log-injection guard). Falling
    back to console-only must not silently drop it."""
    monkeypatch.setattr(logger_mod, "LOGS_DIR", _ReadOnlyDir())

    log = logger_mod.setup_logging("INFO")

    assert any(
        isinstance(f, logger_mod.CRLFScrubFilter) for f in log.handlers[0].filters
    ), "console fallback lost the CRLF injection scrubber"


def test_audit_logger_survives_readonly_fs(monkeypatch):
    monkeypatch.setattr(logger_mod, "LOGS_DIR", _ReadOnlyDir())

    audit = logger_mod.setup_audit_logger()  # must not raise

    assert audit.handlers, "audit logger must still get a handler"
    handler = audit.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    # The audit stream is the most injection-sensitive one — the scrubber must
    # survive the fallback here too.
    assert any(isinstance(f, logger_mod.CRLFScrubFilter) for f in handler.filters)


def test_writable_fs_still_gets_file_handlers(tmp_path, monkeypatch):
    """The fallback must not become the default: on a normal disk the two
    rotating file handlers (text + jsonl) still attach."""
    monkeypatch.setattr(logger_mod, "LOGS_DIR", tmp_path / "logs")

    log = logger_mod.setup_logging("INFO")

    from logging.handlers import RotatingFileHandler

    rotating = [h for h in log.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating) == 2, "writable disk must still produce job360.log + job360.jsonl"
