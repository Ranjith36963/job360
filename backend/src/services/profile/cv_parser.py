"""CV text extraction (PDF/DOCX) and LLM-powered analysis.

Text extraction uses pdfplumber/python-docx (binary file reading).
All understanding, extraction, and classification is done by LLM.
Zero hardcoded patterns, zero domain-specific regex, zero keyword lists.
"""

from __future__ import annotations

import asyncio
import logging
import re
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

_SECTION_HINT_HEADINGS = (
    "summary",
    "experience",
    "education",
    "skills",
    "certifications",
    "projects",
    "achievements",
)


def _build_section_hint(file_path: str) -> str:
    """Batch 1.7b — pre-segment the PDF via font-size clustering and
    emit a compact hint block the LLM can use as structural guidance.

    Returns an empty string when the file isn't a PDF, pdfplumber
    can't read it, no sections are detected, or no recognised heading
    has a body. On success returns a ``SECTIONS_HINT:\\n[KEY]\\n
    body\\n...`` block suitable for appending to the prompt — the
    main prompt still hands the LLM the full raw text, so this hint
    supplements rather than replaces. Matches plan §4.7's "pre-
    segmented sections reduce ambiguity, do not gate extraction".
    """
    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        return ""
    sections = extract_sections_from_pdf(file_path)
    if not sections:
        return ""

    parts: list[str] = []
    for key in _SECTION_HINT_HEADINGS:
        body = sections.get(key, "").strip()
        if body:
            # Truncate to keep the hint compact; the main prompt
            # already has the full text, so hints stay brief.
            if len(body) > 1200:
                body = body[:1200] + "…"
            parts.append(f"[{key.upper()}]\n{body}")
    if not parts:
        return ""
    return (
        "\n\nPRE-SEGMENTED SECTIONS (from PDF layout analysis — use as "
        "structural hints; the full raw text above is authoritative):\n" + "\n\n".join(parts)
    )


# ── CV deterministic pass (Pass 1) — no LLM, plain text heuristics ──

_DET_SKILL_HEADINGS = {
    "skills", "technical skills", "core skills", "key skills",
    "competencies", "technical competencies", "areas of expertise",
}
_DET_SUMMARY_HEADINGS = {
    "summary", "profile", "professional summary", "about", "about me",
    "objective", "personal statement",
}
# Any heading that ends a section body. Broad on purpose so a skills block
# stops at the next section even when that section isn't one we extract.
_DET_OTHER_HEADINGS = {
    "experience", "work experience", "employment", "education", "projects",
    "certifications", "licenses & certifications", "achievements", "awards",
    "publications", "references", "interests", "languages", "contact",
    "volunteer experience", "courses",
}
_DET_ALL_HEADINGS = _DET_SKILL_HEADINGS | _DET_SUMMARY_HEADINGS | _DET_OTHER_HEADINGS

# Split a skills line on commas, pipes, slashes, bullets, semicolons.
_DET_SKILL_SPLIT = re.compile(r"[,•·|;/]+")


def _det_heading_key(line: str) -> str:
    """Normalise a line for heading comparison: lowercase, strip, drop a
    trailing colon. Returns '' for lines too long to be a heading."""
    t = line.strip().rstrip(":").strip().lower()
    # Real headings are short. Guards against a sentence that happens to
    # start with 'Summary of my work ...' being treated as a heading.
    if len(t) > 30:
        return ""
    return t


def _det_collect_section(lines: list[str], heading_set: set) -> list[str]:
    """Return the body lines under the first heading in ``heading_set``,
    stopping at the next recognised heading. Empty list when absent."""
    out: list[str] = []
    capturing = False
    for line in lines:
        key = _det_heading_key(line)
        if not capturing:
            if key in heading_set:
                capturing = True
            continue
        # capturing
        if key and key in _DET_ALL_HEADINGS:
            break  # next section starts
        if line.strip():
            out.append(line.strip())
    return out


def deterministic_cv_fields(raw_text: str) -> dict:
    """Pass 1 for the CV — pull base fields from text with NO LLM.

    Conservative by design: only the clearly-delimited "Skills" and
    "Summary" sections are read. Returns ``{"skills": [...], "summary": str}``.
    The LLM pass later enhances this; this pass guarantees *something* lands
    even when no LLM key is configured, and lets the orchestrator re-run on a
    later change from the stored ``raw_text``.
    """
    if not raw_text or not raw_text.strip():
        return {"skills": [], "summary": ""}
    lines = raw_text.splitlines()

    skill_lines = _det_collect_section(lines, _DET_SKILL_HEADINGS)
    skills: list[str] = []
    seen: set[str] = set()
    for line in skill_lines:
        for token in _DET_SKILL_SPLIT.split(line):
            tok = token.strip()
            if tok and tok.lower() not in seen:
                skills.append(tok)
                seen.add(tok.lower())

    summary = " ".join(_det_collect_section(lines, _DET_SUMMARY_HEADINGS)).strip()
    return {"skills": skills, "summary": summary}


async def parse_cv_async(file_path: str) -> CVData:
    """Parse a CV file using LLM analysis. Works for ANY professional domain.

    Batch 1.1 — routes through ``llm_extract_validated`` with
    ``CVSchema`` so LLM output is type-checked at the boundary and
    self-corrected on validation failure (up to 2 retries).

    Batch 1.7b — when the input is a PDF, font-size section
    segmentation supplements the raw text with a pre-segmented hint
    block. The LLM still sees the full raw text; hints just reduce
    ambiguity on multi-column or heading-heavy layouts. Graceful
    no-op for non-PDFs / pdfplumber failures / no-sections cases.

    The untyped ``_llm_result_to_cvdata`` adapter is kept below as a
    fallback for callers that pass pre-fetched dicts OR when strict
    validation fails after all retries (review fix #3).
    """
    raw_text = extract_text(file_path)
    if not raw_text:
        raise RuntimeError(
            f"Failed to extract text from {file_path}. "
            "File may be corrupted, empty, or in an unsupported format. "
            "Only PDF and DOCX files are supported."
        )

    # PDF-only font-size section hint (needs the file). The LLM extraction
    # itself works purely off text — see ``llm_cv_fields_from_text``.
    section_hint = _build_section_hint(file_path)
    return await llm_cv_fields_from_text(raw_text, section_hint=section_hint)


async def llm_cv_fields_from_text(raw_text: str, section_hint: str = "") -> CVData:
    """Pass 2 for the CV — LLM extraction from raw text only (no file needed).

    Factored out of ``parse_cv_async`` so the two-pass orchestrator can re-run
    the CV LLM pass on a later profile change from the stored ``cv.raw_text``,
    without the original upload. Same validation + graceful-degradation
    contract as the file path (Batch 1.1 / review fix #3).
    """
    from src.services.profile.llm_provider import llm_extract, llm_extract_validated
    from src.services.profile.schemas import CVSchema, cv_schema_to_cvdata

    prompt = _CV_PROMPT.format(cv_text=raw_text) + section_hint

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
            logger.warning("CVSchema validation exhausted retries; using defensive coercion: %s", e)
            try:
                raw = await llm_extract(prompt, system=_CV_SYSTEM)
                return _llm_result_to_cvdata(raw_text, raw)
            except Exception as e2:  # noqa: BLE001
                logger.warning("Defensive fallback also failed; returning CVData with raw_text only: %s", e2)
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


from src.services.profile._llm_utils import coerce_str as _coerce_str  # noqa: E402
from src.services.profile._llm_utils import coerce_str_list as _coerce_str_list  # noqa: E402


def _maybe_normalise_skills_via_esco(
    skills: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Step-1.5 S1.5-D — ESCO-normalise raw skill strings when the
    ``SEMANTIC_ENABLED`` flag is on AND the ESCO index artefacts are on
    disk. Returns ``(canonical_skills, {canonical_label: esco_uri})``.

    Skills with no confident match (cosine < 0.55) pass through unchanged
    and contribute no entry to the URI map. The flag-off / no-data path
    is the identity transform → graceful no-op (CLAUDE.md rule #18).

    The normaliser singleton lazy-loads sentence-transformers + the
    embedding matrix on first call, then caches both for the process
    lifetime — calling per-skill in a loop is intentional and cheap.
    """
    from src.core.settings import SEMANTIC_ENABLED  # noqa: PLC0415 — lazy

    if not SEMANTIC_ENABLED:
        return skills, {}
    try:
        from src.services.profile.skill_normalizer import (  # noqa: PLC0415
            is_available,
            normalize_skill,
        )
    except Exception:
        return skills, {}
    if not is_available():
        return skills, {}

    canonical: list[str] = []
    esco_map: dict[str, str] = {}
    for raw in skills:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            match = normalize_skill(raw)
        except Exception:
            match = None
        if match is not None and match.label:
            canonical.append(match.label)
            esco_map[match.label] = match.uri
        else:
            canonical.append(raw)
    return canonical, esco_map


def _llm_result_to_cvdata(raw_text: str, result: dict) -> CVData:
    """Convert LLM JSON response to CVData dataclass.

    Defensive: all fields are type-guarded so weaker LLMs (Cerebras llama3.1-8b,
    Groq llama-3.3-70b) that deviate from the schema don't crash the parser.
    """
    # Scoring-semantic fields (flow into SearchConfig)
    skills = _coerce_str_list(result.get("skills"))
    # Step-1.5 S1.5-D/E — opt-in ESCO normalisation. No-op when
    # SEMANTIC_ENABLED is false or the ESCO index is missing.
    skills, cv_skills_esco = _maybe_normalise_skills_via_esco(skills)

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
        cv_skills_esco=cv_skills_esco,
    )
