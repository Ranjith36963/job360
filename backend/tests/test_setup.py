import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# PROJECT_ROOT is the repo root, not `backend/`. Phase-1 refactor moved
# tests/ into backend/ but setup.sh + requirements*.txt still live at
# the repo root — hence parent.parent.parent.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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


# test_requirements_prod_no_test_deps and test_requirements_dev_includes_prod
# were DELETED in Batch 3.5.4 — they tested a requirements.txt / requirements-dev.txt
# layout that was removed in phase-1 refactor (commit 0d3ef72) when
# dependencies moved into backend/pyproject.toml under [project.dependencies]
# and [project.optional-dependencies].dev. The invariants they encoded
# ("prod deps don't include test deps") have no direct pyproject.toml
# equivalent — dev extras are additive via `pip install -e .[dev]`, not
# via an "includes" relationship. Tests that don't match the current
# packaging reality shouldn't linger as red ink on the baseline.
