"""The pr-repair conflict-marker guard: it must catch conflicts and nothing else.

WHY THIS TEST EXISTS
--------------------
`pr-repair.yml` refuses to push if a merge-conflict marker survived the agent's
edit. Correct, and load-bearing: half a resolved merge must never ship.

The pattern it used was ``^(<{7}|={7}|>{7})`` — "starts with seven of these",
with no end anchor. Two committed report files open with a 100-character ``=``
rule, which starts with seven equals signs. So the guard fired on a CLEAN tree,
on every PR, for every repair, forever. The fixer was structurally unable to
ship anything for its entire life.

It stayed invisible for exactly one reason: nothing ever started that workflow.
Its only trigger was `workflow_dispatch`, so it ran when a human clicked it, and
nobody clicked it. The hour `finding-watch.yml` began dispatching it
automatically, three runs in a row died here — 32732226564, 32733843746,
32734907978 — all reporting conflict markers against trees that had none.

That is this repo's signature failure once more: a guard that cannot tell the
thing it guards from something harmless, going red (or green) forever, with
nobody in a position to notice.

WHAT THIS TEST PINS, AND WHY IT IS TWO ASSERTIONS AND NOT ONE
A guard has two ways to be useless and they pull in opposite directions:

  * it fires on things that are fine  -> nothing can ever ship (the bug above)
  * it misses the real thing          -> a broken merge ships

So the pattern is read OUT OF THE WORKFLOW ITSELF — never re-typed here, because
a test that re-implements the rule it checks proves only that the test agrees
with itself — and then run against both a real conflict and the real repository.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "pr-repair.yml"

# The line in the workflow that does the refusing. Matched loosely enough to
# survive reformatting, tightly enough that it cannot match the comment above it.
_GUARD_LINE = re.compile(r"^\s*if git grep -lE '(?P<pat>[^']+)'", re.M)


def _pattern_from_workflow() -> str:
    """The ERE the workflow actually runs, read from the workflow."""
    text = WORKFLOW.read_text(encoding="utf-8")
    m = _GUARD_LINE.search(text)
    assert m, (
        "could not find the conflict-marker guard in pr-repair.yml. If the step "
        "was renamed or restructured, update this test — do NOT delete it: the "
        "guard it protects has already been silently broken once."
    )
    return m.group("pat")


def _grep(pattern: str, target: Path, *, no_index: bool) -> int:
    """Run the workflow's own grep. Returns the number of matching files."""
    cmd = ["git", "-C", str(REPO), "grep", "-lE", pattern]
    if no_index:
        cmd.insert(3, "--no-index")
    cmd += ["--", str(target)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    # git grep exits 1 when nothing matched; that is an answer, not an error.
    if p.returncode not in (0, 1):
        pytest.skip(f"git grep unavailable here: {p.stderr.strip()[:120]}")
    return len([ln for ln in p.stdout.splitlines() if ln.strip()])


def test_the_guard_does_not_fire_on_a_clean_tree():
    """THE BUG. A clean checkout must produce zero matches.

    `backend/scripts/top20_report_*_2026-08-15.txt` are the two files that broke
    it — they open with a 100-character `=` rule. They are deliberately NOT
    excluded or deleted to make this pass: the fix is that the PATTERN knows what
    a marker looks like, not that the repository is kept free of text a sloppy
    pattern would trip on. Deleting the evidence would leave the next
    hundred-equals rule to break the fixer all over again.
    """
    pattern = _pattern_from_workflow()
    hits = _grep(pattern, REPO / "backend", no_index=False)
    hits += _grep(pattern, REPO / "frontend", no_index=False)
    assert hits == 0, (
        f"the conflict-marker guard matches {hits} file(s) in a clean tree, so "
        f"`pr-repair` will refuse to ship on EVERY pull request, always, with the "
        f"message 'conflict markers still present'. Pattern: {pattern}"
    )


def test_the_guard_still_catches_a_real_conflict(tmp_path):
    """THE OTHER FAILURE. Loosening the pattern until it stops firing is not a fix.

    A guard tuned only against false positives converges on matching nothing.
    This is the half that stops that, and it uses the real thing git writes.
    """
    pattern = _pattern_from_workflow()
    probe = tmp_path / "conflicted.py"
    probe.write_text(
        "<<<<<<< HEAD\nours = 1\n=======\ntheirs = 2\n>>>>>>> origin/main\n",
        encoding="utf-8",
    )
    p = subprocess.run(
        ["git", "grep", "--no-index", "-lE", pattern, "--", str(probe)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if p.returncode not in (0, 1):
        pytest.skip(f"git grep unavailable here: {p.stderr.strip()[:120]}")
    assert p.returncode == 0 and probe.name in p.stdout, (
        f"the conflict-marker guard no longer recognises a real git conflict. "
        f"A guard that cannot go red is decoration. Pattern: {pattern}"
    )


def test_the_guard_catches_diff3_style_too():
    """`merge.conflictStyle = diff3` adds a `|||||||` section.

    Not hypothetical — it is a per-developer git setting, so the same repository
    produces different marker sets depending on who resolved the merge. A pattern
    that knows only the three classic markers waves the base section through.
    """
    pattern = _pattern_from_workflow()
    assert re.search(r"\|\{7\}|\\\|{7}", pattern), (
        "the guard does not match the diff3 `|||||||` marker, so a merge resolved "
        "with `merge.conflictStyle = diff3` can ship its base section."
    )
