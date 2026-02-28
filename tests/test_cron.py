import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CRON_SCRIPT = PROJECT_ROOT / "cron_setup.sh"

needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None or sys.platform == "win32",
    reason="bash not available (Windows)",
)


def _read_script() -> str:
    return CRON_SCRIPT.read_text()


@needs_bash
def test_cron_script_syntax_valid():
    """bash -n validates the script has no syntax errors."""
    result = subprocess.run(
        ["bash", "-n", str(CRON_SCRIPT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr}"


@needs_bash
def test_cron_script_fails_without_venv():
    """Running in a temp dir with no venv should fail with an error message."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy cron_setup.sh into temp dir (so PROJECT_DIR resolves there)
        tmp_script = Path(tmpdir) / "cron_setup.sh"
        tmp_script.write_text(_read_script())
        result = subprocess.run(
            ["bash", str(tmp_script)],
            capture_output=True, text=True,
            cwd=tmpdir,
        )
        assert result.returncode != 0, "Expected non-zero exit when venv missing"
        assert "virtual environment not found" in result.stderr.lower() or \
               "virtual environment not found" in result.stdout.lower(), \
               f"Expected venv error message, got: {result.stdout}{result.stderr}"


def test_cron_contains_uk_timezone():
    """Cron script must use Europe/London timezone."""
    content = _read_script()
    assert "Europe/London" in content


def test_cron_schedule_6am_6pm():
    """Cron script must schedule runs at 6AM and 6PM."""
    content = _read_script()
    assert "0 6 * * *" in content, "Missing 6AM schedule"
    assert "0 18 * * *" in content, "Missing 6PM schedule"


def test_cron_uses_module_invocation():
    """Cron script should invoke python -m src.main for correct imports."""
    content = _read_script()
    assert "python -m src.main" in content or "$PYTHON -m src.main" in content
