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


# ── THE OTHER HALF OF THE SAME CAGE ────────────────────────────────────────
# The step above the marker check deletes the workflow's own scratch files
# before the cage inspects the tree. It has to: the cage asks
# `git status --porcelain` and refuses anything outside backend/|frontend/, so
# a file THE WORKFLOW wrote in the repo root is indistinguishable from the AGENT
# escaping its cage.
#
# That list is a coupling, and it broke the first time it was tested by reality.
# `threads.json` / `open-threads.json` arrived with the review-thread reader and
# nobody added them to the `rm -f`, so run 32746446218 — the very first repair
# that got past the conflict-marker check — died with
#     agent edited outside the cage: open-threads.json threads.json
# against an agent that had touched neither. A refusal naming the wrong culprit
# is worse than no refusal: it sends whoever reads it looking for a cage escape
# that never happened.

# ANY redirect, anywhere on the line — NOT just a line-initial one.
# The first version anchored with `^\s*(?:\}\s*)?>` and was therefore
# DECORATION: both files that caused the outage are redirected at the END of a
# long command line, so the guard matched neither and passed the mutation test
# with the cleanup deliberately broken. Caught by running that mutation instead
# of trusting the green.
# `(?<![>$\w])` keeps `>>` appends and `2>` out; a redirect to a shell variable
# (`>> "$GITHUB_OUTPUT"`) cannot match the filename character class anyway.
_ROOT_REDIRECT = re.compile(r"(?<![>$\w])>\s*([A-Za-z0-9._-]+\.(?:json|md|txt))")
# THE CONTINUATION ALTERNATIVE COMES FIRST, AND THAT ORDER IS THE WHOLE RULE.
# Written the obvious way — `(?:[^\n]|\\\n)*` — the `[^\n]` branch consumes the
# backslash, the newline then matches neither branch, and the match STOPS at the
# end of the first physical line. So a cleanup written across two lines was read
# as only its first half, and the test reported the second half's files as
# "never deleted" while the workflow deleted them correctly. Trying the
# backslash-newline pair BEFORE the any-character branch is what makes the
# regex see a logical line instead of a physical one.
_RM_LINE = re.compile(r"rm -f((?:\\\n|[^\n])*)", re.M)


def test_every_scratch_file_the_workflow_writes_is_cleaned_before_the_cage():
    """Whatever the workflow redirects into the repo root must be in the `rm -f`.

    Derived from the workflow, not from a list typed here — a hand-copied list
    would be a third place to forget, and forgetting is the entire failure mode.
    """
    text = WORKFLOW.read_text(encoding="utf-8")

    rm = _RM_LINE.search(text)
    assert rm, "pr-repair.yml no longer has an `rm -f` scratch cleanup before the path cage"
    # `.split()` already breaks on the newline; the lone backslash left behind by
    # a line continuation becomes a harmless token that no filename can equal.
    cleaned = set(rm.group(1).split())

    # ONLY WHAT IS WRITTEN *BEFORE* THE CAGE RUNS. Order is the whole point:
    # `checker-input.md` and `checker-verdict.md` are written by the blind
    # checker, which runs AFTER the cage has already inspected the tree, so they
    # cannot possibly be mistaken for an agent edit. Demanding they be cleaned
    # would be a guard firing on something that is fine — the same failure as the
    # conflict-marker pattern two tests up, committed while fixing it. Found by
    # this test's own first run, which reported `checker-input.md`.
    before_cage = text[: rm.start()]
    written = set(_ROOT_REDIRECT.findall(before_cage))
    # `> file` inside a `working-directory: backend` step lands in backend/,
    # which the cage allows; those are written with `../`.
    written = {w for w in written if not w.startswith("..")}

    missed = sorted(written - cleaned)
    assert not missed, (
        f"pr-repair.yml writes {missed} into the repo root but does not delete them "
        f"before the path cage runs. The cage will report them as 'agent edited "
        f"outside the cage' and refuse a repair the agent did nothing wrong in. "
        f"Add them to the `rm -f` line in the same commit that adds the file."
    )
