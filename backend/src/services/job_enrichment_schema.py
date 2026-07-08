"""Pillar 2 Batch 2.5 — Pydantic schema for LLM-enriched job fields.

The `JobEnrichment` model is the target schema for `llm_extract_validated()`
(see `src/services/profile/llm_provider.py:93`). Every field is either
explicitly typed or an enum, and every list/string field is length-bounded so
a malformed LLM response can't bloat the DB.

The 18 fields below are the contract the downstream pipeline (dedup, scorer,
embeddings in Batch 2.6) consumes. Keep this module import-light — no
aiohttp, no Pydantic-v1 compat shims.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class JobCategory(str, Enum):
    """16-enum professional domain taxonomy (plan §4 Batch 2.5)."""
    SOFTWARE_ENGINEERING = "software_engineering"
    DATA_SCIENCE = "data_science"
    MACHINE_LEARNING = "machine_learning"
    DEVOPS_INFRASTRUCTURE = "devops_infrastructure"
    PRODUCT_MANAGEMENT = "product_management"
    DESIGN = "design"
    MARKETING = "marketing"
    SALES = "sales"
    FINANCE = "finance"
    LEGAL = "legal"
    HR_PEOPLE = "hr_people"
    OPERATIONS = "operations"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    ACADEMIA_RESEARCH = "academia_research"
    OTHER = "other"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    APPRENTICESHIP = "apprenticeship"
    FREELANCE = "freelance"
    UNKNOWN = "unknown"


class WorkplaceType(str, Enum):
    REMOTE = "remote"
    ONSITE = "onsite"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class VisaSponsorship(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class SeniorityLevel(str, Enum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    DIRECTOR = "director"
    UNKNOWN = "unknown"


class ExperienceLevel(str, Enum):
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    UNKNOWN = "unknown"


class EmployerType(str, Enum):
    STARTUP = "startup"
    SCALEUP = "scaleup"
    ENTERPRISE = "enterprise"
    AGENCY = "agency"
    NONPROFIT = "nonprofit"
    GOVERNMENT = "government"
    ACADEMIC = "academic"
    HEALTHCARE = "healthcare"
    OTHER = "other"
    UNKNOWN = "unknown"


class SalaryFrequency(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    MONTHLY = "monthly"
    ANNUAL = "annual"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Nested schemas
# ---------------------------------------------------------------------------


class SalaryBand(BaseModel):
    """Nested salary structure — all fields nullable because most job adverts
    omit pay entirely (~70 % of the UK corpus)."""
    min: Optional[float] = Field(default=None, ge=0)
    max: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, max_length=8)
    frequency: SalaryFrequency = Field(default=SalaryFrequency.UNKNOWN)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


# ---------------------------------------------------------------------------
# Top-level enrichment schema — 18 fields
# ---------------------------------------------------------------------------


class JobEnrichment(BaseModel):
    """LLM-extracted normalisation of a job posting.

    Populated by `src/services/job_enrichment.enrich_job()` via the shared
    Gemini→Groq→Cerebras provider chain (`llm_extract_validated()`). Persisted
    to the `job_enrichment` table (one row per `job_id`).
    """

    # 1. Canonical title (free-form string, LLM-rewritten)
    title_canonical: str = Field(..., min_length=1, max_length=200)

    # 2. Category enum (16-way)
    category: JobCategory

    # 3. Employment type
    employment_type: EmploymentType = Field(default=EmploymentType.UNKNOWN)

    # 4. Workplace type
    workplace_type: WorkplaceType = Field(default=WorkplaceType.UNKNOWN)

    # 5. Locations (list of free-form place strings)
    locations: list[str] = Field(default_factory=list, max_length=10)

    # 6. Salary (nested)
    salary: SalaryBand = Field(default_factory=SalaryBand)

    # 7. Required skills (short list — not the kitchen sink)
    required_skills: list[str] = Field(default_factory=list, max_length=30)

    # 8. Preferred skills
    preferred_skills: list[str] = Field(default_factory=list, max_length=30)

    # 9. Minimum years of experience
    experience_min_years: Optional[int] = Field(default=None, ge=0, le=40)

    # 10. Experience level enum
    experience_level: ExperienceLevel = Field(default=ExperienceLevel.UNKNOWN)

    # 11. Requirements summary (≤250 chars — for Batch 2.6 embedding input)
    requirements_summary: str = Field(default="", max_length=250)

    # 12. Language (ISO 639-1)
    language: str = Field(default="en", min_length=2, max_length=2)

    # 13. Employer type
    employer_type: EmployerType = Field(default=EmployerType.UNKNOWN)

    # 14. Visa sponsorship
    visa_sponsorship: VisaSponsorship = Field(default=VisaSponsorship.UNKNOWN)

    # 15. Seniority
    seniority: SeniorityLevel = Field(default=SeniorityLevel.UNKNOWN)

    # 16. Remote region (nullable — only relevant when workplace_type=REMOTE)
    remote_region: Optional[str] = Field(default=None, max_length=60)

    # 17. Apply instructions (nullable)
    apply_instructions: Optional[str] = Field(default=None, max_length=500)

    # 18. Red flags list (e.g. "requires unpaid work", "MLM signal")
    red_flags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("language")
    @classmethod
    def _lower_language(cls, v: str) -> str:
        return v.lower()

    @field_validator("locations", "required_skills", "preferred_skills", "red_flags")
    @classmethod
    def _strip_and_dedup(cls, v: list[str]) -> list[str]:
        """Strip empties and exact-dupes while preserving order."""
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            trimmed = item.strip() if isinstance(item, str) else ""
            key = trimmed.lower()
            if trimmed and key not in seen:
                out.append(trimmed)
                seen.add(key)
        return out
