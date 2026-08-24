"""The ONE chokepoint that fills + accounts for every UNIVERSAL SHELF on
every job (docs/pillars/UNIVERSAL_SHELF.md §5).

WIRED IN (step 2). `src/main.py::_score_dedup_and_filter` calls
`fill_shelves(job)` for every raw job BEFORE it is scored, deduped or
stored, so the scorer reads normalised shelves and no source can bypass the
gate (sources never call it — the orchestrator does, downstream of all of
them). Two things that used to live outside this file now live here:

  * the deadline-extraction pass that used to sit in `main.py` after
    scoring (`extract_deadline(description)`) — now `_fill_deadline`;
  * the salary clamp that used to sit in `models.Job.__post_init__` —
    now `_fill_salary`, and unit-aware: the band is annualised and
    converted to GBP FIRST and only then judged for plausibility, so an
    honest hourly rate survives instead of being nulled by a threshold
    that assumed GBP-annual.

`fill_shelves(job)` is synchronous and does NO I/O: no DB, no HTTP, no LLM
call. It only reads and normalises fields already sitting on the `Job`
object (rule #29's ABSENT contract; rule #30's closed-set enumeration for the
employment/workplace/seniority/period enums, which are bounded sets, unlike
the UK gate's unbounded foreign-city problem).

Two entry points are named in the design (§5 point 4) and BOTH now exist:

  * `fill_shelves(job)` — the ingest path;
  * `apply_enrichment(job, enrichment)` — STEP 3's sweep write-back, which
    lets `how:"llm"` rows share this exact normalisation while never
    overwriting a `source` or `derived` fill. Its caller is
    `services/shelf_enrichment.py` (the two-pass JOB SOURCE ENRICHMENT sweep).

JOB SOURCE ENRICHMENT = an LLM READING a job ad to extract facts about the
JOB. Those facts are identical for every user, so they belong to the shared
CATALOG (rule #10 — no `user_id` anywhere near them).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.core.fx import is_known_currency
from src.models import UNIVERSAL_SHELF, Job
from src.services.deadline import extract_deadline
from src.services.job_enrichment_schema import (
    EmploymentType,
    JobCategory,
    SeniorityLevel,
    WorkplaceType,
)
from src.services.salary import normalize_salary
from src.services.visa_signal import VisaStatus, detect_visa_status

# A description this short (or byte-identical to the title) cannot be a real
# job ad — it is a teaser/stub. Handing it to an LLM (JOB SOURCE ENRICHMENT,
# step 3) would produce a confident-sounding fabrication: `enrich_job` is
# idempotent per `job_id` (second call is a no-op unless `force=True`), so a
# wrong answer extracted here is PERMANENT until someone force-re-runs.
# UNIVERSAL_SHELF.md §2 DESCRIPTION row + §6's fabrication proof.
#
# RAISED 200 -> 600 on 2026-08-17, from evidence rather than taste. 200 was
# chosen as the p10 length floor several sources sit at, which answers "is
# there any text?" — the wrong question. The right one is "is there enough
# text to answer the questions we are about to ask?", and a real 452-char Reed
# teaser proved 200 too low: it says nothing whatsoever about working
# arrangements and the model still returned workplace_mode="onsite". Confident,
# unfounded, and — because enrich_job is idempotent and nothing ever re-reads a
# non-unknown answer — permanent.
#
# 600 covers the measured teaser band (200-599 chars: 640 rows locally, adzuna
# 245 and reed 207) where APIs truncate a real ad into marketing copy. This
# deliberately refuses to read some jobs that do have usable text; that is the
# right trade. A refused job stays honestly absent and can be enriched the
# moment text recovery reaches it. A fabricated one is wrong forever and looks
# exactly like a fact.
_STUB_DESCRIPTION_MIN_CHARS = 600

# Salary plausibility band, applied to the ANNUALISED GBP figure only (see
# _fill_salary). Same two thresholds the old unit-blind clamp in
# models.Job.__post_init__ used, so a GBP-annual job behaves EXACTLY as it
# did before this moved; what changes is that an hourly/monthly/foreign-
# currency job is now converted first and therefore judged as a comparable
# number instead of being nulled (or waved through) on its raw face value.
_SALARY_MIN_PLAUSIBLE_GBP_ANNUAL = 10_000
_SALARY_MAX_PLAUSIBLE_GBP_ANNUAL = 500_000

# Upstream pay-period tokens -> the closed set services/salary.py can
# annualise ("hourly"/"daily"/"weekly"/"monthly"/"annual"). Keys are the raw
# token with every non-letter stripped and lower-cased, so 'per day',
# 'PER_DAY' and 'Per-Day' all arrive as 'perday'. Enumerating this set is
# legal under rule #30: pay periods are a CLOSED, finite set (unlike foreign
# cities). Every token below was seen in a real live payload by the four
# source-recovery batches (reed 'per day', careerjet 'Y'/'H'/'M', indeed
# 'yearly', linkedin JSON-LD 'YEAR', nofluffjobs 'Month', jobicy/himalayas
# 'salaryPeriod'). An UNKNOWN token normalises to None: the shelf keeps the
# source's own numbers untouched and provenance records the raw token, which
# is honest — never a guessed unit (rule #29).
_PERIOD_ALIASES: dict[str, str] = {
    "h": "hourly", "hr": "hourly", "hour": "hourly", "hourly": "hourly",
    "perhour": "hourly", "perhr": "hourly", "hourlyrate": "hourly",
    "d": "daily", "day": "daily", "daily": "daily", "perday": "daily",
    "diem": "daily", "perdiem": "daily", "dayrate": "daily",
    "w": "weekly", "week": "weekly", "weekly": "weekly", "perweek": "weekly",
    "m": "monthly", "month": "monthly", "monthly": "monthly",
    "permonth": "monthly", "monthlyrate": "monthly",
    "y": "annual", "yr": "annual", "year": "annual", "yearly": "annual",
    "peryear": "annual", "annual": "annual", "annually": "annual",
    "annum": "annual", "perannum": "annual", "pa": "annual",
}


def _normalize_period(raw: Any) -> Optional[str]:
    """Raw upstream pay-period token -> closed-set period, or None.

    None means "this source did not tell us the unit" — NOT "annual". The
    difference matters: an unknown unit leaves the amounts exactly as the
    source sent them (every legacy consumer already reads them as GBP-annual),
    whereas a known unit triggers a real conversion.
    """
    if raw is None:
        return None
    key = re.sub(r"[^a-z]", "", str(raw).lower())
    if not key:
        return None
    return _PERIOD_ALIASES.get(key)


def is_stub_description(description: Optional[str], title: Optional[str]) -> bool:
    """True if `description` is too thin to safely hand to an LLM.

    Two independent, either-one-disqualifies signals:
      - shorter than ``_STUB_DESCRIPTION_MIN_CHARS`` (600) once whitespace is
        trimmed. The number is stated once, at the constant, with the
        measurement behind it; this docstring used to hardcode "200 chars",
        which was the earlier value and silently became wrong.
      - byte-identical to the title once both are trimmed (a known live bug:
        successfactors ships description == title for ~1,800 jobs/run)

    This is the function step 3's LLM sweep must call BEFORE enriching a job
    — see the module docstring's note on `apply_enrichment` not existing yet.
    Exported so that caller can reuse it without re-deriving the rule.
    """
    text = (description or "").strip()
    if len(text) < _STUB_DESCRIPTION_MIN_CHARS:
        return True
    if title and text == title.strip():
        return True
    return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_entry(field_name: str, *, raw: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"how": "source", "field": field_name, "at": _now()}
    if raw is not None:
        entry["raw"] = raw
    return entry


def _derived_entry(by: str) -> dict[str, Any]:
    return {"how": "derived", "by": by, "at": _now()}


def _absent_entry(why: str, *, raw: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"how": "absent", "why": why}
    if raw is not None:
        entry["raw"] = raw
    return entry


# Pillar 3 batch (this worker) — google_jobs (SerpApi) sends `schedule_type`
# as "Full–time" with a TYPOGRAPHIC EN DASH (U+2013), not an ASCII hyphen —
# confirmed live 2026-08-17 (38/39 sampled rows). `.replace("-", "_")` alone
# never touches it, so a value that WAS correctly read landed as
# absent/not_mapped on every single row. jsearch's own source comment
# documents the identical "Full–time"/"Contractor" shape from a different
# upstream, so this is a normalisation gap, not a one-source quirk. Widening
# the translate table (not a second `.replace`) keeps this a single pass.
_DASH_VARIANTS = str.maketrans(
    {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}
)

# Pillar 3 ATS batch — Ashby sends `employmentType`/`workplaceType` in bare
# PascalCase with NO space or dash at all ("FullTime", "OnSite" — confirmed
# live 2026-08-17, cohere board: 144/144 employmentType, 137 "OnSite" across
# the 5-board sample). `.replace(" ", "_").replace("-", "_")` has nothing to
# act on there, so "FullTime".lower() == "fulltime" never matched
# EmploymentType.FULL_TIME's "full_time" — every Ashby employment_type row
# landed as absent/not_mapped despite the raw value being 100% present.
# Inserting an underscore at each lower->UPPER boundary before lowering
# fixes the multi-word case ("FullTime" -> "full_time"); comparing a SECOND,
# underscore-stripped form catches the opposite direction, where the enum
# member itself has no internal separator ("OnSite" -> "on_site" still
# would not equal "onsite" without this).
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Pillar 3 vocabulary batch — a TRAILING PARENTHESISED QUALIFIER is an
# annotation on the value, never the value itself. 80,000 Hours writes its
# three experience buckets as "Junior (1-4 years experience)" /
# "Mid (5-9 years experience)" / "Senior (10+ years experience)" (confirmed
# live 2026-08-17: 36+34+2 of 82 rows) — three spellings of three words the
# gate already knows. Stripping the bracket is a SEPARATOR rule, not a
# per-value alias: whatever survives still has to match a real enum member or
# a real alias, so "Multiple experience levels" (no bracket, no honest target)
# is unaffected and stays absent.
_TRAILING_PARENTHETICAL_RE = re.compile(r"\s*\([^()]*\)\s*$")


def _with_squashed(aliases: dict[str, str]) -> dict[str, str]:
    """The alias table plus a separator-free copy of every key.

    Same trick the enum-member comparison already uses. Upstream separators
    are unpredictable in BOTH directions: WeWorkRemotely's "DevOps and
    Sysadmin" gains an underscore from the camelCase-boundary pass
    ("dev_ops_and_sysadmin") while NoFluffJobs' own code for the same thing
    is the bare word "devops". Matching the squashed form too means one
    written alias key covers every separator spelling of that key, instead of
    a new table row per punctuation accident.
    """
    out = dict(aliases)
    for key, target in aliases.items():
        out.setdefault(key.replace("_", ""), target)
    return out

# Small, EVIDENCE-BACKED synonym tables for the closed-enum shelves — never a
# guess dressed as a fact (rule #29): every key below is a raw string
# actually observed live, and every value is a synonym relationship a human
# would sign off on, not a best-effort classification. Ambiguous raw values
# (e.g. Adzuna's "IT Jobs", "Engineering Jobs" — could be
# software_engineering, devops_infrastructure, data_science or other) are
# deliberately left OUT: mapping them would silently misclassify jobs for
# the sake of a higher fill%, which is exactly what rule #29 forbids.
_EMPLOYMENT_TYPE_ALIASES: dict[str, str] = _with_squashed({
    # google_jobs schedule_type, confirmed live 2026-08-17.
    "contractor": "contract",
    # Pillar 3 ATS batch (greenhouse/lever/workable/ashby/smartrecruiters/
    # pinpoint/recruitee/workday/personio/successfactors worker) — confirmed
    # live 2026-08-17. "Permanent" (Lever `categories.commitment` 91/102 on
    # spotify; Personio `<employmentType>`) has no hours signal of its own,
    # so it maps to the closest single member, full_time — the common-case
    # reading, and a source-specific override (see personio.py's <schedule>
    # combine) takes priority when a real hours field exists.
    "permanent": "full_time",
    # "Fixed-Term"/"fixed_term" (Lever, Personio, Recruitee `experience`-
    # adjacent employment codes) is a CONTRACT by definition — a contract
    # with an end date, not an ongoing role.
    "fixed_term": "contract",
    # Personio `<employmentType>` "intern" is a one-word synonym for
    # EmploymentType.INTERNSHIP, not a guess.
    "intern": "internship",
    # Lever "Short Term" (spotify board) — closest single member.
    "short_term": "temporary",
    # Recruitee's OWN compound `employment_type_code` vocabulary (confirmed
    # live 2026-08-17 across dckgroup/transperfect/theentouragegroup: values
    # are literally "fulltime_permanent" / "fulltime_fixed_term" /
    # "parttime_permanent" / "parttime_fixed_term") — a closed, structured
    # field Recruitee itself defines, not free text. The fixed-term pair
    # maps to contract (more decision-relevant than hours, matching the
    # internship/contract precedence used elsewhere); the permanent pair
    # maps by hours since "permanent" alone carries no hours signal.
    "fulltime_permanent": "full_time",
    "fulltime_fixed_term": "contract",
    "parttime_permanent": "part_time",
    "parttime_fixed_term": "contract",
    # ---- Pillar 3 vocabulary batch (harvested 2026-08-17 from the 2,772-job
    # baseline run + live re-probes of the sources whose mapping landed after
    # it). Every key below is a string a real source really sent.
    #
    # Pinpoint writes a COMPOUND "<contract nature> - <hours>" label
    # (`Permanent - Full Time` 156, `Permanent` 121, `Permanent - Part Time`
    # 14, `Fixed Term - Full Time` 9, `Fixed Term Contract` 6,
    # `Fixed Term - Part Time` 2, `Seasonal - Full Time` 2 of 371 rows). The
    # compound halves are read exactly as the single tokens above already
    # are: "permanent" carries no hours signal so the HOURS half decides,
    # and "fixed term" IS a contract by definition so the DURATION half wins
    # (identical precedence to Recruitee's fulltime_fixed_term above).
    "permanent_full_time": "full_time",
    "permanent_part_time": "part_time",
    "fixed_term_full_time": "contract",
    "fixed_term_part_time": "contract",
    "fixed_term_contract": "contract",
    # "Seasonal" is time-limited BY DEFINITION (a season ends) — the duration
    # half wins here too, exactly as fixed-term does.
    "seasonal_full_time": "temporary",
    # Pinpoint "Apprentice" — a one-word synonym for
    # EmploymentType.APPRENTICESHIP, the same shape as "intern" above.
    "apprentice": "apprenticeship",
    # Climatebase writes "Full time role" (22/22 rows) — the same two words
    # every other board sends, with a noun stuck on the end.
    "full_time_role": "full_time",
})

# Seniority synonym table — same evidence bar as employment type above.
# Personio's `<seniority>` and Recruitee's `experience_code` are BOTH their
# own closed, structured vocabularies (confirmed live 2026-08-17, stable
# across every company checked), not free text: Personio only ever emits
# {student, entry-level, experienced, executive}; Recruitee only ever emits
# {student_school, student_college, entry_level, mid_level, experienced,
# manager, senior_manager, executive, senior_executive}. "experienced" is
# deliberately LEFT OUT: it sits ambiguously between mid and senior in both
# vocabularies and picking one would be a guess (rule #29). Recruitee's
# management tiers (manager/senior_manager/senior_executive) are also left
# out — that is a corporate-management ladder, not the IC ladder this enum
# encodes (intern..director), and forcing a translation would misclassify.
_SENIORITY_ALIASES: dict[str, str] = _with_squashed({
    "student": "intern",
    "student_school": "intern",
    "student_college": "intern",
    "entry_level": "junior",
    "mid_level": "mid",
    # Workable `experience` (LinkedIn-style taxonomy, confirmed live on
    # suade/yapily boards) "Associate" is the closest single member.
    "associate": "junior",
    # Top tier in both closed vocabularies above.
    "executive": "director",
    # devitjobs `expLevel` — its OWN closed, structured field (confirmed
    # live 2026-08-17: {Senior: 1276, Regular: 871, Lead: 335, Junior: 133}
    # across 2,615 postings). "Regular" is devitjobs' own word for
    # "ordinary/standard level" — an unambiguous synonym for `mid`, the
    # same single-tier reading as `mid_level` above. "Lead" is deliberately
    # LEFT OUT: it sits ambiguously between staff/principal/director
    # (tech-lead vs people-lead is not decidable from the word alone),
    # the same ambiguity that keeps "experienced" out above.
    "regular": "mid",
    # ---- Pillar 3 vocabulary batch. NoFluffJobs' `seniority` is its own
    # closed 5-way field (confirmed live 2026-08-17 over 21,796 postings:
    # Senior 13,106 / Mid 7,261 / Expert 706 / Junior 692 / Trainee 31).
    # Senior/Mid/Junior already hit the enum exactly. "Trainee" is a
    # position held WHILE LEARNING the job — the same thing
    # SeniorityLevel.INTERN encodes, and the tier below Junior in
    # NoFluffJobs' own ordering. "Expert" is deliberately LEFT OUT: it sits
    # across senior/staff/principal and picking one would be a guess.
    "trainee": "intern",
    # jobicy `jobLevel` — "Midweight" (seen live 2026-08-17 alongside
    # Senior/Director) is the industry's own word for mid-level, one tier
    # spelled without the hyphen; no ambiguity about which tier it names.
    "midweight": "mid",
})

# Seniority-shaped raw values seen live that are deliberately NOT mapped, and
# why — kept as a comment because the honest answer is "no target", not a
# missing row:
#   * gov_apprenticeships `apprenticeshipLevel` — "Intermediate" (49),
#     "Advanced" (92), "Higher" (13), "Degree" (2). These are the DfE's
#     COURSE levels (level 2/3/4-5/6-7), not a professional ladder: an
#     "Advanced" apprenticeship is still a first job. Aliasing them globally
#     would also poison every OTHER source that ever sends the word
#     "Advanced" meaning senior.
#   * workable `experience` "Mid-Senior level" (42/191 rows) — LinkedIn's
#     taxonomy fuses two of our tiers into one bucket; either choice is a
#     coin flip.
#   * eightykhours "Multiple experience levels" (7) — the ad itself says the
#     level is not one value.
#   * personio/recruitee "experienced" (110 combined) — documented above.

# Workplace-mode synonym table — same evidence bar as employment type above.
_WORKPLACE_TYPE_ALIASES: dict[str, str] = _with_squashed({
    # devitjobs `workplace` — its OWN closed, structured field (confirmed
    # live 2026-08-17: {office: 2248, hybrid: 207, remote: 160} across 2,615
    # postings, 100% fill). "office" is devitjobs' own word for on-site
    # attendance — an unambiguous synonym for `onsite`, not a guess: the
    # field is a 3-way closed enum on devitjobs' own site, this is its only
    # non-remote, non-hybrid member.
    "office": "onsite",
    # ---- Pillar 3 vocabulary batch. Climatebase writes "In-person" (16/22
    # rows with a workplace value, confirmed live 2026-08-17) — plain English
    # for "you attend the workplace", i.e. WorkplaceType.ONSITE. The other 6
    # Climatebase rows already say "Remote" and match exactly.
    "in_person": "onsite",
})

# Adzuna's own category taxonomy (confirmed live 2026-08-17: "IT Jobs",
# "Engineering Jobs", "Trade & Construction Jobs") and DfE's 15 published
# "apprenticeship standard routes" (gov_apprenticeships `course.route`,
# confirmed live 2026-08-17: "Digital", "Education and early years") are
# BOTH closed, published vocabularies (rule #30) — but neither is the SAME
# vocabulary as JobCategory's 16-way professional-domain taxonomy, so only
# the unambiguous 1:1 synonyms are mapped here. "IT Jobs" and "Digital" are
# left OUT on purpose: both span software_engineering, devops_infrastructure
# and data_science in real postings, and picking one would be a guess.
_CATEGORY_ALIASES: dict[str, str] = _with_squashed({
    # Adzuna. NOTE: "&" is normalised to "and" before lookup (see
    # `_normalize_closed_enum`), so these keys are written in the "and" form
    # even though Adzuna ships an ampersand.
    "sales_jobs": "sales",
    "marketing_and_pr_jobs": "marketing",
    "pr,_advertising_and_marketing_jobs": "marketing",
    "accounting_and_finance_jobs": "finance",
    "legal_jobs": "legal",
    "hr_and_recruitment_jobs": "hr_people",
    "healthcare_and_nursing_jobs": "healthcare",
    "teaching_jobs": "education",
    "creative_and_design_jobs": "design",
    # DfE apprenticeship standard routes
    "education_and_early_years": "education",
    "education_and_childcare": "education",
    "care_services": "healthcare",
    "health_and_science": "healthcare",
    "creative_and_design": "design",
    # Recruitee `category_code` — its own closed field (confirmed live
    # 2026-08-17, transperfect board): "legal_services" and
    # "recruitment_hr" are unambiguous 1:1 synonyms; every other Recruitee
    # code (e.g. "engineering", "information_technology") is left out
    # because Recruitee spans every industry on its platform, not just
    # tech, so "engineering" there means anything from software to
    # mechanical — mapping it to software_engineering would misclassify.
    "legal_services": "legal",
    "recruitment_hr": "hr_people",
    # Personio `<occupationCategory>` — its OWN closed vocabulary,
    # confirmed live 2026-08-17 across 8 boards (personio/flatpay/stark/
    # merantix/intigriti/olio/gridx/maltego): "it_software" is Personio's
    # umbrella for all software roles, an unambiguous match. The other
    # Personio buckets that could plausibly split across two JobCategory
    # members (e.g. "marketing_and_product", "business_and_strategic_
    # development", "engineering") are deliberately left out for the same
    # reason as "IT Jobs" above.
    "it_software": "software_engineering",
    "human_resources": "hr_people",
    "sales_and_business_development": "sales",
    "accounting_and_finance": "finance",
    "production_and_operations": "operations",
    "logistics_and_transportation": "operations",
    # ---- Pillar 3 vocabulary batch. The category shelf read 0.0% on ALL 39
    # sources, and only ONE source (adzuna) was even sending a value the gate
    # could see. Every key below is a real value from a real payload,
    # harvested 2026-08-17, and only the 1:1 synonyms are here.
    #
    # TheMuse `categories[0].name` (its own professional-domain taxonomy).
    "data_and_analytics": "data_science",
    "human_resources_and_recruitment": "hr_people",
    "business_operations": "operations",
    "design_and_ux": "design",
    # WeWorkRemotely RSS `<category>` (10 fixed board sections, 10 rows each
    # in a single live feed read). The three programming sections are all
    # software engineering; "All Other Remote" is WWR's OWN explicit
    # catch-all, which is exactly what JobCategory.OTHER means, so that one
    # is a synonym and not a shrug. "Sales and Marketing" and "Management
    # and Finance" are deliberately OUT — each fuses two of our members.
    "full_stack_programming": "software_engineering",
    "front_end_programming": "software_engineering",
    "back_end_programming": "software_engineering",
    "dev_ops_and_sysadmin": "devops_infrastructure",
    "product": "product_management",
    "all_other_remote": "other",
    # NoFluffJobs `category` (its own 36-value closed IT taxonomy, confirmed
    # live over 21,796 postings). Its software specialisms all collapse onto
    # software_engineering; "artificialIntelligence" is the ML domain.
    # LEFT OUT on purpose: "testing", "architecture", "security",
    # "businessAnalyst", "projectManager", "erp", "businessIntelligence",
    # "consulting", "agile", "automation", "embedded"-adjacent hardware
    # buckets (mechanics/electronics/electricalEng/telecommunication),
    # "customerService"/"support", "officeAdministration" — each is either a
    # discipline this 16-way taxonomy has no member for, or a word that means
    # something different on a non-IT board (a global alias table is shared
    # by every source, so "architecture" must not mean software here and
    # buildings there).
    "backend": "software_engineering",
    "frontend": "software_engineering",
    "fullstack": "software_engineering",
    "mobile": "software_engineering",
    "game_dev": "software_engineering",
    "artificial_intelligence": "machine_learning",
    "devops": "devops_infrastructure",
    "sys_administrator": "devops_infrastructure",
    "data": "data_science",
    "ux": "design",
    "hr": "hr_people",
    "law": "legal",
    "logistics": "operations",
    # Recruitee `category_code` (28 values seen live across 4 boards).
    "marketing_pr": "marketing",
    "accountancy": "finance",
    # Remotive `category` — its own closed list. "All others" is Remotive's
    # explicit catch-all (same reading as WWR's above); "Information
    # Technology" is left out for the identical reason as Adzuna's "IT Jobs".
    "software_development": "software_engineering",
    "all_others": "other",
    "medical": "healthcare",
    # Jobicy `jobIndustry` — its own closed list (the live feed ships HTML
    # entities, "Data Science &amp; Analytics"; jobicy.py unescapes before
    # handing the raw value over). "Marketing & Sales", "Product &
    # Operations", "Customer Support & Success" and "Project & Program
    # Management" each fuse two members and are left out.
    "data_science_and_analytics": "data_science",
    "devops_and_infrastructure": "devops_infrastructure",
    "finance_and_accounting": "finance",
    "healthcare_and_medical": "healthcare",
})


def _normalize_closed_enum(
    raw: Any, enum_cls: Any, aliases: Optional[dict[str, str]] = None
) -> tuple[Optional[str], Optional[str]]:
    """Normalise `raw` against a CLOSED enum set (rule #30 — employment type,
    workplace mode and seniority are bounded, closed sets, so enumerating
    THEM is legal, unlike the unbounded foreign-city problem the UK gate
    solves with data instead).

    `aliases` is an OPTIONAL synonym table (see `_EMPLOYMENT_TYPE_ALIASES` /
    `_CATEGORY_ALIASES` above) checked AFTER an exact match fails — a source
    vocabulary synonym, never a guess: every entry is a real raw string a
    real source sends, mapped to a real enum member a human confirmed means
    the same thing.

    Returns `(normalized_value_or_None, raw_str_or_None)`. The enum's own
    `UNKNOWN` member (a JOB SOURCE ENRICHMENT / LLM-contract sentinel,
    `job_enrichment_schema.py`) is never a valid NORMALIZED target here — an
    unmatched raw value means the catalog shelf is ABSENT (NULL), never the
    literal string "unknown". Only `visa_status` stores "unknown" as a real
    value — see `_fill_visa_status`, and UNIVERSAL_SHELF.md §1 row 8 / §4.
    """
    if raw is None:
        return None, None
    raw_str = str(raw).strip()
    if not raw_str:
        return None, None
    spaced = _CAMEL_BOUNDARY_RE.sub("_", raw_str)
    key = spaced.lower().translate(_DASH_VARIANTS)
    # A trailing "(...)" is a qualifier ON the value, not the value —
    # "Junior (1-4 years experience)" is the word "junior". Never let the
    # strip empty the key ("(remote)" must stay findable as itself).
    stripped = _TRAILING_PARENTHETICAL_RE.sub("", key).strip()
    if stripped:
        key = stripped
    # "&" and "and" are the same word in every vocabulary observed
    # ("Data Science & Analytics" vs "Education and early years"), so they
    # must not be two different table rows.
    key = key.replace("&", " and ")
    key = key.replace(" ", "_").replace("-", "_")
    key = re.sub(r"_+", "_", key).strip("_")
    squashed = key.replace("_", "")
    for member in enum_cls:
        if member.value == "unknown":
            continue
        if member.value == key or member.value.replace("_", "") == squashed:
            return member.value, raw_str
    if aliases:
        target = aliases.get(key) or aliases.get(squashed)
        if target is not None:
            for member in enum_cls:
                if member.value == target:
                    return member.value, raw_str
    return None, raw_str


def _fill_closed_enum_shelf(
    job: Job, attr: str, enum_cls: Any, aliases: Optional[dict[str, str]] = None
) -> dict[str, Any]:
    """Shared filler for employment_type / seniority / workplace_mode /
    category — all four are closed-set enum shelves normalised the same way
    (UNIVERSAL_SHELF.md §5 point 2). `attr` is both the Job attribute name
    AND the shelf/provenance key for these four (unlike `skills`, which is
    the shelf name for the `source_tags` attribute).
    """
    raw = getattr(job, attr, None)
    normalized, raw_str = _normalize_closed_enum(raw, enum_cls, aliases)
    setattr(job, attr, normalized)
    if normalized is not None:
        return _source_entry(attr, raw=raw_str if raw_str != normalized else None)
    if raw_str is not None:
        # A value came in but the gate's normaliser doesn't recognise the
        # token — counted (never silently dropped), and the raw token
        # survives in provenance so a future alias fix is traceable back to
        # the exact string that failed.
        return _absent_entry("not_mapped", raw=raw_str)
    return _absent_entry("not_mapped")


def _fill_title(job: Job) -> dict[str, Any]:
    if job.title and job.title.strip():
        return _source_entry("title")
    return _absent_entry("not_mapped")


def _fill_company(job: Job) -> dict[str, Any]:
    # "Unknown" is Job._clean_company's sentinel for a missing/broken
    # upstream company name — that IS the absent state for this shelf.
    if job.company and job.company != "Unknown":
        return _source_entry("company")
    return _absent_entry("not_mapped")


def _fill_location(job: Job) -> dict[str, Any]:
    if job.location and job.location.strip():
        return _source_entry("location")
    return _absent_entry("not_mapped")


def _fill_description(job: Job) -> dict[str, Any]:
    if is_stub_description(job.description, job.title):
        return _absent_entry("stub")
    return _source_entry("description")


def _fill_posted_at(job: Job) -> dict[str, Any]:
    if job.posted_at:
        return _source_entry("posted_at")
    return _absent_entry("not_mapped")


def _fill_deadline(job: Job) -> dict[str, Any]:
    """Structured source deadline first, then the free text derivation.

    STEP 2: the `extract_deadline(description)` pass that used to run in
    `main.py` AFTER scoring now runs HERE, inside the gate, before scoring —
    same function, same regex, same `deadline_source='description'` stamp,
    just moved to the one door every job passes through
    (UNIVERSAL_SHELF.md §5 point 2). Two things this buys: a source that
    forgets to map a deadline still gets the text pass, and the reason a
    deadline is missing is now recorded instead of inferred.

    Rule #29: `extract_deadline` returns None unless a deadline KEYWORD is
    tied to an unambiguous future date, so an ad with no deadline keeps
    NULL. The gate NEVER invents one — no "30 days from posting" default.
    """
    if job.deadline is None and job.description:
        result = extract_deadline(job.description)
        if result is not None:
            job.deadline, job.deadline_source = result
    if job.deadline:
        source = job.deadline_source or "listing"
        if source == "listing":
            return _source_entry("deadline")
        return _derived_entry("deadline.extract_deadline@v1")
    # Most boards have no deadline concept at all — models.py already
    # documents "None means no deadline listed. NEVER fabricated."
    return _absent_entry("not_stated")


def _annualise_one(
    amount: Optional[float], currency: Optional[str], period: Optional[str]
) -> Optional[int]:
    """One bound -> annual GBP, via services/salary.normalize_salary.

    Deliberately ONE BOUND AT A TIME. `normalize_salary` mirrors a missing
    bound onto the survivor (a band of one point), which is right for the
    scorer's overlap maths but would be a FABRICATION here: a job that
    advertises "from £45,000" must not end up with a stored maximum of
    £45,000 it never stated (rule #29). Passing one bound and reading back
    only that bound keeps the missing side missing.
    """
    if amount is None:
        return None
    band = normalize_salary(
        {
            "min": amount,
            "max": None,
            "currency": currency or "GBP",
            "frequency": period or "annual",
        }
    )
    return None if band is None else band[0]


def _fill_salary(job: Job) -> dict[str, Any]:
    """Unit-aware salary: annualise + currency-convert FIRST, clamp SECOND.

    STEP 2 (UNIVERSAL_SHELF.md §2 "Gate rule for salary"). The old clamp
    lived in `models.Job.__post_init__` and was unit-blind — it assumed every
    number was GBP-annual, so it nulled an honest £30.27/hour NHS rate and
    stored a €60,000 salary as if it were sterling. Here the band is turned
    into annual GBP using the source's OWN unit sidecars (`salary_period`,
    `salary_currency`, mapped raw by the sources in step 2's recovery work)
    and only then judged for plausibility.

    What lands on the Job afterwards:
      * `salary_min` / `salary_max` — the comparable annual-GBP figure when
        the gate could convert (every existing consumer, from the CSV export
        to the email report, already reads these as GBP-annual), otherwise
        the source's own untouched numbers;
      * `salary_min_gbp_annual` / `salary_max_gbp_annual` — the derived pair,
        set ONLY when the conversion was real;
      * `salary_period` / `salary_currency` — rewritten to "annual"/"GBP"
        when a conversion happened, so the stored unit describes the stored
        number; the pre-conversion values survive in provenance.

    Rule #29 is the hard edge: no amount at all stays NULL with
    `absent:not_stated`, and NOTHING here ever invents a number, a unit or a
    currency. An unknown period is not silently called "annual" — the
    amounts are simply left as the source sent them.
    """
    raw_min, raw_max = job.salary_min, job.salary_max
    raw_period, raw_currency = job.salary_period, job.salary_currency
    period = _normalize_period(raw_period)
    currency = (raw_currency or "").strip().upper() or None
    raw_record: dict[str, Any] = {
        "min": raw_min,
        "max": raw_max,
        "period": raw_period,
        "currency": raw_currency,
        "is_estimated": job.salary_is_estimated,
    }
    # Keep the NORMALISED unit even when no amount came with it — reed sends
    # `salaryType: 'per day'` on contract roles whose amounts are both null,
    # and "we know it is a day rate, we just have no number" is a real fact.
    if period is not None:
        job.salary_period = period

    if raw_min is None and raw_max is None:
        job.salary_min_gbp_annual = None
        job.salary_max_gbp_annual = None
        # ~70% of the UK corpus omits pay entirely — usually a fact about the
        # job, not a gap in our pipeline.
        return _absent_entry("not_stated")

    if currency is not None and not is_known_currency(currency):
        # core/fx has no rate for this code, and `to_gbp` would pass it
        # through at 1:1 — a BRL figure wearing a £ sign is a WRONG number,
        # not a rough one. Leave the source's own numbers + code alone and
        # leave the derived GBP pair NULL rather than fabricate a conversion.
        job.salary_min_gbp_annual = None
        job.salary_max_gbp_annual = None
        entry = _source_entry("salary_min/salary_max", raw=raw_record)
        entry["gbp_annual"] = "unpriceable_currency"
        return entry

    min_annual = _annualise_one(raw_min, currency, period)
    max_annual = _annualise_one(raw_max, currency, period)

    # THE CLAMP, moved here from models.py and now applied to a comparable
    # number. Same thresholds and same one-sided directions as before, so a
    # GBP-annual job's outcome is byte-identical to the old behaviour.
    if min_annual is not None and min_annual < _SALARY_MIN_PLAUSIBLE_GBP_ANNUAL:
        min_annual = None
    if max_annual is not None and max_annual > _SALARY_MAX_PLAUSIBLE_GBP_ANNUAL:
        max_annual = None

    job.salary_min = float(min_annual) if min_annual is not None else None
    job.salary_max = float(max_annual) if max_annual is not None else None
    job.salary_min_gbp_annual = job.salary_min
    job.salary_max_gbp_annual = job.salary_max
    if min_annual is not None or max_annual is not None:
        # The stored numbers ARE annual GBP now, so the stored unit must say
        # so — but ONLY where the source actually stated that unit. Stamping
        # "GBP" on a job whose ad never named a currency would be inventing a
        # fact (rule #29); an unstated currency stays NULL and the number
        # stays exactly what the source sent, which is what every legacy
        # consumer has always assumed.
        if period is not None:
            job.salary_period = "annual"
        if currency is not None:
            job.salary_currency = "GBP"

    if job.salary_min is None and job.salary_max is None:
        # Numbers arrived but nothing survived the plausibility band. That is
        # neither "the ad said nothing" nor "nobody looked": it is a value we
        # refused. Recorded as its own reason, with the original figures kept
        # so the refusal is auditable instead of invisible.
        return _absent_entry("implausible", raw=raw_record)
    return _source_entry("salary_min/salary_max", raw=raw_record)


def _fill_visa_status(job: Job) -> dict[str, Any]:
    # STEP 2 (was TODO): a structured source field DOES feed this now —
    # devitjobs.py sets `job.visa_status` to the raw upstream
    # `hasVisaSponsorship` string ("Yes"/"No") before this gate runs.
    # `detect_visa_status`'s `enrichment_value` param already existed for
    # exactly this (an authoritative verdict beats free-text detection) but
    # nothing ever passed it — the raw signal was being silently overwritten
    # by the regex detector on every job, including the ones with a real
    # structured answer. Capture the pre-gate value BEFORE detect_visa_status
    # overwrites `job.visa_status` with its own normalised output below; a
    # source that does not set this leaves it None, so the call degrades
    # exactly to the old free-text-only behaviour.
    raw_status = job.visa_status
    status = detect_visa_status(job.description, job.title, enrichment_value=raw_status)
    job.visa_status = status.value
    if status is VisaStatus.UNKNOWN:
        # Rule #31: unknown IS the third state, stored as the literal value
        # "unknown" above — but the WHY here is still "the ad never said",
        # not "nobody looked" (the detector DID look).
        return _absent_entry("not_stated")
    if raw_status and str(raw_status).strip().lower() in ("yes", "no", "sponsors", "true", "false", "none"):
        return _source_entry("visa_status", raw=raw_status)
    return _derived_entry("visa_signal.detect_visa_status@v1")


def _fill_skills(job: Job) -> dict[str, Any]:
    if job.source_tags:
        return _source_entry("source_tags")
    return _absent_entry("not_mapped")


# Shelf name -> filler. Keys are exactly UNIVERSAL_SHELF (checked by
# test_gate_accounts_for_every_shelf); a KeyError here on a real run means
# someone added a shelf to the tuple without teaching this dict, which is
# exactly the drift this dict exists to make loud instead of silent.
_SHELF_FILLERS: dict[str, Callable[[Job], dict[str, Any]]] = {
    "title": _fill_title,
    "company": _fill_company,
    "location": _fill_location,
    "description": _fill_description,
    "posted_at": _fill_posted_at,
    "deadline": _fill_deadline,
    "salary": _fill_salary,
    "visa_status": _fill_visa_status,
    "employment_type": lambda job: _fill_closed_enum_shelf(
        job, "employment_type", EmploymentType, _EMPLOYMENT_TYPE_ALIASES
    ),
    "seniority": lambda job: _fill_closed_enum_shelf(
        job, "seniority", SeniorityLevel, _SENIORITY_ALIASES
    ),
    "workplace_mode": lambda job: _fill_closed_enum_shelf(
        job, "workplace_mode", WorkplaceType, _WORKPLACE_TYPE_ALIASES
    ),
    "skills": _fill_skills,
    "category": lambda job: _fill_closed_enum_shelf(job, "category", JobCategory, _CATEGORY_ALIASES),
}


# ---------------------------------------------------------------------------
# STEP 3 — the LLM write-back half (UNIVERSAL_SHELF.md §2 fill chains, §6).
# ---------------------------------------------------------------------------

# The SIX shelves an LLM reading the ad may fill. Deliberately not all 13:
#   * `deadline` is EXCLUDED on purpose — §2's DEADLINE row: `JobEnrichment`
#     has no deadline field and must not get one. The regex already covers
#     prose dates and an LLM-guessed date is exactly the fabrication rule #29
#     bans.
#   * `description` is what the LLM READS; it is never a write target.
#   * `title` / `company` / `location` / `posted_at` come from the source and
#     an LLM rewriting them would be inventing identity, not extracting fact.
#   * `skills` stays in `job_enrichment.required_skills/preferred_skills`;
#     `jobs.source_tags` is the JOB'S OWN declared vocabulary (§1 row 12).
LLM_FILLABLE_SHELVES: tuple[str, ...] = (
    "employment_type",
    "seniority",
    "workplace_mode",
    "category",
    "salary",
    "visa_status",
)

# A shelf carrying one of these in provenance has been filled by a layer the
# LLM sits BELOW in the trust order (§2: source -> derived -> llm -> absent).
# The LLM may never overwrite one of them.
_FILLED_HOWS = frozenset({"source", "derived", "llm"})


def _shelf_snapshot(job: Job, shelf: str) -> Any:
    """The current VALUE of a shelf, as one comparable object.

    Used only to answer "did re-running the gate change this shelf?" — see
    `fill_shelves`'s note on not rewriting an `llm` provenance entry.
    """
    if shelf == "salary":
        return (job.salary_min, job.salary_max)
    return getattr(job, shelf, None)


def shelf_has_value(job: Job, shelf: str) -> bool:
    """True if this shelf currently carries a real value on `job`.

    Covers ALL of `UNIVERSAL_SHELF`, not just the LLM-fillable six, because two
    different callers need it:

      * `absent_shelves` — belt-and-braces beside its provenance check: a
        legacy row stored BEFORE the gate existed has a real value and NO
        provenance at all, and that value is still a source fill the LLM must
        not overwrite.
      * the sweep's "how many shelves did the FREE pass fill?" counter, which
        would under-report if it only looked at the six the LLM can write —
        `deadline` is the clearest case: pass 1 fills it from the ad text, and
        the LLM is deliberately forbidden from ever touching it (§2).

    Shelf names are not always attribute names, and three shelves have their
    own idea of "empty" (§1): `skills` lives in `source_tags`, a `description`
    that is a stub is absent, and `company` uses "Unknown" as its sentinel.
    """
    if shelf == "salary":
        return job.salary_min is not None or job.salary_max is not None
    if shelf == "visa_status":
        # "unknown" is a real stored value (rule #31, §1 row 8) but it is the
        # ABSENCE of an answer — the LLM reading the whole ad may improve on it.
        return bool(job.visa_status) and job.visa_status != "unknown"
    if shelf == "skills":
        return bool(job.source_tags)
    if shelf == "description":
        return not is_stub_description(job.description, job.title)
    if shelf == "company":
        return bool(job.company) and job.company != "Unknown"
    value = getattr(job, shelf, None)
    return value is not None and value != ""


# Kept as the private spelling the trust-order code reads. One function, two
# names, so a future edit cannot teach one of them a rule the other misses.
_shelf_has_value = shelf_has_value


def absent_shelves(job: Job) -> tuple[str, ...]:
    """Which LLM-fillable shelves are HONESTLY absent on this job.

    "Honestly absent" = provenance does not record a `source`/`derived`/`llm`
    fill AND no value is sitting in the column. Both halves matter: the first
    is the trust order (§2), the second catches pre-gate rows that have a
    value but no provenance to describe it.

    This is what the sweep counts to decide whether reading the ad is worth
    the money, and what `apply_enrichment` uses to decide what it may write.
    """
    provenance = job.shelf_provenance or {}
    out: list[str] = []
    for shelf in LLM_FILLABLE_SHELVES:
        entry = provenance.get(shelf)
        if isinstance(entry, dict) and entry.get("how") in _FILLED_HOWS:
            continue
        if _shelf_has_value(job, shelf):
            continue
        out.append(shelf)
    return tuple(out)


def _llm_entry(by: str, at: str, *, raw: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"how": "llm", "by": by, "at": at}
    if raw is not None:
        entry["raw"] = raw
    return entry


def _enum_str(value: Any) -> Optional[str]:
    """An enum member's value, or None when it is the `unknown` sentinel.

    `unknown` is the LLM CONTRACT's word for "I could not tell" — the prompt
    demands it instead of a guess. In the CATALOG that means the shelf stays
    NULL and absent (§1 "Absent everywhere = column NULL"), never the literal
    string "unknown". Only `visa_status` stores "unknown" as a real value.
    """
    if value is None:
        return None
    text = getattr(value, "value", value)
    text = str(text).strip()
    if not text or text == "unknown":
        return None
    return text


_VISA_FROM_LLM = {"yes": VisaStatus.SPONSORS.value, "no": VisaStatus.NO_SPONSORSHIP.value}


def apply_enrichment(job: Job, enrichment: Any, *, by: str = "llm") -> tuple[Job, tuple[str, ...]]:
    """Write LLM-extracted facts into the ONLY shelves that are honestly absent.

    The second gate entry point named in §5 point 4. Everything the ingest
    path normalises, this path normalises identically — the salary band goes
    through the SAME `_fill_salary` (annualise + currency-convert, THEN clamp),
    so an LLM-read "£30 per hour" lands as the same annual-GBP number a source
    field would have.

    Three hard rules, each guarded by a test in tests/test_universal_shelf.py:

      1. **Never overwrite.** Only shelves `absent_shelves(job)` returns are
         touched. Source beats derived beats llm (§2) — an LLM value can only
         ever land in a hole.
      2. **`how:"llm"` only for what the LLM ACTUALLY filled.** Shelves it left
         `unknown`, and shelves that were already filled, keep the provenance
         they had. Provenance is the record of how a value got here; stamping
         it on a shelf the LLM did not fill would make the audit trail a lie.
      3. **`unknown` is not a value.** The prompt mandates the explicit
         `unknown` enum over invention (§4's consumer table); that maps to
         "leave the shelf absent", never to a stored string.

    Args:
        job: the catalog row, already carrying its stored `shelf_provenance`.
        enrichment: a `JobEnrichment` (its enums are Pydantic-validated, so
            the values are already the canonical strings the gate's own
            closed-enum normaliser produces).
        by: what to record in provenance as the filler — the MODEL name in
            production, so a future re-read can tell which model said what.

    Returns:
        `(job, filled_shelves)` — the same mutated job, and the tuple of shelf
        names this call actually filled. An empty tuple means the LLM read the
        ad and honestly had nothing to add, which is a real outcome, not a
        failure.
    """
    targets = set(absent_shelves(job))
    provenance: dict[str, Any] = dict(job.shelf_provenance or {})
    at = _now()
    filled: list[str] = []

    def _set(shelf: str, value: str, *, raw: Any = None) -> None:
        setattr(job, shelf, value)
        provenance[shelf] = _llm_entry(by, at, raw=raw)
        filled.append(shelf)

    for shelf, attr in (
        ("employment_type", "employment_type"),
        ("seniority", "seniority"),
        ("workplace_mode", "workplace_type"),
        ("category", "category"),
    ):
        if shelf not in targets:
            continue
        value = _enum_str(getattr(enrichment, attr, None))
        if value is None:
            continue
        if shelf == "category" and value == JobCategory.OTHER.value:
            # `JobCategory` is the one closed enum in the contract with NO
            # `unknown` member (job_enrichment_schema.py) — Pydantic rejects
            # anything outside the 16, so a model that cannot tell has only one
            # escape hatch and it always takes it. An "other" it was FORCED
            # into carries no information, and storing it would convert "we
            # could not classify this" into "this job is genuinely
            # miscellaneous" — a guess dressed as a fact (rule #29). The shelf
            # stays absent so a real classifier (services/domain_classifier.py,
            # or a source's own taxonomy via _CATEGORY_ALIASES, where an
            # explicit "All Other Remote" IS a real answer) can still fill it.
            continue
        _set(shelf, value)

    if "visa_status" in targets:
        raw_visa = _enum_str(getattr(enrichment, "visa_sponsorship", None))
        mapped = _VISA_FROM_LLM.get((raw_visa or "").lower())
        if mapped is not None:
            _set("visa_status", mapped, raw=raw_visa)

    if "salary" in targets:
        band = getattr(enrichment, "salary", None)
        band_min = getattr(band, "min", None)
        band_max = getattr(band, "max", None)
        if band_min is not None or band_max is not None:
            job.salary_min = band_min
            job.salary_max = band_max
            job.salary_currency = getattr(band, "currency", None) or None
            job.salary_period = _enum_str(getattr(band, "frequency", None))
            # Same policy as ingest, deliberately: annualise + convert, then
            # clamp. If the gate REFUSES the band (implausible / unpriceable
            # currency) that refusal is recorded as-is — an LLM number gets no
            # special treatment just because it cost money.
            entry = _fill_salary(job)
            if entry.get("how") == "source":
                provenance["salary"] = _llm_entry(by, at, raw=entry.get("raw"))
                filled.append("salary")
            else:
                provenance["salary"] = entry

    job.shelf_provenance = provenance
    return job, tuple(filled)


def fill_shelves(job: Job) -> Job:
    """The chokepoint (UNIVERSAL_SHELF.md §5). Every job that reaches storage
    passes through here exactly once, called by
    `main.py::_score_dedup_and_filter` BEFORE scoring — see the module
    docstring.

    Synchronous, no I/O. Normalises the four closed-enum shelves against the
    schemas in `job_enrichment_schema.py`, annualises + converts the salary
    band to GBP and then clamps it, derives a deadline from the ad text, runs
    the free visa-text detector, and stamps `job.shelf_provenance[shelf]` for EVERY shelf in
    `UNIVERSAL_SHELF` — filled or absent. The invariant is not "every shelf
    filled" (impossible — most jobs genuinely lack a deadline or a salary);
    it is "every shelf ACCOUNTED FOR": `set(job.shelf_provenance) ==
    set(UNIVERSAL_SHELF)` after this call, always, or it is a bug in this
    function (guarded by
    tests/test_universal_shelf.py::test_gate_accounts_for_every_shelf).

    Mutates and returns `job` (matches the design doc's signature); callers
    that need the pre-gate object should copy first.

    RE-RUNNING THE GATE on a row already in the catalog (what pass 1 of the
    JOB SOURCE ENRICHMENT sweep does) is safe and is the whole point of the
    sweep's free half — but it must not REWRITE HISTORY. A shelf an LLM filled
    is loaded back off the row as a plain value, and the gate, seeing a value,
    would happily re-stamp it `how:"source"` — turning "a model decided this"
    into "the board told us this". So: a prior `how:"llm"` entry is KEPT
    whenever the shelf's value comes out of this pass unchanged. On the ingest
    path there is no prior provenance at all, so this is a no-op there and the
    behaviour is byte-identical to before.
    """
    prior: dict[str, Any] = dict(job.shelf_provenance) if job.shelf_provenance else {}
    before = {shelf: _shelf_snapshot(job, shelf) for shelf in LLM_FILLABLE_SHELVES} if prior else {}

    provenance: dict[str, Any] = dict(prior)
    for shelf in UNIVERSAL_SHELF:
        filler = _SHELF_FILLERS[shelf]
        provenance[shelf] = filler(job)

    for shelf, snapshot in before.items():
        entry = prior.get(shelf)
        if (
            isinstance(entry, dict)
            and entry.get("how") == "llm"
            and _shelf_snapshot(job, shelf) == snapshot
        ):
            provenance[shelf] = entry

    job.shelf_provenance = provenance
    return job
