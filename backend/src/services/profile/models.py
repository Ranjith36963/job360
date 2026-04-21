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
    # Batch 1.5 — expanded LinkedIn sections (Languages, Projects,
    # Volunteer Experience, Courses). All are LinkedIn-sourced display
    # fields: they inform the CV viewer and feed relevance keywords
    # but do NOT contribute to ``skills`` — they're separate signals
    # so downstream can opt-in rather than polluting primary tiering.
    linkedin_languages: list[dict] = field(default_factory=list)
    linkedin_projects: list[dict] = field(default_factory=list)
    linkedin_volunteer: list[dict] = field(default_factory=list)
    linkedin_courses: list[dict] = field(default_factory=list)
    # GitHub-sourced data
    github_languages: dict[str, int] = field(default_factory=dict)
    github_topics: list[str] = field(default_factory=list)
    github_skills_inferred: list[str] = field(default_factory=list)
    # Batch 1.2 — skills inferred from GitHub dependency-file parsing
    # (requirements.txt / package.json / Cargo.toml / etc.). Kept
    # separate from github_skills_inferred so downstream can audit
    # where a skill came from (language signal vs declared dependency).
    github_frameworks: list[str] = field(default_factory=list)
    # Batch 1.1 — archetype classification (CareerDomain enum value).
    # Optional; None means "LLM did not classify". Consumed by
    # archetype-aware scoring (Pillar 1 #10 / Pillar 2).
    career_domain: Optional[str] = None
    # Batch 1.x.1 (review fix #1) — CV-extracted fields that the
    # CVSchema already parses but the original adapter silently
    # dropped. Separate from ``linkedin_*`` equivalents so the JSON
    # Resume export distinguishes CV-stated languages/industries from
    # LinkedIn-stated ones.
    industries: list[str] = field(default_factory=list)
    cv_languages: list[str] = field(default_factory=list)

    @classmethod
    def from_json_resume(cls, data: dict) -> "CVData":
        """Batch 1.8b — inverse of ``to_json_resume``. Build a CVData
        from a JSON Resume–shaped dict.

        Closes the plan §4.8 interop goal without a breaking rename:
        callers that want to import a third-party JSON Resume export
        (from jsonresume.org tooling, for example) get a canonical
        loader that maps the standard root keys back onto the
        existing CVData field layout. Unknown root keys are ignored.
        Missing keys default to empty collections.
        """
        if not isinstance(data, dict):
            return cls()
        basics = data.get("basics") or {}
        location_obj = basics.get("location") if isinstance(basics, dict) else None
        loc = (
            location_obj.get("address", "") if isinstance(location_obj, dict) else ""
        )

        linkedin_positions = [
            {
                "title": w.get("position", "") or "",
                "company": w.get("name", "") or "",
                "start": w.get("startDate", "") or "",
                "end": w.get("endDate", "") or "",
                "description": w.get("summary", "") or "",
            }
            for w in (data.get("work") or [])
            if isinstance(w, dict)
        ]

        edu_lines: list[str] = []
        for e in data.get("education") or []:
            if not isinstance(e, dict):
                continue
            inst = e.get("institution", "") or ""
            deg = e.get("studyType", "") or e.get("area", "") or ""
            entry = deg if deg else inst
            if deg and inst:
                entry = f"{deg} - {inst}"
            if entry:
                edu_lines.append(entry)

        skills: list[str] = []
        for s in data.get("skills") or []:
            if isinstance(s, dict):
                nm = s.get("name", "")
                if nm:
                    skills.append(nm)
            elif isinstance(s, str):
                skills.append(s)

        linkedin_languages = [
            {
                "language": lang.get("language", "") or "",
                "proficiency": lang.get("fluency", "") or "",
            }
            for lang in (data.get("languages") or [])
            if isinstance(lang, dict) and lang.get("language")
        ]

        linkedin_projects = [
            {
                "title": p.get("name", "") or "",
                "description": p.get("description", "") or "",
                "start": p.get("startDate", "") or "",
                "end": p.get("endDate", "") or "",
                "url": p.get("url", "") or "",
            }
            for p in (data.get("projects") or [])
            if isinstance(p, dict) and p.get("name")
        ]

        linkedin_volunteer = [
            {
                "role": v.get("position", "") or "",
                "organisation": v.get("organization", "") or "",
                "cause": v.get("cause", "") or "",
                "start": v.get("startDate", "") or "",
                "end": v.get("endDate", "") or "",
                "description": v.get("summary", "") or "",
            }
            for v in (data.get("volunteer") or [])
            if isinstance(v, dict)
        ]

        certs = [
            c.get("name", "") if isinstance(c, dict) else str(c)
            for c in (data.get("certificates") or [])
            if (isinstance(c, dict) and c.get("name")) or isinstance(c, str)
        ]

        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        return cls(
            name=basics.get("name", "") if isinstance(basics, dict) else "",
            headline=basics.get("label", "") if isinstance(basics, dict) else "",
            summary=basics.get("summary", "") if isinstance(basics, dict) else "",
            location=loc,
            linkedin_positions=linkedin_positions,
            education=edu_lines,
            skills=skills,
            certifications=certs,
            linkedin_languages=linkedin_languages,
            linkedin_projects=linkedin_projects,
            linkedin_volunteer=linkedin_volunteer,
            linkedin_industry=meta.get("industry", "") if isinstance(meta, dict) else "",
            github_frameworks=list(meta.get("github_frameworks") or []),
            github_topics=list(meta.get("github_topics") or []),
            github_languages=dict(meta.get("github_languages") or {}),
            career_domain=meta.get("career_domain") if isinstance(meta, dict) else None,
        )

    def to_json_resume(self) -> dict:
        """Batch 1.8 — return a JSON Resume canonical-schema dict.

        Additive export (read-only). Does NOT rename existing fields,
        so callers that depend on the raw dataclass layout keep
        working. Schema follows https://jsonresume.org/schema/: root
        keys ``basics`` / ``work`` / ``education`` / ``skills`` /
        ``languages`` / ``projects`` / ``volunteer`` / ``certificates``.
        Custom provenance (``career_domain``, ``github_frameworks``)
        rides under the ``meta`` key — reserved in the schema for
        extensions.
        """
        return {
            "basics": {
                "name": self.name,
                "label": self.headline,
                "summary": self.summary,
                "location": {"address": self.location} if self.location else {},
            },
            "work": [
                {
                    "name": pos.get("company", ""),
                    "position": pos.get("title", ""),
                    "startDate": pos.get("start", ""),
                    "endDate": pos.get("end", ""),
                    "summary": pos.get("description", ""),
                }
                for pos in self.linkedin_positions
            ],
            "education": [{"institution": line} for line in self.education],
            "skills": [{"name": s, "level": "", "keywords": []} for s in self.skills],
            "languages": [
                {"language": lang.get("language", ""), "fluency": lang.get("proficiency", "")}
                for lang in self.linkedin_languages
            ],
            "projects": [
                {
                    "name": p.get("title", ""),
                    "description": p.get("description", ""),
                    "startDate": p.get("start", ""),
                    "endDate": p.get("end", ""),
                    "url": p.get("url", ""),
                }
                for p in self.linkedin_projects
            ],
            "volunteer": [
                {
                    "organization": v.get("organisation", ""),
                    "position": v.get("role", ""),
                    "startDate": v.get("start", ""),
                    "endDate": v.get("end", ""),
                    "summary": v.get("description", ""),
                }
                for v in self.linkedin_volunteer
            ],
            "certificates": [{"name": c} for c in self.certifications],
            "meta": {
                "career_domain": self.career_domain,
                "github_languages": self.github_languages,
                "github_topics": self.github_topics,
                "github_frameworks": self.github_frameworks,
                "industry": self.linkedin_industry,
            },
        }

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
    # Pillar 2 Batch 2.9 — multi-dimensional scoring inputs.
    # `preferred_workplace` is the enum form of `work_arrangement` so the
    # dimension scorer can match against `JobEnrichment.workplace_type`
    # without string juggling. None → user has no preference → neutral score.
    # `needs_visa` gates the visa scorer — when False the dim returns 0
    # (no reward for something the user doesn't need).
    preferred_workplace: Optional[str] = None   # "remote" | "hybrid" | "onsite" | None
    needs_visa: bool = False


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
