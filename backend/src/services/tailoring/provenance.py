"""Line-level provenance: mark which output lines are the user's OWN facts (grounded
in the source CV/job text) vs lines the AI added or reshaped.

Deterministic, no LLM — so "these are your real facts" is a *fact*, not another model
opinion. Backs the "highlight facts before download" feature: the user sees, per line,
what came from their CV vs what the AI wrote, before they trust and send it.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9]+")
# >= this share of a line's words appearing in the source => it's grounded (your fact).
_GROUND_THRESHOLD = 0.6


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text or "")}


def annotate_provenance(output_text: str, source_text: str) -> list[dict]:
    """Split ``output_text`` into lines; label each grounded (your fact) or AI-added.

    A line is ``grounded`` when at least ``_GROUND_THRESHOLD`` of its content words also
    appear in the source (your CV + the job). Blank lines are neutral (grounded). With
    no source at all, non-blank lines can't be verified, so they're marked AI-added.
    Returns one ``{"text": str, "grounded": bool}`` per line, preserving text verbatim.
    """
    source = _tokens(source_text)
    segments: list[dict] = []
    for line in (output_text or "").split("\n"):
        line_tokens = _tokens(line)
        if not line_tokens:
            segments.append({"text": line, "grounded": True})  # blank / structural
            continue
        if not source:
            segments.append({"text": line, "grounded": False})  # nothing to verify against
            continue
        overlap = len(line_tokens & source) / len(line_tokens)
        segments.append({"text": line, "grounded": overlap >= _GROUND_THRESHOLD})
    return segments
