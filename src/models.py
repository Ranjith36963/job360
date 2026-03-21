import html
import re
from dataclasses import dataclass, field
from typing import Optional

_COMPANY_SUFFIXES = re.compile(
    r"\s+(ltd|limited|inc|plc|corporation|corp|group|llc|gmbh|ag|sa|co|company|holdings|solutions|technologies|services|systems|pty)\.?\s*$",
    re.IGNORECASE,
)

_COMPANY_REGION_SUFFIXES = re.compile(
    r"\s+(uk|us|usa|de|sg|eu|emea|apac|global|international)\s*$",
    re.IGNORECASE,
)

# Trailing job codes like "- 12345" or "/ REQ-123"
_TRAILING_CODE_RE = re.compile(r'\s*[-/]\s*[A-Z0-9]{2,}[-_]?\d+\s*$', re.IGNORECASE)

# Parentheticals like "(London)" or "(Remote)"
_PAREN_RE = re.compile(r'\s*\([^)]*\)\s*$')


@dataclass
class Job:
    title: str
    company: str
    apply_url: str
    source: str
    date_found: str
    location: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    description: str = ""
    match_score: int = 0
    visa_flag: bool = False
    is_new: bool = True
    experience_level: str = ""
    job_type: str = ""
    match_data: str = ""  # JSON: score breakdown + skill match lists
    embedding: str = ""   # base64-encoded 384-dim vector

    def __post_init__(self):
        # Decode HTML entities in title and company
        self.title = html.unescape(self.title)
        self.company = html.unescape(self.company)
        # Clean broken company names ("nan", "", "None" → "Unknown")
        self.company = self._clean_company(self.company)
        # Salary sanity: reject only negative values (data errors)
        if self.salary_min is not None and self.salary_min < 0:
            self.salary_min = None
        if self.salary_max is not None and self.salary_max < 0:
            self.salary_max = None

    @staticmethod
    def _clean_company(name: str) -> str:
        if not name:
            return "Unknown"
        cleaned = name.strip()
        if not cleaned or cleaned.lower() in ("nan", "none", "n/a", "null", "unknown"):
            return "Unknown"
        return cleaned

    def normalized_key(self) -> tuple[str, str]:
        company = _COMPANY_SUFFIXES.sub("", self.company).strip()
        company = _COMPANY_REGION_SUFFIXES.sub("", company).strip().lower()
        title = self.title.strip()
        title = _TRAILING_CODE_RE.sub('', title)
        title = _PAREN_RE.sub('', title)
        title = title.strip().lower()
        return (company, title)
