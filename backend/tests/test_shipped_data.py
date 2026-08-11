"""Data the repo deliberately COMMITS must survive the Docker build.

Two ignore files describe the same intent from opposite directions, and nothing
made them agree. `.gitignore` ignores `data/` and then un-ignores
`backend/data/uk_gazetteer/**` and `backend/data/job_signals/**` — the exemptions
are the reason those files are in the repository at all. `.dockerignore` ignored
`data/` with no exemptions, and the Dockerfile is `COPY . .`, so the image was
built without precisely the files someone had gone out of their way to commit.

Nothing failed. Both loaders degrade silently by design:

    uk_gate._read()          -> frozenset()  when the file is absent
    job_signals._load_terms() -> {}           when the file is absent

So the UK gate (rule #30's entire basis) and the seniority / workplace
detectors would answer "nothing" in production, with no exception and no log
line, while passing every test on a developer machine where the files exist.

This is the same shape as every other bug in this batch: two hand-maintained
copies of one intent, drifting apart with nobody watching. The test is therefore
DERIVED from `.gitignore` rather than hard-coding a list of directories — a
third exemption added tomorrow is covered automatically.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent


def _negations(path: Path) -> set[str]:
    """Un-ignore patterns (`!...`) under a data/ directory, normalised.

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
        if pattern.startswith("data/"):
            out.add(pattern.rstrip("/").removesuffix("/**"))
    return out


class TestTheImageShipsWhatTheRepoCommits:
    def test_dockerignore_exempts_everything_gitignore_exempts(self) -> None:
        wanted = _negations(_REPO / ".gitignore")
        assert wanted, (
            "no data/ exemptions found in .gitignore — if the committed "
            "reference data moved, this test needs to move with it"
        )
        have = _negations(_BACKEND / ".dockerignore")
        missing = sorted(wanted - have)
        assert not missing, (
            "These directories are deliberately committed to the repo but "
            f"excluded from the Docker image: {missing}. The loaders degrade "
            "silently on a missing file, so production answers 'nothing' with "
            "no error while every local test passes."
        )

    @pytest.mark.parametrize("rel", ["data/uk_gazetteer", "data/job_signals"])
    def test_the_committed_data_actually_exists(self, rel: str) -> None:
        """The exemption is worthless if the directory is empty — that would
        look identical to the bug it prevents."""
        d = _BACKEND / rel
        assert d.is_dir(), f"{rel} is exempted from both ignore files but absent"
        files = [p for p in d.iterdir() if p.is_file() and p.suffix in (".txt", "")]
        assert files, f"{rel} exists but ships no data files"


class TestTheLoadersStillDegradeQuietly:
    """Pins WHY this needed a test rather than being caught at runtime: neither
    loader raises. If someone later makes them raise, that is an improvement —
    but it should be a deliberate change, seen here first."""

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
