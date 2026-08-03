"""Step-1.5 S1.5-D/E — ESCO normalisation activates from cv_parser.

Tests the wiring without requiring the real ~13.9k-row ESCO embedding
matrix to be on disk. We monkeypatch `normalize_skill` and `is_available`
on the skill_normalizer module so the cv_parser code path runs end to
end against a deterministic in-memory match table.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.services.profile.cv_parser import _maybe_normalise_skills_via_esco


@dataclass(frozen=True)
class _StubMatch:
    uri: str
    label: str
    similarity: float = 0.9


@pytest.fixture
def fake_esco(monkeypatch):
    """Pretend SEMANTIC_ENABLED=true and ESCO index is loaded."""
    monkeypatch.setattr("src.core.settings.SEMANTIC_ENABLED", True)
    table = {
        "py": _StubMatch(uri="esco://skill/python", label="Python"),
        "python": _StubMatch(uri="esco://skill/python", label="Python"),
        "js": _StubMatch(uri="esco://skill/javascript", label="JavaScript"),
    }

    def fake_normalize(raw):
        return table.get(raw.lower())

    monkeypatch.setattr("src.services.profile.skill_normalizer.normalize_skill", fake_normalize)
    monkeypatch.setattr("src.services.profile.skill_normalizer.is_available", lambda: True)
    return table


def test_canonical_labels_replace_raw_skills_when_match_found(fake_esco):
    """Two surface forms ('Py', 'Python') collapse to the canonical 'Python'."""
    canonical, esco_map = _maybe_normalise_skills_via_esco(["Py", "JS"])
    assert canonical == ["Python", "JavaScript"]
    assert esco_map == {
        "Python": "esco://skill/python",
        "JavaScript": "esco://skill/javascript",
    }


def test_unmatched_skill_passes_through_unchanged(fake_esco):
    """Skills with no ESCO match keep the raw string and contribute no URI."""
    canonical, esco_map = _maybe_normalise_skills_via_esco(["Py", "Bespoke Tooling"])
    assert canonical == ["Python", "Bespoke Tooling"]
    assert "Bespoke Tooling" not in esco_map


def test_no_op_when_semantic_disabled(monkeypatch):
    """SEMANTIC_ENABLED=false → identity transform, empty URI map.

    Defends rule #18: default-off behaviour must be byte-identical to
    the pre-Pillar-2 path.
    """
    monkeypatch.setattr("src.core.settings.SEMANTIC_ENABLED", False)
    canonical, esco_map = _maybe_normalise_skills_via_esco(["Python", "JavaScript"])
    assert canonical == ["Python", "JavaScript"]
    assert esco_map == {}


def test_no_op_when_esco_index_unavailable(monkeypatch):
    """SEMANTIC_ENABLED=true but ESCO data missing → graceful pass-through."""
    monkeypatch.setattr("src.core.settings.SEMANTIC_ENABLED", True)
    monkeypatch.setattr("src.services.profile.skill_normalizer.is_available", lambda: False)
    canonical, esco_map = _maybe_normalise_skills_via_esco(["Python"])
    assert canonical == ["Python"]
    assert esco_map == {}


def test_normalise_handles_blank_strings(fake_esco):
    """Empty/whitespace skill entries must be silently dropped."""
    canonical, esco_map = _maybe_normalise_skills_via_esco(["", "  ", "Py"])
    assert canonical == ["Python"]


def test_normalise_swallows_normaliser_exceptions(monkeypatch):
    """A raising normaliser must not crash CV parsing — fall back to raw."""
    monkeypatch.setattr("src.core.settings.SEMANTIC_ENABLED", True)
    monkeypatch.setattr("src.services.profile.skill_normalizer.is_available", lambda: True)

    def boom(_raw):
        raise RuntimeError("ESCO encoder OOM")

    monkeypatch.setattr("src.services.profile.skill_normalizer.normalize_skill", boom)
    canonical, esco_map = _maybe_normalise_skills_via_esco(["Python"])
    assert canonical == ["Python"]
    assert esco_map == {}


def test_full_cv_parse_pipeline_populates_esco_map(fake_esco):
    """End-to-end: a fake LLM result containing 'Py' should land in CVData
    with skills=['Python'] AND cv_skills_esco={'Python': 'esco://...'}."""
    from src.services.profile.cv_parser import _llm_result_to_cvdata

    cvdata = _llm_result_to_cvdata(
        raw_text="dummy",
        result={"skills": ["Py", "Bespoke Tooling"]},
    )
    assert "Python" in cvdata.skills
    assert "Bespoke Tooling" in cvdata.skills
    assert cvdata.cv_skills_esco == {"Python": "esco://skill/python"}


# ---------------------------------------------------------------------------
# Structural noise guards (added 2026-08-03, driven by the real 7-CV corpus in
# User_info/). These are GRAMMAR rules, not skill vocabularies — rule #28 holds.
# ---------------------------------------------------------------------------

def test_prose_fragments_are_not_skills():
    """A CV that states skills in sentences gets comma-split into fake skills.
    Real examples pulled from the corpus."""
    from src.services.profile.cv_parser import _det_is_prose
    for prose in (
        "Production Python for data engineering",
        "integrating various sensors and actuators.",
        "grounded LLM answers. Containers",       # two sentences glued together
    ):
        assert _det_is_prose(prose), f"should be dropped as prose: {prose!r}"


def test_real_skill_names_survive_the_prose_guard():
    """The guard must not eat legitimate skills — including dotted names and
    abbreviations, which an over-eager sentence check silently deletes."""
    from src.services.profile.cv_parser import _det_is_prose
    for skill in (
        "Node.js", ".NET", "asp.net core", "PySpark", "Deep Learning",
        "Generative AI", "U.S. GAAP", "M.Sc. Statistics",
    ):
        assert not _det_is_prose(skill), f"real skill wrongly dropped: {skill!r}"


def test_acronym_and_expansion_collapse_to_one_skill():
    """'RAG (Retrieval Augmented Generation)' expands to BOTH terms, counting
    one capability twice and inflating the profile."""
    from src.services.profile.cv_parser import _det_collapse_acronyms
    out = _det_collapse_acronyms(["RAG", "Retrieval Augmented Generation", "Python"])
    assert "RAG" in out and "Python" in out
    assert "Retrieval Augmented Generation" not in out


def test_collapse_leaves_unrelated_multiword_skills_alone():
    """Only a genuine acronym/expansion pair collapses — not every phrase."""
    from src.services.profile.cv_parser import _det_collapse_acronyms
    out = _det_collapse_acronyms(["Deep Learning", "Computer Vision", "Python"])
    assert len(out) == 3
