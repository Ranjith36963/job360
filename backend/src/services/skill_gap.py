"""Skill-gap analysis — split a job's required skills into matched / missing /
transferable against the user's skills.

Deterministic; rapidfuzz for near-matches (lazy-imported per rules #11/#16). Backs the
job page's Skill Analysis panel, which was an empty shell (nothing computed the split).
This is read-time analysis, not scoring — it does not touch the scorer or enrichment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Optional

# >= this ratio => the user effectively has the skill (e.g. Postgres ~ PostgreSQL).
_FUZZY_MATCH = 88


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def compute_skill_gap(
    user_skills: Optional[Iterable[Optional[str]]],
    required_skills: Optional[Iterable[Optional[str]]],
) -> dict[str, list[str]]:
    """Return ``{"matched": [...], "missing": [...], "transferable": [...]}``.

    - **matched**: required skills the user has (exact case-insensitive OR fuzzy >= 88).
    - **missing**: required skills with no such match.
    - **transferable**: user skills (not already a match) that share a word with a
      missing requirement — "you have something adjacent you can lean on".
    """
    from rapidfuzz import fuzz  # lazy import (rules #11/#16)

    users = [(s or "").strip() for s in (user_skills or []) if (s or "").strip()]
    users_lc = {u.lower(): u for u in users}

    matched: list[str] = []
    missing: list[str] = []
    for req in required_skills or []:
        r = (req or "").strip()
        if not r:
            continue
        rl = r.lower()
        if rl in users_lc or any(fuzz.ratio(rl, u) >= _FUZZY_MATCH for u in users_lc):
            matched.append(r)
        else:
            missing.append(r)

    missing_tokens: set[str] = set()
    for m in missing:
        missing_tokens |= _tokens(m)

    matched_lc = {m.lower() for m in matched}
    transferable: list[str] = []
    for u in users:
        if u.lower() in matched_lc:
            continue
        if _tokens(u) & missing_tokens and u not in transferable:
            transferable.append(u)

    return {"matched": matched, "missing": missing, "transferable": transferable}
