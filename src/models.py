import html
import re
from dataclasses import dataclass, field
from typing import Optional

_COMPANY_SUFFIXES = re.compile(
    r"\s+(ltd|limited|inc|plc|corporation|corp|group|llc|gmbh|ag|sa|co|company|holdings|solutions|technologies|services|systems|pty)\.?\s*$",
    re.IGNORECASE,
)


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

    def __post_init__(self):
        # Decode HTML entities in title and company
        self.title = html.unescape(self.title)
        self.company = html.unescape(self.company)
        # Clean broken company names ("nan", "", "None" → "Unknown")
        self.company = self._clean_company(self.company)
        # Salary sanity: <10k likely hourly, >500k likely non-GBP
        if self.salary_min is not None and self.salary_min < 10000:
            self.salary_min = None
        if self.salary_max is not None and self.salary_max > 500000:
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
        company = _COMPANY_SUFFIXES.sub("", self.company).strip().lower()
        title = self.title.strip().lower()
        return (company, title)
