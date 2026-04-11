"""Core dataclasses for user profile and dynamic search configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CVData:
    raw_text: str = ""
    # Scoring-semantic fields — these flow into SearchConfig and influence matching
    skills: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    summary: str = ""
    experience_text: str = ""
    # Display-only fields — used by CV viewer for highlighting, NOT for scoring
    name: str = ""
    headline: str = ""
    location: str = ""
    achievements: list[str] = field(default_factory=list)
    # LinkedIn-sourced data
    linkedin_positions: list[dict] = field(default_factory=list)
    linkedin_skills: list[str] = field(default_factory=list)
    linkedin_industry: str = ""
    # GitHub-sourced data
    github_languages: dict[str, int] = field(default_factory=dict)
    github_topics: list[str] = field(default_factory=list)
    github_skills_inferred: list[str] = field(default_factory=list)

    @property
    def highlights(self) -> list[str]:
        """All terms to highlight in the CV viewer (scoring-safe aggregation)."""
        result = []
        if self.name:
            result.append(self.name)
        if self.headline:
            result.append(self.headline)
        if self.location:
            result.append(self.location)
        result.extend(self.skills)
        result.extend(self.job_titles)
        result.extend(self.companies)
        result.extend(self.achievements)
        return result


@dataclass
class UserPreferences:
    target_job_titles: list[str] = field(default_factory=list)
    additional_skills: list[str] = field(default_factory=list)
    excluded_skills: list[str] = field(default_factory=list)
    preferred_locations: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    work_arrangement: str = ""  # "remote", "hybrid", "onsite", or ""
    experience_level: str = ""
    negative_keywords: list[str] = field(default_factory=list)
    about_me: str = ""
    github_username: str = ""


@dataclass
class UserProfile:
    cv_data: CVData = field(default_factory=CVData)
    preferences: UserPreferences = field(default_factory=UserPreferences)

    @property
    def is_complete(self) -> bool:
        has_cv = bool(self.cv_data.raw_text)
        has_prefs = bool(
            self.preferences.target_job_titles
            or self.preferences.additional_skills
        )
        return has_cv or has_prefs


@dataclass
class SearchConfig:
    job_titles: list[str] = field(default_factory=list)
    primary_skills: list[str] = field(default_factory=list)
    secondary_skills: list[str] = field(default_factory=list)
    tertiary_skills: list[str] = field(default_factory=list)
    relevance_keywords: list[str] = field(default_factory=list)
    negative_title_keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    visa_keywords: list[str] = field(default_factory=list)
    core_domain_words: set[str] = field(default_factory=set)
    supporting_role_words: set[str] = field(default_factory=set)
    search_queries: list[str] = field(default_factory=list)

    @classmethod
    def from_defaults(cls) -> SearchConfig:
        """Return a minimal SearchConfig with no domain assumptions.

        When no user profile exists, we use empty skill lists rather than
        hardcoded AI/ML keywords. The user MUST upload a CV or set preferences
        for meaningful job matching.
        """
        from src.core.keywords import LOCATIONS, VISA_KEYWORDS
        return cls(
            job_titles=[],
            primary_skills=[],
            secondary_skills=[],
            tertiary_skills=[],
            relevance_keywords=[],
            negative_title_keywords=[],
            locations=list(LOCATIONS),
            visa_keywords=list(VISA_KEYWORDS),
            core_domain_words=set(),
            supporting_role_words=set(),
            search_queries=[],
        )
