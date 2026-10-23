"""Two Chain-wires steps fail a PR only for findings the PR adds (owner, 2026-10-22).

`ruleset_gate.py --check --baseline DIR` and `drill_registry.py --run-drills
--baseline DIR` run the base commit's OWN checker on the base tree and fail only
on findings the base does not have. Pinned here: inherited -> pass, the PR's
own -> fail, a new reason inside a drill main already fails -> fail, base not
comparable -> fail (fail closed), no --baseline (push to main) -> still red.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import drill_registry as dr  # noqa: E402
import pr_baseline as pb  # noqa: E402
import ruleset_gate as rg  # noqa: E402

APP = 15368


@pytest.fixture(autouse=True)
def _no_summary(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


# ─── ruleset_gate ─────────────────────────────────────────────────────────────


def _snapshot(root: Path, *, age_days: int, required: tuple[str, ...] = ("ctx",), checker: bool = True) -> Path:
    """A recorded world where `ctx` runs on every head; anything else required is dead."""
    heads = [{"sha": f"{i:040x}", "label": f"PR #{i}", "is_pr_head": True, "number": i, "author": "h",
              "runs": [{"name": "ctx", "app_id": APP, "conclusion": "success"}]} for i in range(1, 9)]
    raw = {"id": 1, "name": "gate", "enforcement": "active", "bypass_actors": [],
           "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
           "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [
               {"context": c, "integration_id": APP} for c in required]}}]}
    fetched = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age_days)
    snap = root / ".github" / "rulesets" / "main-gate-snapshot.json"
    snap.parent.mkdir(parents=True)
    snap.write_text(json.dumps({"fetched_at": fetched.isoformat(), "ruleset": raw, "pr_heads": heads,
                                "main_commits": [], "open_prs": []}), encoding="utf-8")
    if checker:
        (root / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "ruleset_gate.py", root / "scripts" / "ruleset_gate.py")
    return snap


def _gate(monkeypatch, head: Path, snap: Path, *extra: str) -> int:
    monkeypatch.setattr(rg, "ROOT", head)
    monkeypatch.setattr(rg, "SNAPSHOT", snap)
    real_load = rg.load_snapshot  # its default path was bound at import
    monkeypatch.setattr(rg, "load_snapshot", lambda path=snap: real_load(path))
    return rg.main(["--check", "--scope", "ruleset", *extra])


@pytest.mark.parametrize(
    ("base_age", "head_age", "head_required", "want"),
    [
        (20, 20, ("ctx",), 0),           # main's stale snapshot, carried untouched: inherited
        (1, 20, ("ctx",), 1),            # the PR made the evidence stale: its own
        (20, 20, ("ctx", "ghost"), 1),   # main's stale + a dead context the PR added: red
        (1, 1, ("ctx",), 0),             # clean: the base never runs
    ],
)
def test_ruleset_gate_fails_only_on_the_prs_own_wedges(tmp_path, monkeypatch, capsys, base_age, head_age,
                                                       head_required, want):
    _snapshot(tmp_path / "base", age_days=base_age)
    snap = _snapshot(tmp_path / "head", age_days=head_age, required=head_required, checker=False)
    assert _gate(monkeypatch, tmp_path / "head", snap, "--baseline", str(tmp_path / "base")) == want
    out = capsys.readouterr().out
    if base_age > 14 and head_age > 14:
        assert "inherited from main (not this PR's): 1" in out
        assert "::warning title=ruleset gate: inherited from main (not this PR's)::[STALE_SNAPSHOT]" in out
    if want:
        assert "::error title=ruleset gate: added by this PR::" in out


def test_ruleset_gate_fails_closed_when_the_base_cannot_be_judged(tmp_path, monkeypatch, capsys):
    _snapshot(tmp_path / "base", age_days=20, checker=False)  # no checker to run
    snap = _snapshot(tmp_path / "head", age_days=20, checker=False)
    assert _gate(monkeypatch, tmp_path / "head", snap, "--baseline", str(tmp_path / "base")) == 1
    assert "ruleset gate: baseline unavailable" in capsys.readouterr().out


def test_ruleset_gate_without_baseline_is_still_red(tmp_path, monkeypatch):
    """Push to main passes no --baseline: main's own stale evidence is still a red build."""
    snap = _snapshot(tmp_path / "main", age_days=20, checker=False)
    assert _gate(monkeypatch, tmp_path / "main", snap) == 1


# ─── drill_registry ───────────────────────────────────────────────────────────

_GUARD = """import sys
from pathlib import Path
if "--blind" in sys.argv:
    sys.exit(1)
broken = Path(__file__).resolve().parent.parent.joinpath("broken.txt").read_text().split()
for why in broken:
    print("guard did not fire:", why)
sys.exit(1 if broken else 0)
"""

# The base runs the REAL drill_registry code, with its registry pointed at the planted guard.
_BASE_REGISTRY = """import sys
from pathlib import Path
sys.path.insert(0, {scripts!r})
import drill_registry as dr
dr.ROOT = Path(__file__).resolve().parent.parent
dr.REGISTRY = {{"scripts/g.py": dr.Guard(status="drilled", drill=[sys.executable, "scripts/g.py"],
                                         negative=[sys.executable, "scripts/g.py", "--blind"])}}
sys.exit(dr.main())
"""


def _drill_tree(root: Path, broken: list[str], checker: bool = True) -> Path:
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  j:\n    steps:\n      - run: python scripts/g.py\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "g.py").write_text(_GUARD, encoding="utf-8")
    (root / "broken.txt").write_text(" ".join(broken), encoding="utf-8")
    if checker:
        (root / "scripts" / "drill_registry.py").write_text(
            _BASE_REGISTRY.format(scripts=str(REPO / "scripts")), encoding="utf-8")
    return root


def _drills(monkeypatch, head: Path, *extra: str) -> int:
    monkeypatch.setattr(dr, "ROOT", head)
    monkeypatch.setattr(dr, "REGISTRY", {"scripts/g.py": dr.Guard(
        status="drilled", drill=[sys.executable, "scripts/g.py"],
        negative=[sys.executable, "scripts/g.py", "--blind"])})
    return dr.main(["--run-drills", *extra])


@pytest.mark.parametrize(
    ("base_broken", "head_broken", "want"),
    [
        (["main"], ["main"], 0),           # main's failing drill, carried: inherited
        ([], ["pr"], 1),                   # only the PR breaks it: red
        (["main"], ["main", "pr"], 1),     # a new reason inside the drill main fails: red
        ([], [], 0),                       # clean
    ],
)
def test_drill_registry_fails_only_on_the_prs_own_failures(tmp_path, monkeypatch, capsys, base_broken,
                                                           head_broken, want):
    _drill_tree(tmp_path / "base", base_broken)
    head = _drill_tree(tmp_path / "head", head_broken, checker=False)
    assert _drills(monkeypatch, head, "--baseline", str(tmp_path / "base")) == want
    out = capsys.readouterr().out
    if base_broken and base_broken == head_broken:
        assert "inherited from main (not this PR's): 1" in out
    if want:
        assert "::error title=drill registry: added by this PR::DRILL FAILED: scripts/g.py" in out


def test_drill_registry_fails_closed_when_the_base_cannot_be_judged(tmp_path, monkeypatch, capsys):
    _drill_tree(tmp_path / "base", ["main"], checker=False)
    head = _drill_tree(tmp_path / "head", ["main"], checker=False)
    assert _drills(monkeypatch, head, "--baseline", str(tmp_path / "base")) == 1
    assert "drill registry: baseline unavailable" in capsys.readouterr().out


def test_drill_registry_without_baseline_is_still_red(tmp_path, monkeypatch):
    assert _drills(monkeypatch, _drill_tree(tmp_path / "main", ["main"], checker=False)) == 1


def test_the_verdict_reaches_the_job_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _drill_tree(tmp_path / "base", ["main"])
    head = _drill_tree(tmp_path / "head", ["main"], checker=False)
    assert _drills(monkeypatch, head, "--baseline", str(tmp_path / "base")) == 0
    assert "inherited from main (not this PR's)" in summary.read_text(encoding="utf-8")


def test_comparable_masks_line_numbers_temp_dirs_and_layout():
    assert pb.comparable("  x.yml:40 line 12 in /tmp/tmpab12/f.py\n      y") == "x.yml:N line N in <tmp> y"
