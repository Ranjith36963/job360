"""Pydantic schemas for LLM-structured CV extraction.

Batch 1.1 (Pillar 1). Replaces the hand-rolled ``_llm_result_to_cvdata``
coercion in ``cv_parser.py`` with a typed validation layer that:

* enforces field types at the schema boundary (not spread across the
  parser);
* surfaces ``pydantic.ValidationError`` so the LLM retry loop can feed
  the error text back to the model for self-correction;
* carries a closed ``CareerDomain`` enum so downstream (Pillar 1 Batch
  1.10, Pillar 2 archetype weights) can branch on a trusted value.

The schema is **permissive**: every field is optional with a sensible
default. LLMs — especially the weaker Groq/Cerebras models — sometimes
omit fields or emit ``None`` where we asked for a list. Accepting that
cleanly here keeps retries focused on real structural bugs (wrong
types, hallucinated keys, broken JSON) rather than churn on missing
optionals.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.services.profile.cv_parser import _maybe_normalise_skills_via_esco
from src.services.profile.models import CVData


class CareerDomain(str, Enum):
    """Coarse career buckets used for archetype-aware matching (Pillar 1 #10 / Pillar 2)."""

    SOFTWARE_ENGINEERING = "software_engineering"
    DATA_AND_AI = "data_and_ai"
    PRODUCT_AND_DESIGN = "product_and_design"
    MARKETING_AND_GROWTH = "marketing_and_growth"
    SALES_AND_BIZDEV = "sales_and_bizdev"
    FINANCE_AND_ACCOUNTING = "finance_and_accounting"
    OPERATIONS_AND_SUPPLY = "operations_and_supply"
    HUMAN_RESOURCES = "human_resources"
    LEGAL_AND_COMPLIANCE = "legal_and_compliance"
    HEALTHCARE_AND_LIFESCIENCES = "healthcare_and_lifesciences"
    EDUCATION_AND_RESEARCH = "education_and_research"
    ENGINEERING_PHYSICAL = "engineering_physical"
    CUSTOMER_SUPPORT = "customer_support"
    MEDIA_AND_CONTENT = "media_and_content"
    SKILLED_TRADES = "skilled_trades"
    OTHER = "other"


def _coerce_to_str_list(value: Any) -> list[str]:
    """Normalise LLM list fields to ``list[str]``.

    Weaker LLMs return ``None``, a single string, comma-joined prose, or
    lists of dicts. We do not want retries to churn on these: clean
    them up at the boundary and let real structural errors bubble up.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("skill") or item.get("title")
                if name:
                    out.append(str(name))
            elif item not in (None, ""):
                out.append(str(item))
        return out
    return [str(value)]


class ExperienceEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: Optional[str] = ""
    title: Optional[str] = ""
    dates: Optional[str] = ""
    location: Optional[str] = ""
    bullets: list[str] = Field(default_factory=list)

    @field_validator("bullets", mode="before")
    @classmethod
    def _bullets_list(cls, v: Any) -> list[str]:
        return _coerce_to_str_list(v)

    @field_validator("company", "title", "dates", "location", mode="before")
    @classmethod
    def _strings_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class EducationEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    degree: Optional[str] = ""
    institution: Optional[str] = ""
    dates: Optional[str] = ""
    details: list[str] = Field(default_factory=list)

    @field_validator("details", mode="before")
    @classmethod
    def _details_list(cls, v: Any) -> list[str]:
        return _coerce_to_str_list(v)

    @field_validator("degree", "institution", "dates", mode="before")
    @classmethod
    def _strings_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class CVSchema(BaseModel):
    """Typed shape of the LLM's CV-extraction JSON output.

    Every field is optional (default empty) so minor omissions do not
    trigger a retry. Type violations and unknown enum values on
    ``career_domain`` DO trigger ``ValidationError``, which the retry
    loop feeds back to the LLM for self-correction.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: Optional[str] = ""
    headline: Optional[str] = ""
    location: Optional[str] = ""
    summary: Optional[str] = ""

    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    experience_level: Optional[str] = ""
    # TRI-STATE by design (rule #31): "" means the CV never said, which is
    # NOT the same as "needs sponsorship". Free text, not an enum — a UK CV
    # states this a dozen different ways and an enum would force a guess.
    right_to_work: Optional[str] = ""
    career_domain: Optional[CareerDomain] = None

    @field_validator(
        "skills",
        "certifications",
        "achievements",
        "industries",
        "languages",
        mode="before",
    )
    @classmethod
    def _lists_of_strings(cls, v: Any) -> list[str]:
        return _coerce_to_str_list(v)

    @field_validator("name", "headline", "location", "summary", "experience_level", mode="before")
    @classmethod
    def _str_or_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, (list, dict)):
            return ""
        return str(v)

    @field_validator("career_domain", mode="before")
    @classmethod
    def _domain_nullable(cls, v: Any) -> Any:
        """Treat empty/unknown strings as None rather than failing validation.

        This is the one enum-coercion we allow: LLMs commonly write
        ``""`` or ``"unknown"`` when they don't know. We want that to
        become ``None`` (skip), not a retry trigger. An outright wrong
        enum value (e.g. ``"banana"``) still fails — strict mode for
        genuine errors, soft mode for genuine gaps.
        """
        if v in (None, "", "unknown", "n/a", "none"):
            return None
        return v


# ── Schema → CVData adapter ─────────────────────────────────────────

def cv_schema_to_cvdata(schema: CVSchema, raw_text: str) -> CVData:
    """Flatten the typed schema into the existing ``CVData`` dataclass.

    Keeps the scoring-semantic vs display-only split intact (CVData
    docstring). Does NOT touch LinkedIn/GitHub fields — those stay
    empty at CV-parse time and get filled by later enrichers.
    """
    job_titles = [e.title for e in schema.experience if e.title]
    companies = [e.company for e in schema.experience if e.company]

    # ADAPTER-PARITY FIX (2026-08-07) — Step-1.5 S1.5-D ESCO normalisation.
    # `_llm_result_to_cvdata` (the untyped fallback, reached only when strict
    # CVSchema validation exhausts its retries) has called
    # `_maybe_normalise_skills_via_esco` since it was built. THIS adapter —
    # the one every successful extraction actually returns through — never
    # did, so `CVData(...)` below had no `cv_skills_esco=` argument at all.
    # Prod runs `SEMANTIC_ENABLED=true` and every profile shipped with
    # `cv_skills_esco={}` regardless. Same shape as the `cv_positions` bug:
    # one adapter fixed, the other silently isn't. Graceful no-op (skills
    # unchanged, empty map) when the flag is off or the ESCO index isn't on
    # disk — see the helper's own docstring for the guard chain.
    skills, cv_skills_esco = _maybe_normalise_skills_via_esco(list(schema.skills))

    experience_lines: list[str] = []
    for e in schema.experience:
        experience_lines.extend(e.bullets)

    # STRUCTURED experience — the dated, paired record (2026-08-07).
    #
    # ``ExperienceEntry`` has always carried ``dates`` and ``location``, and
    # the CV prompt has always asked for them, but this function flattened
    # experience into two UNPAIRED lists and dropped both. Nothing downstream
    # could say which title was held at which company, for how long, or how
    # recently — the reason the engine has no skill-recency signal and the
    # judge could not answer the seniority question its own rubric asks.
    #
    # PR #241 added ``cv_positions`` to the OTHER adapter
    # (``cv_parser._llm_result_to_cvdata``) — but ``llm_cv_fields_from_text``
    # returns through THIS one, so the fix reached no real extraction. Caught
    # by the Pillar-1 audit on a live CV: the LLM returned
    # "Freelance AI Trainer, 2023-2024" and cv_positions still came out 0.
    # Two adapters for one contract is the bug; both now populate the field.
    cv_positions: list[dict[str, Any]] = [
        {
            "company": e.company or "",
            "title": e.title or "",
            "dates": e.dates or "",
            "location": e.location or "",
            "bullets": list(e.bullets),
        }
        for e in schema.experience
        if (e.title or e.company)
    ]

    # ONE entry per degree — "degree — institution | dates" combined on a single
    # line so the "Education: N" stat counts qualifications, not lines. The
    # per-degree DETAILS (coursework, thesis, project bullets) are NOT flattened
    # in — each was becoming its own "education entry", inflating the count to a
    # meaningless number (28 for a 2-degree CV). Details stay in raw_text.
    # (Education is NOT a CV-highlight source, so combining is display-safe.)
    education_lines: list[str] = []
    for edu in schema.education:
        parts: list[str] = []
        if edu.degree:
            parts.append(edu.degree)
        if edu.institution:
            inst = edu.institution
            if edu.dates:
                inst += f" | {edu.dates}"
            parts.append(inst)
        if parts:
            education_lines.append(" — ".join(parts))

    return CVData(
        raw_text=raw_text,
        skills=skills,
        job_titles=job_titles,
        companies=companies,
        education=education_lines,
        certifications=list(schema.certifications),
        summary=schema.summary or "",
        experience_text="\n".join(experience_lines),
        cv_positions=cv_positions,
        name=schema.name or "",
        headline=schema.headline or "",
        location=schema.location or "",
        achievements=list(schema.achievements),
        career_domain=schema.career_domain.value if schema.career_domain else None,
        # Review fix #1 — CVSchema.industries + CVSchema.languages were
        # being silently dropped. They are now plumbed through to the
        # matching CVData fields so the JSON Resume export shows real
        # values for CVs that list them.
        cv_industries=list(schema.industries),
        cv_languages=list(schema.languages),
        # Asked for by the prompt, declared by the schema, and until 2026-08-09
        # dropped right here.
        cv_experience_level=(schema.experience_level or "").strip(),
        cv_right_to_work=(schema.right_to_work or "").strip(),
        cv_skills_esco=cv_skills_esco,
    )
