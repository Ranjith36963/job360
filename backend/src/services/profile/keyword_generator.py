"""Convert a UserProfile into a SearchConfig for dynamic keyword-driven search."""

from __future__ import annotations

import re
from src.core.keywords import VISA_KEYWORDS, LOCATIONS
from src.core.skill_synonyms import canonicalize_skill
from src.services.profile.models import SearchConfig, UserProfile
from src.services.profile.skill_tiering import (
    collect_evidence_from_profile,
    tier_skills_by_evidence,
)


def _canonicalize_skill_list(skills: list[str]) -> list[str]:
    """Pillar 2 Batch 2.3 — collapse a skill list to canonical forms while
    preserving order and dedup. E.g. ['JS', 'JavaScript'] → ['javascript'],
    ['K8S'] → ['kubernetes']. Unknown entries pass through with case/whitespace
    normalisation only, so domain-specific CV terms aren't discarded."""
    out: list[str] = []
    seen: set[str] = set()
    for s in skills:
        canonical = canonicalize_skill(s)
        if canonical and canonical not in seen:
            out.append(canonical)
            seen.add(canonical)
    return out


# Words to ignore when building relevance keywords
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "to", "for", "with", "on",
    "at", "by", "is", "it", "as", "be", "was", "are", "from", "that",
    "this", "have", "has", "had", "not", "but", "its", "can", "will",
    "do", "does", "did",
}

# Common role words that support but don't define a domain
_ROLE_WORDS = {
    "engineer", "developer", "architect", "analyst", "consultant",
    "manager", "specialist", "lead", "head", "director", "scientist",
    "researcher", "designer", "coordinator", "administrator", "officer",
    "technician", "associate", "assistant", "intern", "trainee",
}


def generate_search_config(profile: UserProfile) -> SearchConfig:
    """Generate a SearchConfig from a UserProfile."""
    prefs = profile.preferences
    cv = profile.cv_data

    # --- Job titles ---
    titles = list(prefs.target_job_titles)
    seen = {t.lower() for t in titles}
    for t in cv.job_titles:
        if t.lower() not in seen:
            titles.append(t)
            seen.add(t.lower())

    # LinkedIn position titles
    for pos in cv.linkedin_positions:
        title = pos.get("title", "")
        if title and title.lower() not in seen:
            titles.append(title)
            seen.add(title.lower())

    # --- Skills (Batch 1.3a — evidence-based tiering by source + frequency) ---
    # Replaces the naive position-based thirds split (pillar_1_report #1).
    # Primary tier requires either a user-declared skill OR ≥2 supporting
    # sources (e.g. cv_explicit + linkedin). See ``skill_tiering`` for the
    # weight table. ``all_skills`` is still emitted for the relevance
    # keyword build below — it's the full deduped union.
    evidence = collect_evidence_from_profile(profile)
    primary, secondary, tertiary = tier_skills_by_evidence(evidence)
    all_skills = [e.name for e in evidence]

    # --- Relevance keywords ---
    rel_set: set[str] = set()
    for title in titles:
        for word in re.findall(r'\w+', title.lower()):
            if word not in _STOPWORDS and len(word) > 1:
                rel_set.add(word)
    for skill in all_skills:
        rel_set.add(skill.lower())

    # LinkedIn industry words
    if cv.linkedin_industry:
        for word in re.findall(r'\w+', cv.linkedin_industry.lower()):
            if word not in _STOPWORDS and len(word) > 1:
                rel_set.add(word)

    relevance_keywords = sorted(rel_set)

    # --- Negative title keywords ---
    negatives = list(prefs.negative_keywords)

    # --- Locations ---
    locations = list(LOCATIONS)  # Start with UK defaults
    for loc in prefs.preferred_locations:
        if loc not in locations:
            locations.append(loc)
    if prefs.work_arrangement:
        arrangement = prefs.work_arrangement.capitalize()
        if arrangement not in locations:
            locations.append(arrangement)

    # --- Core domain words & supporting role words ---
    core_words: set[str] = set()
    support_words: set[str] = set()
    for title in titles:
        for word in re.findall(r'\w+', title.lower()):
            if word in _STOPWORDS or len(word) <= 1:
                continue
            if word in _ROLE_WORDS:
                support_words.add(word)
            else:
                core_words.add(word)

    # --- Search queries (top 8 titles x top 2 locations) ---
    top_titles = titles[:8]
    search_locations = prefs.preferred_locations[:2] if prefs.preferred_locations else ["UK"]
    queries = []
    for title in top_titles:
        for loc in search_locations:
            queries.append(f"{title} {loc}")
    queries = queries[:16]

    return SearchConfig(
        job_titles=titles,
        primary_skills=_canonicalize_skill_list(primary),
        secondary_skills=_canonicalize_skill_list(secondary),
        tertiary_skills=_canonicalize_skill_list(tertiary),
        relevance_keywords=relevance_keywords,
        negative_title_keywords=negatives,
        locations=locations,
        visa_keywords=list(VISA_KEYWORDS),
        core_domain_words=core_words,
        supporting_role_words=support_words,
        search_queries=queries,
    )
