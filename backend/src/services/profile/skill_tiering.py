"""Evidence-based skill tiering.

Batch 1.3a (Pillar 1). Replaces the naive position-based thirds split
in ``keyword_generator.py:67-75`` with tiering driven by *where the
skill came from* and *how many sources mention it*.

Rationale from ``pillar_1_report.md`` item #1: slicing an insertion-
ordered skill list into thirds means whichever source happens to be
added first dominates the primary tier — regardless of whether those
skills are the user's strongest signal. The report's recommendation
is to tier by evidence (ESCO-essential / frequency / recency), which
produces demonstrably better matching on the 3 target occupations in
the report's §10 acceptance criterion.

This module ships the **frequency + source-confidence** half of that
idea. ESCO semantic normalisation is Batch 1.3b — it will feed the
same ``SkillEvidence`` stream into this tiering logic, so callers do
not need to change between 1.3a and 1.3b.

Weight semantics (ranked per the report's §"Absorbing multi-source
signal" guidance):

| Source          | Weight | Rationale                                     |
|---              |---     |---                                            |
| ``user_declared``    | 3.0  | Explicit self-attestation — highest trust     |
| ``cv_explicit``      | 2.0  | LLM-extracted from CV prose; high-confidence |
| ``linkedin``         | 2.0  | Endorsed / self-listed on LinkedIn profile    |
| ``github_dep``       | 1.5  | Declared in a dep file — demonstrated usage   |
| ``github_lang``      | 1.0  | Inferred from language bytes in public repos  |

Primary tier: total weight ≥ 3.0 (either user-declared alone, or any
combination of ≥2 non-user sources — e.g. cv_explicit + linkedin).
Secondary: 1.5–3.0. Tertiary: < 1.5. Thresholds were tuned against
the 3 report-target occupations (software dev, nurse practitioner,
financial analyst) to avoid over-packing the primary tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# Source identifier literals. Kept loose (plain strings) rather than
# enums so future additions (``github_topic``, ``esco_expansion``) can
# land without a schema migration on the CVData boundary.
SourceName = str

_SOURCE_WEIGHTS: dict[SourceName, float] = {
    "user_declared": 3.0,
    "cv_explicit": 2.0,
    "linkedin": 2.0,
    # Two-pass LLM enhance sources. ``about_me_llm`` = skills the LLM mined
    # from the user's own free-text blurb (the user's own words → trust on a
    # par with an explicit CV mention). ``github_llm`` = skills the LLM read
    # off repo prose (demonstrated usage but inferred → same trust as a
    # declared dependency).
    "about_me_llm": 2.0,
    "github_dep": 1.5,
    "github_llm": 1.5,
    "github_lang": 1.0,
}

PRIMARY_THRESHOLD = 3.0
SECONDARY_THRESHOLD = 1.5


@dataclass
class SkillEvidence:
    """Aggregated evidence for a single skill across the profile's sources.

    ``sources`` is order-insensitive (dedup by set semantics in
    ``weight``). ``name`` preserves the original casing of the first
    sighting — downstream SearchConfig serialisation does not
    lower-case.
    """

    name: str
    sources: list[SourceName] = field(default_factory=list)

    @property
    def weight(self) -> float:
        return sum(_SOURCE_WEIGHTS.get(s, 0.0) for s in set(self.sources))


def tier_skills_by_evidence(
    evidence: Iterable[SkillEvidence],
) -> tuple[list[str], list[str], list[str]]:
    """Split an evidence stream into ``(primary, secondary, tertiary)``.

    Ordering within each tier is **(weight desc, insertion order asc)**
    — higher-evidence skills come first, and ties fall back to the
    order the caller built the evidence list (typically source-order).
    Stable enough for deterministic tests and intuitively matches what
    a human would call "more certain" skills.
    """
    ev_list = list(evidence)
    # enumerate before sort to preserve insertion order on ties
    indexed = [(i, e) for i, e in enumerate(ev_list)]
    indexed.sort(key=lambda pair: (-pair[1].weight, pair[0]))

    primary: list[str] = []
    secondary: list[str] = []
    tertiary: list[str] = []
    for _, e in indexed:
        if e.weight >= PRIMARY_THRESHOLD:
            primary.append(e.name)
        elif e.weight >= SECONDARY_THRESHOLD:
            secondary.append(e.name)
        else:
            tertiary.append(e.name)
    return primary, secondary, tertiary


def collect_evidence_from_profile(profile) -> list[SkillEvidence]:
    """Walk the five known skill fields and build ``SkillEvidence`` rows.

    Dedup key is ``name.casefold()``. The first sighting wins for the
    display casing; subsequent sources append to the same row's
    ``sources`` list. Empty strings are silently skipped.

    Accepts a ``UserProfile`` duck-typed object (we import the type
    lazily to avoid a cycle with ``models.py`` → ``schemas.py``).
    """
    evidence: dict[str, SkillEvidence] = {}

    def _add(name: str, source: SourceName) -> None:
        if not name or not isinstance(name, str):
            return
        name = name.strip()
        if not name:
            return
        key = name.casefold()
        if key not in evidence:
            evidence[key] = SkillEvidence(name=name)
        if source not in evidence[key].sources:
            evidence[key].sources.append(source)

    prefs = getattr(profile, "preferences", None)
    cv = getattr(profile, "cv_data", None)

    if prefs is not None:
        for s in getattr(prefs, "additional_skills", []) or []:
            _add(s, "user_declared")

    if cv is not None:
        for s in getattr(cv, "skills", []) or []:
            _add(s, "cv_explicit")
        for s in getattr(cv, "linkedin_skills", []) or []:
            _add(s, "linkedin")
        for s in getattr(cv, "github_frameworks", []) or []:
            _add(s, "github_dep")
        for s in getattr(cv, "github_skills_inferred", []) or []:
            _add(s, "github_lang")
        # Two-pass LLM enhance sources.
        for s in getattr(cv, "github_llm_skills", []) or []:
            _add(s, "github_llm")
        for s in getattr(cv, "about_me_inferred_skills", []) or []:
            _add(s, "about_me_llm")

    return list(evidence.values())
