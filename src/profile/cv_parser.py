"""PDF/DOCX text extraction and CV section parsing."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.profile.models import CVData

logger = logging.getLogger("job360.profile.cv_parser")

# Section header patterns (case-insensitive)
_SECTION_PATTERNS = {
    "skills": re.compile(
        r'^(?:skills|technical\s+skills|core\s+skills|key\s+skills|'
        r'core\s+competencies|professional\s+skills|it\s+skills|'
        r'competencies|technologies|tools|technical\s+and\s+it\s+skills|'
        r'technical\s+competencies|areas\s+of\s+expertise)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "experience": re.compile(
        r'^(?:experience|work\s+experience|employment|professional\s+experience|'
        r'career\s+history|work\s+history|relevant\s+work\s+experience|'
        r'practical\s+experience|professional\s+background|'
        r'employment\s+history|positions?\s+held)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "education": re.compile(
        r'^(?:education|qualifications|academic|degrees|'
        r'education\s+and\s+qualifications|academic\s+qualifications)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "certifications": re.compile(
        r'^(?:certifications?|certificates?|accreditations?|licenses?|'
        r'courses?\s*(?:and|&)\s*certifications?|professional\s+development)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "projects": re.compile(
        r'^(?:projects?|personal\s+projects?|key\s+projects?|portfolio)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "summary": re.compile(
        r'^(?:summary|profile|objective|about\s+me|personal\s+statement|overview)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
}

# Pattern to detect "Title at Company" in experience sections
_TITLE_AT_COMPANY = re.compile(
    r'^(.+?)\s+(?:at|@|-|–|,)\s+(.+?)$',
    re.MULTILINE,
)

# Lines that look like date ranges — NOT job titles
_DATE_LINE_RE = re.compile(
    r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|'
    r'Dec(?:ember)?)\s+\d{4}',
    re.IGNORECASE,
)

# Delimiters for splitting skill lists
_SKILL_DELIMITERS = re.compile(r'[,;|•·▪\n]')

# Valid single-character programming language names
SINGLE_CHAR_SKILLS = {"R", "C"}

# Pattern for capitalized tool/technology names (fallback extraction)
_TECH_NAME = re.compile(r'\b[A-Z][a-zA-Z0-9+#.]*(?:\s+[A-Z][a-zA-Z0-9+#.]*){0,2}\b')


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
        logger.error(f"Failed to read PDF {file_path}: {e}")
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
        logger.error(f"Failed to read DOCX {file_path}: {e}")
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
        logger.warning(f"Legacy .doc format not supported. Please convert to .docx: {file_path}")
        return ""
    else:
        logger.warning(f"Unsupported file type: {ext}")
        return ""


def _find_sections(text: str) -> dict[str, str]:
    """Split CV text into named sections."""
    # Find all section headers and their positions
    matches = []
    for section_name, pattern in _SECTION_PATTERNS.items():
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), section_name))

    if not matches:
        return {"full_text": text}

    matches.sort(key=lambda x: x[0])
    sections = {}
    for i, (start, end, name) in enumerate(matches):
        next_start = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        sections[name] = text[end:next_start].strip()

    sections["full_text"] = text
    return sections


def _extract_skills_from_text(text: str) -> list[str]:
    """Extract skills from a skills section by splitting on common delimiters."""
    items = _SKILL_DELIMITERS.split(text)
    skills = []
    for item in items:
        cleaned = item.strip().strip("-•·▪?  ")
        # Skills are short phrases (1-4 words typically), not sentences
        if not cleaned:
            continue
        word_count = len(cleaned.split())
        if word_count > 5:
            continue  # Too long — probably a sentence, not a skill
        if len(cleaned) > 50:
            continue
        if len(cleaned) < 2 and cleaned not in SINGLE_CHAR_SKILLS:
            continue
        # Skip lines that look like descriptions
        if cleaned.lower().startswith(("achieved", "managed", "developed", "led ", "worked")):
            continue
        skills.append(cleaned)
    return skills


def _extract_titles_from_experience(text: str) -> list[str]:
    """Extract job titles from experience section text.

    Filters out lines that are actually date ranges or company+date lines
    (e.g. 'Calnex Solutions | June 2025 - Present').
    """
    titles = []
    for match in _TITLE_AT_COMPANY.finditer(text):
        title = match.group(1).strip()
        company = match.group(2).strip()
        if not (3 < len(title) < 80):
            continue
        # Skip if the "title" contains a date (it's really a company+date line)
        if _DATE_LINE_RE.search(title):
            continue
        # Skip if the "company" is just a date word like "Present", "Current"
        if company.lower() in ("present", "current", "now", "ongoing"):
            continue
        titles.append(title)
    return titles


def _extract_tech_names(text: str) -> list[str]:
    """Fallback: extract capitalized technology names from full text."""
    matches = _TECH_NAME.findall(text)
    # Filter out common non-tech words
    noise = {
        "The", "This", "That", "With", "From", "Have", "Been", "Will",
        "About", "Summary", "Education", "Experience", "Skills",
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
        "University", "College", "School", "London", "Manchester",
    }
    return [m for m in matches if m not in noise and len(m) > 1]


def _extract_known_skills(text: str) -> list[str]:
    """Extract skills by matching against the KNOWN_SKILLS database.

    This catches skills embedded in experience bullets, education descriptions,
    or anywhere in the CV — even without a dedicated 'Skills' section.
    Uses word-boundary matching to avoid false positives.
    """
    try:
        from src.config.keywords import KNOWN_SKILLS
    except ImportError:
        return []

    # Short acronyms that commonly appear in non-tech text as regular words
    _AMBIGUOUS_SHORT = {
        "r", "c", "go", "rest", "soc", "rag", "arm", "gin",
        "make", "lua", "dart", "chai", "express", "unity", "lean",
        "eks", "iam", "dba", "cto",
    }

    found: list[str] = []
    for skill in KNOWN_SKILLS:
        # Skip ambiguous short terms — they cause false positives in non-tech CVs
        if skill.lower() in _AMBIGUOUS_SHORT:
            continue
        # Word-boundary match for all skills
        pattern = r'\b' + re.escape(skill) + r'\b'
        if re.search(pattern, text, re.IGNORECASE):
            found.append(skill)
    return found


def _extract_known_titles(text: str) -> list[str]:
    """Extract job titles by matching against KNOWN_TITLE_PATTERNS.

    Catches titles mentioned anywhere in the CV — profile paragraphs,
    experience entries, or objective statements.
    Uses word-boundary matching to avoid false positives.
    """
    try:
        from src.config.keywords import KNOWN_TITLE_PATTERNS
    except ImportError:
        return []

    # Short abbreviations that appear in non-relevant context
    _AMBIGUOUS_TITLES = {"dba", "cto", "sre", "sdet", "professor", "doctor"}

    found: list[str] = []
    for title in KNOWN_TITLE_PATTERNS:
        if title.lower() in _AMBIGUOUS_TITLES:
            continue
        pattern = r'\b' + re.escape(title) + r'\b'
        if re.search(pattern, text, re.IGNORECASE):
            found.append(title)
    return found


def _extract_titles_from_entries(text: str) -> list[str]:
    """Extract job titles from varied experience entry formats.

    Handles:
      - "Senior Developer, Google, London"
      - "Senior Developer | Google | 2020-2023"
      - "Senior Developer at Google"
      - "Registered Nurse, Delvaney Health Group, York"
    """
    titles: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or len(line) < 8 or len(line) > 100:
            continue
        # Skip bullet points, dates, blank-ish lines
        if line.startswith(("•", "-", "·", "*", "?")) or _DATE_LINE_RE.match(line):
            continue
        # Skip lines starting with digits (dates like "2020", "06/2017")
        if re.match(r'^[\d/]', line):
            continue
        # Skip lines that look like descriptions (too many words or lowercase start)
        words = line.split()
        if len(words) > 8:
            continue

        # Pattern: "Title, Company, Location" or "Title | Company"
        parts = re.split(r'\s*[,|]\s*', line)
        if 2 <= len(parts) <= 4:
            candidate = parts[0].strip()
            if 3 < len(candidate) < 60 and not _DATE_LINE_RE.search(candidate):
                # Must start with capital and not be a URL/email
                if candidate[0].isupper() and "http" not in candidate and "@" not in candidate:
                    # Must not be a pure date or location
                    if not re.match(r'^(?:Sept|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Oct|Nov|Dec)', candidate):
                        titles.append(candidate)
    return titles


def parse_cv(file_path: str) -> CVData:
    """Parse a CV file and extract structured data."""
    raw_text = extract_text(file_path)
    if not raw_text:
        return CVData()

    sections = _find_sections(raw_text)
    cv = CVData(raw_text=raw_text)

    # Extract skills — layered approach
    if "skills" in sections:
        cv.skills = _extract_skills_from_text(sections["skills"])
    if len(cv.skills) < 5:
        # Supplement with KNOWN_SKILLS scan across full text
        known = _extract_known_skills(raw_text)
        existing_lower = {s.lower() for s in cv.skills}
        for skill in known:
            if skill.lower() not in existing_lower:
                cv.skills.append(skill)
                existing_lower.add(skill.lower())
    if not cv.skills:
        # Final fallback: capitalized tech names
        cv.skills = _extract_tech_names(raw_text)[:30]
    cv.skills = cv.skills[:50]  # Cap at 50 skills

    # Extract job titles — layered approach
    if "experience" in sections:
        cv.job_titles = _extract_titles_from_experience(sections["experience"])
    if len(cv.job_titles) < 2:
        # Try entry-based extraction from experience section
        exp_text = sections.get("experience", "")
        if exp_text:
            entry_titles = _extract_titles_from_entries(exp_text)
            existing_lower = {t.lower() for t in cv.job_titles}
            for t in entry_titles:
                if t.lower() not in existing_lower:
                    cv.job_titles.append(t)
                    existing_lower.add(t.lower())
    if len(cv.job_titles) < 2:
        # Supplement with KNOWN_TITLE_PATTERNS from full text
        known_titles = _extract_known_titles(raw_text)
        existing_lower = {t.lower() for t in cv.job_titles}
        for t in known_titles:
            if t.lower() not in existing_lower:
                cv.job_titles.append(t)
                existing_lower.add(t.lower())

    # Extract education
    if "education" in sections:
        lines = [l.strip() for l in sections["education"].split("\n") if l.strip()]
        cv.education = lines[:10]

    # Extract certifications
    if "certifications" in sections:
        lines = [l.strip() for l in sections["certifications"].split("\n") if l.strip()]
        cv.certifications = lines[:10]

    # Extract summary
    if "summary" in sections:
        cv.summary = sections["summary"][:500]

    # Structured parsing (work experience, education, projects, seniority)
    try:
        from src.profile.cv_structured_parser import enhance_cv_data
        cv = enhance_cv_data(cv, sections)
    except Exception as e:
        logger.warning(f"Structured CV parsing failed (continuing with flat data): {e}")

    # Optional LLM enrichment
    try:
        from src.profile.cv_summarizer import is_configured, extract_from_cv_text, merge_llm_extraction
        if is_configured():
            logger.info("LLM configured — supplementing regex CV parsing with AI extraction")
            extraction = extract_from_cv_text(raw_text)
            cv = merge_llm_extraction(cv, extraction)
    except ImportError:
        pass  # LLM libraries not installed — continue with regex-only parsing
    except Exception as e:
        logger.warning(f"LLM CV enrichment failed (continuing with regex results): {e}")

    return cv


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
            pass  # File locked or already removed
