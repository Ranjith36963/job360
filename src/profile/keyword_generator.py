"""Convert a UserProfile into a SearchConfig for dynamic keyword-driven search."""

from __future__ import annotations

import re
from src.config.keywords import VISA_KEYWORDS, LOCATIONS
from src.profile.models import SearchConfig, UserProfile
from src.profile.skill_graph import infer_skills


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
    "chief", "vice", "deputy", "president",
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

    # --- Skills (source-based tiering) ---
    # Primary: user preferences (strongest signal of intent)
    primary = list(prefs.additional_skills)
    seen_skills = {s.lower() for s in primary}

    # Secondary: CV-extracted skills (proven experience)
    secondary = []
    for s in cv.skills:
        if s.lower() not in seen_skills:
            secondary.append(s)
            seen_skills.add(s.lower())

    # Tertiary: LinkedIn + GitHub (supplementary evidence)
    tertiary = []
    for s in cv.linkedin_skills:
        if s.lower() not in seen_skills:
            tertiary.append(s)
            seen_skills.add(s.lower())
    for s in cv.github_skills_inferred:
        if s.lower() not in seen_skills:
            tertiary.append(s)
            seen_skills.add(s.lower())

    all_skills = primary + secondary + tertiary

    # --- Controlled skill inference (inferred go to tertiary only) ---
    inferred = infer_skills(all_skills, threshold=0.7)
    for s in inferred:
        if s.lower() not in seen_skills:
            tertiary.append(s)
            seen_skills.add(s.lower())
            all_skills.append(s)

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

    # --- Search queries: "{title} UK" for all titles (UK-wide search) ---
    queries = [f"{title} UK" for title in titles]

    return SearchConfig(
        job_titles=titles,
        primary_skills=primary,
        secondary_skills=secondary,
        tertiary_skills=tertiary,
        relevance_keywords=relevance_keywords,
        negative_title_keywords=negatives,
        locations=locations,
        visa_keywords=list(VISA_KEYWORDS),
        core_domain_words=core_words,
        supporting_role_words=support_words,
        search_queries=queries,
    )
