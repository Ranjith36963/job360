"""Pillar 2 Batch 2.3 — tests for the static skill synonym table.

Covers:
  - canonicalize_skill(): alias resolution, case/whitespace normalization,
    unknown-term pass-through, idempotence.
  - aliases_for(): reverse lookup returns canonical + all surface aliases.
  - Integration: skill_matcher._text_contains_skill matches via aliases,
    and JobScorer gives the same score to a job whose description uses the
    alias ("k8s") as one that uses the canonical form ("kubernetes").
  - Profile flow: keyword_generator collapses aliases before they hit
    the SearchConfig, so duplicated skills (["JS", "JavaScript"]) coalesce.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.skill_synonyms import (
    aliases_for,
    canonicalize_skill,
    total_entries,
)

# ---------------------------------------------------------------------------
# Basic alias lookups
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("js", "javascript"),
        ("JS", "javascript"),
        ("Js", "javascript"),
        ("ecmascript", "javascript"),
        ("nodejs", "node.js"),
        ("node", "node.js"),
        ("ts", "typescript"),
        ("py", "python"),
        ("python3", "python"),
        ("cpp", "c++"),
        ("c sharp", "c#"),
        ("golang", "go"),
        ("k8s", "kubernetes"),
        ("kube", "kubernetes"),
        ("aws", "amazon web services"),
        ("gcp", "google cloud platform"),
        ("nextjs", "next.js"),
        ("reactjs", "react"),
        ("pg", "postgresql"),
        ("postgres", "postgresql"),
        ("mongo", "mongodb"),
        ("tf", "terraform"),
        ("ci/cd", "continuous integration and delivery"),
        ("cicd", "continuous integration and delivery"),
        ("ml", "machine learning"),
        ("nlp", "natural language processing"),
        ("llm", "large language model"),
        ("rag", "retrieval augmented generation"),
        ("hf", "hugging face"),
        ("sklearn", "scikit-learn"),
    ],
)
def test_canonicalize_alias_resolves_to_canonical(raw: str, expected: str) -> None:
    assert canonicalize_skill(raw) == expected


# ---------------------------------------------------------------------------
# Medical, finance, legal, HR aliases — UK professional coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Medical / NHS
        ("cpr", "cardiopulmonary resuscitation"),
        ("ecg", "electrocardiogram"),
        ("ekg", "electrocardiogram"),
        ("mri", "magnetic resonance imaging"),
        ("a&e", "accident and emergency"),
        ("gp", "general practitioner"),
        # Finance
        ("p&l", "profit and loss"),
        ("roi", "return on investment"),
        ("kpi", "key performance indicator"),
        ("m&a", "mergers and acquisitions"),
        ("aml", "anti-money laundering"),
        ("kyc", "know your customer"),
        # Legal
        ("nda", "non-disclosure agreement"),
        ("sla", "service level agreement"),
        ("ip", "intellectual property"),
        # HR / PM
        ("hr", "human resources"),
        ("pm", "project management"),
        ("pmp", "project management professional"),
    ],
)
def test_canonicalize_uk_professional_aliases(raw: str, expected: str) -> None:
    assert canonicalize_skill(raw) == expected


# ---------------------------------------------------------------------------
# Normalisation semantics
# ---------------------------------------------------------------------------


def test_canonicalize_strips_whitespace():
    assert canonicalize_skill("   aws   ") == "amazon web services"


def test_canonicalize_collapses_internal_whitespace():
    assert canonicalize_skill("node    js") == "node.js"


def test_canonicalize_unknown_term_passes_through_lower_cased():
    """A term not in the table falls through with only case normalisation —
    domain-specific CV skills aren't silently dropped."""
    assert canonicalize_skill("Haskell") == "haskell"
    assert canonicalize_skill("Ocaml Wizardry") == "ocaml wizardry"


def test_canonicalize_empty_string_returns_empty():
    assert canonicalize_skill("") == ""


def test_canonicalize_idempotent_on_canonical_forms():
    """canonicalize(canonicalize(x)) must equal canonicalize(x) — critical
    invariant so the scorer can safely canonicalize both sides repeatedly
    without drift."""
    for raw in ["js", "k8s", "aws", "ml", "unknown skill", "Haskell"]:
        first = canonicalize_skill(raw)
        second = canonicalize_skill(first)
        assert first == second, f"drift on {raw}: {first!r} != {second!r}"


# ---------------------------------------------------------------------------
# Reverse lookup: aliases_for
# ---------------------------------------------------------------------------


def test_aliases_for_includes_canonical_itself():
    assert "kubernetes" in aliases_for("kubernetes")
    assert "kubernetes" in aliases_for("k8s")


def test_aliases_for_covers_all_known_aliases():
    k8s_set = set(aliases_for("k8s"))
    for expected in {"k8s", "kube", "kubectl", "kubernetes"}:
        assert expected in k8s_set


def test_aliases_for_unknown_skill_returns_just_itself():
    """Unknown skills have no synonyms — the return set is just the normalised
    term wrapped in a single-element tuple."""
    result = aliases_for("Haskell")
    assert result == ("haskell",)


# ---------------------------------------------------------------------------
# Integration with skill_matcher — the user-visible payoff
# ---------------------------------------------------------------------------


def test_text_contains_skill_matches_via_alias():
    """_text_contains_skill is what _skill_score calls; it must find the
    canonical term when the text contains an alias, and vice versa."""
    from src.services.skill_matcher import _text_contains_skill

    assert _text_contains_skill("We use k8s heavily", "kubernetes") is True
    assert _text_contains_skill("Looking for kubernetes expertise", "k8s") is True


def test_text_contains_skill_respects_word_boundaries():
    """Aliases must still match as whole words — "ai" in "sustain" must not
    false-match even though "ai" is an alias key (it isn't in the table, but
    the invariant matters for short aliases like "ts", "py", "ml")."""
    from src.services.skill_matcher import _text_contains_skill

    assert _text_contains_skill("Python developer", "py") is True
    assert _text_contains_skill("proudly claim", "py") is False  # substring trap


def test_jobscorer_scores_same_for_alias_and_canonical():
    """End-to-end: a job described with 'k8s' should score the same as one
    described with 'kubernetes', given the same profile requesting either."""
    from src.models import Job
    from src.services.profile.models import SearchConfig
    from src.services.skill_matcher import JobScorer

    config = SearchConfig(
        job_titles=["SRE"],
        primary_skills=["kubernetes", "terraform"],
    )
    scorer = JobScorer(config)

    def make(desc: str) -> Job:
        return Job(
            title="SRE",
            company="Co",
            apply_url="https://example.com",
            source="greenhouse",
            location="London, UK",
            description=desc,
            date_found=datetime.now(timezone.utc).isoformat(),
        )

    score_alias = scorer.score(make("Seeking k8s and tf expertise")).match_score
    score_canonical = scorer.score(make("Seeking kubernetes and terraform expertise")).match_score
    assert score_alias == score_canonical


def test_jobscorer_profile_side_alias_matches_canonical_text():
    """The profile can ALSO use an alias — a user who wrote "k8s, tf" in
    their CV must match a job description written in canonical form."""
    from src.models import Job
    from src.services.profile.models import SearchConfig
    from src.services.skill_matcher import JobScorer

    config = SearchConfig(
        job_titles=["SRE"],
        primary_skills=["k8s", "tf"],  # profile uses aliases
    )
    scorer = JobScorer(config)
    job = Job(
        title="SRE",
        company="Co",
        apply_url="https://example.com",
        source="greenhouse",
        location="London, UK",
        description="Seeking kubernetes and terraform experts",
        date_found=datetime.now(timezone.utc).isoformat(),
    )
    assert scorer.score(job).match_score > 25  # gate passes + skill hits canonical form


# ---------------------------------------------------------------------------
# Integration with keyword_generator — profile-side canonicalization
# ---------------------------------------------------------------------------


def test_keyword_generator_deduplicates_aliases():
    """UserProfile skills ["JS", "JavaScript"] should collapse to a single
    'javascript' entry in the SearchConfig, not two separate scoring terms."""
    from src.services.profile.keyword_generator import _canonicalize_skill_list

    result = _canonicalize_skill_list(["JS", "JavaScript", "js", "TypeScript", "TS"])
    assert result == ["javascript", "typescript"]


def test_keyword_generator_preserves_unknown_skills():
    """Domain-specific or niche skills still flow through — only known aliases
    are collapsed. Unknown terms are only case/whitespace normalised."""
    from src.services.profile.keyword_generator import _canonicalize_skill_list

    result = _canonicalize_skill_list(["Haskell", "Echocardiography", "Welding"])
    assert result == ["haskell", "echocardiography", "welding"]


def test_keyword_generator_preserves_order_of_first_occurrence():
    """Canonicalisation must be order-preserving — the primary/secondary/tertiary
    tier semantics encode "more important first"."""
    from src.services.profile.keyword_generator import _canonicalize_skill_list

    result = _canonicalize_skill_list(["Python", "JS", "Haskell", "js"])
    # Second 'js' is dedup'd; Python/javascript/haskell stay in order.
    assert result == ["python", "javascript", "haskell"]


# ---------------------------------------------------------------------------
# Table size guard — prevent silent shrinkage during future edits
# ---------------------------------------------------------------------------


def test_synonym_table_size_floor():
    """The curated table should not drop below 400 entries without a
    corresponding plan note. Target is ~500 per plan §4 Batch 2.3."""
    assert total_entries() >= 400
