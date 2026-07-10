"""Pillar 2 Batch 2.4 — classify a user's professional domain(s) from profile.

Used by `_build_sources()` to filter job sources so a healthcare user doesn't
spin up tech-only boards (bcs_jobs / climatebase / aijobs_*) and a tech user
doesn't waste quota on the NHS XML feed. The plan explicitly holds
telemetry-driven auto-tuning out of scope — this is purely static
config-driven.

The classifier is deliberately additive: any signal it finds adds a domain,
nothing subtracts. Zero-profile users return an empty set and `_build_sources`
interprets that as "include every source" (graceful fallback).
"""
from __future__ import annotations

import re

from src.services.profile.models import UserProfile

# Domain → keyword set. Keys are matched against:
#   - job titles (preferences + CV extracted + LinkedIn positions)
#   - skills (all tiers)
#   - LinkedIn industry text
#
# Lower-case for case-insensitive substring match. Multi-word phrases are
# supported; they match only when the full phrase appears as a substring.
_DOMAIN_KEYWORDS: dict[str, set[str]] = {
    "tech": {
        "engineer", "developer", "programmer", "architect", "devops",
        "software", "data scientist", "data engineer", "ml", "machine learning",
        "ai", "artificial intelligence", "backend", "frontend", "full stack",
        "fullstack", "mobile", "cloud", "sre", "site reliability", "platform",
        "security engineer", "infosec", "cybersecurity", "qa engineer",
        "test engineer", "python", "java", "javascript", "typescript",
        "kubernetes", "docker", "aws", "azure", "gcp", "react", "angular",
        "node.js", "django", "flask", "spring", "postgresql", "mongodb",
    },
    "healthcare": {
        "nurse", "nursing", "doctor", "physician", "consultant", "surgeon",
        "clinician", "clinical", "gp", "general practitioner", "paramedic",
        "radiographer", "physiotherapist", "physio", "pharmacist", "pharmacy",
        "midwife", "midwifery", "healthcare", "health care", "medical",
        "medicine", "nhs", "hospital", "patient", "dental", "dentist",
        "psychiatric", "psychology", "psychotherapy", "occupational therapy",
        "biotech", "pharmaceutical", "pharma", "biology", "biologist",
        "clinical trial", "epidemiology",
    },
    "academia": {
        "professor", "lecturer", "postdoctoral", "postdoc", "phd", "dphil",
        "research fellow", "research associate", "assistant professor",
        "associate professor", "senior lecturer", "principal investigator",
        "pi ", "tenure", "university", "college", "higher education",
        "academic", "scholarly", "scholar",
    },
    "education": {
        "teacher", "teaching", "headteacher", "head teacher", "headmaster",
        "headmistress", "classroom", "primary school", "secondary school",
        "sixth form", "tutor", "tutoring", "qts", "pgce",
        "special educational needs", "sen ", "apprentice", "apprenticeship",
        "training provider",
    },
    "climate": {
        "climate", "sustainability", "sustainable", "renewable", "clean energy",
        "solar", "wind energy", "environmental", "carbon", "net zero",
        "decarbonisation", "decarbonization", "esg ", "climate tech",
    },
}


def _collect_text(profile: UserProfile) -> str:
    """Concatenate every field we inspect into one lower-cased haystack."""
    prefs = profile.preferences
    cv = profile.cv_data

    parts: list[str] = []
    parts.extend(prefs.target_job_titles)
    parts.extend(prefs.additional_skills)
    parts.extend(cv.job_titles)
    parts.extend(cv.skills)
    parts.extend(cv.linkedin_skills)
    parts.extend(cv.github_skills_inferred)
    for pos in cv.linkedin_positions:
        title = pos.get("title") if isinstance(pos, dict) else ""
        if title:
            parts.append(title)
        company = pos.get("company") if isinstance(pos, dict) else ""
        if company:
            parts.append(company)
    if cv.linkedin_industry:
        parts.append(cv.linkedin_industry)

    return " ".join(parts).lower()


def classify_user_domain(profile: UserProfile | None) -> set[str]:
    """Return the set of domains the user's profile spans.

    - A zero-profile or empty-profile user returns an empty set, which
      `_build_sources()` treats as "include every source" (graceful fallback).
    - Multiple domains are supported (e.g. a data scientist moving into
      climate tech may map to {"tech", "climate"}).
    - "general" is intentionally never emitted — it is the base-class default
      on sources, and the build-time filter short-circuits every
      `"general"`-tagged source into the result regardless of user domains.
    """
    if profile is None:
        return set()

    haystack = _collect_text(profile)
    if not haystack.strip():
        return set()

    domains: set[str] = set()
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            # Use word-boundary-aware match for short keywords (avoid "ai" in
            # "maintain") and substring match for multi-word phrases.
            if " " in kw:
                if kw in haystack:
                    domains.add(domain)
                    break
            else:
                if re.search(r'\b' + re.escape(kw) + r'\b', haystack):
                    domains.add(domain)
                    break

    return domains


def source_matches_user_domains(source_domains: set[str],
                                 user_domains: set[str]) -> bool:
    """Return True if a source with `source_domains` should be included for a
    user whose classified domains are `user_domains`.

    Rules (plan §4 Batch 2.4):
    - Empty `user_domains` → graceful fallback, include every source.
    - `"general"` in `source_domains` → always include (cross-domain boards
      like Reed, Indeed, LinkedIn).
    - Otherwise include only if `source_domains ∩ user_domains` is non-empty.
    """
    if not user_domains:
        return True
    if "general" in source_domains:
        return True
    return bool(source_domains & user_domains)
