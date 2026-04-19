"""CV text extraction (PDF/DOCX) and LLM-powered analysis.

Text extraction uses pdfplumber/python-docx (binary file reading).
All understanding, extraction, and classification is done by LLM.
Zero hardcoded patterns, zero domain-specific regex, zero keyword lists.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.services.profile.models import CVData

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


def extract_sections_from_pdf(file_path: str) -> dict[str, str] | None:
    """Batch 1.7 — layout-aware PDF section extraction.

    Pulls word-level metadata (``fontname``, ``size``, ``top``, ``x0``)
    from each page and hands it to ``layout.segment_sections_from_words``
    for font-size clustering. Returns ``None`` (not empty-dict) when the
    PDF can't be opened — that lets callers fall back to the flat
    ``extract_text_from_pdf`` path without ambiguity.
    """
    try:
        import pdfplumber
    except ImportError:
        return None

    from src.services.profile.layout import segment_sections_from_words

    all_words: list[dict] = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                try:
                    page_words = page.extract_words(extra_attrs=["fontname", "size"])
                except Exception as e:  # noqa: BLE001
                    logger.debug("extract_words failed on page %d of %s: %s", page_idx, file_path, e)
                    continue
                for w in page_words:
                    w["page"] = page_idx
                all_words.extend(page_words)
    except Exception as e:
        logger.warning("Failed to read PDF for layout extraction %s: %s", file_path, e)
        return None

    if not all_words:
        return None
    return segment_sections_from_words(all_words)


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
    """Parse a CV file using LLM analysis. Works for ANY professional domain.

    Batch 1.1 — routes through ``llm_extract_validated`` with
    ``CVSchema`` so LLM output is type-checked at the boundary and
    self-corrected on validation failure (up to 2 retries). The raw
    dict path via ``_llm_result_to_cvdata`` is retained below as an
    untyped fallback for callers that pass pre-fetched dicts.
    """
    raw_text = extract_text(file_path)
    if not raw_text:
        raise RuntimeError(
            f"Failed to extract text from {file_path}. "
            "File may be corrupted, empty, or in an unsupported format. "
            "Only PDF and DOCX files are supported."
        )

    from src.services.profile.llm_provider import llm_extract, llm_extract_validated
    from src.services.profile.schemas import CVSchema, cv_schema_to_cvdata

    prompt = _CV_PROMPT.format(cv_text=raw_text)

    try:
        schema = await llm_extract_validated(prompt, CVSchema, system=_CV_SYSTEM)
        return cv_schema_to_cvdata(schema, raw_text)
    except RuntimeError as e:
        # Review fix #3 — preserve pre-Batch-1.1 graceful-degradation
        # contract. Validation exhaustion (LLM produced JSON that
        # couldn't be coerced after retries) falls back to the
        # defensive path so callers still get a best-effort CVData.
        # Genuine provider-chain failures (no API keys, all providers
        # down) still raise so operators are alerted.
        msg = str(e).lower()
        if "validation" in msg:
            logger.warning(
                "CVSchema validation exhausted retries; using defensive coercion: %s", e
            )
            try:
                raw = await llm_extract(prompt, system=_CV_SYSTEM)
                return _llm_result_to_cvdata(raw_text, raw)
            except Exception as e2:  # noqa: BLE001
                logger.warning(
                    "Defensive fallback also failed; returning CVData with raw_text only: %s", e2
                )
                return CVData(raw_text=raw_text)
        logger.error("LLM CV analysis failed: %s", e)
        raise


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


from src.services.profile._llm_utils import coerce_str as _coerce_str
from src.services.profile._llm_utils import coerce_str_list as _coerce_str_list


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


