"""Optional LLM-powered CV summarization — supplements regex parsing.

Uses the multi-provider pool (``src.llm.client``) to distribute requests
across free LLM providers.  Gemini Flash is preferred for CV parsing
because quality matters most for a single profile-setup call.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("job360.profile.cv_summarizer")

EXTRACTION_PROMPT = """Extract the following from this CV/resume text. Only extract what is EXPLICITLY stated — do not infer or add anything not present in the text.

Return a JSON object with these fields:
- skills: list of technical and professional skills mentioned
- job_titles: list of job titles/roles held
- education: list of degrees/qualifications
- certifications: list of certifications/accreditations
- summary: a 2-3 sentence professional summary
- years_experience: estimated total years of experience (integer or null)

CV Text:
{text}

Return ONLY valid JSON, no explanation."""


@dataclass
class LLMExtraction:
    skills: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    summary: str = ""
    years_experience: int | None = None
    success: bool = True
    error: str = ""


def is_configured() -> bool:
    """Check if at least one LLM provider has an API key set."""
    from src.llm.client import is_configured as _configured
    return _configured()


def extract_from_cv_text(text: str) -> LLMExtraction:
    """Call LLM API to extract structured data from CV text."""
    from src.llm.client import llm_complete, parse_json_response
    from src.llm.providers import CV_PREFERRED_MODEL, CV_PREFERRED_PROVIDER

    if not is_configured():
        return LLMExtraction(success=False, error="No LLM providers configured")

    if len(text.strip()) < 50:
        return LLMExtraction(success=False, error="CV text too short for LLM extraction")

    prompt = EXTRACTION_PROMPT.format(text=text[:8000])

    try:
        raw = llm_complete(
            prompt,
            prefer=CV_PREFERRED_PROVIDER,
            model_override=CV_PREFERRED_MODEL,
        )
        if raw is None:
            return LLMExtraction(success=False, error="LLM returned no response")

        data = parse_json_response(raw)
        return LLMExtraction(
            skills=data.get("skills", []),
            job_titles=data.get("job_titles", []),
            education=data.get("education", []),
            certifications=data.get("certifications", []),
            summary=data.get("summary", ""),
            years_experience=data.get("years_experience"),
        )
    except json.JSONDecodeError:
        return LLMExtraction(success=False, error="Failed to parse LLM response as JSON")
    except Exception as e:
        logger.warning(f"LLM extraction failed: {e}")
        return LLMExtraction(success=False, error=str(e))


def merge_llm_extraction(cv_data, extraction: LLMExtraction):
    """Supplement CVData with LLM extraction. Never replaces existing data — only adds."""
    if not extraction.success:
        return cv_data

    # Add new skills (deduplicated)
    existing_lower = {s.lower() for s in cv_data.skills}
    for skill in extraction.skills:
        if skill.lower() not in existing_lower:
            cv_data.skills.append(skill)
            existing_lower.add(skill.lower())

    # Add new job titles
    existing_titles_lower = {t.lower() for t in cv_data.job_titles}
    for title in extraction.job_titles:
        if title.lower() not in existing_titles_lower:
            cv_data.job_titles.append(title)
            existing_titles_lower.add(title.lower())

    # Add new education
    existing_edu_lower = {e.lower() for e in cv_data.education}
    for edu in extraction.education:
        if edu.lower() not in existing_edu_lower:
            cv_data.education.append(edu)

    # Add new certifications
    existing_cert_lower = {c.lower() for c in cv_data.certifications}
    for cert in extraction.certifications:
        if cert.lower() not in existing_cert_lower:
            cv_data.certifications.append(cert)

    # Use LLM summary only if no existing summary
    if not cv_data.summary and extraction.summary:
        cv_data.summary = extraction.summary

    return cv_data
