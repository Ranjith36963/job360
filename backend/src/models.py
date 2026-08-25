import html
import re
from dataclasses import dataclass, field
from typing import Optional

# Max chars per normalized-key component — see normalized_key() for the why.
_KEY_COMPONENT_MAX = 300

# The fixed set of shelves EVERY job carries, whatever its source
# (docs/pillars/UNIVERSAL_SHELF.md §1). This tuple is the SINGLE SOURCE OF
# TRUTH: migrations/0031_universal_shelf, services/shelf_gate.py, and
# tests/test_universal_shelf.py all import it rather than re-typing the list.
# Add a shelf here without teaching shelf_gate.py to fill it and
# test_gate_accounts_for_every_shelf fails loudly instead of silently
# drifting. Names are SHELF names, not always column names — "skills" is the
# shelf; it is stored in the `source_tags` column (§1 row 12).
UNIVERSAL_SHELF: tuple[str, ...] = (
    "title",
    "company",
    "location",
    "description",
    "posted_at",
    "deadline",
    "salary",
    "visa_status",
    "employment_type",
    "seniority",
    "workplace_mode",
    "skills",
    "category",
)

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
    # Universal Shelf, Step 1 (docs/pillars/UNIVERSAL_SHELF.md §1/§6).
    # RAW-VALUE fields until a source mapper or services/shelf_gate.py
    # normalises them — sources write whatever upstream calls it ('Full
    # time', 'FULLTIME', 'permanent'...); normalisation is the GATE's job,
    # never the source's (§5 point 1: "sources become dumb mappers"). As of
    # 2026-08-25: steps 2 and 3 now ship in the SAME PR as step 1, so this no
    # longer describes the world. Source mappers DO write these (lever.py,
    # recruitee.py, smartrecruiters.py and others), and the pipeline DOES call
    # fill_shelves() — see services/shelf_enrichment.py. The step-1 narrative
    # was written before the steps were combined and would have told the next
    # reader these columns are dead. (CodeRabbit, PR #388.)
    #
    # `seniority` is deliberately a DIFFERENT field from `experience_level`
    # above: that one is free-text, filled today only by a title regex; this
    # one is the closed 7-enum (job_enrichment_schema.SeniorityLevel) the
    # gate fills and normalises against.
    employment_type: Optional[str] = None
    workplace_mode: Optional[str] = None
    seniority: Optional[str] = None
    category: Optional[str] = None
    source_tags: list[str] = field(default_factory=list)
    # 3-state visa read (rule #31): "sponsors" / "no_sponsorship" / "unknown".
    # Distinct from the legacy `visa_flag` bool above, which conflates "the
    # ad says no" with "the ad never mentions it" — see services/visa_signal.py.
    visa_status: Optional[str] = None
    # Salary unit metadata. Without these the raw salary_min/salary_max
    # numbers below are misleading, not just incomplete: landingjobs stores
    # EUR/BRL figures as if they were GBP, careerjet mixes hourly and annual
    # numbers in the same field (UNIVERSAL_SHELF.md §2 SALARY). No source
    # mapper writes the RAW upstream token ('per day', 'Y', 'YEAR'); the GATE
    # (services/shelf_gate.py::_fill_salary) normalises the unit, annualises
    # + converts the amounts to GBP, and only THEN clamps.
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None       # "hourly" | "daily" | "monthly" | "annual"
    salary_is_estimated: Optional[bool] = None
    # DERIVED by the gate (UNIVERSAL_SHELF.md section 1 row 7): the same band
    # expressed as annual GBP, so 39 sources' hourly/monthly/EUR/USD figures
    # become comparable. NULL whenever the gate could not honestly convert
    # (no amount at all, or a currency core/fx cannot price) - never a guess.
    salary_min_gbp_annual: Optional[float] = None
    salary_max_gbp_annual: Optional[float] = None
    # Every shelf, filled or absent, with HOW it got that way — "no salary
    # offered" (a fact about the job) vs "nobody looked" (a fact about our
    # pipeline) are different facts that route different work (§3/§4). Keys
    # are exactly the UNIVERSAL_SHELF tuple above, no more, no fewer. Stamped
    # by services/shelf_gate.py::fill_shelves — nothing calls it yet (step 1
    # ships the gate built and tested in isolation; wiring it in is step 2).
    shelf_provenance: dict = field(default_factory=dict)
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
        # NO SALARY CLAMP HERE ANY MORE (Universal Shelf step 2,
        # docs/pillars/UNIVERSAL_SHELF.md section 2 "Gate rule for salary").
        # This used to null salary_min < 10k and salary_max > 500k, blind to
        # the unit: it destroyed every honest hourly/daily/monthly figure (an
        # NHS 30.27/h rate, nofluffjobs' 3,600/Month) exactly as hard as it
        # caught a mislabeled non-GBP number, and it let a 45/hour maximum
        # through untouched because 45 is not > 500,000. The clamp now lives
        # in services/shelf_gate.py::_fill_salary, where it runs AFTER
        # annualising + converting to GBP, so it judges a comparable number.
        # A Job constructed outside the pipeline therefore keeps whatever the
        # source said - which is the honest raw value, and exactly what a
        # "dumb mapper" source is supposed to hand over.

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
