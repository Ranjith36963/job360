"""Data the repo deliberately COMMITS must survive the Docker build.

Two ignore files describe the same intent from opposite directions, and nothing
made them agree. `.gitignore` ignores `data/` and then un-ignores
`backend/src/data/uk_gazetteer/**` and `backend/src/data/job_signals/**` — the exemptions
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


def _negations(path: Path, *, prefix: str) -> set[str]:
    """Un-ignore patterns (`!...`) under `prefix`, normalised.

    `.gitignore` sits at the repo root and writes `backend/src/data/...`;
    `.dockerignore` sits in `backend/` and writes `src/data/...`. Stripping the
    `backend/` prefix makes them comparable.
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
        pattern = pattern.rstrip("/").removesuffix("/**")
        if pattern.startswith(prefix) and pattern != prefix.rstrip("/"):
            out.add(pattern)
    return out


class TestTheImageShipsWhatTheRepoCommits:
    """WHAT CHANGED 2026-08-13, and why this test changed shape with it.

    Both reference-data directories now live INSIDE the package
    (`backend/src/data/`), so `.dockerignore` no longer needs an exemption for
    either — its `data/` rule is root-anchored and never reached inside `src/`.
    Comparing the two ignore files therefore has nothing left to compare.

    The exemption was never the real guarantee anyway. It only put the files at
    `/app/data`, which the API process happens to see because
    `uvicorn main:app` runs `sys.path.insert(0, ".")`. The ARQ worker runs the
    console script `arq`, and arq/cli.py does `sys.path.append(os.getcwd())` —
    APPEND — so site-packages wins the `import src` and the `/app` copy is never
    loaded. What actually carries data into BOTH processes is
    `[tool.setuptools.package-data]` + `pip install .`. So that is what this
    test now pins.
    """

    def test_package_data_covers_every_committed_src_data_dir(self) -> None:
        wanted = _negations(_REPO / ".gitignore", prefix="src/data/")
        assert wanted, (
            "no src/data/ exemptions found in .gitignore — if the committed "
            "reference data moved, this test needs to move with it"
        )
        pyproject = (_BACKEND / "pyproject.toml").read_text(encoding="utf-8")
        missing = sorted(
            d for d in wanted if f'"{d.removeprefix("src/")}/' not in pyproject
        )
        assert not missing, (
            f"{missing} are committed on purpose but absent from "
            "[tool.setuptools.package-data] in backend/pyproject.toml, so "
            "`pip install .` leaves them out of site-packages. The loaders "
            "degrade silently on a missing file, so the worker answers "
            "'nothing' with no error while every local test passes."
        )

    # BOTH now live inside the package (backend/src/data/) so that
    # `pip install .` actually carries them — the wheel, not the working tree,
    # is what runs in the container. uk_gazetteer moved first (issue #260);
    # job_signals followed on 2026-08-13 once the worker path was measured:
    # `arq` is a console script and arq/cli.py does `sys.path.append(cwd)`, so
    # site-packages wins the `import src` and the old `/app/data/job_signals`
    # was invisible to every worker process.
    @pytest.mark.parametrize("rel", ["src/data/uk_gazetteer", "src/data/job_signals"])
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


class TestTheVocabularyIsActuallyThere:
    """The counterweight to the class above.

    "Degrades quietly" is the right runtime behaviour and the wrong test
    posture: every consumer of these tables would keep passing against an empty
    vocabulary. `keyword_generator.strip_seniority` in particular becomes a
    silent no-op — the rank word stays in the query and the ~99.9% recall loss
    it exists to prevent comes straight back, with nothing red anywhere. These
    assertions are the loud part.
    """

    def test_seniority_vocabulary_loads_and_is_not_empty(self) -> None:
        from src.services.job_signals import _seniority_terms

        terms = _seniority_terms()
        assert terms, "seniority_terms.txt did not load — check job_signals._DATA"
        assert "intern" in terms and "senior" in terms

    def test_workplace_vocabularies_load_and_are_not_empty(self) -> None:
        from src.services.job_signals import _location_terms, _workplace_terms

        assert _workplace_terms(), "workplace_terms.txt did not load"
        assert _location_terms(), "workplace_location_terms.txt did not load"

    def test_data_resolves_inside_the_package_not_beside_it(self) -> None:
        """Pins the move itself. The old `backend/data/job_signals` path is
        invisible to the ARQ worker (site-packages wins the import), so a
        revert to it must fail here rather than in production silence."""
        from src.services import job_signals

        assert job_signals._DATA == job_signals._PACKAGE_DATA
        assert job_signals._DATA.parent.parent.name == "src"
