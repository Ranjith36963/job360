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
    formatter_types = [type(h.formatter).__name__ for h in result.handlers]
    assert "JSONFormatter" in formatter_types


def test_setup_logging_writes_jsonl(tmp_path):
    import src.utils.logger as log_mod
    parent = logging.getLogger("job360")
    parent.handlers.clear()
    with mock.patch.object(log_mod, "LOGS_DIR", tmp_path):
        lg = log_mod.setup_logging()
    lg.info("structured event")
    for h in lg.handlers:
        h.flush()
    jsonl_path = tmp_path / "job360.jsonl"
    assert jsonl_path.exists(), f"Expected {jsonl_path} to be created"
    lines = [l for l in jsonl_path.read_text().strip().split("\n") if l]
    entry = json.loads(lines[-1])
    assert entry["message"] == "structured event"
    assert entry["level"] == "INFO"
