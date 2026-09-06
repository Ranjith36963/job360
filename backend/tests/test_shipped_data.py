"""Data the repo deliberately COMMITS must reach the RUNNING container.

`.gitignore` ignores `data/` and then un-ignores specific directories — those
`!` lines are the only reason those files are in the repository at all. But
being committed is not the same as being deployed, and the repo has now been
bitten twice by exactly that gap:

    ESCO           — never built or shipped at all
    job_signals    — issue #260 / #321: `pip install .` ships only `src*`, so
                     `backend/data/job_signals` resolved to
                     `<site-packages>/data/job_signals` and the detectors ran
                     blind, answering UNKNOWN with no error

The loader degrades silently by design:

    seniority._load_terms() -> {}   when the file is absent

so the seniority detector answers "nothing" in production, with no exception,
while passing every test on a developer machine where the files exist.

TWO MECHANISMS CARRY DATA INTO THE IMAGE, and which one applies depends on
WHERE the data lives:

    backend/src/data/...  -> the WHEEL. `pip install .` copies it only if
                             `[tool.setuptools.package-data]` declares it.
    backend/data/...      -> the DOCKER BUILD CONTEXT. `Dockerfile` is
                             `COPY . .`, and `.dockerignore`'s root-anchored
                             `data/` rule deletes it unless a `!` exemption
                             says otherwise.

This test is DERIVED from `.gitignore` rather than hard-coding a list, and it
asserts the RIGHT mechanism for each location — so a fourth exemption added
tomorrow is covered automatically, and an exemption in a shape nobody has
thought about FAILS rather than passing vacuously.

THAT DERIVATION HAS A HOLE, WHICH IS WHY THERE IS A SECOND ONE BELOW.
`.gitignore`'s `!backend/src/data/` un-ignores the WHOLE subtree, so a new
directory beneath it is committable with no `!` line of its own. Measured:

    git check-ignore backend/src/data/newthing/terms.txt   -> rc 1, NOT ignored

So the `!`-derived list above never sees such a directory, while
`[tool.setuptools.package-data]` — an explicit per-directory list with no
wildcard — does not ship it. Committed, unshipped, unchecked: instance three,
pre-armed. The file-level check below therefore derives from what git actually
TRACKS and matches every committed file against the real packaging globs. That
also catches a new EXTENSION inside an already-declared directory: the only
pattern is `*.txt`, so a `terms.json` added tomorrow would be dropped from the
wheel exactly as silently, and no directory-level check could ever see it.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent

# Bare parent directories that exist only so git can descend into them. They
# hold no data files of their own, so the per-directory checks skip them.
_CONTAINER_DIRS = {"data", "src/data"}


def _negations(path: Path) -> set[str]:
    """Un-ignore patterns (`!...`) pointing at data directories, normalised.

    Both files are read into the same namespace: `.gitignore` sits at the repo
    root and writes `backend/data/...`, `.dockerignore` sits in `backend/` and
    writes `data/...`. Stripping the `backend/` prefix makes them comparable.
    """
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("!"):
            continue
        pattern = line[1:].strip().lstrip("/")
        pattern = re.sub(r"^backend/", "", pattern)
        if pattern.startswith(("data/", "src/data/")) or pattern.rstrip("/") in _CONTAINER_DIRS:
            out.add(pattern.rstrip("/").removesuffix("/**"))
    return out


def _shipped_data_dirs() -> set[str]:
    """Every directory `.gitignore` goes out of its way to commit."""
    return {d for d in _negations(_REPO / ".gitignore") if d not in _CONTAINER_DIRS}


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile one setuptools package-data glob into a regex.

    `*` matches inside ONE path segment and does NOT cross `/`, which is how
    setuptools expands package-data. It is spelled out here instead of reusing
    `fnmatch`, whose `*` DOES cross `/` — this repo already carries one matcher
    where a `*` silently crossed a separator, and a lenient matcher here would
    make the whole check pass vacuously.
    """
    out = ["^"]
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        char = pattern[i]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def _package_data_globs() -> list[str]:
    """Every path `[tool.setuptools.package-data]` ships, relative to `backend/`.

    Each key is a package name and each value a list of globs relative to that
    package, so `src` + `data/job_signals/*.txt` becomes
    `src/data/job_signals/*.txt`.

    Raises:
        RuntimeError: if no TOML parser is importable, so the check fails loudly
            rather than skipping — a silent skip is the exact failure this file
            exists to prevent.
    """
    try:
        import tomllib
    except ImportError as exc:  # pragma: no cover - Python < 3.11 only
        raise RuntimeError(
            "no tomllib (needs Python 3.11+; CI runs 3.12). This check must not "
            "be skipped — skipping it is how the data went missing three times."
        ) from exc

    parsed = tomllib.loads((_BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = parsed.get("tool", {}).get("setuptools", {}).get("package-data", {})
    return [
        f"{package.replace('.', '/')}/{pattern}"
        for package, patterns in package_data.items()
        for pattern in patterns
    ]


def _committed_src_data_files() -> list[str]:
    """Files git TRACKS under `backend/src/data/`, relative to `backend/`.

    Tracked is the right question, not on-disk. An untracked scratch file in one
    of the many parallel worktrees must not fail the build, and a file that IS
    committed is precisely the thing required to reach the wheel.

    Raises:
        RuntimeError: if git cannot be consulted, for the same reason as above —
            an unanswerable check must be loud, never quietly empty.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--", "src/data"],
            cwd=_BACKEND,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not list git-tracked data files: {exc}") from exc
    return sorted(path for path in proc.stdout.split("\0") if path)


def _uncovered(files: Iterable[str], globs: Sequence[str]) -> list[str]:
    """Return the files no packaging glob matches — i.e. absent from the wheel."""
    compiled = [_glob_to_regex(glob) for glob in globs]
    return sorted(f for f in files if not any(rx.match(f) for rx in compiled))


class TestEveryCommittedDataFileReachesTheWheel:
    """File-level net under the directory-level one above.

    The directory check derives from `.gitignore`'s `!` lines; this one derives
    from the git index, so it sees a data directory that was never named in
    `.gitignore` at all, and a file whose EXTENSION no glob covers.
    """

    def test_it_is_reading_real_committed_files(self) -> None:
        """Non-vacuity: an empty file list would pass every assertion below."""
        files = _committed_src_data_files()
        assert "src/data/job_signals/seniority_terms.txt" in files, (
            "the seniority vocabulary this check is built on is not in the git "
            "index — either the data moved and this check must move with it, "
            f"or it really is uncommitted. Saw: {files}"
        )

    def test_the_globs_were_actually_parsed(self) -> None:
        """Non-vacuity: an empty glob list would fail everything, not pass it —
        but an empty FILE list plus empty globs would pass, so pin both."""
        assert _package_data_globs(), (
            "pyproject declares no package-data, so `pip install .` copies .py "
            "files only and every data-driven service ships blind"
        )

    def test_no_committed_data_file_is_left_out_of_the_wheel(self) -> None:
        """The load-bearing assertion: committed must imply shipped."""
        missing = _uncovered(_committed_src_data_files(), _package_data_globs())
        assert not missing, (
            "these files are committed under backend/src/data/ but no "
            "[tool.setuptools.package-data] glob matches them, so `pip install .` "
            "leaves them out of the wheel. The loaders degrade to empty WITHOUT "
            "raising, so production answers 'nothing' while every local test "
            f"passes — issue #260 / #312 / #321, fourth time: {missing}"
        )

    def test_it_catches_a_data_directory_nobody_declared(self) -> None:
        """Teeth. This is the case the .gitignore-derived check cannot see:
        `!backend/src/data/` makes the file committable with no `!` line, so
        only a file-level check notices it is missing from the wheel."""
        assert _uncovered(
            ["src/data/newthing/terms.txt"], _package_data_globs()
        ) == ["src/data/newthing/terms.txt"]

    def test_it_catches_a_new_extension_in_a_declared_directory(self) -> None:
        """Teeth. `*.txt` is the only pattern for job_signals, so a JSON build
        output beside them would be dropped from the wheel — a directory-level
        check calls that directory covered and moves on."""
        assert _uncovered(
            ["src/data/job_signals/terms.json"], _package_data_globs()
        ) == ["src/data/job_signals/terms.json"]

    def test_a_star_does_not_cross_a_slash(self) -> None:
        """Teeth on the matcher itself: a lenient `*` would call every nested
        path covered and turn the whole class into a rubber stamp."""
        rx = _glob_to_regex("src/data/job_signals/*.txt")
        assert rx.match("src/data/job_signals/seniority_terms.txt")
        assert not rx.match("src/data/job_signals/nested/seniority_terms.txt")


class TestEveryCommittedDataDirHasAWayIntoTheImage:
    """The load-bearing assertion. Each exempted directory must be carried by
    the mechanism that matches WHERE IT LIVES — and an unrecognised location is
    a failure, not a pass, because the whole class of bug is 'we assumed
    something carried it and nothing did'."""

    def test_there_is_something_to_check(self) -> None:
        assert _shipped_data_dirs(), (
            "no shipped-data exemptions found in .gitignore — if the committed "
            "reference data moved, this test must move with it rather than "
            "quietly checking nothing"
        )

    @pytest.mark.parametrize("rel", sorted(_shipped_data_dirs()))
    def test_the_right_mechanism_carries_it(self, rel: str) -> None:
        if rel.startswith("src/data/"):
            # The wheel is what runs in the container, not the working tree.
            pyproject = (_BACKEND / "pyproject.toml").read_text(encoding="utf-8")
            assert "[tool.setuptools.package-data]" in pyproject, (
                "pyproject declares no package-data at all, so `pip install .` "
                "copies .py files only"
            )
            assert rel.removeprefix("src/") in pyproject, (
                f"{rel} lives inside the package but is NOT declared in "
                "[tool.setuptools.package-data], so the wheel production "
                "installs does not contain it. That is issue #260's exact shape."
            )
        elif rel.startswith("data/"):
            # Root-anchored `data/` in .dockerignore deletes it from the build
            # context unless mirrored here.
            have = _negations(_BACKEND / ".dockerignore")
            assert rel in have, (
                f"{rel} is deliberately committed to the repo but excluded from "
                "the Docker image. The loaders degrade silently on a missing "
                "file, so production answers 'nothing' with no error while "
                "every local test passes."
            )
        else:
            pytest.fail(
                f"{rel} is exempted in .gitignore but sits in a location this "
                "test knows no delivery mechanism for. Say how it reaches the "
                "container before shipping it."
            )

    @pytest.mark.parametrize("rel", sorted(_shipped_data_dirs()))
    def test_the_committed_data_actually_exists(self, rel: str) -> None:
        """The exemption is worthless if the directory is empty — that would
        look identical to the bug it prevents."""
        d = _BACKEND / rel
        assert d.is_dir(), f"{rel} is exempted in .gitignore but absent on disk"
        files = [p for p in d.iterdir() if p.is_file() and p.suffix in (".txt", "")]
        assert files, f"{rel} exists but ships no data files"


class TestTheLoaderDegradesWithoutRaising:
    """Pins WHY this needed a test rather than being caught at runtime: the
    loader does not raise. It now LOGS an error (silence is what hid the bug
    twice), but a lost data file still must not crash a profile save. If
    someone later makes it raise, that is a deliberate change and belongs here
    first.

    Slice 5 (#483) deleted `services/uk_gate.py` (the search pipeline's UK
    door) and `services/job_signals.py`; the seniority half of the second
    moved to `services/profile/seniority.py`, which is the loader below.
    """

    def test_seniority_loader_returns_empty_dict_for_a_missing_file(self) -> None:
        from src.services.profile.seniority import _load_terms

        assert _load_terms("definitely-not-a-real-file.txt") == {}
