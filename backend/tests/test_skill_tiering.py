"""Batch 1.3a (Pillar 1) — evidence-based skill tiering tests.

Covers:
  * SkillEvidence weight arithmetic
  * tier_skills_by_evidence thresholds + stable ordering
  * collect_evidence_from_profile across the 5 CVData source fields
  * keyword_generator integration — user_declared lands in primary,
    lone github_lang lands in tertiary
"""

from __future__ import annotations

from src.services.profile.keyword_generator import generate_search_config
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


# ── keyword_generator integration ───────────────────────────────────


def test_keyword_generator_user_declared_lands_in_primary():
    # Batch 2.3 — skill lists exit the SearchConfig in canonical (lower-case) form.
    prefs = UserPreferences(additional_skills=["Product Strategy"])
    cv = CVData()
    profile = UserProfile(cv_data=cv, preferences=prefs)
    cfg = generate_search_config(profile)
    assert "product strategy" in cfg.primary_skills
    assert "product strategy" not in cfg.secondary_skills
    assert "product strategy" not in cfg.tertiary_skills


def test_keyword_generator_lone_github_lang_lands_in_tertiary():
    # A lone significant GitHub language = github_lang (1.0) → tertiary.
    prefs = UserPreferences()
    cv = CVData(github_languages={"Haskell": 200_000})
    profile = UserProfile(cv_data=cv, preferences=prefs)
    cfg = generate_search_config(profile)
    assert "haskell" in cfg.tertiary_skills
    assert "haskell" not in cfg.primary_skills


def test_keyword_generator_multi_source_skill_lands_in_primary():
    """CV + LinkedIn = 4.0 → primary, beating lone user_declared of a different skill.

    Batch 2.3 — canonical (lower-case) skill assertion.
    """
    prefs = UserPreferences(additional_skills=["Marketing"])
    cv = CVData(skills=["Kubernetes"], linkedin_skills=["Kubernetes"])
    profile = UserProfile(cv_data=cv, preferences=prefs)
    cfg = generate_search_config(profile)
    # Kubernetes (4.0) should be ordered before Marketing (3.0) within primary
    assert cfg.primary_skills.index("kubernetes") < cfg.primary_skills.index("marketing")


def test_keyword_generator_no_skills_yields_empty_tiers():
    profile = UserProfile(cv_data=CVData(), preferences=UserPreferences())
    cfg = generate_search_config(profile)
    assert cfg.primary_skills == []
    assert cfg.secondary_skills == []
    assert cfg.tertiary_skills == []


def test_keyword_generator_excludes_raw_github_frameworks():
    """TRUST fix: raw dependency package names (github_frameworks) are build
    metadata, NOT skills, and must NOT flow into the search config. The meaningful
    frameworks are recovered via the LLM repo-skills pass instead."""
    prefs = UserPreferences()
    cv = CVData(
        github_frameworks=["pytest", "@capacitor/core", "django"],  # raw deps → excluded
        github_llm_skills=["Django", "React"],                       # LLM-read → included
    )
    profile = UserProfile(cv_data=cv, preferences=prefs)
    cfg = generate_search_config(profile)
    all_skills = set(cfg.primary_skills) | set(cfg.secondary_skills) | set(cfg.tertiary_skills)
    assert "pytest" not in all_skills
    assert "@capacitor/core" not in all_skills
    # the LLM-read frameworks ARE present (github_llm = 1.5 → secondary)
    assert {"django", "react"} <= all_skills
