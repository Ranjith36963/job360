"""LLM-curation passes — the reasoning layer that the deterministic + fuzzy
passes can't safely do:

* ``llm_merge_duplicates`` — collapse semantically-duplicate free-text entries
  (a degree or role phrased two different ways across CV + LinkedIn) into ONE
  clean canonical title. Fuzzy string matching can't do this safely (two
  DIFFERENT degrees at the same university look near-identical); an LLM reasons
  about meaning, so it can.
* ``llm_suggest_adjacent_skills`` — propose skills NEIGHBOURING the user's
  (PyTorch → TensorFlow/Keras/JAX). These are SUGGESTIONS the user opts into,
  never auto-counted, so job-matching only ever uses skills the user truly has.

Both are LLM REASONING (rule #28 allows the LLM pass — no hardcoded vocabulary),
both no-op on trivial input, and both degrade gracefully (never lose data / never
raise) so a provider outage can't break extraction.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("job360.profile.llm_curate")

_MERGE_SYSTEM = (
    "You clean up lists extracted from CVs/LinkedIn. You MERGE entries that are "
    "the SAME real-world thing written differently, and you KEEP genuinely "
    "different entries separate. You never invent new entries. Return JSON only."
)

_MERGE_PROMPT = """Below is a list of {kind} entries pulled from a person's CV and
LinkedIn. Some are DUPLICATES of each other — the same {kind} written in different
words (e.g. "Master of Science - AI and Robotics - University of Hertfordshire"
and "Master's degree, AI and Robotics - University of Hertfordshire" are the SAME
degree; "AI/ML Engineer Intern" and "AI / ML intern" are the SAME role).

Merge each set of duplicates into ONE clean, complete, well-worded entry (pick the
clearest form and keep the most useful detail — institution, dates, company).
Keep entries that are genuinely DIFFERENT as separate items. Do NOT invent
anything not implied by the input.

Return JSON: {{"items": ["clean entry 1", "clean entry 2", ...]}}

{kind} entries:
{items}"""

_SUGGEST_SYSTEM = (
    "You are a career skills advisor. Given the skills a person already has, you "
    "suggest a short list of CLOSELY-RELATED, adjacent skills they most likely "
    "also use or should add — same tools/frameworks/domains, one step out. Return "
    "JSON only. Never repeat a skill they already listed."
)

_SUGGEST_PROMPT = """The person already has these skills:
{skills}

Suggest up to {n} ADJACENT skills — closely related tools, frameworks, libraries,
or techniques that naturally sit next to what they already have (e.g. if they have
PyTorch, adjacent = TensorFlow, Keras, JAX; if they have Terraform + Kubernetes,
adjacent = Helm, Docker, Ansible). Only real, specific, named skills. Do NOT
include any skill they already listed.

Return JSON: {{"suggestions": ["Skill A", "Skill B", ...]}}"""


def _clean_list(raw: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for s in raw:
        if isinstance(s, str) and s.strip() and s.strip().lower() not in seen:
            out.append(s.strip())
            seen.add(s.strip().lower())
    return out


async def llm_merge_duplicates(items: list[str], kind: str) -> list[str]:
    """Merge semantic duplicates in ``items`` (a list of degrees or roles) into
    clean canonical entries. No-op on <2 items; returns the ORIGINAL list on any
    failure (never drops data)."""
    cleaned = [it for it in items if it and it.strip()]
    if len(cleaned) < 2:
        return cleaned
    prompt = _MERGE_PROMPT.format(kind=kind, items="\n".join(f"- {c}" for c in cleaned))
    try:
        from src.services.profile.llm_provider import llm_extract  # noqa: PLC0415

        result = await llm_extract(prompt, system=_MERGE_SYSTEM)
    except Exception as e:  # noqa: BLE001 — never break extraction
        logger.warning("llm_merge_duplicates(%s) failed, keeping original: %s", kind, e)
        return cleaned
    merged = _clean_list(result.get("items") if isinstance(result, dict) else None)
    # Safety: the merge must not blow the list up or empty it — if the model
    # returned nothing usable, or MORE than we sent, keep the original.
    if not merged or len(merged) > len(cleaned):
        return cleaned
    return merged


async def llm_suggest_adjacent_skills(skills: list[str], n: int = 12) -> list[str]:
    """Suggest up to ``n`` skills adjacent to ``skills`` — filtered to exclude any
    the user already has. Empty input → [] without an LLM call. Never raises."""
    have = [s for s in skills if s and s.strip()]
    if not have:
        return []
    held = {s.strip().lower() for s in have}
    prompt = _SUGGEST_PROMPT.format(skills=", ".join(have[:120]), n=n)
    try:
        from src.services.profile.llm_provider import llm_extract  # noqa: PLC0415

        result = await llm_extract(prompt, system=_SUGGEST_SYSTEM)
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_suggest_adjacent_skills failed: %s", e)
        return []
    raw = result.get("suggestions") if isinstance(result, dict) else None
    return [s for s in _clean_list(raw) if s.lower() not in held][:n]
