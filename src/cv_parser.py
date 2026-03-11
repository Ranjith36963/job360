"""CV Parser — extracts skills profile from PDF/DOCX CVs."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config.keywords import (
    JOB_TITLES,
    PRIMARY_SKILLS,
    SECONDARY_SKILLS,
    TERTIARY_SKILLS,
    LOCATIONS,
)
from src.config.settings import CV_PROFILE_PATH

logger = logging.getLogger("job360.cv_parser")


def extract_text(file_path: str) -> str:
    """Extract raw text from a PDF or DOCX file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n".join(pages)
    elif suffix == ".docx":
        from docx import Document
        doc = Document(str(path))
        return "\n".join(para.text for para in doc.paragraphs)
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .pdf or .docx")


def _match_terms(text: str, master_list: list) -> list:
    """Return all terms from master_list found in text (case-insensitive)."""
    text_lower = text.lower()
    return [term for term in master_list if term.lower() in text_lower]


def extract_profile(text: str) -> dict:
    """Match CV text against master keyword lists and return a profile dict."""
    return {
        "job_titles": _match_terms(text, JOB_TITLES),
        "primary_skills": _match_terms(text, PRIMARY_SKILLS),
        "secondary_skills": _match_terms(text, SECONDARY_SKILLS),
        "tertiary_skills": _match_terms(text, TERTIARY_SKILLS),
        "locations": _match_terms(text, LOCATIONS),
        "source_file": "",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def save_profile(profile: dict, path: Path | None = None) -> Path:
    """Write profile dict as JSON. Returns the path written to."""
    dest = path or CV_PROFILE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(profile, indent=2))
    logger.info("CV profile saved to %s", dest)
    return dest


def load_profile(path: Path | None = None) -> dict | None:
    """Load profile from JSON. Returns None if file doesn't exist."""
    src = path or CV_PROFILE_PATH
    if not src.exists():
        return None
    return json.loads(src.read_text())
