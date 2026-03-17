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
        r'^(?:skills|technical\s+skills|core\s+skills|key\s+skills|competencies|technologies|tools)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "experience": re.compile(
        r'^(?:experience|work\s+experience|employment|professional\s+experience|career\s+history)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "education": re.compile(
        r'^(?:education|qualifications|academic|degrees)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "certifications": re.compile(
        r'^(?:certifications?|certificates?|accreditations?|licenses?)\s*[:\-]?\s*$',
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
        cleaned = item.strip().strip("-•·▪ ")
        if cleaned and (len(cleaned) > 1 or cleaned in SINGLE_CHAR_SKILLS) and len(cleaned) < 60:
            skills.append(cleaned)
    return skills


def _extract_titles_from_experience(text: str) -> list[str]:
    """Extract job titles from experience section text."""
    titles = []
    for match in _TITLE_AT_COMPANY.finditer(text):
        title = match.group(1).strip()
        if 3 < len(title) < 80:
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


def parse_cv(file_path: str) -> CVData:
    """Parse a CV file and extract structured data."""
    raw_text = extract_text(file_path)
    if not raw_text:
        return CVData()

    sections = _find_sections(raw_text)
    cv = CVData(raw_text=raw_text)

    # Extract skills
    if "skills" in sections:
        cv.skills = _extract_skills_from_text(sections["skills"])
    if not cv.skills:
        # Fallback: try to find tech names from full text
        cv.skills = _extract_tech_names(raw_text)[:30]

    # Extract job titles from experience
    if "experience" in sections:
        cv.job_titles = _extract_titles_from_experience(sections["experience"])

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
