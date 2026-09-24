"""The fit picture on the application page (owner ask, 2026-09-20).

Job360 draws two things it ALREADY stores side by side — the agent's fit
verdict (``save_fit``) and which of the candidate's own profile skills appear
in the ad text as it read the day it was brought. Nothing here judges,
scores or ranks (VISION rule 4): "skill X is a word in this ad" is a fact
about two stored strings, not an opinion about the candidate, and the skill
list is the candidate's data (rule 28: no keyword list of ours).
"""
from __future__ import annotations

import re
from typing import Any

from src.core import settings


def _skill_pattern(skill: str) -> re.Pattern[str]:
    """Whole-token match, case-insensitive. Word boundaries fail on skills
    that start or end with a non-word character ("C++", ".NET", "Node.js"),
    so the edges use lookarounds on whitespace / punctuation instead."""
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(skill.strip()) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def skills_in_text(skills: list[str], text: str) -> tuple[list[str], list[str]]:
    """``(found, not_found)`` — the candidate's skills that occur in ``text``
    as whole tokens, and those that do not. Order follows ``skills``;
    duplicates (case-insensitive) collapse to the first spelling; blanks and
    one-character entries are skipped (a lone "R" or "C" matches prose)."""
    seen: set[str] = set()
    found: list[str] = []
    missing: list[str] = []
    for raw in skills:
        skill = (raw or "").strip()
        key = skill.lower()
        if len(skill) < 2 or key in seen:
            continue
        seen.add(key)
        (found if _skill_pattern(skill).search(text) else missing).append(skill)
        if len(found) + len(missing) >= settings.ALIGNMENT_MAX_SKILLS:
            break
    return found, missing


def candidate_skills(profile: Any) -> list[str]:
    """The candidate's skills — THE one list every surface shows
    (``skill_tiering.profile_skills``): CV, LinkedIn, significant GitHub
    languages and the ones the user typed, deduped, minus the user's own
    ``excluded_skills``. So "N of M of your skills" uses the same M as the
    profile page and ``get_profile``."""
    from src.services.profile.skill_tiering import profile_skills  # noqa: PLC0415 — rule #16

    return [row["name"] for row in profile_skills(profile)]
