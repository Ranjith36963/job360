"""Batch 1.3a (Pillar 1) — evidence-based skill tiering tests.

Covers:
  * SkillEvidence weight arithmetic
  * tier_skills_by_evidence thresholds + stable ordering
  * collect_evidence_from_profile across the 5 CVData source fields
"""

from __future__ import annotations

from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.skill_tiering import (
    PRIMARY_THRESHOLD,
    SECONDARY_THRESHOLD,
    SkillEvidence,
    collect_evidence_from_profile,
    tier_skills_by_evidence,
)


def test_github_raw_deps_topics_and_filetypes_do_not_become_skills():
    """TRUST: the live profile page was flooded with raw GitHub build-metadata —
    dependency package names (pytest, @capacitor/core, workbox-window), repo
    topics (gmail, slack, hitl), and config-file 'languages' (Makefile, Procfile,
    Batchfile). None are skills. The tiers must use SIGNIFICANT languages +
    LLM-read repo skills only. Source-quality decision, not a keyword denylist
    (rule #28)."""
    cv = CVData(
        github_frameworks=["pytest", "@capacitor/core", "ruff", "FastAPI"],  # raw deps
        github_topics=["gmail", "slack", "hitl"],                             # raw topics
        github_languages={"Python": 1_000_000, "TypeScript": 800_000,
                          "Makefile": 500, "Procfile": 150, "Dockerfile": 300},
        github_skills_inferred=["Python", "Makefile", "gmail", "Procfile"],   # raw langs+topics
        github_llm_skills=["React", "RAG"],                                   # clean LLM
    )
    profile = UserProfile(cv_data=cv, preferences=UserPreferences())
    names = {e.name for e in collect_evidence_from_profile(profile)}
    # significant languages survive
    assert "Python" in names and "TypeScript" in names
    # LLM-read repo skills survive
    assert "React" in names and "RAG" in names
    # raw dependency names are GONE
    assert "pytest" not in names and "ruff" not in names
    assert "@capacitor/core" not in names
    # raw topics are GONE
    assert "gmail" not in names and "hitl" not in names
    # config-file 'languages' are GONE
    assert "Makefile" not in names and "Procfile" not in names and "Dockerfile" not in names


def test_collect_evidence_drops_non_skill_shaped_junk():
    """Defense-in-depth: whatever the source, a token that is a sentence, a date,
    or a section header is NOT a skill and must never enter the evidence stream
    (and thus never the tiers or search). Structural shape check, not a keyword
    list (rule #28)."""
    profile = UserProfile(
        cv_data=CVData(
            skills=[
                "Python",                      # keep
                "CI/CD Pipelines",             # keep (compound)
                "Accelerated data retrieval performance through Redis caching.",  # sentence
                "June 2025 – September 2025",  # date range
                "PROFESSIONAL EXPERIENCE",     # section header
                "Calnex Solutions",            # (kept — can't tell a company from a skill structurally)
            ]
        ),
        preferences=UserPreferences(),
    )
    names = {e.name for e in collect_evidence_from_profile(profile)}
    assert "Python" in names
    assert "CI/CD Pipelines" in names
    assert not any(n.endswith(".") for n in names)         # no sentences
    assert "June 2025 – September 2025" not in names        # no dates
    assert "PROFESSIONAL EXPERIENCE" not in names           # no section headers


# ── SkillEvidence.weight ────────────────────────────────────────────


def test_weight_single_source_user_declared():
    ev = SkillEvidence(name="Python", sources=["user_declared"])
    assert ev.weight == 3.0


def test_weight_single_source_cv_explicit():
    ev = SkillEvidence(name="Docker", sources=["cv_explicit"])
    assert ev.weight == 2.0


def test_weight_multi_source_sums():
    ev = SkillEvidence(name="FastAPI", sources=["cv_explicit", "linkedin"])
    assert ev.weight == 4.0


def test_weight_dedupes_duplicate_sources():
    """Same source counted twice must not inflate weight — evidence is set-like."""
    ev = SkillEvidence(name="Rust", sources=["github_lang", "github_lang"])
    assert ev.weight == 1.0


def test_weight_unknown_source_ignored():
    ev = SkillEvidence(name="X", sources=["linkedin", "nonsense_future_source"])
    assert ev.weight == 2.0


# ── tier_skills_by_evidence ─────────────────────────────────────────


def test_tier_primary_requires_threshold():
    """Exactly meeting PRIMARY_THRESHOLD must tier primary (>= not >)."""
    ev = [SkillEvidence(name="A", sources=["user_declared"])]  # 3.0
    p, s, t = tier_skills_by_evidence(ev)
    assert p == ["A"] and s == [] and t == []


def test_tier_secondary_band():
    ev = [
        SkillEvidence(name="A", sources=["cv_explicit"]),   # 2.0
        SkillEvidence(name="B", sources=["github_dep"]),     # 1.5
        SkillEvidence(name="C", sources=["github_lang"]),    # 1.0
    ]
    p, s, t = tier_skills_by_evidence(ev)
    assert p == []
    assert set(s) == {"A", "B"}
    assert t == ["C"]


def test_tier_multi_source_promotes_to_primary():
    """cv_explicit (2.0) + linkedin (2.0) = 4.0 → primary."""
    ev = [SkillEvidence(name="FastAPI", sources=["cv_explicit", "linkedin"])]
    p, _, _ = tier_skills_by_evidence(ev)
    assert p == ["FastAPI"]


def test_tier_preserves_insertion_order_on_equal_weight():
    ev = [
        SkillEvidence(name="Rust", sources=["github_lang"]),
        SkillEvidence(name="Scala", sources=["github_lang"]),
    ]
    _, _, tertiary = tier_skills_by_evidence(ev)
    assert tertiary == ["Rust", "Scala"]


def test_tier_sorts_by_weight_descending_across_tiers():
    ev = [
        SkillEvidence(name="Low", sources=["github_lang"]),           # 1.0
        SkillEvidence(name="High", sources=["user_declared"]),        # 3.0
        SkillEvidence(name="Mid", sources=["cv_explicit"]),           # 2.0
    ]
    p, s, t = tier_skills_by_evidence(ev)
    assert p == ["High"]
    assert s == ["Mid"]
    assert t == ["Low"]


def test_tier_empty_input_returns_three_empty_lists():
    p, s, t = tier_skills_by_evidence([])
    assert (p, s, t) == ([], [], [])


def test_thresholds_are_exported():
    """Thresholds must stay importable — downstream (Batch 1.4) will use them."""
    assert PRIMARY_THRESHOLD == 3.0
    assert SECONDARY_THRESHOLD == 1.5


# ── collect_evidence_from_profile ───────────────────────────────────


def test_collect_evidence_merges_sources_on_same_skill():
    prefs = UserPreferences(additional_skills=["Python"])
    cv = CVData(
        skills=["Python", "Docker"],
        linkedin_skills=["Python"],
        github_languages={"Rust": 500_000},   # significant language → github_lang
        github_llm_skills=["React"],           # LLM-read repo skill → github_llm
    )
    profile = UserProfile(cv_data=cv, preferences=prefs)

    evidence = collect_evidence_from_profile(profile)
    by_name = {e.name.casefold(): e for e in evidence}

    # Python appears in user_declared + cv_explicit + linkedin — all three
    assert set(by_name["python"].sources) == {"user_declared", "cv_explicit", "linkedin"}
    assert by_name["docker"].sources == ["cv_explicit"]
    assert by_name["rust"].sources == ["github_lang"]
    assert by_name["react"].sources == ["github_llm"]


def test_collect_evidence_merges_acronym_with_expansion():
    """GENERAL (all profiles): an acronym and its spelled-out expansion are the
    same skill — 'RAG' ⇄ 'Retrieval Augmented Generation', 'NLP' ⇄ 'Natural
    Language Processing'. Merge via an initials-matching ALGORITHM (keeps the
    acronym form), not a hardcoded synonym map (rule #28)."""
    prefs = UserPreferences()
    cv = CVData(
        skills=["RAG", "Retrieval Augmented Generation", "NLP", "Natural Language Processing"],
    )
    profile = UserProfile(cv_data=cv, preferences=prefs)
    names = [e.name for e in collect_evidence_from_profile(profile)]
    assert "RAG" in names
    assert "Retrieval Augmented Generation" not in names   # merged into RAG
    assert "NLP" in names
    assert "Natural Language Processing" not in names

    def _count(n):
        return sum(1 for x in names if x == n)
    assert _count("RAG") == 1 and _count("NLP") == 1


def test_acronym_merge_does_not_touch_distinct_or_more_specific_skills():
    """GUARD (must hold for all profiles): only a TRUE acronym↔expansion pair
    merges. A more specific compound ('RAG Pipelines') and unrelated skills stay
    separate; a coincidental leading-letter match ('Go' vs 'Google') does not
    merge."""
    prefs = UserPreferences()
    cv = CVData(skills=["RAG", "RAG Pipelines", "Go", "Google", "Python", "Java"])
    profile = UserProfile(cv_data=cv, preferences=prefs)
    names = {e.name for e in collect_evidence_from_profile(profile)}
    assert "RAG" in names and "RAG Pipelines" in names   # distinct granularity
    assert "Go" in names and "Google" in names            # not an acronym match
    assert "Python" in names and "Java" in names


def test_collect_evidence_dedups_spacing_variants():
    """TRUST: 'ChromaDB' and 'Chroma DB' both showed as separate skills — a pure
    spacing difference. Skill identity ignores whitespace (and case), so they
    collapse to one entry carrying both sources."""
    prefs = UserPreferences()
    cv = CVData(skills=["ChromaDB"], linkedin_skills=["Chroma DB"])
    profile = UserProfile(cv_data=cv, preferences=prefs)
    ev = collect_evidence_from_profile(profile)
    names = [e.name for e in ev]
    assert len(names) == 1
    # both sources merged onto the single entry
    assert set(ev[0].sources) == {"cv_explicit", "linkedin"}


def test_collect_evidence_first_sighting_casing_wins():
    """If ``Python`` comes before ``python``, the first casing is kept."""
    prefs = UserPreferences(additional_skills=["Python"])
    cv = CVData(skills=["python"])  # lowercase — should dedup onto Python
    profile = UserProfile(cv_data=cv, preferences=prefs)
    names = [e.name for e in collect_evidence_from_profile(profile)]
    assert names == ["Python"]


def test_collect_evidence_ignores_empty_strings():
    prefs = UserPreferences(additional_skills=["", "   "])
    cv = CVData(skills=["Real Skill"])
    profile = UserProfile(cv_data=cv, preferences=prefs)
    names = [e.name for e in collect_evidence_from_profile(profile)]
    assert names == ["Real Skill"]
