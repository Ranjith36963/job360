"""Rule #18's gate invariant, pinned here so docs need not enumerate call sites.

Six LIVING docs used to list every ``ENGINE2_ENABLED or ENRICHMENT_ENABLED``
site by ``file:line``. Those lists rotted on the next unrelated edit above
them and had drifted into miscounting the sites outright. The invariant is
what a reader actually needs, and an invariant belongs in a test:

  * every runtime read of a legacy engine flag is paired with its ``ENGINEx``
    partner in the same ``or``, so either name opens the gate;
  * ``SEMANTIC_ENABLED`` is the deliberate exception — it gates the embedding
    WRITE path alone, while ``ENGINE3_ENABLED`` opens only the READ path;
  * the multi-dimension scoring path is gated on ``user_preferences`` alone,
    on no flag at all (rule #20) — the flag decides only whether the dims
    read real enrichment data or their neutral halves (rule #29).

Add a new gate that reads only the legacy name and the first test goes red
with the file and line, which is the failure the doc lists never caught.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.models import Job
from src.services.profile.models import SearchConfig, UserPreferences
from src.services.skill_matcher import JobScorer

SRC = Path(__file__).resolve().parents[1] / "src"

# legacy name -> the ENGINEx name that must open the same gate
PAIRED_FLAGS = {
    "ENRICHMENT_ENABLED": "ENGINE2_ENABLED",
    "MATCHER_ENABLED": "ENGINE4_ENABLED",
}


def _python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _unpaired_reads(tree: ast.AST, legacy: str, partner: str) -> list[ast.Name]:
    """Every Load of ``legacy`` that is NOT inside an ``or`` naming ``partner``."""
    paired: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if legacy in names and partner in names:
            paired.update(
                id(n)
                for n in ast.walk(node)
                if isinstance(n, ast.Name) and n.id == legacy
            )
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Name)
        and n.id == legacy
        and isinstance(n.ctx, ast.Load)
        and id(n) not in paired
    ]


@pytest.mark.parametrize(("legacy", "partner"), sorted(PAIRED_FLAGS.items()))
def test_legacy_engine_flag_is_never_read_without_its_enginex_partner(
    legacy: str, partner: str
) -> None:
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [
            f"{path.relative_to(SRC.parent)}:{n.lineno}"
            for n in _unpaired_reads(tree, legacy, partner)
        ]
    assert not offenders, (
        f"{legacy} is read without `{partner} or ...` at: {', '.join(offenders)}. "
        f"Rule #18: both names must open the same gate."
    )


def test_semantic_enabled_gates_the_embedding_write_path_alone() -> None:
    """ENGINE3_ENABLED opens the read path only — it must NOT reach the writes."""
    main = ast.parse((SRC / "main.py").read_text(encoding="utf-8"))
    bare = _unpaired_reads(main, "SEMANTIC_ENABLED", "ENGINE3_ENABLED")
    assert bare, "src/main.py no longer gates the embedding writes on SEMANTIC_ENABLED alone"

    routes = ast.parse((SRC / "api" / "routes" / "jobs.py").read_text(encoding="utf-8"))
    assert not _unpaired_reads(routes, "SEMANTIC_ENABLED", "ENGINE3_ENABLED"), (
        "the hybrid READ path must accept either ENGINE3_ENABLED or SEMANTIC_ENABLED"
    )


def _job() -> Job:
    return Job(
        title="ML Engineer",
        company="Acme",
        apply_url="https://example.com",
        source="reed",
        location="London, UK",
        description="Python PyTorch role",
        date_found="2026-01-01T00:00:00+00:00",
    )


def _config() -> SearchConfig:
    return SearchConfig(job_titles=["ML Engineer"], primary_skills=["python", "pytorch"])


def test_dimension_path_is_gated_on_user_preferences_not_on_a_flag() -> None:
    """With both E2 names off and no enrichment row, the dims still fire (rule #29).

    They score their neutral halves rather than zero, which is the difference
    the enrichment flag actually makes.
    """
    prefs = UserPreferences(
        salary_min=70000,
        salary_max=90000,
        experience_level="senior",
        work_arrangement="remote",
        needs_visa=True,
    )
    with_prefs = JobScorer(_config(), user_preferences=prefs).score(_job())
    dims = (
        with_prefs.seniority_score
        + with_prefs.salary_score
        + with_prefs.visa_score
        + with_prefs.workplace_score
    )
    assert dims > 0, "dims must run on user_preferences alone, with no enrichment data"

    without_prefs = JobScorer(_config()).score(_job())
    assert (
        without_prefs.seniority_score
        + without_prefs.salary_score
        + without_prefs.visa_score
        + without_prefs.workplace_score
    ) == 0, "a user with no preferences keeps the legacy 4-component formula"
