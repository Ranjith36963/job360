"""Data the repo deliberately COMMITS must reach the RUNNING container.

`.gitignore` ignores `data/` and then un-ignores specific directories — those
`!` lines are the only reason those files are in the repository at all. But
being committed is not the same as being deployed, and the repo has now been
bitten three times by exactly that gap:

    ESCO           — never built or shipped at all
    uk_gazetteer   — issue #260 / PR #312: `pip install .` ships only `src*`,
                     so `backend/data/uk_gazetteer` resolved to
                     `<site-packages>/data/uk_gazetteer` and the UK gate ran
                     blind for four days while logging "blocked N jobs"
    job_signals    — the same bug, same shape, found while fixing the second

Both loaders degrade silently by design:

    uk_gate._read()           -> frozenset()  when the file is absent
    job_signals._load_terms() -> {}           when the file is absent

so the UK gate (rule #30's entire basis) and the seniority / workplace
detectors answer "nothing" in production, with no exception, while passing
every test on a developer machine where the files exist.

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
"""
from __future__ import annotations

import re
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


class TestTheLoadersDegradeWithoutRaising:
    """Pins WHY this needed a test rather than being caught at runtime: neither
    loader raises. Both now LOG an error (silence is what hid the bug three
    times), but a lost data file still must not crash the pipeline. If someone
    later makes them raise, that is a deliberate change and belongs here first.
    """

    def test_uk_gate_returns_empty_rather_than_raising(self) -> None:
        from src.services import uk_gate

        # A missing data directory yields empty sets, not an error — which is
        # exactly why an image built without them looked healthy.
        places, foreign, ambiguous = uk_gate._gazetteer()
        for s in (places, foreign, ambiguous):
            assert isinstance(s, frozenset)

    def test_job_signals_returns_empty_dict_for_a_missing_file(self) -> None:
        from src.services.job_signals import _load_terms

        assert _load_terms("definitely-not-a-real-file.txt") == {}
