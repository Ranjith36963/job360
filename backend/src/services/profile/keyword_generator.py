"""Convert a UserProfile into a SearchConfig for dynamic keyword-driven search."""

from __future__ import annotations

import re

from src.core.keywords import LOCATIONS, VISA_KEYWORDS
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
    # Dead-title-lever fix (funnel redesign 2026-08-05). Titles pulled from a CV
    # are hyper-specific ("AI Solutions Engineer - R&D Department"): real
    # postings never equal or contain them, so title scoring falls back to this
    # vocabulary — which used to be raw title tokens, i.e. tiny AND polluted by
    # junk like 'department'. Measured on prod: no job scored above 8/40 on
    # title, capping every match_score at ~69 and quietly making LOCATION the
    # feed's sort key. All corroboration below is evidence from the profile
    # itself (titles, skills) — no hardcoded vocabulary (rule #28 spirit).
    #
    # A title token joins core only with corroboration:
    #   - it appears in >=2 different titles (cross-title evidence), or
    #   - it appears inside the user's skill tokens (skill evidence), or
    #   - it is a short acronym token (<=3 chars: 'ai', 'ml', 'nlp' — domain
    #     acronyms; 1-char junk like the 'r'/'d' of "R&D" is already dropped).
    # Skill tokens seen across >=2 skills join core too, so "Machine Learning
    # Engineer" postings match a profile whose titles never say 'learning'.
    support_words: set[str] = set()
    title_token_titles: dict[str, int] = {}  # token -> number of DISTINCT titles
    seen_token_sets: set[frozenset[str]] = set()
    for title in titles:
        seen_in_title: set[str] = set()
        for word in re.findall(r'\w+', title.lower()):
            if word in _STOPWORDS or len(word) <= 1:
                continue
            if word in _ROLE_WORDS:
                support_words.add(word)
            else:
                seen_in_title.add(word)
        # Near-duplicate titles ("… - R&D Department" vs "… – R&D Department",
        # or the same title arriving from both CV and LinkedIn) must count as
        # ONE piece of evidence, or their junk tokens fake cross-title
        # corroboration. Identity = the token set, not the raw string.
        fs = frozenset(seen_in_title)
        if not fs or fs in seen_token_sets:
            continue
        seen_token_sets.add(fs)
        for word in seen_in_title:
            title_token_titles[word] = title_token_titles.get(word, 0) + 1

    skill_token_freq: dict[str, int] = {}  # token -> number of skills it appears in
    for skill in primary + secondary:
        for word in set(re.findall(r'\w+', skill.lower())):
            if word in _STOPWORDS or word in _ROLE_WORDS or len(word) <= 1:
                continue
            skill_token_freq[word] = skill_token_freq.get(word, 0) + 1

    core_words: set[str] = {
        tok for tok, n_titles in title_token_titles.items()
        if n_titles >= 2 or tok in skill_token_freq or len(tok) <= 3
    }
    core_words |= {tok for tok, freq in skill_token_freq.items() if freq >= 2}
    if not core_words:
        # Thin-profile safety net (one title, no skills): an empty vocabulary
        # would kill title scoring entirely — keep the old all-tokens behaviour.
        core_words = set(title_token_titles)

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
