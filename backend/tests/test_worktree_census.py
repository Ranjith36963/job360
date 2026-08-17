"""The broom must never sweep up work the owner has not saved.

WHY THIS TEST EXISTS
--------------------
Measured on this laptop 2026-08-17: 62 worktrees, 209 local branches, doubling
roughly every two weeks. The obvious cleanup rule -- "the branch is merged into
main, therefore the folder is junk" -- is WRONG here, and wrong in the most
expensive direction. Of 20 worktrees holding uncommitted work, 14 sit on
branches already merged into main. One holds 19 staged files. That rule deletes
14 folders of real, unsaved work.

The two questions are not the same question:

    "is the branch merged?"  is about GIT HISTORY.
    "does the folder hold work?"  is about the OWNER'S UNSAVED FILES.

Conflating them is the same failure that shipped ten dead guards here: code that
looks correct while answering a DIFFERENT QUESTION than the one that matters.

So the invariant these tests pin, and the reason the content checks run FIRST in
the classifier rather than as a tie-breaker:

    UNCOMMITTED CONTENT, OR UNTRACKED-BUT-VALUABLE CONTENT, ALWAYS BEATS
    MERGED-NESS. Merged-ness can only ever be a NECESSARY condition for SAFE,
    never a sufficient one.

Two of these cases are invisible to `git status` and would be missed by any
classifier that trusted it alone -- which is why they are tested explicitly:

  * a live `.env` (gitignore:9) -- two worktrees on this machine hold real
    secrets;
  * runtime data under `backend/data/` (matched by the bare `data/` rule at
    gitignore:24) -- 45 of 58 worktrees held some, and `git status --porcelain`
    prints NOTHING for any of it.

The fixtures below are a real git repository in a temp dir with real worktrees
in each interesting state, not mocks. A classifier that passes against mocked
`git status` output proves nothing about the machine that has the problem.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import worktree_census as wc  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# fixtures: a real repo, real worktrees, one per interesting state
# ─────────────────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    """Run git. encoding+errors are mandatory: text=True alone decodes with the
    Windows locale and dies on an em-dash (scripts/encoding_guard.py)."""
    out = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
        encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr}")
    return out.stdout


def _commit(repo: Path, name: str, body: str = "x") -> None:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")


@pytest.fixture(scope="module")
def sprawl(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repo with one worktree per state the classifier must tell apart."""
    root = tmp_path_factory.mktemp("sprawl")
    origin = root / "origin.git"
    repo = root / "repo"

    _git(root, "init", "--bare", "-b", "main", str(origin))
    _git(root, "clone", str(origin), str(repo))
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    # `data/` and `.env` are invisible to git status here exactly as they are in
    # the real repo -- that is the point of two of the cases below.
    (repo / ".gitignore").write_text("data/\n.env\n", encoding="utf-8")
    _commit(repo, ".gitignore-seed")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignores")
    _git(repo, "push", "-u", "origin", "main")

    wt = root / "wt"
    wt.mkdir()

    def merged_worktree(name: str) -> Path:
        """A worktree whose branch IS an ancestor of origin/main."""
        p = wt / name
        _git(repo, "worktree", "add", "-b", name, str(p))
        _commit(p, f"{name}.txt")
        _git(repo, "merge", "--no-ff", "-m", f"merge {name}", name)
        _git(repo, "push", "origin", "main")
        return p

    def unmerged_worktree(name: str) -> Path:
        p = wt / name
        _git(repo, "worktree", "add", "-b", name, str(p))
        _commit(p, f"{name}.txt")
        return p

    # 1. clean + merged  -> the ONLY shape that may be SAFE (negative control)
    merged_worktree("clean-merged")

    # 2. dirty (tracked file modified) + merged -> KEEP. THE RULE THAT MATTERS.
    p = merged_worktree("dirty-merged")
    (p / "dirty-merged.txt").write_text("edited, never committed", encoding="utf-8")

    # 3. untracked new file + merged -> KEEP (git status sees this one)
    p = merged_worktree("untracked-merged")
    (p / "notes.md").write_text("scratch", encoding="utf-8")

    # 4. staged-but-uncommitted + merged -> KEEP (one real worktree has 19 of these)
    p = merged_worktree("staged-merged")
    (p / "staged.txt").write_text("staged", encoding="utf-8")
    _git(p, "add", "staged.txt")

    # 5. clean, branch NOT merged -> KEEP
    unmerged_worktree("clean-unmerged")

    # 6. clean + merged but holds a live .env -> KEEP, and git status says NOTHING
    p = merged_worktree("holds-env")
    (p / ".env").write_text("API_KEY=redacted\n", encoding="utf-8")

    # 7. clean + merged but holds runtime data under backend/data/ -> KEEP,
    #    and git status says NOTHING (bare `data/` rule)
    p = merged_worktree("holds-data")
    (p / "backend" / "data").mkdir(parents=True)
    (p / "backend" / "data" / "jobs.db").write_text("runtime", encoding="utf-8")

    # 8. locked -> ASK (another session may own it; never SAFE)
    p = merged_worktree("locked-merged")
    _git(repo, "worktree", "lock", str(p))

    # 9. corrupt HEAD: 41 zero bytes, exactly the crash shape found on
    #    .claude/worktrees/wf_888d2a05-67d-3 (verified byte-for-byte with od -c).
    p = merged_worktree("corrupt-head")
    head = repo / ".git" / "worktrees" / "corrupt-head" / "HEAD"
    head.write_bytes(b"\0" * 41)

    return repo


def _row(rows: list, name: str):
    hits = [r for r in rows if Path(r.path).name == name]
    assert hits, f"classifier never reported a row for {name}"
    return hits[0]


@pytest.fixture(scope="module")
def rows(sprawl: Path) -> list:
    return wc.census(sprawl).worktrees


# ─────────────────────────────────────────────────────────────────────────────
# the invariant
# ─────────────────────────────────────────────────────────────────────────────

def test_clean_merged_worktree_is_safe(rows: list) -> None:
    """NEGATIVE CONTROL. A checker that calls everything unsafe checks nothing."""
    assert _row(rows, "clean-merged").verdict == "SAFE"


@pytest.mark.parametrize(
    "name",
    ["dirty-merged", "untracked-merged", "staged-merged"],
)
def test_uncommitted_content_beats_merged(rows: list, name: str) -> None:
    """The 14-folder mistake. Merged is TRUE for all three and must not win."""
    r = _row(rows, name)
    assert r.verdict == "KEEP", f"{name} classified {r.verdict}: {r.reasons}"


def test_unmerged_is_never_safe(rows: list) -> None:
    assert _row(rows, "clean-unmerged").verdict == "KEEP"


def test_live_env_beats_merged_even_though_git_status_is_silent(
    sprawl: Path, rows: list,
) -> None:
    """git status prints nothing for a gitignored .env. The classifier must not
    inherit that blindness -- two worktrees on this machine hold real secrets."""
    p = sprawl.parent / "wt" / "holds-env"
    assert _git(p, "status", "--porcelain").strip() == "", (
        "fixture is wrong: git can see the .env, so this proves nothing"
    )
    r = _row(rows, "holds-env")
    assert r.verdict == "KEEP", f"holds-env classified {r.verdict}: {r.reasons}"


def test_runtime_data_beats_merged_even_though_git_status_is_silent(
    sprawl: Path, rows: list,
) -> None:
    """45 of 58 worktrees held backend/data/. git status shows none of it."""
    p = sprawl.parent / "wt" / "holds-data"
    assert _git(p, "status", "--porcelain").strip() == "", (
        "fixture is wrong: git can see backend/data, so this proves nothing"
    )
    r = _row(rows, "holds-data")
    assert r.verdict == "KEEP", f"holds-data classified {r.verdict}: {r.reasons}"


def test_locked_worktree_is_ask_not_safe(rows: list) -> None:
    """A lock means another session may be standing in it. A cross-worktree
    write has already clobbered a session's work on this machine once."""
    assert _row(rows, "locked-merged").verdict == "ASK"


def test_corrupt_head_is_ask_and_does_not_crash_the_census(rows: list) -> None:
    """The census must survive it AND still classify every other worktree --
    a guard that dies on the one broken input has stopped answering."""
    assert _row(rows, "corrupt-head").verdict == "ASK"
    assert len(rows) >= 9, "one bad worktree truncated the whole census"


def test_reasons_are_never_empty_for_keep_or_ask(rows: list) -> None:
    """A verdict with no reason is unauditable, and the owner has to trust it."""
    for r in rows:
        if r.verdict in ("KEEP", "ASK"):
            assert r.reasons, f"{r.path} says {r.verdict} with no reason"


# ─────────────────────────────────────────────────────────────────────────────
# the plan: prints, deletes nothing, and refuses a stale --execute
# ─────────────────────────────────────────────────────────────────────────────

def test_plan_deletes_nothing_and_prints_commands(sprawl: Path, capsys) -> None:
    before = sorted(p.name for p in (sprawl.parent / "wt").iterdir())
    rc = wc.main(["--repo", str(sprawl)])
    after = sorted(p.name for p in (sprawl.parent / "wt").iterdir())
    assert rc == 0
    assert before == after, "the default run deleted something"
    out = capsys.readouterr().out
    assert "git worktree remove" in out, "the plan must print the exact commands"


def test_execute_refuses_when_state_changed_since_the_plan(
    sprawl: Path, tmp_path: Path,
) -> None:
    """An --execute that trusts a plan written minutes ago can delete a folder
    the owner has since started working in."""
    plan = tmp_path / "plan.json"
    assert wc.main(["--repo", str(sprawl), "--plan-out", str(plan)]) == 0

    # the owner starts editing the one folder the plan called SAFE
    (sprawl.parent / "wt" / "clean-merged" / "new-idea.md").write_text(
        "work in progress", encoding="utf-8")

    rc = wc.main(["--repo", str(sprawl), "--execute", "--plan", str(plan)])
    assert rc != 0, "--execute ran against a stale plan"
    assert (sprawl.parent / "wt" / "clean-merged").exists(), "it deleted the folder"
    # leave the fixture as we found it for any later test
    os.remove(sprawl.parent / "wt" / "clean-merged" / "new-idea.md")


def test_execute_refuses_anything_that_is_not_safe(sprawl: Path, tmp_path: Path) -> None:
    """--execute is SAFE-only. A plan hand-edited to include a KEEP row must be
    refused, not obeyed."""
    plan = tmp_path / "plan.json"
    assert wc.main(["--repo", str(sprawl), "--plan-out", str(plan)]) == 0
    import json
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["safe"].append({"path": str(sprawl.parent / "wt" / "dirty-merged"),
                         "branch": "dirty-merged", "state": "forged"})
    plan.write_text(json.dumps(data), encoding="utf-8")

    rc = wc.main(["--repo", str(sprawl), "--execute", "--plan", str(plan)])
    assert rc != 0
    assert (sprawl.parent / "wt" / "dirty-merged" / "dirty-merged.txt").read_text(
        encoding="utf-8") == "edited, never committed"


# ─────────────────────────────────────────────────────────────────────────────
# branches
# ─────────────────────────────────────────────────────────────────────────────

def test_branch_checked_out_in_a_dirty_worktree_is_never_safe(sprawl: Path) -> None:
    """`git branch --merged` calls it merged. Deleting it is how you strand the
    worktree that still holds the owner's edits."""
    rows = wc.census(sprawl).branches
    hits = [b for b in rows if b.name == "dirty-merged"]
    assert hits, "branch dirty-merged missing from the census"
    assert hits[0].verdict != "SAFE", hits[0].reasons


def test_current_branch_and_main_are_never_safe(sprawl: Path) -> None:
    rows = wc.census(sprawl).branches
    for b in rows:
        if b.name == "main":
            assert b.verdict == "KEEP"
