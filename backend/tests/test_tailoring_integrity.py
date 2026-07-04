"""Proper-noun integrity pass — guardrail #2 deterministic enforcement.

Covers the real bug (model wrote "Monox" for "Monzo") and the two safety
properties: a correct name is never touched, and an unrelated real word is
never rewritten into a source name.
"""

import pytest

from src.services.tailoring.generator import generate_document
from src.services.tailoring.integrity import proper_nouns, repair_proper_nouns


def test_typo_of_source_name_is_autocorrected():
    # The exact bug from the live run: "Monzo" -> "Monox".
    out, flagged = repair_proper_nouns(
        "Payments engineer at Monox, building on AWS.",
        "Software engineer at Monzo Bank. Skills: Python, AWS.",
    )
    assert "Monzo" in out
    assert "Monox" not in out
    assert flagged == []


def test_correct_name_left_untouched():
    out, flagged = repair_proper_nouns(
        "I worked at Monzo on payments.",
        "Monzo Bank, London. Python, AWS.",
    )
    assert out == "I worked at Monzo on payments."
    assert flagged == []


def test_unrelated_real_word_not_corrupted():
    # "Manager" must NEVER be rewritten into the source name "Monzo".
    out, _flagged = repair_proper_nouns("Senior Manager role.", "Monzo Bank. Python.")
    assert "Manager" in out
    assert "Monzo" not in out


def test_fabricated_brand_is_flagged():
    # A camelCase brand that resembles nothing in the source -> surfaced for review.
    out, flagged = repair_proper_nouns(
        "Delivered the project on AcmeCloud.",
        "Experience: Python, AWS, Monzo.",
    )
    assert "AcmeCloud" in flagged
    assert out == "Delivered the project on AcmeCloud."  # not silently changed


def test_no_source_nouns_is_a_noop():
    out, flagged = repair_proper_nouns("Anything here Monox.", "")
    assert out == "Anything here Monox."
    assert flagged == []


def test_proper_nouns_extractor():
    got = proper_nouns("Worked at Monzo and GitHub using AWS and python.")
    assert {"Monzo", "GitHub", "AWS"} <= got
    assert "python" not in got  # lowercase -> not a proper noun


@pytest.mark.asyncio
async def test_generator_applies_the_repair():
    """End-to-end through the generator: a stubbed LLM emits the typo,
    the returned document has it corrected."""

    async def fake_llm(_prompt, _system):
        return {"document": "Backend engineer at Monox. Python, AWS."}

    doc = await generate_document(
        doc_kind="cv",
        cv_text="Backend engineer at Monzo. Python, AWS.",
        job_title="Backend Engineer",
        company="Monzo",
        job_description="Build payments infrastructure at Monzo.",
        llm_extract_fn=fake_llm,
    )
    assert "Monzo" in doc.document
    assert "Monox" not in doc.document
    assert doc.flagged_terms == []
