import json
import logging
from unittest import mock


def test_jsonformatter_produces_valid_json():
    from src.utils.logger import JSONFormatter
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="job360.test", level=logging.INFO, pathname="", lineno=0,
        msg="hello world", args=(), exc_info=None,
    )
    output = formatter.format(record)
    entry = json.loads(output)
    assert entry["level"] == "INFO"
    assert entry["message"] == "hello world"
    assert "timestamp" in entry
    assert "run_id" in entry


def test_jsonformatter_includes_extra_fields():
    from src.utils.logger import JSONFormatter
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="job360.llm", level=logging.INFO, pathname="", lineno=0,
        msg="llm_call", args=(), exc_info=None,
    )
    record.provider = "gemini"
    record.latency_ms = 123
    record.outcome = "ok"
    output = formatter.format(record)
    entry = json.loads(output)
    assert entry["provider"] == "gemini"
    assert entry["latency_ms"] == 123
    assert entry["outcome"] == "ok"


def test_jsonformatter_includes_exception_info():
    from src.utils.logger import JSONFormatter
    import sys
    formatter = JSONFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="job360.test", level=logging.ERROR, pathname="", lineno=0,
        msg="error occurred", args=(), exc_info=exc_info,
    )
    output = formatter.format(record)
    entry = json.loads(output)
    assert "exception" in entry
    assert "ValueError" in entry["exception"]


def test_setup_logging_registers_json_handler(tmp_path):
    import src.utils.logger as log_mod
    parent = logging.getLogger("job360")
    parent.handlers.clear()
    with mock.patch.object(log_mod, "LOGS_DIR", tmp_path):
        result = log_mod.setup_logging()
    try:
        formatter_types = [type(h.formatter).__name__ for h in result.handlers]
        assert "JSONFormatter" in formatter_types
    finally:
        for h in list(result.handlers):
            h.close()
            result.removeHandler(h)


def test_setup_logging_writes_jsonl(tmp_path):
    import src.utils.logger as log_mod
    parent = logging.getLogger("job360")
    parent.handlers.clear()
    with mock.patch.object(log_mod, "LOGS_DIR", tmp_path):
        lg = log_mod.setup_logging()
    try:
        lg.info("structured event")
        for h in lg.handlers:
            h.flush()
        jsonl_path = tmp_path / "job360.jsonl"
        assert jsonl_path.exists(), f"Expected {jsonl_path} to be created"
        lines = [l for l in jsonl_path.read_text().strip().split("\n") if l]
        entry = json.loads(lines[-1])
        assert entry["message"] == "structured event"
        assert entry["level"] == "INFO"
    finally:
        for h in list(lg.handlers):
            h.close()
            lg.removeHandler(h)


def test_jsonformatter_includes_run_uuid_when_set():
    from src.utils.logger import JSONFormatter, _run_uuid_var
    token = _run_uuid_var.set("abc123")
    try:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="job360.test", level=logging.INFO, pathname="", lineno=0,
            msg="correlated", args=(), exc_info=None,
        )
        entry = json.loads(formatter.format(record))
        assert entry["run_uuid"] == "abc123"
    finally:
        _run_uuid_var.reset(token)


# ---------------------------------------------------------------------------
# Task 4 — audit logger tests
# ---------------------------------------------------------------------------

def test_setup_audit_logger_creates_handler(tmp_path):
    import src.utils.logger as log_mod
    audit = logging.getLogger("job360.audit")
    audit.handlers.clear()
    with mock.patch.object(log_mod, "LOGS_DIR", tmp_path):
        result = log_mod.setup_audit_logger()
    try:
        assert result.name == "job360.audit"
        assert not result.propagate
        formatter_types = [type(h.formatter).__name__ for h in result.handlers]
        assert "JSONFormatter" in formatter_types
    finally:
        for h in list(result.handlers):
            h.close()
            result.removeHandler(h)


def test_setup_audit_logger_is_idempotent(tmp_path):
    import src.utils.logger as log_mod
    audit = logging.getLogger("job360.audit")
    audit.handlers.clear()
    with mock.patch.object(log_mod, "LOGS_DIR", tmp_path):
        log_mod.setup_audit_logger()
        log_mod.setup_audit_logger()  # second call must not add a second handler
    try:
        assert len(audit.handlers) == 1
    finally:
        for h in list(audit.handlers):
            h.close()
            audit.removeHandler(h)


def test_audit_logger_writes_json_event(tmp_path):
    import src.utils.logger as log_mod
    audit = logging.getLogger("job360.audit")
    audit.handlers.clear()
    with mock.patch.object(log_mod, "LOGS_DIR", tmp_path):
        lg = log_mod.setup_audit_logger()
    try:
        lg.info("auth", extra={"event": "register", "user_id": "abc", "status": "ok"})
        for h in lg.handlers:
            h.flush()
        audit_path = tmp_path / "audit.log"
        assert audit_path.exists()
        line = [l for l in audit_path.read_text().strip().split("\n") if l][-1]
        entry = json.loads(line)
        assert entry["message"] == "auth"
        assert entry["event"] == "register"
        assert entry["status"] == "ok"
    finally:
        for h in list(lg.handlers):
            h.close()
            lg.removeHandler(h)
