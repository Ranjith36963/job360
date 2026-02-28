import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETUP_SCRIPT = PROJECT_ROOT / "setup.sh"

needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None or sys.platform == "win32",
    reason="bash not available (Windows)",
)


@needs_bash
def test_setup_script_syntax_valid():
    """bash -n validates the script has no syntax errors."""
    result = subprocess.run(
        ["bash", "-n", str(SETUP_SCRIPT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr}"


def test_setup_checks_python_version():
    """setup.sh should check for Python 3.9+."""
    content = SETUP_SCRIPT.read_text()
    assert "3.9" in content or "sys.version_info" in content


def test_setup_creates_data_dirs():
    """setup.sh should create data directories."""
    content = SETUP_SCRIPT.read_text()
    assert "data/exports" in content
    assert "data/reports" in content
    assert "data/logs" in content


def test_setup_validates_env_example():
    """setup.sh should check .env.example exists before copying."""
    content = SETUP_SCRIPT.read_text()
    assert ".env.example" in content


def test_requirements_prod_no_test_deps():
    """requirements.txt (prod) should NOT contain test dependencies."""
    content = (PROJECT_ROOT / "requirements.txt").read_text()
    assert "pytest" not in content
    assert "aioresponses" not in content


def test_requirements_dev_includes_prod():
    """requirements-dev.txt should include prod requirements."""
    content = (PROJECT_ROOT / "requirements-dev.txt").read_text()
    assert "-r requirements.txt" in content
    assert "pytest" in content
