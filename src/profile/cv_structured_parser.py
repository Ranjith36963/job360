"""Structured CV extraction — parses work experience, education, and projects.

Enhances flat CVData with typed dataclasses (WorkExperience,
StructuredEducation, Project) and computes total experience + seniority.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from src.profile.models import (
    CVData,
    WorkExperience,
    StructuredEducation,
    Project,
)

# ── Date parsing ──────────────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Matches: "Jan 2020", "January 2020", "01/2020", "2020-01", "2020"
_DATE_RE = re.compile(
    r'(?:(\w+)\s+(\d{4}))'           # Month Year
    r'|(?:(\d{1,2})/(\d{4}))'        # MM/YYYY
    r'|(?:(\d{4})-(\d{1,2}))'        # YYYY-MM
    r'|(?:\b(\d{4})\b)',             # bare YYYY
    re.IGNORECASE,
)

# Date range: "start - end" or "start – end" or "start to end"
_DATE_RANGE_RE = re.compile(
    r'('
    r'(?:\w+\s+\d{4}|\d{1,2}/\d{4}|\d{4}-\d{1,2}|\d{4})'
    r')'
    r'\s*[-–—to]+\s*'
    r'('
    r'(?:\w+\s+\d{4}|\d{1,2}/\d{4}|\d{4}-\d{1,2}|\d{4}|[Pp]resent|[Cc]urrent|[Nn]ow|[Oo]ngoing)'
    r')',
    re.IGNORECASE,
)


def _parse_date(text: str) -> Optional[tuple[int, int]]:
    """Parse a date string into (year, month). Returns None if unparseable."""
    text = text.strip()
    if not text:
        return None
    if text.lower() in ("present", "current", "now", "ongoing"):
        now = datetime.now()
        return (now.year, now.month)

    m = _DATE_RE.search(text)
    if not m:
        return None

    # Group 1,2: "Month Year"
    if m.group(1) and m.group(2):
        month_str = m.group(1).lower()
        month = _MONTHS.get(month_str)
        year = int(m.group(2))
        if month and 1900 < year < 2100:
            return (year, month)
    # Group 3,4: "MM/YYYY"
    if m.group(3) and m.group(4):
        month = int(m.group(3))
        year = int(m.group(4))
        if 1 <= month <= 12 and 1900 < year < 2100:
            return (year, month)
    # Group 5,6: "YYYY-MM"
    if m.group(5) and m.group(6):
        year = int(m.group(5))
        month = int(m.group(6))
        if 1 <= month <= 12 and 1900 < year < 2100:
            return (year, month)
    # Group 7: bare "YYYY"
    if m.group(7):
        year = int(m.group(7))
        if 1900 < year < 2100:
            return (year, 6)  # assume mid-year

    return None


def _duration_months(start: Optional[tuple[int, int]],
                     end: Optional[tuple[int, int]]) -> int:
    """Calculate months between two (year, month) tuples."""
    if not start or not end:
        return 0
    months = (end[0] - start[0]) * 12 + (end[1] - start[1])
    return max(months, 0)


# ── Experience parsing ────────────────────────────────────────────────

# "Title at Company", "Title - Company", "Title | Company", "Title, Company"
_TITLE_COMPANY_RE = re.compile(
    r'^(.+?)\s+(?:at|@)\s+(.+?)$'
    r'|^(.+?)\s+[-–—|]\s+(.+?)$',
    re.MULTILINE,
)

# Skill extraction from experience description (inline tech mentions)
_INLINE_SKILL_RE = re.compile(
    r'\b(?:Python|Java|JavaScript|TypeScript|React|Angular|Vue|Node\.?js|Docker|'
    r'Kubernetes|AWS|Azure|GCP|SQL|PostgreSQL|MySQL|MongoDB|Redis|'
    r'TensorFlow|PyTorch|Spark|Airflow|Snowflake|dbt|Kafka|'
    r'Git|CI/CD|REST|GraphQL|Agile|Scrum|Jira|'
    r'Salesforce|HubSpot|Excel|Tableau|Power\s*BI|SAP|'
    r'NHS|CQC|ACCA|CIMA|CIPD|PRINCE2|Six\s*Sigma)\b',
    re.IGNORECASE,
)


def _parse_experience_section(text: str) -> list[WorkExperience]:
    """Parse an experience section into structured WorkExperience entries."""
    if not text or len(text) < 20:
        return []

    entries: list[WorkExperience] = []
    lines = text.split('\n')

    current: Optional[WorkExperience] = None
    desc_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Check date ranges FIRST — "Jan 2019 - Dec 2023" must not be
        # misread as "title - company" by the title/company regex.
        dr_match = _DATE_RANGE_RE.search(stripped)
        if dr_match:
            if current:
                start = _parse_date(dr_match.group(1))
                end = _parse_date(dr_match.group(2))
                current.start_date = dr_match.group(1).strip()
                current.end_date = dr_match.group(2).strip()
                current.duration_months = _duration_months(start, end)
            continue

        # Try to detect a title/company line
        tc_match = _TITLE_COMPANY_RE.match(stripped)
        if tc_match:
            # Save previous entry
            if current:
                current.description = ' '.join(desc_lines).strip()
                current.skills_used = _extract_inline_skills(current.description)
                entries.append(current)
                desc_lines = []

            title = (tc_match.group(1) or tc_match.group(3) or "").strip()
            company = (tc_match.group(2) or tc_match.group(4) or "").strip()
            if 3 < len(title) < 80:
                current = WorkExperience(title=title, company=company)
            continue

        # Otherwise, it's a description line
        if current:
            desc_lines.append(stripped)

    # Save the last entry
    if current:
        current.description = ' '.join(desc_lines).strip()
        current.skills_used = _extract_inline_skills(current.description)
        entries.append(current)

    return entries


def _extract_inline_skills(text: str) -> list[str]:
    """Extract technology/skill mentions from description text."""
    if not text:
        return []
    found = set()
    for m in _INLINE_SKILL_RE.finditer(text):
        found.add(m.group(0))
    return sorted(found)


# ── Education parsing ─────────────────────────────────────────────────

# UK degree patterns: BSc, BA, BEng, MSc, MA, MBA, PhD, PGCE, etc.
_DEGREE_RE = re.compile(
    r'\b('
    # Full-text degree names (international + UK)
    r'Master of Science|Master of Arts|Master of Engineering'
    r'|Master of Business Administration|Master of Research|Master of Philosophy'
    r'|Master of Laws|Master of Education|Master of Fine Arts'
    r'|Bachelor of Science|Bachelor of Arts|Bachelor of Engineering'
    r'|Bachelor of Laws|Bachelor of Commerce|Bachelor of Education'
    r'|Bachelor of Music|Bachelor of Technology'
    r'|Doctor of Philosophy|Doctor of Education'
    r"|Master'?s Degree|Bachelor'?s Degree|Doctoral Degree"
    # Abbreviated forms (UK standard)
    r'|PhD|DPhil|Doctorate|EdD'
    r'|MSc|MA|MEng|MRes|MPhil|MBA|LLM|MChem|MMath'
    r'|BSc|BA|BEng|LLB|BMus|BEd|BCom'
    r'|PGCE|PGDE|PGDip|PGCert'
    r'|HND|HNC|Foundation Degree'
    r'|NVQ|BTEC|Level\s*\d'
    r'|A[\s-]?Levels?|GCSEs?)\b',
    re.IGNORECASE,
)

# UK grades
_GRADE_RE = re.compile(
    r'\b(First[\s-]?Class|1st[\s-]?Class|First'
    r'|2:1|2\.1|Upper[\s-]?Second'
    r'|2:2|2\.2|Lower[\s-]?Second'
    r'|Third|3rd'
    r'|Distinction|Merit|Pass'
    r'|Cum\s*Laude|Summa\s*Cum\s*Laude|Magna\s*Cum\s*Laude)\b',
    re.IGNORECASE,
)

# "Field of Study" — typically appears after degree: "BSc Computer Science"
_FIELD_RE = re.compile(
    r'(?:in|of)?\s*([A-Z][a-zA-Z]+(?:\s+(?:and|&)\s+[A-Z][a-zA-Z]+|\s+[A-Z][a-zA-Z]+){0,4})'
)

# Year pattern (for graduation year)
_YEAR_RE = re.compile(r'\b(19\d{2}|20[0-3]\d)\b')


def _parse_education_section(text: str) -> list[StructuredEducation]:
    """Parse an education section into structured entries."""
    if not text or len(text) < 10:
        return []

    entries: list[StructuredEducation] = []
    # Split by double newlines or by detecting degree patterns
    lines = text.split('\n')
    current_block: list[str] = []
    blocks: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
        else:
            current_block.append(stripped)
    if current_block:
        blocks.append('\n'.join(current_block))

    # If no block breaks, treat each line as a potential entry
    if len(blocks) == 1 and '\n' in blocks[0]:
        blocks = [l.strip() for l in blocks[0].split('\n') if l.strip()]

    for block in blocks:
        deg_match = _DEGREE_RE.search(block)
        if not deg_match:
            continue

        edu = StructuredEducation(degree=deg_match.group(1))

        # Extract grade
        grade_match = _GRADE_RE.search(block)
        if grade_match:
            edu.grade = grade_match.group(1)

        # Extract year
        year_match = _YEAR_RE.search(block)
        if year_match:
            edu.year = int(year_match.group(1))

        # Extract field of study — text after degree name
        after_degree = block[deg_match.end():]
        field_match = _FIELD_RE.search(after_degree)
        if field_match:
            field_text = field_match.group(1).strip()
            # Filter out noise words that aren't fields
            noise = {"at", "from", "university", "college", "school", "the"}
            if field_text.lower() not in noise and len(field_text) > 2:
                edu.field_of_study = field_text

        # Extract institution — look for "University", "College", etc.
        inst_match = re.search(
            r'((?:University|College|School|Institute|Academy|Polytechnic)'
            r'(?:\s+of)?\s+[\w\s&,]+)',
            block, re.IGNORECASE,
        )
        if inst_match:
            edu.institution = inst_match.group(1).strip()[:100]

        entries.append(edu)

    return entries


# ── Project parsing ───────────────────────────────────────────────────

_URL_RE = re.compile(r'https?://\S+')


def _parse_projects_section(text: str) -> list[Project]:
    """Parse a projects section into structured entries."""
    if not text or len(text) < 10:
        return []

    entries: list[Project] = []
    lines = text.split('\n')
    current: Optional[Project] = None
    desc_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Heuristic: project name lines are short, possibly bold/caps
        # and followed by description lines
        is_heading = (
            len(stripped) < 80
            and not stripped.startswith(('-', '•', '·', '*'))
            and not _URL_RE.search(stripped)
            and stripped[0].isupper()
        )

        if is_heading and (not current or len(desc_lines) > 0):
            if current:
                current.description = ' '.join(desc_lines).strip()
                current.technologies = _extract_inline_skills(current.description)
                entries.append(current)
                desc_lines = []
            current = Project(name=stripped)
            # Check for URL
            url_match = _URL_RE.search(stripped)
            if url_match:
                current.url = url_match.group(0)
            continue

        if current:
            url_match = _URL_RE.search(stripped)
            if url_match and not current.url:
                current.url = url_match.group(0)
            desc_lines.append(stripped)

    if current:
        current.description = ' '.join(desc_lines).strip()
        current.technologies = _extract_inline_skills(current.description)
        entries.append(current)

    return entries


# ── Seniority computation ────────────────────────────────────────────

_SENIORITY_TITLE_SIGNALS: dict[str, str] = {
    "intern": "entry",
    "trainee": "entry",
    "graduate": "entry",
    "junior": "entry",
    "assistant": "entry",
    "mid": "mid",
    "senior": "senior",
    "staff": "senior",
    "principal": "lead",
    "lead": "lead",
    "head": "lead",
    "manager": "lead",
    "director": "executive",
    "vp": "executive",
    "vice president": "executive",
    "chief": "executive",
    "cto": "executive",
    "ceo": "executive",
    "cfo": "executive",
    "cio": "executive",
    "coo": "executive",
    "partner": "executive",
}


def _compute_seniority(total_months: int,
                       job_titles: list[str]) -> str:
    """Compute seniority level from experience duration and title signals."""
    # Check title signals first (highest priority)
    best_seniority = ""
    seniority_rank = {"entry": 1, "mid": 2, "senior": 3, "lead": 4, "executive": 5}

    for title in job_titles:
        title_lower = title.lower()
        for keyword, level in _SENIORITY_TITLE_SIGNALS.items():
            if keyword in title_lower:
                if not best_seniority or seniority_rank.get(level, 0) > seniority_rank.get(best_seniority, 0):
                    best_seniority = level

    if best_seniority:
        return best_seniority

    # Fall back to experience duration
    years = total_months / 12
    if years < 2:
        return "entry"
    elif years < 5:
        return "mid"
    elif years < 10:
        return "senior"
    elif years < 15:
        return "lead"
    else:
        return "executive"


# ── Main entry point ─────────────────────────────────────────────────

def enhance_cv_data(cv: CVData, sections: dict[str, str]) -> CVData:
    """Enhance CVData with structured parsing from CV sections.

    Args:
        cv: Existing CVData with flat fields already populated.
        sections: Dict of section_name -> section_text from cv_parser._find_sections().

    Returns:
        The same CVData instance, mutated with structured fields populated.
    """
    # Parse work experience
    if "experience" in sections:
        cv.work_experiences = _parse_experience_section(sections["experience"])

    # Parse education
    if "education" in sections:
        cv.structured_education = _parse_education_section(sections["education"])

    # Parse projects (if section exists)
    projects_text = sections.get("projects", "")
    if projects_text:
        cv.projects = _parse_projects_section(projects_text)

    # Compute total experience
    cv.total_experience_months = sum(
        we.duration_months for we in cv.work_experiences
    )

    # Override flat-parsed job_titles with properly structured ones
    # (the flat parser often misidentifies company+date lines as titles)
    if cv.work_experiences:
        structured_titles = [we.title for we in cv.work_experiences if we.title]
        if structured_titles:
            cv.job_titles = structured_titles

    # Compute seniority from titles + experience
    all_titles = cv.job_titles[:]
    all_titles.extend(we.title for we in cv.work_experiences)
    cv.computed_seniority = _compute_seniority(
        cv.total_experience_months, all_titles
    )

    return cv
