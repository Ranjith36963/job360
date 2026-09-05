import html
import re
from dataclasses import dataclass
from typing import Optional

# Max chars per normalized-key component — see normalized_key() for the why.
_KEY_COMPONENT_MAX = 300

_COMPANY_SUFFIXES = re.compile(
    r"\s+(ltd|limited|inc|plc|corporation|corp|group|llc|gmbh|ag|sa|co|company|holdings|solutions|technologies|services|systems|pty)\.?\s*$",
    re.IGNORECASE,
)

_COMPANY_REGION_SUFFIXES = re.compile(
    r"\s+(uk|us|usa|de|sg|eu|emea|apac|global|international)\s*$",
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
    visa_flag: bool = False
    experience_level: str = ""
    # Pillar 3 Batch 1 — 5-column date model.
    # posted_at: claimed posting date (None when no trustworthy field).
    # date_confidence: high / medium / low. A brought ad is stamped "low":
    # "now" is the most honest date we have, and we say so.
    # date_posted_raw: raw pre-parse value, audit-only.
    posted_at: Optional[str] = None
    date_confidence: str = "low"
    date_posted_raw: Optional[str] = None
    # Application deadline — extracted from the ad, or None for "no deadline
    # listed" (UI shows a fallback). NEVER fabricated.
    deadline: Optional[str] = None          # ISO date YYYY-MM-DD
    deadline_source: Optional[str] = None   # "listing" | "description" | None
    # Lifecycle timestamps. Both default to now inside `insert_job` when the
    # caller leaves them unset.
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    # Database row id, populated AFTER the row is inserted (None for a Job
    # that has not been persisted yet). Declared last so positional
    # construction is unaffected, and declared AT ALL so a missing id reads as
    # a visible None instead of an AttributeError dodged by getattr.
    id: Optional[int] = None

    def __post_init__(self) -> None:
        # Decode HTML entities in title and company
        self.title = html.unescape(self.title)
        self.company = html.unescape(self.company)
        # Clean broken company names ("nan", "", "None" → "Unknown")
        self.company = self._clean_company(self.company)
        # NO SALARY CLAMP. One used to null salary_min < 10k and
        # salary_max > 500k, blind to the unit, which destroyed every honest
        # hourly/daily/monthly figure. A brought ad keeps whatever it said.

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
        # Collapse internal whitespace runs (docs/fable/02, rule #1): the same job
        # from two sources with cosmetically different spacing — "Software  Engineer"
        # (double space / tab / nbsp) vs "Software Engineer" — must produce the SAME
        # key, or it persists as a duplicate row. Whitespace only, NOT punctuation
        # (stripping punctuation risks over-merging distinct roles).
        company = re.sub(r"\s+", " ", company)
        title = re.sub(r"\s+", " ", self.title.strip().lower())
        # Cap each component. Found live 2026-07-30: one scraped job carried a
        # title so long that (normalized_company, normalized_title) blew
        # Postgres's btree index-row limit — "index row size 3128 exceeds
        # maximum 2704" on jobs' UNIQUE(normalized_company, normalized_title) —
        # and that ONE poison row aborted the whole catalog insert. No real
        # company or title approaches 300 chars; anything longer is scraped
        # garbage whose first 300 chars identify it just as well for dedup.
        # 300+300 chars stays under the ~2704-byte limit even fully multibyte.
        # Rule #1 holds because BOTH consumers of the key — the in-memory
        # deduplicator and the DB UNIQUE constraint — flow through this one
        # function, so they keep agreeing after truncation.
        return (company[:_KEY_COMPONENT_MAX], title[:_KEY_COMPONENT_MAX])
