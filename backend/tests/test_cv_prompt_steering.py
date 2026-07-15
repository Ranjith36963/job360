"""Guards for the CV LLM prompt's prose-mining steering.

Diagnosis (docs/PILLAR1_DEEP_DIAGNOSIS.md): with gpt-4o-mini the LLM pass
anchored on the explicit "Skills" section and returned essentially the same list
the deterministic pass already had — leaving concept-skills (techniques, methods,
architectures) that live ONLY in project/experience prose unextracted
(pavan 69%, sofia 62% recall). The fix is prompt-steering (allowed by rule #28):
explicitly direct the model to mine the prose. These tests guard that steering so
it can't be silently dropped. They assert PROMPT CONTENT, not LLM behaviour
(behaviour is validated by the live eval).
"""

import re

from src.services.profile import cv_parser


def _norm(s: str) -> str:
    """Collapse whitespace so line-wrapping in the prompt can't hide a phrase."""
    return re.sub(r"\s+", " ", s).lower()


def test_cv_prompt_steers_prose_mining_of_concept_skills():
    p = _norm(cv_parser._CV_PROMPT)
    # Directs the model at project/experience prose, not just the skills list.
    assert "mine the prose" in p
    # A skill counts even when it never appears in the skills section.
    assert "do not limit skills" in p


def test_cv_prompt_prose_rule_is_domain_agnostic_not_a_keyword_list():
    """Rule #28 guard: the steering uses ILLUSTRATIVE examples across domains, it
    must not become a fixed enumerated skill vocabulary. We assert the rule frames
    examples as 'e.g.' rather than a closed checklist."""
    p = _norm(cv_parser._CV_PROMPT)
    assert "technique" in p and "method" in p
    assert "e.g." in p  # examples are illustrative, not an exhaustive list
