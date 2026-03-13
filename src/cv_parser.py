"""CV Parser — extracts a personalised skills profile from any PDF/DOCX CV.

Key design: skills are discovered from the CV text itself using the broad
KNOWN_SKILLS database, then **auto-categorised** into primary / secondary /
tertiary based on frequency and position in the document.  This means a Java
developer's CV produces a completely different profile to an AI engineer's.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.config.keywords import (
    # Legacy fixed lists — used as fallback when no CV is uploaded
    JOB_TITLES,
    PRIMARY_SKILLS,
    SECONDARY_SKILLS,
    TERTIARY_SKILLS,
    LOCATIONS,
    # Broad multi-domain databases for open-ended extraction
    KNOWN_SKILLS,
    KNOWN_TITLE_PATTERNS,
    KNOWN_LOCATIONS,
)
from src.config.settings import CV_PROFILE_PATH

logger = logging.getLogger("job360.cv_parser")


# ---------------------------------------------------------------------------
# Text extraction (PDF / DOCX)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Legacy matcher (kept for backward compat in tests)
# ---------------------------------------------------------------------------

def _match_terms(text: str, master_list: list) -> list:
    """Return all terms from master_list found in text (case-insensitive)."""
    text_lower = text.lower()
    return [term for term in master_list if term.lower() in text_lower]


# ---------------------------------------------------------------------------
# Smart extraction helpers
# ---------------------------------------------------------------------------

_SECTION_HEADERS = re.compile(
    r"(?:^|\n)\s*(?:#+\s*)?("
    r"skills?|technical\s+skills?|core\s+competenc|"
    r"technologies|tech\s+stack|proficien|"
    r"tools?\s*(?:&|and)?\s*technologies|"
    r"key\s+skills?"
    r")[\s:]*(?:\n|$)",
    re.IGNORECASE,
)


def _find_skills_in_text(text: str) -> Counter:
    """Scan *text* against the KNOWN_SKILLS database.

    Returns a Counter mapping each found skill to the number of times
    it appears.  Matching is case-insensitive and uses word-boundary
    checks for short terms to avoid false positives (e.g. "R" inside
    "Research").
    """
    text_lower = text.lower()
    counts: Counter = Counter()

    for skill in KNOWN_SKILLS:
        skill_lower = skill.lower()
        # For very short terms (<=2 chars) use word-boundary regex
        if len(skill_lower) <= 2:
            pattern = rf"\b{re.escape(skill_lower)}\b"
            n = len(re.findall(pattern, text_lower))
        else:
            n = text_lower.count(skill_lower)
        if n > 0:
            counts[skill] = n

    return counts


def _find_job_titles(text: str) -> list[str]:
    """Extract job titles mentioned in the CV text."""
    text_lower = text.lower()
    found = []
    for title in KNOWN_TITLE_PATTERNS:
        if title.lower() in text_lower:
            found.append(title)
    return found


def _find_locations(text: str) -> list[str]:
    """Extract locations mentioned in the CV text."""
    text_lower = text.lower()
    found = []
    for loc in KNOWN_LOCATIONS:
        loc_lower = loc.lower()
        # Word-boundary check for short location names
        if len(loc_lower) <= 3:
            if re.search(rf"\b{re.escape(loc_lower)}\b", text_lower):
                found.append(loc)
        else:
            if loc_lower in text_lower:
                found.append(loc)
    return found


def _is_in_skills_section(text: str, skill: str) -> bool:
    """Heuristic: is *skill* mentioned near a skills-section header?"""
    for m in _SECTION_HEADERS.finditer(text):
        # Look at the ~600 chars after the header
        window = text[m.end(): m.end() + 600].lower()
        if skill.lower() in window:
            return True
    return False


def _categorise_skills(
    skill_counts: Counter,
    full_text: str,
) -> tuple[list[str], list[str], list[str]]:
    """Split found skills into primary / secondary / tertiary.

    Categorisation rules (in priority order):
    1. Skills in the "Skills" section AND mentioned 2+ times → primary
    2. Skills mentioned 3+ times → primary
    3. Skills in the "Skills" section OR mentioned 2 times → secondary
    4. Everything else → tertiary
    """
    primary: list[str] = []
    secondary: list[str] = []
    tertiary: list[str] = []

    for skill, count in skill_counts.most_common():
        in_skills_section = _is_in_skills_section(full_text, skill)

        if (in_skills_section and count >= 2) or count >= 3:
            primary.append(skill)
        elif in_skills_section or count >= 2:
            secondary.append(skill)
        else:
            tertiary.append(skill)

    return primary, secondary, tertiary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_profile(text: str) -> dict:
    """Extract a personalised profile from CV text.

    Uses the broad KNOWN_SKILLS / KNOWN_TITLE_PATTERNS / KNOWN_LOCATIONS
    databases so that ANY CV (Java dev, accountant, AI engineer, etc.)
    produces a meaningful profile.
    """
    if not text.strip():
        return {
            "job_titles": [],
            "primary_skills": [],
            "secondary_skills": [],
            "tertiary_skills": [],
            "locations": [],
            "source_file": "",
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }

    # 1. Discover skills with frequency counts
    skill_counts = _find_skills_in_text(text)

    # 2. Auto-categorise by frequency + section position
    primary, secondary, tert = _categorise_skills(skill_counts, text)

    # 3. Extract job titles and locations
    titles = _find_job_titles(text)
    locations = _find_locations(text)

    profile = {
        "job_titles": titles,
        "primary_skills": primary,
        "secondary_skills": secondary,
        "tertiary_skills": tert,
        "locations": locations,
        "source_file": "",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }

    total = len(primary) + len(secondary) + len(tert)
    logger.info(
        "Extracted profile: %d titles, %d skills (%d/%d/%d), %d locations",
        len(titles), total, len(primary), len(secondary), len(tert),
        len(locations),
    )
    return profile


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
