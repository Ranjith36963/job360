"""Pillar 2 Batch 2.3 — tests for the static skill synonym table.

Covers:
  - canonicalize_skill(): alias resolution, case/whitespace normalization,
    unknown-term pass-through, idempotence.
  - aliases_for(): reverse lookup returns canonical + all surface aliases.

Rule #28 carve-out: this table is scoring/search VOCABULARY and reads no CV
input, so it survives the sourcing deletion even though its two biggest
consumers (the scorer and the keyword generator) do not.
"""

from __future__ import annotations

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
# Table size guard — prevent silent shrinkage during future edits
# ---------------------------------------------------------------------------


def test_synonym_table_size_floor():
    """The curated table should not drop below 400 entries without a
    corresponding plan note. Target is ~500 per plan §4 Batch 2.3."""
    assert total_entries() >= 400
