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

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Structural skill-shape guard (rule #28: shape, NOT a skill vocabulary). A skill
# is a short noun phrase — not a sentence, a date, or a section header. This is
# the last line of defence so junk from ANY source (a mis-parsed CV line, a stray
# LLM output) never reaches the tiers or search.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_SECTION_HEADERS = {
    "professional experience", "work experience", "experience", "employment",
    "education", "professional summary", "summary", "certifications",
    "licenses & certifications", "projects", "achievements", "awards",
    "publications", "references", "interests", "contact", "profile",
}


def significant_github_languages(languages: dict[str, Any]) -> list[str]:
    """The user's real programming languages from the GitHub language-byte map.

    GitHub reports config-file 'languages' (Makefile, Dockerfile, Procfile,
    Batchfile, PowerShell, Just…) alongside real ones, but those are always a
    tiny fraction of the bytes. Keep the top few by bytes plus anything ≥0.5% of
    the total — that drops the config-file noise structurally (by proportion, not
    a name denylist: rule #28) while keeping real secondary languages."""
    if not isinstance(languages, dict) or not languages:
        return []
    ranked = sorted(
        ((k, v) for k, v in languages.items()
         if isinstance(k, str) and isinstance(v, (int, float)) and v > 0),
        key=lambda kv: -kv[1],
    )
    total = sum(v for _, v in ranked) or 1
    out: list[str] = []
    for i, (lang, byts) in enumerate(ranked):
        # The #1 language always shows; everything else must be ≥0.5% of bytes.
        if i == 0 or byts / total >= 0.005:
            out.append(lang)
    return out


def _acronym_of(short: str, long: str) -> bool:
    """True if ``short`` is the initialism of the multi-word ``long`` — 'RAG' of
    'Retrieval Augmented Generation', 'NLP' of 'Natural Language Processing'. A
    general algorithm (first letters), NOT a synonym vocabulary (rule #28).

    Guards keep it safe for ALL profiles: the acronym is 3-6 alphanumerics (2-char
    ones like 'AI'/'ML' are too ambiguous — 'AI' also initials 'Adobe
    Illustrator'), and the expansion is a 2-6 word phrase whose word-initials
    match EXACTLY. So 'RAG Pipelines' (initials 'RP') and 'Go' vs 'Google' never
    merge."""
    s = re.sub(r"[^a-z0-9]", "", short.lower())
    if not (3 <= len(s) <= 6):
        return False
    words = [w for w in long.split() if w and w[0].isalnum()]
    if not (2 <= len(words) <= 6):
        return False
    return s == "".join(w[0].lower() for w in words)


def _merge_acronym_expansions(ev_list: list[SkillEvidence]) -> list[SkillEvidence]:
    """Collapse acronym↔expansion pairs, keeping the acronym form and unioning
    sources. Order-preserving; only exact initialism pairs merge."""
    consumed: set[int] = set()
    for i in range(len(ev_list)):
        if i in consumed:
            continue
        for j in range(len(ev_list)):
            if i == j or j in consumed or i in consumed:
                continue
            a, b = ev_list[i], ev_list[j]
            if _acronym_of(a.name, b.name):        # a = acronym, b = expansion
                a.sources = list(dict.fromkeys(a.sources + b.sources))
                consumed.add(j)
            elif _acronym_of(b.name, a.name):      # b = acronym, a = expansion
                b.sources = list(dict.fromkeys(b.sources + a.sources))
                consumed.add(i)
    return [e for k, e in enumerate(ev_list) if k not in consumed]


def _looks_like_skill(name: str) -> bool:
    """True if ``name`` is skill-shaped: a short phrase, not prose/date/header."""
    s = name.strip()
    if not s:
        return False
    if s.endswith("."):                       # a sentence, not a skill
        return False
    if len(s) > 50 or len(s.split()) > 6:      # prose, not a skill
        return False
    if _YEAR_RE.search(s):                     # a date range, not a skill
        return False
    if s.lower() in _SECTION_HEADERS:          # a CV section header, not a skill
        return False
    return True

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


def collect_evidence_from_profile(profile: Any) -> list[SkillEvidence]:
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
        if not name or not _looks_like_skill(name):
            return
        # Whitespace-insensitive identity so "Chroma DB" and "ChromaDB" (and
        # "MLOps" / "ML Ops") collapse to one skill instead of showing twice.
        key = re.sub(r"\s+", "", name).casefold()
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
        # GitHub contributes ONLY: (1) significant programming languages (byte-
        # thresholded) and (2) the LLM-read repo skills. Raw dependency package
        # names (github_frameworks: pytest, @capacitor/core, workbox-window) and
        # raw repo topics (github_topics: gmail, slack, hitl) are build metadata,
        # NOT user skills — surfacing them floods the profile and destroys trust.
        # The meaningful frameworks (React, FastAPI) are recovered by the LLM pass.
        for s in significant_github_languages(getattr(cv, "github_languages", {}) or {}):
            _add(s, "github_lang")
        # Two-pass LLM enhance sources.
        for s in getattr(cv, "github_llm_skills", []) or []:
            _add(s, "github_llm")
        for s in getattr(cv, "about_me_inferred_skills", []) or []:
            _add(s, "about_me_llm")

    # Collapse acronym↔expansion pairs (RAG ⇄ Retrieval Augmented Generation) so
    # the same skill isn't shown under two names.
    return _merge_acronym_expansions(list(evidence.values()))
