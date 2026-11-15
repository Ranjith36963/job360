"""lock_deps.py upgrades packages that pip-audit flags, with no hand-kept list (2026-11-15).

Twice in one week a CVE (opentelemetry-api, then dnspython) turned the required
`pip-audit (backend deps)` check red until someone hand-added to COOLDOWN_EXEMPT.
No subprocess and no network here: pip-audit's JSON is a fixture.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "lock_deps.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lock_deps_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ld = _load()


def _audit(*deps: dict) -> str:
    return json.dumps({"dependencies": list(deps), "fixes": []})


FIXED = {"name": "dnspython", "version": "2.8.0",
         "vulns": [{"id": "CVE-2026-1", "fix_versions": ["2.8.1"], "aliases": []}]}
UNFIXED = {"name": "leftpad", "version": "1.0",
           "vulns": [{"id": "CVE-2026-2", "fix_versions": [], "aliases": []}]}
CLEAN = {"name": "idna", "version": "3.10", "vulns": []}


def test_vuln_with_fix_version_becomes_upgrade(capsys):
    assert ld.packages_to_upgrade(ld.parse_audit(_audit(FIXED, CLEAN))) == {"dnspython"}
    out = capsys.readouterr().out
    assert "dnspython 2.8.0: CVE-2026-1 -> upgrading (fix 2.8.1)" in out


def test_vuln_without_fix_warns_and_does_not_upgrade(capsys):
    assert ld.packages_to_upgrade(ld.parse_audit(_audit(UNFIXED))) == set()
    out = capsys.readouterr().out
    assert "WARNING" in out and "leftpad" in out and "CVE-2026-2" in out


def test_missing_pip_audit_is_empty_set_not_crash(monkeypatch, capsys):
    def boom(*a, **k):
        raise FileNotFoundError("pip-audit")

    monkeypatch.setattr(ld.subprocess, "run", boom)
    assert ld.cve_upgrades() == set()
    assert "pip-audit" in capsys.readouterr().out


def test_network_error_or_garbage_is_empty_set(monkeypatch):
    done = subprocess.CompletedProcess([], 1, stdout="not json", stderr="Connection refused")
    monkeypatch.setattr(ld.subprocess, "run", lambda *a, **k: done)
    assert ld.cve_upgrades() == set()


def test_compile_command_upgrades_flagged_package():
    cmd = ld.build_compile_cmd("uv", "requirements.lock.txt", None, False, "2026-11-08T00:00:00Z",
                               "2026-11-15T00:00:00Z", {"dnspython"})
    assert "dnspython=2026-11-15T00:00:00Z" in cmd
    assert cmd[cmd.index("dnspython=2026-11-15T00:00:00Z") - 1] == "--exclude-newer-package"
    assert cmd[cmd.index("dnspython") - 1] == "--upgrade-package"


def test_post_check_fails_when_vulns_remain(monkeypatch, capsys):
    monkeypatch.setattr(ld, "run_pip_audit", lambda: _audit(FIXED))
    assert ld.post_check() != 0
    assert "dnspython" in capsys.readouterr().out


def test_post_check_passes_when_clean(monkeypatch):
    monkeypatch.setattr(ld, "run_pip_audit", lambda: _audit(CLEAN))
    assert ld.post_check() == 0


def test_pip_audit_command_matches_ci():
    cmd = ld.pip_audit_cmd()
    for flag in ("--require-hashes", "--disable-pip", "-f", "json"):
        assert flag in cmd
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-r"] == [
        "requirements.lock.txt", "requirements-browser.lock.txt", "requirements-dev.lock.txt"]
