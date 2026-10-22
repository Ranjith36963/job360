"""drift-check fails a PR only for drift the PR itself adds (owner, 2026-10-22).

On 2026-10-21 one duplicate migration prefix on main turned 28 open PRs red on
`drift-check`. `doc_sync_check.py --baseline BASE_DIR` runs the base's own
checker on the base tree and fails only on findings the base does not have.
These tests pin the three answers that matter: inherited -> pass, new -> fail,
base not comparable -> fail as before (fail closed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import doc_sync_check as dsc  # noqa: E402

_HEADER = "| File | Line | Fact | Doc says | Code says |\n|------|------|------|----------|-----------|\n"


def _tree(root: Path, rows: list[str] | None = None, crash: str | None = None, rc: int | None = None) -> Path:
    """A fake checkout whose scripts/doc_sync_check.py prints a fixed report."""
    (root / "scripts").mkdir(parents=True)
    if crash is not None:
        body = f"import sys\nprint({('doc_sync_check crashed: ' + crash)!r}, file=sys.stderr)\nsys.exit(2)\n"
    elif rc is not None:
        body = f"import sys\nsys.exit({rc})\n"
    elif rows:
        report = "# Doc-sync drift report\n\n" + _HEADER + "".join(r + "\n" for r in rows)
        body = f"import sys\nsys.stdout.write({report!r})\nsys.exit(1)\n"
    else:
        body = "print('No drift. OK')\n"
    (root / "scripts" / "doc_sync_check.py").write_text(body, encoding="utf-8")
    return root


def test_findings_drop_the_line_number_and_the_header():
    out = _HEADER + "| README.md | 12 | dead-path | `a/b` | gone |\n| X.md | - | freshness | old | new |\n"
    assert dsc.report_findings(1, out, "", Path("/r")) == {
        "README.md | dead-path | `a/b` | gone",
        "X.md | freshness | old | new",
    }


def test_a_crash_is_its_message_and_anything_else_is_not_comparable():
    err = "Traceback...\ndoc_sync_check crashed: duplicate migration prefix 0067: a and b\n"
    assert dsc.report_findings(2, "", err, Path("/r")) == {
        "doc_sync_check crashed: duplicate migration prefix 0067: a and b"}
    assert dsc.report_findings(2, "", "python: can't open file", Path("/r")) is None
    assert dsc.report_findings(1, "no table here", "", Path("/r")) is None
    assert dsc.report_findings(3, "", "", Path("/r")) is None
    assert dsc.report_findings(0, "", "", Path("/r")) == set()


@pytest.mark.parametrize(
    ("base_rows", "head_rows", "want"),
    [
        # main's drift, carried by the PR (the line moved too): inherited, green
        (["| A.md | 3 | dead-link | x | gone |"], ["| A.md | 9 | dead-link | x | gone |"], 0),
        # drift only the PR has: red
        ([], ["| A.md | 3 | dead-link | x | gone |"], 1),
        # main's drift plus the PR's own: red for the PR's
        (["| A.md | 3 | dead-link | x | gone |"],
         ["| A.md | 3 | dead-link | x | gone |", "| B.md | 1 | dead-link | y | gone |"], 1),
    ],
)
def test_only_the_prs_own_drift_fails(tmp_path, monkeypatch, capsys, base_rows, head_rows, want):
    base = _tree(tmp_path / "base", base_rows)
    monkeypatch.setattr(dsc, "ROOT", _tree(tmp_path / "head", head_rows))
    assert dsc.baseline_main(base) == want
    out = capsys.readouterr().out
    if base_rows:
        assert "inherited from main (not this PR's): 1" in out
        assert "::warning title=drift inherited from main" in out
    if want:
        assert "::error title=drift added by this PR::B.md" in out or not base_rows


def test_the_same_crash_on_base_and_head_is_inherited(tmp_path, monkeypatch):
    base = _tree(tmp_path / "base", crash="duplicate migration prefix 0067: a and b")
    monkeypatch.setattr(dsc, "ROOT", _tree(tmp_path / "head", crash="duplicate migration prefix 0067: a and b"))
    assert dsc.baseline_main(base) == 0


@pytest.mark.parametrize("broken", [{"rc": 3}, {"crash": None}])
def test_a_base_with_nothing_comparable_fails_closed(tmp_path, monkeypatch, capsys, broken):
    base = tmp_path / "base"
    if broken.get("rc"):
        _tree(base, rc=3)
    else:
        base.mkdir()  # no checker at all
    monkeypatch.setattr(dsc, "ROOT", _tree(tmp_path / "head", ["| A.md | 3 | dead-link | x | gone |"]))
    assert dsc.baseline_main(base) == 1
    assert "drift baseline unavailable" in capsys.readouterr().out
