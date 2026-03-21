"""Core dataclasses for user profile and dynamic search configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkExperience:
    """A single work-experience entry extracted from the CV."""
    title: str = ""
    company: str = ""
    start_date: str = ""       # e.g. "Jan 2020", "2020"
    end_date: str = ""         # e.g. "Dec 2023", "Present", ""
    duration_months: int = 0   # computed from dates
    description: str = ""
    skills_used: list[str] = field(default_factory=list)


@dataclass
class StructuredEducation:
    """A single education entry extracted from the CV."""
    institution: str = ""
    degree: str = ""           # e.g. "BSc", "MSc", "PhD", "PGCE", "MBA"
    field_of_study: str = ""   # e.g. "Computer Science", "Nursing"
    year: Optional[int] = None # graduation year
    grade: str = ""            # e.g. "First Class", "2:1", "Distinction"


@dataclass
class Project:
    """A project entry extracted from the CV."""
    name: str = ""
    description: str = ""
    technologies: list[str] = field(default_factory=list)
    url: str = ""


@dataclass
class CVData:
    raw_text: str = ""
    skills: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    summary: str = ""
    # Structured data (Phase 0A)
    work_experiences: list[WorkExperience] = field(default_factory=list)
    structured_education: list[StructuredEducation] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    total_experience_months: int = 0
    computed_seniority: str = ""  # "entry", "mid", "senior", "lead", "executive"
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

