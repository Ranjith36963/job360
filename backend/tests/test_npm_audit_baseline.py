"""The PR npm-audit gate goes red only for advisories the PR brings in.

scripts/npm_audit_baseline.py compares the PR's `npm audit --json` report with
the base commit's. Inherited advisories warn; new ones fail; a missing base
report falls back to failing on every gated advisory (fail closed).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import npm_audit_baseline as gate  # noqa: E402

OLD = ("next", "https://github.com/advisories/GHSA-old", "high")
NEW = ("braces", "https://github.com/advisories/GHSA-new", "critical")
LOW = ("hono", "https://github.com/advisories/GHSA-low", "moderate")


def _write(tmp_path: Path, name: str, report: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(report), encoding="utf-8")
    return str(path)


def test_inherited_only_passes_and_warns(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    head = _write(tmp_path, "head.json", gate._report(OLD, LOW))
    base = _write(tmp_path, "base.json", gate._report(OLD))
    assert gate.main(["--head", head, "--base", base]) == 0
    out = capsys.readouterr().out
    assert "::warning::inherited from main (not this PR's): next" in out
    assert "inherited from main (not this PR's)" in summary.read_text(encoding="utf-8")


def test_new_advisory_fails(tmp_path, capsys):
    head = _write(tmp_path, "head.json", gate._report(OLD, NEW))
    base = _write(tmp_path, "base.json", gate._report(OLD))
    assert gate.main(["--head", head, "--base", base]) == 1
    assert "::error::introduced by this PR: braces" in capsys.readouterr().out


def test_new_advisory_below_gate_passes(tmp_path):
    head = _write(tmp_path, "head.json", gate._report(LOW))
    base = _write(tmp_path, "base.json", gate._report())
    assert gate.main(["--head", head, "--base", base]) == 0


def test_missing_or_errored_base_fails_closed(tmp_path):
    head = _write(tmp_path, "head.json", gate._report(OLD))
    errored = _write(tmp_path, "base.json", {"message": "audit endpoint", "error": {}})
    assert gate.main(["--head", head]) == 1
    assert gate.main(["--head", head, "--base", errored]) == 1
    assert gate.main(["--head", head, "--base", str(tmp_path / "absent.json")]) == 1


def test_unusable_head_fails_closed(tmp_path):
    bad = tmp_path / "head.json"
    bad.write_text("not json", encoding="utf-8")
    base = _write(tmp_path, "base.json", gate._report())
    assert gate.main(["--head", str(bad), "--base", base]) == 1


def test_unknown_severity_counts_as_gated():
    report = gate._report(("x", "https://github.com/advisories/GHSA-x", "weird"))
    assert gate.compare(report, gate._report(), "high")[0] == 1


def test_drill_passes_and_its_negative_control_goes_red():
    assert gate.drill() == 0
    assert gate.drill(frozenset({"NEW"})) == 1
