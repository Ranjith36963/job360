"""Line-level provenance: mark which output lines are the user's OWN facts (grounded
in their profile) vs lines copied from the job ad vs lines the AI added or reshaped.

Deterministic, no LLM — so "these are your real facts" is a *fact*, not another model
opinion. Backs the "highlight facts before download" feature: the user sees, per line,
what came from their profile vs the ad vs the AI, before they trust and send it.

A posting can give employer context but never establishes a candidate fact, so the
ad is a SEPARATE source: it can label a line ``from_ad`` but never ``grounded``.
"""

from __future__ import annotations

import re
from typing import Any

_WORD = re.compile(r"[A-Za-z0-9]+")
# >= this share of a line's words appearing in a source => the line comes from it.
_GROUND_THRESHOLD = 0.6

SOURCE_PROFILE = "profile"  # grounded: the user's own CV / LinkedIn / GitHub / profile fields
SOURCE_AD = "ad"  # reaches the threshold only against the job ad: not the user's fact
SOURCE_NONE = "none"  # in neither: added by the AI


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text or "")}


def _overlap(line_tokens: set[str], source: set[str]) -> float:
    return len(line_tokens & source) / len(line_tokens) if source else 0.0


def annotate_provenance(
    output_text: str, profile_text: str, ad_text: str = ""
) -> list[dict[str, Any]]:
    """Split ``output_text`` into lines; label each profile / ad / none.

    ``profile_text`` is the user's OWN material (see ``cv_checks.profile_source_text``);
    ``ad_text`` is the job description. A line is ``grounded`` (``source="profile"``)
    when at least ``_GROUND_THRESHOLD`` of its words also appear in the profile, even if
    the ad has them too. Otherwise, if it reaches the threshold against the ad, it is
    ``source="ad"`` with ``grounded=False``. Otherwise ``source="none"``, ``grounded=False``.
    Blank lines are neutral (``grounded=True``, ``source="profile"``).
    Returns one ``{"text", "grounded", "source"}`` per line, preserving text verbatim.
    """
    profile = _tokens(profile_text)
    ad = _tokens(ad_text)
    segments: list[dict[str, Any]] = []
    for line in (output_text or "").split("\n"):
        line_tokens = _tokens(line)
        if not line_tokens:
            segments.append({"text": line, "grounded": True, "source": SOURCE_PROFILE})  # blank
        elif _overlap(line_tokens, profile) >= _GROUND_THRESHOLD:
            segments.append({"text": line, "grounded": True, "source": SOURCE_PROFILE})
        elif _overlap(line_tokens, ad) >= _GROUND_THRESHOLD:
            segments.append({"text": line, "grounded": False, "source": SOURCE_AD})
        else:
            segments.append({"text": line, "grounded": False, "source": SOURCE_NONE})
    return segments
