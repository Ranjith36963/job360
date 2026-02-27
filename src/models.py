import re
from dataclasses import dataclass, field
from typing import Optional

_COMPANY_SUFFIXES = re.compile(
    r"\s+(ltd|limited|inc|plc|corporation|corp|group)\.?\s*$",
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

    def normalized_key(self) -> tuple[str, str]:
        company = _COMPANY_SUFFIXES.sub("", self.company).strip().lower()
        title = self.title.strip().lower()
        return (company, title)
