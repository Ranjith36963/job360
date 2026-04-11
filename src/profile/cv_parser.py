"""CV text extraction (PDF/DOCX) and LLM-powered analysis.

Text extraction uses pdfplumber/python-docx (binary file reading).
All understanding, extraction, and classification is done by LLM.
Zero hardcoded patterns, zero domain-specific regex, zero keyword lists.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.profile.models import CVData

logger = logging.getLogger("job360.profile.cv_parser")

# ── LLM prompt for CV analysis ──────────────────────────────────

_CV_SYSTEM = """You are an expert CV/resume analyst. You extract ALL professional information from CVs across ANY domain — technology, medical, legal, construction, finance, education, or any other field.

Your job is to extract EVERYTHING a recruiter or job matching engine would need. Miss nothing. Every skill, every achievement, every role, every metric, every certification matters.

You return structured JSON. Nothing else."""

_CV_PROMPT = """Analyze this CV/resume text and extract ALL professional information. Be exhaustive — extract every single skill, technology, tool, methodology, certification, achievement, and qualification mentioned anywhere in the document.

Return a JSON object with exactly these fields:

{{
  "name": "Full name of the candidate",
  "headline": "Their stated role/title from the CV header (e.g., 'AI/ML Engineer | Generative AI Specialist' or 'Cardiology Consultant')",
  "location": "Their location (e.g., 'United Kingdom', 'London')",
  "summary": "Their professional summary paragraph, verbatim from the CV",
  "skills": [
    "Every skill, technology, tool, framework, methodology, domain expertise mentioned ANYWHERE in the CV. Include compound terms like 'AWS Bedrock', 'Docker deployment', 'HIPAA compliance', 'Contract negotiation'. Include soft skills, domain-specific skills, certification topics. Be exhaustive — if they mentioned it, extract it."
  ],
  "experience": [
    {{
      "company": "Company name",
      "title": "Job title/role",
      "dates": "Date range as written",
      "location": "Location if mentioned",
      "bullets": ["Each achievement/responsibility as a separate string"]
    }}
  ],
  "education": [
    {{
      "degree": "Degree name",
      "institution": "University/school name",
      "dates": "Date range",
      "details": ["Coursework, dissertation, projects — each as separate string"]
    }}
  ],
  "certifications": [
    "Each certification with issuer and date, as a single string"
  ],
  "achievements": [
    "Every quantified achievement (percentages, metrics, time improvements, cost savings). Extract the full phrase, e.g., 'achieving 95% response accuracy', 'reducing query latency by 35%'"
  ],
  "experience_level": "One of: intern, junior, mid, senior, lead, principal, director — infer from experience duration and roles",
  "industries": ["Industries/domains they have experience in"],
  "languages": ["Human languages they speak, if mentioned"]
}}

RULES:
1. Extract EVERYTHING. If in doubt, include it. A missed skill means a missed job match.
2. Skills should be individual items, not categories. "Python" not "Programming Languages: Python".
3. For compound tools, keep them together: "AWS Bedrock" not just "AWS" and "Bedrock" separately.
4. Include achievements with their metrics: "achieving 90% accuracy" not just "90%".
5. If something appears in both the skills section AND experience bullets, include it once in skills.
6. Domain-agnostic: whether it's "TensorFlow" or "HIPAA compliance" or "Contract negotiation" — extract it.

CV TEXT:
---
{cv_text}
---"""


# ── File reading (infrastructure — not LLM) ─────────────────────

def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a PDF file using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed. Run: pip install pdfplumber")
        return ""

    text_parts = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as e:
        logger.error("Failed to read PDF %s: %s", file_path, e)
        return ""
    return "\n".join(text_parts)


def extract_text_from_docx(file_path: str) -> str:
    """Extract text from a DOCX file using python-docx."""
    try:
        import docx
    except ImportError:
        logger.error("python-docx not installed. Run: pip install python-docx")
        return ""

    try:
        doc = docx.Document(file_path)
        return "\n".join(para.text for para in doc.paragraphs if para.text.strip())
    except Exception as e:
        logger.error("Failed to read DOCX %s: %s", file_path, e)
        return ""


def extract_text(file_path: str) -> str:
    """Extract text from PDF or DOCX based on file extension."""
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext == ".doc":
        logger.warning("Legacy .doc format not supported. Please convert to .docx: %s", file_path)
        return ""
    else:
        logger.warning("Unsupported file type: %s", ext)
        return ""


# ── LLM-powered CV analysis ─────────────────────────────────────

async def parse_cv_async(file_path: str) -> CVData:
    """Parse a CV file using LLM analysis. Works for ANY professional domain."""
    raw_text = extract_text(file_path)
    if not raw_text:
        raise RuntimeError(
            f"Failed to extract text from {file_path}. "
            "File may be corrupted, empty, or in an unsupported format. "
            "Only PDF and DOCX files are supported."
        )

    from src.profile.llm_provider import llm_extract

    prompt = _CV_PROMPT.format(cv_text=raw_text)

    try:
        result = await llm_extract(prompt, system=_CV_SYSTEM)
    except RuntimeError as e:
        logger.error("LLM CV analysis failed: %s", e)
        raise

    return _llm_result_to_cvdata(raw_text, result)


def parse_cv(file_path: str) -> CVData:
    """Synchronous wrapper for parse_cv_async (used by CLI)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already in an async context — create a new thread to avoid nested event loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(lambda: asyncio.run(parse_cv_async(file_path))).result()
    else:
        return asyncio.run(parse_cv_async(file_path))


def _coerce_str_list(value) -> list[str]:
    """Defensive coercion of LLM output to a clean list[str].

    Handles weaker models that may return strings, None, dicts, or mixed types
    for fields that should be list[str]. Never raises — always returns a list.
    """
    if value is None:
        return []
    if isinstance(value, str):
        # Weaker models sometimes return comma-separated strings
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, dict):
        # Model confused the schema — return values as strings
        return [str(v) for v in value.values() if v]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    result.append(item.strip())
            elif isinstance(item, dict):
                # Extract "name" field if present, else stringify
                name = item.get("name") or item.get("skill") or item.get("title")
                if name:
                    result.append(str(name))
            elif item is not None:
                result.append(str(item))
        return result
    return []


def _coerce_str(value) -> str:
    """Defensive coercion to a string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return ""  # Wrong type — don't use
    return str(value)


def _llm_result_to_cvdata(raw_text: str, result: dict) -> CVData:
    """Convert LLM JSON response to CVData dataclass.

    Defensive: all fields are type-guarded so weaker LLMs (Cerebras llama3.1-8b,
    Groq llama-3.3-70b) that deviate from the schema don't crash the parser.
    """
    # Scoring-semantic fields (flow into SearchConfig)
    skills = _coerce_str_list(result.get("skills"))

    # Display-only fields (NOT used in scoring — kept separate to avoid pollution)
    name = _coerce_str(result.get("name"))
    headline = _coerce_str(result.get("headline"))
    location = _coerce_str(result.get("location"))
    achievements = _coerce_str_list(result.get("achievements"))

    # Education: flatten nested dicts to list of strings for display
    education_lines: list[str] = []
    edu_raw = result.get("education", [])
    if isinstance(edu_raw, list):
        for edu in edu_raw:
            if isinstance(edu, dict):
                degree = _coerce_str(edu.get("degree"))
                institution = _coerce_str(edu.get("institution"))
                dates = _coerce_str(edu.get("dates"))
                if degree:
                    education_lines.append(degree)
                if institution:
                    line = institution
                    if dates:
                        line += f" | {dates}"
                    education_lines.append(line)
                for detail in _coerce_str_list(edu.get("details")):
                    education_lines.append(detail)
            elif isinstance(edu, str):
                education_lines.append(edu)

    # Experience: separate job_titles (roles) from companies — don't overload one field
    job_titles: list[str] = []
    companies: list[str] = []
    experience_lines: list[str] = []
    exp_raw = result.get("experience", [])
    if isinstance(exp_raw, list):
        for exp in exp_raw:
            if isinstance(exp, dict):
                company = _coerce_str(exp.get("company"))
                title = _coerce_str(exp.get("title"))
                if title:
                    job_titles.append(title)
                if company:
                    companies.append(company)
                for bullet in _coerce_str_list(exp.get("bullets")):
                    experience_lines.append(bullet)
            elif isinstance(exp, str):
                job_titles.append(exp)

    # Certifications: already type-guarded
    certifications = _coerce_str_list(result.get("certifications"))

    # Summary
    summary = _coerce_str(result.get("summary"))

    return CVData(
        raw_text=raw_text,
        # Scoring-semantic: ONLY clean skills (no name/headline/achievements pollution)
        skills=skills,
        job_titles=job_titles,
        companies=companies,
        education=education_lines,
        certifications=certifications,
        summary=summary,
        experience_text="\n".join(experience_lines),
        # Display-only (accessed via CVData.highlights property for CV viewer)
        name=name,
        headline=headline,
        location=location,
        achievements=achievements,
    )


def parse_cv_from_bytes(content: bytes, filename: str) -> CVData:
    """Parse CV from in-memory bytes (for Streamlit file_uploader)."""
    import tempfile
    import os

    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return parse_cv(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
