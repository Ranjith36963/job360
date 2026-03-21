"""Parse structured information from job descriptions.

Two levels of extraction:
1. detect_job_type() — quick label (Full-time, Contract, etc.)
2. parse_jd() — full structured ParsedJD with required/preferred skills,
   experience years, qualifications, and section text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ── Job type detection (existing) ─────────────────────────────────────

# Job type patterns — ordered by specificity (most specific first)
_JOB_TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Fixed Term", re.compile(
        r'\b(fixed[\s-]?term|ftc|fixed[\s-]?term[\s-]?contract)\b', re.IGNORECASE)),
    ("Freelance", re.compile(
        r'\b(freelance|freelancer|self[\s-]?employed)\b', re.IGNORECASE)),
    ("Contract", re.compile(
        r'\b(contract(?:or)?|contracting)\b', re.IGNORECASE)),
    ("Part-time", re.compile(
        r'\b(part[\s-]?time)\b', re.IGNORECASE)),
    ("Permanent", re.compile(
        r'\b(permanent|perm)\b', re.IGNORECASE)),
    ("Full-time", re.compile(
        r'\b(full[\s-]?time)\b', re.IGNORECASE)),
]


def detect_job_type(text: str) -> str:
    """Extract job type from title + description text.

    Returns the first (most specific) match, or "" if none found.
    """
    for label, pattern in _JOB_TYPE_PATTERNS:
        if pattern.search(text):
            return label
    return ""


# ── Structured JD parsing ────────────────────────────────────────────


@dataclass
class ParsedJD:
    """Structured representation of a job description."""
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    experience_years: Optional[int] = None       # minimum years required
    qualifications: list[str] = field(default_factory=list)
    responsibilities: str = ""
    benefits: str = ""
    salary_mentioned: bool = False
    seniority_signal: str = ""  # "entry", "mid", "senior", "lead", "executive"


# ── JD section detection ─────────────────────────────────────────────

_JD_SECTION_PATTERNS: dict[str, re.Pattern] = {
    "required": re.compile(
        r'^(?:requirements?|essential|must[\s-]?have|required|'
        r'what\s+you(?:\'ll)?\s+need|what\s+we(?:\'re)?\s+looking\s+for|'
        r'minimum\s+qualifications?|key\s+requirements?)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "preferred": re.compile(
        r'^(?:nice[\s-]?to[\s-]?have|desirable|preferred|bonus|'
        r'advantageous|ideally|optional|additional)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "responsibilities": re.compile(
        r'^(?:responsibilities|duties|what\s+you(?:\'ll)?\s+do|'
        r'the\s+role|role\s+overview|about\s+the\s+role|key\s+duties)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "qualifications": re.compile(
        r'^(?:qualifications?|education|credentials?|'
        r'academic\s+requirements?)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "benefits": re.compile(
        r'^(?:benefits?|perks|what\s+we\s+offer|package|compensation)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
}

# ── Experience year extraction ────────────────────────────────────────

_EXPERIENCE_RE = re.compile(
    r'(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)',
    re.IGNORECASE,
)

# Alternative: "at least 5 years"
_EXPERIENCE_ALT_RE = re.compile(
    r'(?:at\s+least|minimum|min)\s+(\d+)\s*(?:years?|yrs?)',
    re.IGNORECASE,
)

# ── Salary detection ─────────────────────────────────────────────────

_SALARY_RE = re.compile(
    r'(?:£|GBP)\s*[\d,]+|[\d,]+\s*(?:per\s+annum|p\.?a\.?|salary)',
    re.IGNORECASE,
)

# ── Seniority signals ────────────────────────────────────────────────

_JD_SENIORITY_SIGNALS: dict[str, list[str]] = {
    "entry": ["entry level", "graduate", "junior", "trainee", "intern",
              "no experience required", "0-1 year", "0-2 year"],
    "mid": ["mid-level", "mid level", "2-5 year", "3-5 year",
            "some experience"],
    "senior": ["senior", "experienced", "5+ year", "5-10 year",
               "significant experience"],
    "lead": ["lead", "principal", "staff", "head of", "manager",
             "team lead", "10+ year"],
    "executive": ["director", "vp", "vice president", "chief",
                  "c-level", "executive"],
}

# ── Inline required/preferred signal words ────────────────────────────

_REQUIRED_SIGNALS = re.compile(
    r'\b(?:essential|required|must[\s-]?have|mandatory|necessary|critical)\b',
    re.IGNORECASE,
)

_PREFERRED_SIGNALS = re.compile(
    r'\b(?:desirable|nice[\s-]?to[\s-]?have|preferred|bonus|advantageous|ideally)\b',
    re.IGNORECASE,
)

# ── Skill extraction from bullet points ───────────────────────────────

_BULLET_RE = re.compile(r'^[\s]*[-•·▪*]\s*(.+)$', re.MULTILINE)

# Qualification patterns
_QUAL_RE = re.compile(
    r'\b(PhD|DPhil|MSc|MA|MEng|MBA|BSc|BA|BEng|LLB|PGCE|PGDip|PGCert'
    r'|NVQ|BTEC|HND|HNC|ACCA|CIMA|CFA|CIPD|PRINCE2|PMP|ITIL'
    r'|AWS\s+Certif|Azure\s+Certif|GCP\s+Certif'
    r'|Chartered|Fellow|MRICS|MCSP|RGN|RMN)\b',
    re.IGNORECASE,
)

# Skill-like items in bullet points (capitalized terms, tech names)
_SKILL_ITEM_RE = re.compile(
    r'\b(?:Python|Java|JavaScript|TypeScript|React|Angular|Vue|Node\.?js|'
    r'Docker|Kubernetes|AWS|Azure|GCP|SQL|PostgreSQL|MySQL|MongoDB|Redis|'
    r'TensorFlow|PyTorch|Spark|Airflow|Snowflake|dbt|Kafka|'
    r'Git|CI/CD|REST|GraphQL|Agile|Scrum|Jira|'
    r'Salesforce|HubSpot|Excel|Tableau|Power\s*BI|SAP|'
    r'C\+\+|C#|\.NET|Go|Rust|Scala|Ruby|PHP|Swift|Kotlin|'
    r'HTML|CSS|SASS|Webpack|Vite|'
    r'Machine Learning|Deep Learning|NLP|Computer Vision|'
    r'Data Analysis|Data Engineering|DevOps|SRE|'
    r'NHS|CQC|ACCA|CIMA|CIPD|PRINCE2|Six\s*Sigma|Lean|'
    r'GDPR|Compliance|Risk Management|Audit)\b',
    re.IGNORECASE,
)


def _find_jd_sections(text: str) -> dict[str, str]:
    """Split job description into named sections."""
    matches = []
    for section_name, pattern in _JD_SECTION_PATTERNS.items():
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), section_name))

    if not matches:
        return {}

    matches.sort(key=lambda x: x[0])
    sections: dict[str, str] = {}
    for i, (start, end, name) in enumerate(matches):
        next_start = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        sections[name] = text[end:next_start].strip()

    return sections


def _extract_skills_from_section(text: str) -> list[str]:
    """Extract skill/technology mentions from a JD section."""
    found: set[str] = set()
    for m in _SKILL_ITEM_RE.finditer(text):
        found.add(m.group(0))
    return sorted(found)


def _extract_qualifications(text: str) -> list[str]:
    """Extract qualification/certification mentions."""
    found: set[str] = set()
    for m in _QUAL_RE.finditer(text):
        found.add(m.group(0))
    return sorted(found)


def _extract_experience_years(text: str) -> Optional[int]:
    """Extract minimum years of experience required."""
    m = _EXPERIENCE_RE.search(text)
    if m:
        return int(m.group(1))
    m = _EXPERIENCE_ALT_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def _detect_seniority(text: str) -> str:
    """Detect seniority level from JD text."""
    text_lower = text.lower()
    # Check from most senior to least — return highest match
    for level in ("executive", "lead", "senior", "mid", "entry"):
        for signal in _JD_SENIORITY_SIGNALS[level]:
            if signal in text_lower:
                return level
    return ""


def _classify_inline_skills(text: str) -> tuple[list[str], list[str]]:
    """Classify skills as required or preferred based on surrounding context.

    Scans each line/paragraph for required/preferred signal words,
    then extracts any skill mentions within that context.
    """
    required: set[str] = set()
    preferred: set[str] = set()

    # Split into paragraphs/sentences
    chunks = re.split(r'\n\n|\.\s+', text)
    for chunk in chunks:
        skills = set()
        for m in _SKILL_ITEM_RE.finditer(chunk):
            skills.add(m.group(0))
        if not skills:
            continue

        if _REQUIRED_SIGNALS.search(chunk):
            required.update(skills)
        elif _PREFERRED_SIGNALS.search(chunk):
            preferred.update(skills)
        else:
            # Default: treat as required (most JDs list requirements)
            required.update(skills)

    return sorted(required), sorted(preferred)


def parse_jd(description: str) -> ParsedJD:
    """Parse a job description into structured components.

    Extracts required/preferred skills, experience requirements,
    qualifications, and seniority signals.
    """
    if not description or len(description) < 20:
        return ParsedJD()

    result = ParsedJD()

    # Try section-based extraction first
    sections = _find_jd_sections(description)

    if sections:
        # Section-based skill classification
        if "required" in sections:
            result.required_skills = _extract_skills_from_section(sections["required"])
        if "preferred" in sections:
            result.preferred_skills = _extract_skills_from_section(sections["preferred"])
        if "responsibilities" in sections:
            result.responsibilities = sections["responsibilities"][:500]
        if "qualifications" in sections:
            result.qualifications = _extract_qualifications(sections["qualifications"])
        if "benefits" in sections:
            result.benefits = sections["benefits"][:500]

        # If no section-based skills found, try inline classification
        if not result.required_skills and not result.preferred_skills:
            req, pref = _classify_inline_skills(description)
            result.required_skills = req
            result.preferred_skills = pref
    else:
        # No section headers — use inline signal classification
        req, pref = _classify_inline_skills(description)
        result.required_skills = req
        result.preferred_skills = pref

    # Also scan qualifications section for skills
    if "qualifications" in sections and not result.qualifications:
        result.qualifications = _extract_qualifications(sections["qualifications"])
    # Fallback: scan entire text for qualifications
    if not result.qualifications:
        result.qualifications = _extract_qualifications(description)

    # Experience years
    result.experience_years = _extract_experience_years(description)

    # Salary mention
    result.salary_mentioned = bool(_SALARY_RE.search(description))

    # Seniority signal
    result.seniority_signal = _detect_seniority(description)

    return result
