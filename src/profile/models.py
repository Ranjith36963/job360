"""Core dataclasses for user profile and dynamic search configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.config.keywords import (
    JOB_TITLES,
    LOCATIONS,
    PRIMARY_SKILLS,
    SECONDARY_SKILLS,
    TERTIARY_SKILLS,
    RELEVANCE_KEYWORDS,
    NEGATIVE_TITLE_KEYWORDS,
    VISA_KEYWORDS,
)


@dataclass
class CVData:
    raw_text: str = ""
    skills: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    summary: str = ""
    # LinkedIn-sourced data
    linkedin_positions: list[dict] = field(default_factory=list)
    linkedin_skills: list[str] = field(default_factory=list)
    linkedin_industry: str = ""
    # GitHub-sourced data
    github_languages: dict[str, int] = field(default_factory=dict)
    github_topics: list[str] = field(default_factory=list)
    github_skills_inferred: list[str] = field(default_factory=list)


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
        """Return SearchConfig with the current hard-coded AI/ML keywords."""
        core_ai_words = {
            "ai", "ml", "machine", "learning", "deep", "nlp", "data",
            "genai", "llm", "rag", "mlops", "neural", "transformer",
            "generative", "vision", "computer",
        }
        supporting_words = {
            "scientist", "engineer", "research", "applied", "platform",
            "infrastructure", "conversational", "robotics", "alignment",
        }
        return cls(
            job_titles=list(JOB_TITLES),
            primary_skills=list(PRIMARY_SKILLS),
            secondary_skills=list(SECONDARY_SKILLS),
            tertiary_skills=list(TERTIARY_SKILLS),
            relevance_keywords=list(RELEVANCE_KEYWORDS),
            negative_title_keywords=list(NEGATIVE_TITLE_KEYWORDS),
            locations=list(LOCATIONS),
            visa_keywords=list(VISA_KEYWORDS),
            core_domain_words=core_ai_words,
            supporting_role_words=supporting_words,
            search_queries=[],
        )
