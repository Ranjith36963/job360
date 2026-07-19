import html
import re
from dataclasses import dataclass
from typing import Optional

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
    match_score: int = 0
    visa_flag: bool = False
    is_new: bool = True
    experience_level: str = ""
    # Pillar 3 Batch 1 — 5-column date model.
    # posted_at: source-claimed posting date (None when no trustworthy field).
    # date_confidence: high / medium / low / fabricated / repost_backdated.
    # date_posted_raw: raw pre-parse value from source, audit-only.
    posted_at: Optional[str] = None
    date_confidence: str = "low"
    date_posted_raw: Optional[str] = None
    # Application deadline — extracted from description or structured source.
    # None means "no deadline listed" (UI shows fallback). NEVER fabricated.
    deadline: Optional[str] = None          # ISO date YYYY-MM-DD
    deadline_source: Optional[str] = None   # "listing" | "description" | None
    # Pillar 3 Batch 1 — lifecycle timestamps + ghost-detection state.
    # first_seen_at: ingestion lifecycle start (None ⇒ insert_job defaults to now).
    # last_seen_at: most-recent scrape that saw this job (None ⇒ insert_job defaults to now).
    # staleness_state: active / stale / expired — managed by ghost detector.
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    staleness_state: Optional[str] = None
    # Step-1.5 S1.1 — per-dimension score breakdown persisted to jobs columns
    # (migration 0011). main.py:run_search() captures every component of
    # ScoreBreakdown into these fields before insert_job() writes the row;
    # _row_to_job_response() reads them back so JobResponse exposes the radar
    # values that Step 1 promised. Names mirror JobResponse field names —
    # `role` is the title-component score, `recency` is recency_score, etc.
    role: int = 0
    skill: int = 0
    seniority_score: int = 0
    experience: int = 0
    credentials: int = 0
    location_score: int = 0
    recency: int = 0
    semantic: int = 0
    penalty: int = 0
    # Database row id, populated AFTER the row is inserted (None for a Job that
    # has not been persisted yet). Declared last so positional construction is
    # unaffected.
    #
    # This field was previously absent and stapled on at runtime
    # (`job.id = row[0]`), which worked only because the dataclass isn't
    # slotted. The cost was real: every reader had to guess with
    # `getattr(job, "id", None)`, and when the attribute was missing at scoring
    # time the enrichment lookup silently missed, scoring every enrichment
    # dimension 0 — the documented dim-scoring bug.
    #
    # Declaring it does NOT by itself fix that bug (the ordering of "set id"
    # vs "score" is the real defect, and lives in the scoring path). It removes
    # the mine: the attribute now always exists, so a missing id is a visible
    # None instead of an AttributeError dodged by getattr.
    id: Optional[int] = None

    def __post_init__(self) -> None:
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
        company = _COMPANY_SUFFIXES.sub("", self.company).strip()
        company = _COMPANY_REGION_SUFFIXES.sub("", company).strip().lower()
        # Collapse internal whitespace runs (docs/fable/02, rule #1): the same job
        # from two sources with cosmetically different spacing — "Software  Engineer"
        # (double space / tab / nbsp) vs "Software Engineer" — must produce the SAME
        # key, or it persists as a duplicate row. Whitespace only, NOT punctuation
        # (stripping punctuation risks over-merging distinct roles).
        company = re.sub(r"\s+", " ", company)
        title = re.sub(r"\s+", " ", self.title.strip().lower())
        return (company, title)
