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


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run git grep and INSIST on an answer.

    NOT `pytest.skip` ON A BAD EXIT CODE. A skipped guard-test is green and
    proves nothing, which is the same disease as the guard this file exists to
    fix — and it bit here immediately: the first version passed absolute
    `tmp_path` paths, `git grep --no-index` refused them ("is outside repository
    at ..."), the helper skipped, and two of three cases reported green while
    executing no assertion at all.

    git grep's contract is: 0 = matched, 1 = no match, anything else = it could
    not do the job. Only the first two are answers.
    """
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    assert p.returncode in (0, 1), (
        f"`git grep` could not answer (exit {p.returncode}): "
        f"{(p.stderr or p.stdout).strip()[:200]}\ncmd: {' '.join(cmd)}"
    )
    return p


def _grep_repo(pattern: str, target: Path) -> int:
    """Matching files under `target` in the real repository."""
    p = _run(["git", "-C", str(REPO), "grep", "-lE", pattern, "--", str(target)])
    return len([ln for ln in p.stdout.splitlines() if ln.strip()])


def _grep_dir(pattern: str, directory: Path, only: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the guard over a scratch directory, FROM INSIDE IT.

    `--no-index` still refuses a path that lives outside the enclosing
    repository, so the probe cannot be addressed absolutely from the repo root —
    it has to be the working directory. `only` narrows to one file when the
    directory holds several probes.
    """
    return _run(["git", "grep", "--no-index", "-lE", pattern, "--", only or "."],
                cwd=directory)


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
    hits = _grep_repo(pattern, REPO / "backend")
    hits += _grep_repo(pattern, REPO / "frontend")
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
    p = _grep_dir(pattern, probe.parent)
    assert p.returncode == 0 and probe.name in p.stdout, (
        f"the conflict-marker guard no longer recognises a real git conflict. "
        f"A guard that cannot go red is decoration. Pattern: {pattern}"
    )


def test_the_guard_catches_diff3_style_too(tmp_path):
    """`merge.conflictStyle = diff3` adds a `|||||||` section.

    Not hypothetical — it is a per-developer git setting, so the same repository
    produces different marker sets depending on who resolved the merge. A pattern
    that knows only the three classic markers waves the base section through.

    RUN, DO NOT READ. The first version of this case asserted on the SOURCE TEXT
    of the pattern (`re.search(r"\\|\\{7\\}", pattern)`), which is the very
    mistake this file's own docstring warns about one test earlier: a pattern
    like `\\|{7}x` satisfies that assertion and matches no real marker at all.
    Checking a regex by grepping the regex proves the characters are present,
    never that they do anything. So this executes the guard, exactly as the
    workflow does, against text git really writes in diff3 mode.
    (CodeRabbit, PR #390.)
    """
    pattern = _pattern_from_workflow()
    probe = tmp_path / "diff3.py"
    probe.write_text(
        "<<<<<<< HEAD\nours = 1\n"
        "||||||| merged common ancestors\nbase = 0\n"
        "=======\ntheirs = 2\n>>>>>>> origin/main\n",
        encoding="utf-8",
    )
    p = _grep_dir(pattern, probe.parent)
    assert p.returncode == 0 and probe.name in p.stdout, (
        f"the guard does not match a diff3-style conflict, so a merge resolved "
        f"with `merge.conflictStyle = diff3` can ship its base section. "
        f"Pattern: {pattern}"
    )

    # ...AND THE `|||||||` LINE SPECIFICALLY, not merely the `<<<<<<<` above it.
    # Without this the case passes on a pattern that knows nothing about diff3,
    # because every diff3 conflict also carries the three classic markers — the
    # test would be green while testing something else entirely.
    base_only = tmp_path / "base_only.py"
    base_only.write_text("||||||| merged common ancestors\nbase = 0\n", encoding="utf-8")
    p2 = _grep_dir(pattern, base_only.parent, only=base_only.name)
    assert p2.returncode == 0, (
        f"the guard matched the diff3 sample only through its `<<<<<<<` line — it "
        f"does not recognise `|||||||` at all. Pattern: {pattern}"
    )
