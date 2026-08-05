"""One capability must be listed once.

FOUND IN A REAL PROFILE, on screen, during browser verification. The LinkedIn
skill list showed:

    RAG        AND   Retrieval-Augmented Generation
    LoRA       AND   Low-Rank Adaptation
    LLM'S

Counting one capability two or three times inflates the skill count without
adding capability, which makes a profile look broad and match everything weakly
— the opposite of what the person wants. "LLM'S" matches no job posting at all
and simply reads as junk to its owner.

Two causes, both fixed here:

  1. ``_det_collapse_acronyms`` split on SPACES only, so
     "Retrieval-Augmented Generation" built the initials "rg" and never matched
     the "RAG" sitting beside it in the same list.
  2. The LinkedIn skill list was never cleaned at all — only the CV list was.

Everything below is punctuation-structural. No skill vocabulary is involved
(rule #28), so it behaves the same for a nurse, a welder, or an ML engineer.
"""

from __future__ import annotations

from src.services.profile.cv_parser import _det_collapse_acronyms, _strip_possessive


def test_a_hyphenated_expansion_collapses_into_its_acronym():
    """THE OBSERVED BUG. Hyphens join words inside a term; the old split() saw
    two words and built the wrong initials."""
    out = _det_collapse_acronyms(["RAG", "Retrieval-Augmented Generation", "Python"])
    assert "RAG" in out
    assert "Retrieval-Augmented Generation" not in out
    assert "Python" in out, "unrelated skills must survive untouched"


def test_slashes_are_treated_as_word_joins_too():
    """Same class of separator, same handling — and a DIFFERENT profession, so
    this cannot be quietly tuned to software terms."""
    out = _det_collapse_acronyms(["CBT", "Cognitive/Behavioural Therapy"])
    assert out == ["CBT"]


def test_possessives_and_stylised_plurals_are_stripped():
    assert _strip_possessive("LLM'S") == "LLM"
    assert _strip_possessive("LLM’s") == "LLM"       # smart quote, what PDFs emit
    assert _strip_possessive("Python") == "Python"    # untouched


def test_a_genuine_acronym_is_never_dropped_for_its_expansion():
    """Direction matters: keep the SHORT form, because that is what job ads
    use. Dropping "RAG" in favour of the long form would hurt matching."""
    out = _det_collapse_acronyms(["Retrieval-Augmented Generation", "RAG"])
    assert out == ["RAG"]


def test_unrelated_multiword_skills_are_not_collapsed():
    """The guard against over-collapsing. "Applied Machine Learning" must not
    vanish just because some unrelated three-letter token exists."""
    out = _det_collapse_acronyms(["Applied Machine Learning", "SQL", "AWS SageMaker"])
    assert len(out) == 3


def test_stylised_acronyms_are_known_to_survive():
    """HONEST LIMIT, recorded on purpose so nobody 'fixes' it with a word list.

    "LoRA" is Low-Rank Adaptation — two letters from each word, not initials.
    No structural rule recovers that; it needs a vocabulary, and rule #28 bans
    skill vocabularies in extraction. So this duplicate REMAINS, and that is a
    deliberate trade: a rare cosmetic duplicate is cheaper than a hand-typed
    dictionary that overfits and rots.
    """
    out = _det_collapse_acronyms(["LoRA", "Low-Rank Adaptation"])
    assert len(out) == 2, "if this ever passes, check it was not done with a keyword list"


def test_the_real_linkedin_list_gets_cleaner_not_emptier():
    """End-to-end shape check on the ACTUAL list seen on screen."""
    observed = [
        "Pandas (Software)", "AWS SageMaker", "Applied Machine Learning",
        "Data Handling", "Fine-tuning", "Instruction Tuning", "Domain Adaptation",
        "RAG", "Retrieval-Augmented Generation", "Pre-training",
        "Cloud-based ML Workflows", "Reproducible Pipelines", "AI Training", "LLM'S",
    ]
    out = _det_collapse_acronyms([_strip_possessive(s) for s in observed])
    assert "Retrieval-Augmented Generation" not in out
    assert "LLM'S" not in out and "LLM" in out
    assert "AWS SageMaker" in out and "Applied Machine Learning" in out
    assert len(out) == len(observed) - 1, "exactly one duplicate should be removed"
