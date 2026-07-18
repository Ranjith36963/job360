"""Log-injection guard tests (CodeQL py/log-injection, ~30 call sites).

User-controlled values — emails, usernames, GitHub handles — are logged all over
the API (``logger.info("... %s", email)``). A value containing a newline lets an
attacker forge entire log lines, e.g. an email of::

    victim@x.com\\n2026-01-01 [INFO] job360.api.auth: login status=success

poisons the audit trail an operator reads during an incident.

``CRLFScrubFilter`` escapes CR/LF once, on the HANDLERS, so every call site is
covered — including the ~30 that live on CHILD loggers. That distinction is the
whole point of these tests: a filter attached to the *logger* would silently
miss propagated records, which is exactly where the findings are.
"""

from __future__ import annotations

import logging

from src.utils.logger import CRLFScrubFilter, _scrub

_PAYLOAD = "victim@x.com\n2026-01-01 [INFO] job360.api.auth: login status=success"


def test_scrub_escapes_crlf():
    assert _scrub("a\nb") == "a\\nb"
    assert _scrub("a\r\nb") == "a\\r\\nb"


def test_scrub_passes_clean_and_nonstring_through():
    assert _scrub("plain@example.com") == "plain@example.com"
    assert _scrub(42) == 42  # %d formatting must still work
    assert _scrub(None) is None


def test_filter_scrubs_positional_args():
    record = logging.LogRecord(
        name="job360.api.auth", level=logging.INFO, pathname=__file__, lineno=1,
        msg="login attempt email=%s", args=(_PAYLOAD,), exc_info=None,
    )
    CRLFScrubFilter().filter(record)
    out = record.getMessage()
    assert "\n" not in out, "newline survived — log forging still possible"
    assert "\\n" in out


def test_filter_scrubs_dict_args_and_msg():
    # NB: dict args must be passed inside a 1-tuple — LogRecord unwraps
    # ``args[0]`` when it is a Mapping (passing the dict bare raises KeyError: 0).
    record = logging.LogRecord(
        name="job360.api.auth", level=logging.INFO, pathname=__file__, lineno=1,
        msg="user=%(u)s", args=({"u": _PAYLOAD},), exc_info=None,
    )
    assert isinstance(record.args, dict)  # confirm the unwrap happened
    CRLFScrubFilter().filter(record)
    assert "\n" not in record.getMessage()

    record2 = logging.LogRecord(
        name="job360", level=logging.INFO, pathname=__file__, lineno=1,
        msg=_PAYLOAD, args=None, exc_info=None,
    )
    CRLFScrubFilter().filter(record2)
    assert "\n" not in record2.getMessage()


def test_filter_never_drops_records():
    record = logging.LogRecord(
        name="job360", level=logging.INFO, pathname=__file__, lineno=1,
        msg="clean", args=None, exc_info=None,
    )
    assert CRLFScrubFilter().filter(record) is True


def test_child_logger_output_is_scrubbed_end_to_end(tmp_path):
    """The case that matters: a CHILD logger propagating to a scrubbed handler.

    A filter on the *logger* would NOT cover this — only a handler filter does.
    """
    parent = logging.getLogger("job360_testparent")
    parent.setLevel(logging.INFO)
    parent.handlers.clear()
    log_file = tmp_path / "out.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(CRLFScrubFilter())  # same wiring as setup_logging()
    parent.addHandler(handler)

    logging.getLogger("job360_testparent.api.auth").info("email=%s", _PAYLOAD)
    handler.close()

    written = log_file.read_text(encoding="utf-8")
    assert written.count("\n") == 1, (
        f"expected exactly one line, got {written.count(chr(10))} — "
        "forged log lines got through"
    )
    assert "\\n" in written
    assert "status=success" in written  # payload preserved, just neutralised
