"""Provenance-tracked skill entry + multi-source merge.

Batch 1.4 (Pillar 1). Closes ``pillar_1_report.md`` item #8: every
skill extracted from CV / LinkedIn / GitHub carries an explicit
``source`` and ``confidence`` so downstream (Pillar 2 scoring, UI)
can reason about where a claim came from and which source to trust
when two disagree.

Why a separate dataclass instead of migrating ``CVData.skills`` to
``list[SkillEntry]``: the migration touches storage JSON serde,
keyword_generator, JobScorer, frontend ``types.ts``, and the pipeline
worker. Plan §8 flagged that as high risk. ``SkillEntry`` ships as an
additive structure — callers that want provenance use
``build_skill_entries_from_profile`` and ``merge_skill_entries``; the
existing ``list[str]`` path is untouched. When Batch 1.3b lands ESCO
normalisation it feeds ``esco_uri`` into the same structure.

Source-weight → confidence mapping chosen to mirror ``skill_tiering``
so that a primary-tier skill (weight ≥ 3.0) also shows confidence
≥ 0.85. This keeps the two signals directionally consistent; they
measure different things (strength vs trust) but should rarely
disagree about a skill's importance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

# Source → confidence map. Values anchored to pillar_1_report.md §8 guidance:
#   * ``user_declared``  — explicit self-attestation, highest trust
#   * ``cv_explicit``    — LLM extracted from CV prose; high but not perfect
#   * ``linkedin``       — endorsed on LinkedIn profile (peer-validated)
#   * ``github_dep``     — declared in a dep file (demonstrated usage)
#   * ``github_lang``    — inferred from language bytes in public repos
#   * ``github_topic``   — inferred from repo topic tags (noisiest signal)
SOURCE_CONFIDENCE: dict[str, float] = {
    "user_declared": 1.0,
    "cv_explicit": 0.85,
    "linkedin": 0.9,
    "github_dep": 0.7,
    "github_lang": 0.5,
    "github_topic": 0.4,
}


@dataclass
class SkillEntry:
    """One skill attestation from one source.

    Multiple rows per skill are expected — one per (skill, source).
    ``merge_skill_entries`` collapses them to one row per canonical skill.
    """

    name: str
    source: str
    confidence: float = 0.5
    esco_uri: Optional[str] = None  # populated by Batch 1.3b ESCO normalisation
    last_seen: Optional[str] = None  # ISO-8601 timestamp; None for CV/prefs

    @classmethod
    def from_source(
        cls,
        name: str,
        source: str,
        esco_uri: Optional[str] = None,
        last_seen: Optional[str] = None,
    ) -> "SkillEntry":
        """Construct with confidence derived from the ``SOURCE_CONFIDENCE`` table."""
        conf = SOURCE_CONFIDENCE.get(source, 0.5)
        return cls(
            name=name,
            source=source,
            confidence=conf,
            esco_uri=esco_uri,
            last_seen=last_seen,
        )


def _dedup_key(entry: SkillEntry) -> str:
    """Collision key: prefer ESCO URI (cross-lingual canonical), else name.casefold()."""
    return entry.esco_uri or entry.name.casefold()


def _recency_tiebreaker(entry: SkillEntry) -> float:
    """Return an epoch float used to break ties on equal confidence.

    Missing / unparseable ``last_seen`` falls to 0.0 — i.e. loses any
    tie-break against a timestamped entry. Intentional: "we know when
    it was seen" beats "we don't".
    """
    if not entry.last_seen:
        return 0.0
    try:
        dt = datetime.fromisoformat(entry.last_seen.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def merge_skill_entries(entries: Iterable[SkillEntry]) -> list[SkillEntry]:
    """Collapse multi-source entries to one winning row per skill.

    Winner = highest ``confidence``; tie-break on ``last_seen`` recency.
    The merged row inherits ``name`` and ``esco_uri`` from the winner
    and leaves a breadcrumb: the losing sources are NOT retained in
    this return value — callers that need the full audit trail should
    work with the pre-merge list directly (it's by design a thin
    function, not a reporting layer).

    Insertion order of the returned list matches first-sighting order
    of each dedup key — stable for downstream deterministic tests.
    """
    winners: dict[str, SkillEntry] = {}
    order: list[str] = []
    for entry in entries:
        if not entry.name:
            continue
        key = _dedup_key(entry)
        existing = winners.get(key)
        if existing is None:
            winners[key] = entry
            order.append(key)
            continue
        # Compare: higher confidence wins; on tie, higher recency wins.
        new_score = (entry.confidence, _recency_tiebreaker(entry))
        old_score = (existing.confidence, _recency_tiebreaker(existing))
        if new_score > old_score:
            winners[key] = entry
    return [winners[k] for k in order]


# ── Profile → entries adapter ───────────────────────────────────────


def build_skill_entries_from_profile(profile, last_seen: Optional[str] = None) -> list[SkillEntry]:
    """Walk the 5 source fields on a ``UserProfile`` and emit ``SkillEntry``s.

    Intentionally mirrors ``skill_tiering.collect_evidence_from_profile``
    except it produces the richer ``SkillEntry`` structure with
    ``confidence`` and ``esco_uri`` placeholders. If callers pass
    ``last_seen``, every emitted entry gets stamped with it — useful
    for recency tie-breaks across a multi-profile merge.
    """
    out: list[SkillEntry] = []

    def _add(name: str, source: str) -> None:
        if not isinstance(name, str):
            return
        name = name.strip()
        if not name:
            return
        out.append(SkillEntry.from_source(name, source, last_seen=last_seen))

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

    return out
