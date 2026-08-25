"""Universal Shelf, Step 1 — the frame (docs/pillars/UNIVERSAL_SHELF.md §5/§6).

Covers `services/shelf_gate.fill_shelves()` in ISOLATION. Nothing in the
pipeline calls `fill_shelves()` yet (that wiring is step 2), so these tests
build `Job` objects directly and call the gate themselves — exactly the
"built and tested in isolation" contract the step-1 task set out.
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from src.models import UNIVERSAL_SHELF, Job
from src.repositories.database import JobDatabase
from src.services.shelf_gate import fill_shelves, is_stub_description


@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer",
        company="DeepMind",
        apply_url="https://example.com/job",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London",
        description="AI role",
    )
    defaults.update(overrides)
    return Job(**defaults)


# ---------------------------------------------------------------------------
# 1. Every shelf is ACCOUNTED FOR — filled or absent, never missing.
# ---------------------------------------------------------------------------


def test_gate_accounts_for_every_shelf():
    """`fill_shelves(Job(minimal))` -> `set(job.shelf_provenance) ==
    set(UNIVERSAL_SHELF)` exactly. UNIVERSAL_SHELF is a frozen tuple in
    models.py — the single source of truth the gate, migration, and this
    test all import. Add a shelf to the tuple without teaching the gate and
    this test (and, independently, a KeyError inside fill_shelves itself)
    both fail loudly instead of the shelf silently going unaccounted-for.
    """
    job = _make_job()
    fill_shelves(job)

    assert set(job.shelf_provenance.keys()) == set(UNIVERSAL_SHELF)
    # Every entry has a `how`, and every `how` is one of the four defined
    # values (UNIVERSAL_SHELF.md §3) — the invariant is "accounted for", not
    # "filled": absent entries are still valid, complete entries.
    for shelf, entry in job.shelf_provenance.items():
        assert entry["how"] in ("source", "derived", "llm", "absent"), shelf
        if entry["how"] == "absent":
            assert "why" in entry, shelf


def test_gate_is_the_single_source_of_truth_for_shelf_names():
    """A shelf gains no meaning by being IN `UNIVERSAL_SHELF` alone — the gate
    must actually know how to fill it. This is the flip side of the test
    above: every name the gate CAN produce is also a name UNIVERSAL_SHELF
    declares, so the two can never drift apart silently.
    """
    from src.services.shelf_gate import _SHELF_FILLERS  # noqa: PLC0415

    assert set(_SHELF_FILLERS.keys()) == set(UNIVERSAL_SHELF)


# ---------------------------------------------------------------------------
# 2. ABSENT is typed — NULL + a reason, never a guess, never 0.
# ---------------------------------------------------------------------------


def test_absent_is_typed(db):
    """A job with no salary stores NULL + {"how": "absent"} in memory AND
    after a real DB round trip — never 0, never a guess (rule #29).
    """
    job = _make_job(salary_min=None, salary_max=None)
    fill_shelves(job)

    assert job.salary_min is None
    assert job.salary_max is None
    assert job.shelf_provenance["salary"] == {"how": "absent", "why": "not_stated"}

    # And it survives storage as NULL, not 0 or "" — a DB default masking an
    # absent value would be exactly the schema-presence-without-value-presence
    # bug rule #21 exists to catch.
    asyncio.run(db.insert_job(job))
    rows = asyncio.run(db.get_recent_jobs(days=9999))
    row = next(r for r in rows if r["title"] == job.title)
    assert row["salary_min"] is None
    assert row["salary_max"] is None


def test_absent_employment_type_keeps_the_raw_value_for_audit():
    """A raw value the gate's normaliser doesn't recognise is counted (never
    silently dropped) — the shelf is ABSENT (NULL), but the original string
    survives in provenance so a future alias fix is traceable.
    """
    job = _make_job(employment_type="some upstream string nobody taught the gate")
    fill_shelves(job)

    assert job.employment_type is None
    entry = job.shelf_provenance["employment_type"]
    assert entry["how"] == "absent"
    assert entry["why"] == "not_mapped"
    assert entry["raw"] == "some upstream string nobody taught the gate"


def test_typographic_en_dash_normalises_the_same_as_ascii_hyphen():
    """google_jobs (SerpApi) sends `schedule_type` as "Full–time" with a
    typographic EN DASH (U+2013), confirmed live 2026-08-17 on 38/39 sampled
    rows — every single one landed as absent/not_mapped before this fix,
    even though the value WAS correctly read off the payload.
    """
    job = _make_job(employment_type="Full–time")
    fill_shelves(job)

    assert job.employment_type == "full_time"
    assert job.shelf_provenance["employment_type"]["how"] == "source"


def test_employment_type_alias_maps_a_known_synonym():
    """"Contractor" (google_jobs schedule_type, confirmed live) is a real
    synonym for EmploymentType.CONTRACT, not a guess — the alias table maps
    it; an UNRELATED string still falls through to absent/not_mapped."""
    job = _make_job(employment_type="Contractor")
    fill_shelves(job)
    assert job.employment_type == "contract"


def test_ambiguous_category_is_left_unmapped_not_guessed():
    """Adzuna's "IT Jobs" spans software_engineering, devops_infrastructure
    and data_science in real postings — deliberately NOT in the alias table
    (rule #29: never guess to make a fill% look better)."""
    job = _make_job(category="IT Jobs")
    fill_shelves(job)
    assert job.category is None
    entry = job.shelf_provenance["category"]
    assert entry["how"] == "absent"
    assert entry["raw"] == "IT Jobs"


def test_category_alias_maps_an_unambiguous_adzuna_label():
    """Adzuna's "Healthcare & Nursing Jobs" (confirmed live) maps 1:1 onto
    JobCategory.HEALTHCARE — an honest synonym, not a classification guess.
    """
    job = _make_job(category="Healthcare & Nursing Jobs")
    fill_shelves(job)
    assert job.category == "healthcare"
    assert job.shelf_provenance["category"]["how"] == "source"


def test_category_alias_maps_a_dfe_apprenticeship_route():
    """gov_apprenticeships' `course.route` "Education and early years" (one
    of DfE's 15 published, closed routes, confirmed live) maps 1:1 onto
    JobCategory.EDUCATION."""
    job = _make_job(category="Education and early years")
    fill_shelves(job)
    assert job.category == "education"


# ---------------------------------------------------------------------------
# 2b. Pillar 3 ATS batch (greenhouse/lever/workable/ashby/smartrecruiters/
# pinpoint/recruitee/workday/personio/successfactors) — PascalCase and
# compound-token normalisation + the new employment_type/seniority/category
# synonym entries, every one confirmed against a real live payload.
# ---------------------------------------------------------------------------


def test_bare_camelcase_employment_type_normalises_without_an_alias():
    """Ashby sends `employmentType` in bare PascalCase with NO space or dash
    ("FullTime" — confirmed live 2026-08-17, cohere board: 144/144 filled).
    `.replace(" ", "_").replace("-", "_")` has nothing to act on, so
    "FullTime".lower() == "fulltime" never matched "full_time" before the
    camelCase-boundary fix — every Ashby employment_type row landed as
    absent/not_mapped despite the raw value being 100% present. This is a
    real value the gate's OWN normaliser now reaches; it needs no alias.
    """
    job = _make_job(employment_type="FullTime")
    fill_shelves(job)
    assert job.employment_type == "full_time"
    assert job.shelf_provenance["employment_type"]["how"] == "source"


def test_bare_pascalcase_workplace_mode_normalises_the_opposite_direction():
    """Ashby's `workplaceType` "OnSite" (confirmed live 2026-08-17, cohere
    board) needs the OPPOSITE fix from "FullTime": inserting an underscore
    at the case boundary produces "on_site", which still would not equal
    WorkplaceType.ONSITE's "onsite" (no internal separator at all) without
    also comparing the underscore-STRIPPED forms.
    """
    job = _make_job(workplace_mode="OnSite")
    fill_shelves(job)
    assert job.workplace_mode == "onsite"


def test_bare_compound_word_employment_type_normalises_via_squash():
    """Recruitee's `employment_type_code` also emits bare, already-lowercase
    compound words with no separator at all ("fulltime"/"parttime",
    confirmed live 2026-08-17, theentouragegroup board) — the squash-compare
    catches this case too, with no alias needed."""
    job = _make_job(employment_type="fulltime")
    fill_shelves(job)
    assert job.employment_type == "full_time"


def test_employment_type_alias_maps_recruitees_compound_code():
    """Recruitee's OWN closed `employment_type_code` vocabulary combines
    hours and contract nature into one token ("fulltime_permanent",
    "parttime_fixed_term", confirmed live 2026-08-17 across 3 boards,
    939 combined rows) — a structured field, not free text, so the
    translation is a synonym lookup, not a guess."""
    job = _make_job(employment_type="fulltime_permanent")
    fill_shelves(job)
    assert job.employment_type == "full_time"

    job2 = _make_job(employment_type="parttime_fixed_term")
    fill_shelves(job2)
    assert job2.employment_type == "contract"


def test_employment_type_alias_maps_permanent_and_fixed_term():
    """"Permanent" (Lever `categories.commitment`, 91/102 spotify rows) and
    "Fixed-Term" (Lever "Fixed-Term", Personio `fixed_term`) are both real,
    unambiguous synonyms in the closed-set sense rule #30 allows."""
    job = _make_job(employment_type="Permanent")
    fill_shelves(job)
    assert job.employment_type == "full_time"

    job2 = _make_job(employment_type="Fixed-Term")
    fill_shelves(job2)
    assert job2.employment_type == "contract"


def test_seniority_alias_maps_personios_closed_vocabulary():
    """Personio's `<seniority>` is its OWN closed, structured vocabulary
    (confirmed live 2026-08-17, stable across 8 boards):
    {student, entry-level, experienced, executive}. "experienced" is
    deliberately NOT aliased — it sits ambiguously between mid and senior
    (rule #29)."""
    job = _make_job(seniority="entry-level")
    fill_shelves(job)
    assert job.seniority == "junior"

    job2 = _make_job(seniority="executive")
    fill_shelves(job2)
    assert job2.seniority == "director"

    job3 = _make_job(seniority="experienced")
    fill_shelves(job3)
    assert job3.seniority is None
    assert job3.shelf_provenance["seniority"]["raw"] == "experienced"


def test_seniority_alias_leaves_recruitees_management_ladder_unmapped():
    """Recruitee's `experience_code` also carries a corporate-management
    ladder (manager/senior_manager/senior_executive, confirmed live
    2026-08-17, transperfect board: 21+4=25/591 rows) that does not fit the
    IC ladder SeniorityLevel encodes (intern..director) — forcing a
    translation would misclassify a manager as a "director"-level IC, so
    these stay honestly unmapped."""
    job = _make_job(seniority="senior_manager")
    fill_shelves(job)
    assert job.seniority is None


def test_seniority_alias_maps_devitjobs_regular_but_leaves_lead_unmapped():
    """devitjobs' `expLevel` is its OWN closed, structured field (confirmed
    live 2026-08-17: {Senior: 1276, Regular: 871, Lead: 335, Junior: 133}
    across 2,615 postings). "Regular" is devitjobs' word for "ordinary/
    standard level" — an unambiguous synonym for `mid`. "Lead" is
    deliberately NOT aliased — tech-lead vs people-lead is not decidable
    from the word alone (same ambiguity as "experienced" above)."""
    job = _make_job(seniority="Regular")
    fill_shelves(job)
    assert job.seniority == "mid"

    job2 = _make_job(seniority="Lead")
    fill_shelves(job2)
    assert job2.seniority is None
    assert job2.shelf_provenance["seniority"]["raw"] == "Lead"


def test_workplace_mode_alias_maps_devitjobs_office_to_onsite():
    """devitjobs' `workplace` is its OWN closed, structured 3-way field
    (confirmed live 2026-08-17: {office: 2248, hybrid: 207, remote: 160}
    across 2,615 postings, 100% fill). "office" is devitjobs' own word for
    on-site attendance — the field's only non-remote, non-hybrid member, so
    the synonym is unambiguous, not a guess."""
    job = _make_job(workplace_mode="office")
    fill_shelves(job)
    assert job.workplace_mode == "onsite"


def test_category_alias_maps_personios_it_software_bucket():
    """Personio's `<occupationCategory>` "it_software" is its own closed
    umbrella for every software role (confirmed live 2026-08-17 across 8
    boards — the single most common bucket on 6 of them)."""
    job = _make_job(category="it_software")
    fill_shelves(job)
    assert job.category == "software_engineering"


def test_category_alias_maps_recruitees_legal_services_and_hr():
    """Recruitee's `category_code` "legal_services"/"recruitment_hr"
    (confirmed live 2026-08-17, transperfect board) are unambiguous 1:1
    synonyms; "engineering"/"information_technology" from the SAME field
    are deliberately left unmapped — Recruitee spans every industry, so
    "engineering" there is not always software (rule #29)."""
    job = _make_job(category="legal_services")
    fill_shelves(job)
    assert job.category == "legal"

    job2 = _make_job(category="engineering")
    fill_shelves(job2)
    assert job2.category is None


# ---------------------------------------------------------------------------
# 2c. Pillar 3 VOCABULARY batch — the seniority and category shelves read
# 0.0% on ALL 39 sources in the 2,772-job baseline run (2026-08-17), and
# workplace_mode/visa_status were near-zero. The cause was NOT missing data:
# the values arrived and the gate honestly recorded absent/not_mapped with
# the raw string kept. These tables are that harvest, verbatim — every
# string below was read off a real payload (the baseline DB's provenance,
# plus live re-probes of themuse / weworkremotely / workable / nofluffjobs /
# recruitee / remotive / jobicy, whose source-side mapping landed after the
# baseline run).
#
# Each table is (raw_value, expected_enum_or_None, where_it_came_from).
# `None` means "deliberately unmapped": rule #29 — a wrong enum is worse
# than an empty shelf, because JOB SOURCE ENRICHMENT caches it and it
# mis-ranks jobs forever. The raw value must survive in provenance either
# way, which is what made THIS gap findable and must keep the next one
# findable.
# ---------------------------------------------------------------------------

_EMPLOYMENT_TYPE_HARVEST = [
    # (raw, expected, source it was observed on)
    ("Permanent - Full Time", "full_time", "pinpoint (156 rows)"),
    ("Permanent - Part Time", "part_time", "pinpoint (14 rows)"),
    ("Permanent", "full_time", "pinpoint (121) / lever (30) / nhs_jobs (4)"),
    ("Fixed Term - Full Time", "contract", "pinpoint (9 rows)"),
    ("Fixed Term - Part Time", "contract", "pinpoint (2 rows)"),
    ("Fixed Term Contract", "contract", "pinpoint (6 rows)"),
    ("Seasonal - Full Time", "temporary", "pinpoint (2 rows)"),
    ("Apprentice", "apprenticeship", "pinpoint (1 row)"),
    ("Full time role", "full_time", "climatebase (22 rows)"),
    ("Full–time", "full_time", "google_jobs EN DASH (32 rows)"),
    ("Contractor", "contract", "google_jobs (3 rows)"),
    ("FullTime", "full_time", "ashby (37 rows)"),
    ("fulltime", "full_time", "indeed (104) / recruitee"),
    ("parttime", "part_time", "indeed (1 row)"),
    ("fulltime_permanent", "full_time", "recruitee (482 rows)"),
    ("parttime_fixed_term", "contract", "recruitee (23 rows)"),
    ("freelance", "freelance", "recruitee (91 rows)"),
    ("Full-Time", "full_time", "weworkremotely (95 rows)"),
    ("Contract", "contract", "weworkremotely (5 rows)"),
    ("Full-time", "full_time", "workable (35 rows)"),
    ("permanent", "full_time", "adzuna (41) / personio (30)"),
    ("fixed_term", "contract", "personio (1 row)"),
    # --- deliberately NOT mapped ---
    # A multi-value list cannot land in a single-valued shelf honestly.
    ("Full–time and Contractor", None, "google_jobs (3 rows)"),
    ("parttime, fulltime", None, "indeed (1 row)"),
    ("temporary, parttime, fulltime, volunteer, internship", None, "indeed (1)"),
    ("Full Time Contractor", None, "lever (1 row)"),
    # NHS/care "Bank" is a staffing-pool concept, not an hours or duration
    # statement — bank contracts can run for years.
    ("Bank", None, "nhs_jobs (3) / pinpoint (10)"),
    # Zero-hours says nothing about full/part time OR duration.
    ("Zero Hours", None, "pinpoint (3 rows)"),
    # Volunteering is unpaid — none of the seven paid types fit.
    ("Volunteer", None, "pinpoint (1 row)"),
    # "Other" is the source saying it does not know.
    ("Other", None, "workable (3 rows)"),
]

_SENIORITY_HARVEST = [
    ("Junior (1-4 years experience)", "junior", "eightykhours (36 rows)"),
    ("Mid (5-9 years experience)", "mid", "eightykhours (34 rows)"),
    ("Senior (10+ years experience)", "senior", "eightykhours (2 rows)"),
    ("Entry-level", "junior", "eightykhours (3) / personio (6)"),
    ("Entry level", "junior", "workable (10 rows)"),
    ("Associate", "junior", "workable (12 rows)"),
    ("Director", "director", "workable (1 row)"),
    ("entry_level", "junior", "recruitee (435 rows)"),
    ("mid_level", "mid", "recruitee (221 rows)"),
    ("student_school", "intern", "recruitee (24 rows)"),
    ("executive", "director", "recruitee (2) / personio (2)"),
    ("Senior", "senior", "nofluffjobs (13,106 rows)"),
    ("Mid", "mid", "nofluffjobs (7,261 rows)"),
    ("Junior", "junior", "nofluffjobs (692 rows)"),
    ("Trainee", "intern", "nofluffjobs (31 rows)"),
    ("Midweight", "mid", "jobicy jobLevel (live 2026-08-17)"),
    # --- deliberately NOT mapped ---
    # DfE apprenticeship COURSE levels, not a professional ladder: an
    # "Advanced" apprenticeship is still someone's first job, and a global
    # alias would poison every other source that means senior by "Advanced".
    ("Intermediate", None, "gov_apprenticeships (49 rows)"),
    ("Advanced", None, "gov_apprenticeships (92 rows)"),
    ("Higher", None, "gov_apprenticeships (13 rows)"),
    ("Degree", None, "gov_apprenticeships (2 rows)"),
    # Fuses two of our tiers into one bucket — either choice is a coin flip.
    ("Mid-Senior level", None, "workable (42 rows)"),
    ("experienced", None, "recruitee (164) / personio (28)"),
    ("Expert", None, "nofluffjobs (706 rows)"),
    # A corporate-management ladder, not the IC ladder this enum encodes.
    ("manager", None, "recruitee (2 rows)"),
    ("senior_manager", None, "recruitee (11 rows)"),
    ("senior_executive", None, "recruitee (3 rows)"),
    # The ad itself says the level is not one value.
    ("Multiple experience levels", None, "eightykhours (7 rows)"),
]

_WORKPLACE_MODE_HARVEST = [
    ("In-person", "onsite", "climatebase (16 rows)"),
    ("Remote", "remote", "climatebase (6 rows)"),
    ("OnSite", "onsite", "ashby"),
    ("office", "onsite", "devitjobs (2,248 rows)"),
    ("hybrid", "hybrid", "devitjobs (207 rows)"),
]

_CATEGORY_HARVEST = [
    # TheMuse `categories[0].name`
    ("Software Engineering", "software_engineering", "themuse"),
    ("Data and Analytics", "data_science", "themuse"),
    ("Human Resources and Recruitment", "hr_people", "themuse"),
    ("Business Operations", "operations", "themuse"),
    ("Design and UX", "design", "themuse"),
    ("Legal Services", "legal", "themuse / recruitee"),
    ("Sales", "sales", "themuse (9 rows) / jobicy (19 rows)"),
    # WeWorkRemotely RSS <category> (10 fixed board sections)
    ("Full-Stack Programming", "software_engineering", "weworkremotely"),
    ("Front-End Programming", "software_engineering", "weworkremotely"),
    ("Back-End Programming", "software_engineering", "weworkremotely"),
    ("DevOps and Sysadmin", "devops_infrastructure", "weworkremotely"),
    ("Product", "product_management", "weworkremotely"),
    ("Design", "design", "weworkremotely"),
    ("All Other Remote", "other", "weworkremotely — its own catch-all"),
    # NoFluffJobs `category` (own 36-value closed IT taxonomy)
    ("backend", "software_engineering", "nofluffjobs (3,453 rows)"),
    ("frontend", "software_engineering", "nofluffjobs (486 rows)"),
    ("fullstack", "software_engineering", "nofluffjobs (1,312 rows)"),
    ("mobile", "software_engineering", "nofluffjobs (465 rows)"),
    ("gameDev", "software_engineering", "nofluffjobs (17 rows)"),
    ("artificialIntelligence", "machine_learning", "nofluffjobs (1,168 rows)"),
    ("devops", "devops_infrastructure", "nofluffjobs (1,435 rows)"),
    ("sysAdministrator", "devops_infrastructure", "nofluffjobs (308 rows)"),
    ("data", "data_science", "nofluffjobs (2,805 rows)"),
    ("productManagement", "product_management", "nofluffjobs (537 rows)"),
    ("ux", "design", "nofluffjobs (331 rows)"),
    ("hr", "hr_people", "nofluffjobs (187 rows)"),
    ("law", "legal", "nofluffjobs (3 rows)"),
    ("logistics", "operations", "nofluffjobs (71) / recruitee (1)"),
    ("other", "other", "nofluffjobs (550 rows) — exact enum member"),
    # Recruitee `category_code`
    ("marketing_pr", "marketing", "recruitee (32 rows)"),
    ("accountancy", "finance", "recruitee (4 rows)"),
    ("recruitment_hr", "hr_people", "recruitee (16 rows)"),
    ("education", "education", "recruitee (11 rows)"),
    ("healthcare", "healthcare", "recruitee (1 row)"),
    # Remotive `category`
    ("Software Development", "software_engineering", "remotive"),
    ("All others", "other", "remotive — its own catch-all"),
    ("Medical", "healthcare", "remotive"),
    # Jobicy `jobIndustry` (unescaped by the source — the feed sends &amp;)
    ("Data Science & Analytics", "data_science", "jobicy (2 rows)"),
    ("DevOps & Infrastructure", "devops_infrastructure", "jobicy (1 row)"),
    ("Finance & Accounting", "finance", "jobicy (2 rows)"),
    ("Healthcare & Medical", "healthcare", "jobicy (2 rows)"),
    # Adzuna — the ampersand spellings still work after the "&"->"and" pass
    ("Healthcare & Nursing Jobs", "healthcare", "adzuna"),
    ("Marketing & PR Jobs", "marketing", "adzuna"),
    ("Accounting & Finance Jobs", "finance", "adzuna"),
    ("HR & Recruitment Jobs", "hr_people", "adzuna"),
    ("Creative & Design Jobs", "design", "adzuna"),
    # --- deliberately NOT mapped ---
    # Spans software_engineering / devops_infrastructure / data_science.
    ("IT Jobs", None, "adzuna (102 rows)"),
    ("Information Technology", None, "remotive / recruitee"),
    ("Digital", None, "gov_apprenticeships route"),
    # "Engineering" on a general board is anything from software to
    # mechanical; a shared alias table cannot mean both.
    ("Engineering Jobs", None, "adzuna (10 rows)"),
    ("Engineering", None, "workable function (18 rows)"),
    ("Science and Engineering", None, "themuse (4 rows)"),
    # Each of these fuses two of our members into one bucket.
    ("Sales and Marketing", None, "weworkremotely"),
    ("Management and Finance", None, "weworkremotely"),
    ("Marketing & Sales", None, "jobicy (3 rows)"),
    ("Product & Operations", None, "jobicy (2 rows)"),
    ("Project & Program Management", None, "jobicy (1 row)"),
    # Real disciplines this 16-way taxonomy simply has no member for.
    ("Trade & Construction Jobs", None, "adzuna (1 row)"),
    ("Construction", None, "themuse (2 rows)"),
    ("Real Estate", None, "themuse (1 row)"),
    ("Customer Support", None, "weworkremotely"),
    ("Customer Service", None, "remotive"),
    ("Writing", None, "remotive (2 rows)"),
    ("Cybersecurity", None, "jobicy (3 rows)"),
    ("security", None, "nofluffjobs (881 rows)"),
    ("testing", None, "nofluffjobs (1,282 rows)"),
    ("businessIntelligence", None, "nofluffjobs (112 rows)"),
    ("retail", None, "recruitee (174 rows)"),
    # Project management is NOT product management.
    ("Project Management", None, "themuse (1 row)"),
    # Spans sales and operations depending on the company.
    ("Account Management", None, "themuse (2 rows, live 2026-08-17)"),
    ("projectManager", None, "nofluffjobs (892 rows)"),
    # On an IT board "architecture" means software; on a general board it
    # means buildings. One shared table cannot honestly mean both.
    ("architecture", None, "nofluffjobs (887 rows)"),
]


@pytest.mark.parametrize(
    "shelf,table",
    [
        ("employment_type", _EMPLOYMENT_TYPE_HARVEST),
        ("seniority", _SENIORITY_HARVEST),
        ("workplace_mode", _WORKPLACE_MODE_HARVEST),
        ("category", _CATEGORY_HARVEST),
    ],
)
def test_harvested_vocabulary_normalises_to_the_right_enum(shelf, table):
    """Every raw string a real source really sent lands on the enum member a
    human signed off on — or stays honestly UNSET.

    Failures are printed with the source they came from, because that is the
    thing you need to re-probe when an upstream changes its vocabulary.
    """
    wrong = []
    for raw, expected, origin in table:
        job = _make_job(**{shelf: raw})
        fill_shelves(job)
        actual = getattr(job, shelf)
        if actual != expected:
            wrong.append(f"{shelf}={raw!r} ({origin}): expected {expected!r}, got {actual!r}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize(
    "shelf,table",
    [
        ("employment_type", _EMPLOYMENT_TYPE_HARVEST),
        ("seniority", _SENIORITY_HARVEST),
        ("workplace_mode", _WORKPLACE_MODE_HARVEST),
        ("category", _CATEGORY_HARVEST),
    ],
)
def test_harvested_vocabulary_always_keeps_the_raw_value(shelf, table):
    """MAPPED OR NOT, the raw string survives in provenance.

    That is the whole reason this batch was findable: the 0.0% shelves were
    not silent, they were recorded as absent/not_mapped WITH the exact string
    that failed. A mapped value keeps its raw too whenever the raw differs
    from the normalised value.
    """
    missing = []
    for raw, expected, origin in table:
        job = _make_job(**{shelf: raw})
        fill_shelves(job)
        entry = job.shelf_provenance[shelf]
        if expected is None:
            if entry.get("how") != "absent" or entry.get("why") != "not_mapped":
                missing.append(f"{shelf}={raw!r} ({origin}): not typed absent/not_mapped: {entry}")
            elif entry.get("raw") != raw:
                missing.append(f"{shelf}={raw!r} ({origin}): raw lost: {entry}")
        else:
            if entry.get("how") != "source":
                missing.append(f"{shelf}={raw!r} ({origin}): not typed source: {entry}")
            elif raw != expected and entry.get("raw") != raw:
                missing.append(f"{shelf}={raw!r} ({origin}): raw lost: {entry}")
    assert not missing, "\n".join(missing)


def test_an_unknown_value_lands_absent_with_its_raw_not_coerced():
    """The contract for a vocabulary NOBODY has taught the gate: never
    coerced into the nearest enum, never the literal "unknown", never
    dropped. Absent, with the raw string kept for the next harvest."""
    for shelf in ("employment_type", "seniority", "workplace_mode", "category"):
        job = _make_job(**{shelf: "Gluten Free Wizardry (Tier 9)"})
        fill_shelves(job)
        assert getattr(job, shelf) is None, shelf
        entry = job.shelf_provenance[shelf]
        assert entry["how"] == "absent", shelf
        assert entry["why"] == "not_mapped", shelf
        assert entry["raw"] == "Gluten Free Wizardry (Tier 9)", shelf


def test_a_trailing_parenthetical_is_a_qualifier_not_the_value():
    """80,000 Hours writes three spellings of three words the gate already
    knows ("Junior (1-4 years experience)"). Stripping the bracket is a
    SEPARATOR rule, so whatever is left must STILL match a real member —
    a bracketed value with no honest target is untouched by it."""
    job = _make_job(seniority="Mid (5-9 years experience)")
    fill_shelves(job)
    assert job.seniority == "mid"
    assert job.shelf_provenance["seniority"]["raw"] == "Mid (5-9 years experience)"

    job2 = _make_job(seniority="Wizard (all levels)")
    fill_shelves(job2)
    assert job2.seniority is None


def test_ampersand_and_and_are_not_two_different_table_rows():
    """"Data Science & Analytics" (jobicy) and "Data and Analytics"
    (themuse) are the same words with different punctuation — one alias row
    covers both because "&" is normalised to "and" before lookup."""
    for raw in ("Data Science & Analytics", "Data Science and Analytics"):
        job = _make_job(category=raw)
        fill_shelves(job)
        assert job.category == "data_science", raw


def test_visa_unknown_is_a_real_absent_value_not_a_missing_one():
    """Rule #31: `unknown` IS the third visa state, stored as a literal
    value — but its provenance `how` is still "absent" (the ad never said),
    distinct from a shelf nobody looked at.
    """
    job = _make_job(description="We are hiring a great engineer to join our team.")
    fill_shelves(job)

    assert job.visa_status == "unknown"
    assert job.shelf_provenance["visa_status"] == {"how": "absent", "why": "not_stated"}


def test_visa_status_prefers_a_structured_source_signal_over_free_text():
    """A source that sets `job.visa_status` to a raw structured verdict
    (devitjobs.py's `hasVisaSponsorship`, "Yes"/"No") BEFORE the gate runs
    must win over the free-text detector, not be silently clobbered by it —
    `detect_visa_status`'s `enrichment_value` param existed for exactly this
    and was never wired until now. Description text says nothing either way,
    so a text-only detector would call this "unknown"."""
    job = _make_job(
        description="We are hiring a great engineer to join our team.",
        visa_status="Yes",
    )
    fill_shelves(job)
    assert job.visa_status == "sponsors"
    assert job.shelf_provenance["visa_status"]["how"] == "source"

    job2 = _make_job(
        description="We are hiring a great engineer to join our team.",
        visa_status="No",
    )
    fill_shelves(job2)
    assert job2.visa_status == "no_sponsorship"
    assert job2.shelf_provenance["visa_status"]["how"] == "source"


def test_visa_status_with_no_structured_signal_falls_back_to_free_text():
    """A source that never sets `job.visa_status` (the vast majority today)
    must degrade to EXACTLY the old free-text-only behaviour."""
    job = _make_job(description="Visa sponsorship is available for this role.")
    fill_shelves(job)
    assert job.visa_status == "sponsors"
    assert job.shelf_provenance["visa_status"]["how"] == "derived"


# ---------------------------------------------------------------------------
# 3. Stub descriptions block JOB SOURCE ENRICHMENT (the LLM never fabricates
#    from a teaser).
# ---------------------------------------------------------------------------


def test_llm_blocked_on_stub():
    """A stub-description job is never handed to `enrich_job`.

    Step 3 (JOB SOURCE ENRICHMENT at scale) has no real call site yet — what
    step 1 ships is the block itself: `is_stub_description()` plus the
    `absent:stub` provenance stamp it drives. This test proves both halves
    using the exact guard shape step 3's sweep will use
    (`if is_stub_description(...): skip`), so a stub can never reach an LLM
    and cache a fabrication (`enrich_job` is idempotent per job_id — a wrong
    answer here would be PERMANENT until someone force-re-runs).
    """
    stub_job = _make_job(title="Backend Engineer", description="Short teaser.")
    # Uses the shared real-LENGTH ad rather than a short paragraph. A few
    # hundred characters "reads like" an ad but sits inside the measured
    # teaser band (200-599) that the threshold now refuses — the point of this
    # assertion is that a GENUINE ad passes, so it has to be a genuine length.
    real_job = _make_job(title="Backend Engineer", description=_REAL_AD)
    # Byte-identical to the title, but long enough to clear the length floor
    # on its own — proves the "==title" signal fires independently.
    identical_job = _make_job(
        title="Senior Backend Engineer " * 10,
        description="Senior Backend Engineer " * 10,
    )

    assert is_stub_description(stub_job.description, stub_job.title) is True
    assert is_stub_description(real_job.description, real_job.title) is False
    assert is_stub_description(identical_job.description, identical_job.title) is True

    calls = []

    def fake_enrich_job(job):
        calls.append(job)

    for job in (stub_job, real_job, identical_job):
        if not is_stub_description(job.description, job.title):
            fake_enrich_job(job)

    assert calls == [real_job]

    fill_shelves(stub_job)
    assert stub_job.shelf_provenance["description"] == {"how": "absent", "why": "stub"}

    fill_shelves(real_job)
    assert real_job.shelf_provenance["description"]["how"] == "source"


# ---------------------------------------------------------------------------
# 4. Value-presence, not schema-presence (rule #21) — a filled shelf survives
#    a real write + read. Pattern: test_database.py::test_dim_columns_round_trip.
# ---------------------------------------------------------------------------


def test_gate_output_round_trips_through_the_db(db):
    """A non-default value the gate produces must survive insert_job ->
    get_recent_jobs unchanged — not just have a column ready to receive it.
    """
    job = _make_job(
        title="Round Trip Engineer",
        company="RoundTripCo",
        description=(
            "We are hiring a Round Trip Engineer to build and maintain our "
            "core data pipeline, working across ingestion, storage and the "
            "API layer with a small, senior team."
        ),
        salary_min=45000,
        salary_max=60000,
        employment_type="Full time",  # raw upstream value — the gate normalises it
        source_tags=["python", "postgres"],
    )
    fill_shelves(job)
    assert job.employment_type == "full_time"  # normalised, not the raw string
    assert job.shelf_provenance["employment_type"]["raw"] == "Full time"

    inserted = asyncio.run(db.insert_job(job))
    assert inserted is True

    rows = asyncio.run(db.get_recent_jobs(days=9999))
    row = next(r for r in rows if r["title"] == "Round Trip Engineer")

    # Typed value columns — real values, not just non-NULL.
    assert row["employment_type"] == "full_time"
    assert row["visa_status"] == job.visa_status
    assert json.loads(row["source_tags"]) == ["python", "postgres"]

    # shelf_provenance is native JSONB — psycopg deserialises it to a dict
    # automatically (unlike every JSON-in-TEXT column in this codebase), so
    # no json.loads() here; asserting that IS part of what this test proves.
    provenance = row["shelf_provenance"]
    assert isinstance(provenance, dict)
    assert set(provenance.keys()) == set(UNIVERSAL_SHELF)
    assert provenance["employment_type"] == {
        "how": "source",
        "field": "employment_type",
        "raw": "Full time",
        "at": provenance["employment_type"]["at"],  # timestamp — just needs to exist
    }
    assert provenance["salary"]["how"] == "source"


# ---------------------------------------------------------------------------
# 5. Rule #29 at the gate: an ABSENT value never becomes a number.
# ---------------------------------------------------------------------------


def test_gate_never_turns_an_absent_value_into_a_number():
    """The whole point of the salary hook is conversion, and conversion is
    exactly where invention creeps in. Nothing the gate does may manufacture a
    figure, a bound, a unit or a currency that the source never sent."""
    job = _make_job(
        description=(
            "We are hiring a data engineer to join a small team building "
            "pipelines. You will work with Python and Postgres and own your "
            "work end to end. The role is based in our London office with "
            "flexible hours and a generous holiday allowance."
        ),
    )
    fill_shelves(job)

    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_min_gbp_annual is None
    assert job.salary_max_gbp_annual is None
    assert job.salary_period is None
    assert job.salary_currency is None
    assert job.shelf_provenance["salary"] == {"how": "absent", "why": "not_stated"}
    # No deadline keyword anywhere in that ad -> no deadline. Never "30 days
    # from posting", never today+N.
    assert job.deadline is None
    assert job.shelf_provenance["deadline"] == {"how": "absent", "why": "not_stated"}


def test_gate_never_mirrors_a_missing_salary_bound():
    """normalize_salary mirrors a missing bound onto the survivor, which is
    right for the scorer's overlap maths and WRONG as a stored fact: a job
    advertising "from 45,000" must not gain a maximum it never stated."""
    job = _make_job(salary_min=45000, salary_max=None, salary_currency="GBP",
                    salary_period="year")
    fill_shelves(job)

    assert job.salary_min == 45000
    assert job.salary_max is None
    assert job.salary_min_gbp_annual == 45000
    assert job.salary_max_gbp_annual is None


def test_gate_keeps_a_unit_that_arrived_without_an_amount():
    """reed sends salaryType 'per day' on contract roles whose amounts are
    both null. "We know it is a day rate, we have no number" is a real fact —
    the unit is normalised and kept, and no amount is invented to match it."""
    job = _make_job(salary_min=None, salary_max=None, salary_period="per day")
    fill_shelves(job)

    assert job.salary_period == "daily"
    assert job.salary_min is None and job.salary_max is None
    assert job.salary_min_gbp_annual is None
    assert job.shelf_provenance["salary"]["how"] == "absent"


def test_gate_refuses_to_price_a_currency_fx_does_not_know():
    """landingjobs ships BRL. core/fx.to_gbp passes an unknown code through at
    1:1, which would render a BRL figure wearing a pound sign — a WRONG
    number, not a rough one. The derived GBP pair stays NULL and the source's
    own numbers and code are left untouched."""
    job = _make_job(salary_min=120000, salary_max=150000, salary_currency="BRL",
                    salary_period="year")
    fill_shelves(job)

    assert job.salary_min == 120000        # untouched, still BRL
    assert job.salary_currency == "BRL"    # never relabelled GBP
    assert job.salary_min_gbp_annual is None
    assert job.salary_max_gbp_annual is None
    entry = job.shelf_provenance["salary"]
    assert entry["how"] == "source"
    assert entry["gbp_annual"] == "unpriceable_currency"


def test_gate_clamps_after_converting_not_before():
    """A monthly 3,600 (nofluffjobs) annualises to 43,200 and is plausible; the
    old unit-blind clamp saw "3,600 < 10,000" and destroyed it."""
    job = _make_job(salary_min=3600, salary_max=4200, salary_period="Month",
                    salary_currency="GBP")
    fill_shelves(job)

    assert job.salary_min == 43200
    assert job.salary_max == 50400
    assert job.salary_min_gbp_annual == 43200
    assert job.shelf_provenance["salary"]["how"] == "source"
    # The pre-conversion figures survive for audit — a converted number whose
    # original nobody can see is not reviewable.
    assert job.shelf_provenance["salary"]["raw"]["min"] == 3600
    assert job.shelf_provenance["salary"]["raw"]["period"] == "Month"


def test_a_refused_salary_is_typed_as_refused_not_as_unstated():
    """"The ad said nothing" and "the ad said something we refuse to believe"
    are different facts about a job and route different work (the empty-shelf-
    three-causes rule). A band that survives neither plausibility bound is
    stored NULL with why='implausible', keeping the original figures."""
    job = _make_job(salary_min=1, salary_max=900000, salary_currency="GBP",
                    salary_period="year")
    fill_shelves(job)

    assert job.salary_min is None and job.salary_max is None
    entry = job.shelf_provenance["salary"]
    assert entry == {"how": "absent", "why": "implausible", "raw": entry["raw"]}
    assert entry["raw"]["max"] == 900000


def test_an_unknown_period_token_is_not_silently_called_annual():
    """A token the closed set does not know leaves the amounts exactly as the
    source sent them (what every legacy consumer already assumes) and keeps the
    raw token for a future alias fix — it is never guessed into a unit that
    would multiply the number by 2,080."""
    job = _make_job(salary_min=55000, salary_max=65000, salary_period="fortnightly")
    fill_shelves(job)

    assert job.salary_min == 55000
    assert job.salary_period == "fortnightly"   # untouched, still the raw token
    assert job.shelf_provenance["salary"]["raw"]["period"] == "fortnightly"


# ---------------------------------------------------------------------------
# 6. The deadline pass now runs INSIDE the gate (it used to run in main.py,
#    after scoring).
# ---------------------------------------------------------------------------


def test_deadline_is_extracted_inside_the_gate():
    job = _make_job(
        description=(
            "Senior Data Engineer wanted for a UK-wide programme of work. You "
            "will build ingestion pipelines in Python. Closing date: 30 June "
            "2027. Interviews will follow in the same week."
        ),
    )
    assert job.deadline is None
    fill_shelves(job)

    assert job.deadline == "2027-06-30"
    assert job.deadline_source == "description"
    assert job.shelf_provenance["deadline"]["how"] == "derived"
    assert job.shelf_provenance["deadline"]["by"] == "deadline.extract_deadline@v1"


def test_a_structured_deadline_is_never_overwritten_by_the_text_pass():
    """Trust order (UNIVERSAL_SHELF.md section 2): a structured source field
    beats a derivation, and a lower layer never overwrites a higher one."""
    job = _make_job(
        deadline="2027-01-15",
        deadline_source="listing",
        description="Apply by 30 June 2027 at the latest.",
    )
    fill_shelves(job)

    assert job.deadline == "2027-01-15"
    assert job.shelf_provenance["deadline"]["how"] == "source"


def test_a_bare_date_in_the_ad_body_is_not_a_deadline():
    """Rule #29 again: extract_deadline only fires when a deadline KEYWORD is
    tied to the date, so an ad that merely names a start date keeps a NULL
    deadline instead of gaining a plausible-looking wrong one."""
    job = _make_job(
        description=(
            "This is a fixed-term contract starting 30 June 2027 and running "
            "for twelve months. You will join an established platform team "
            "and work on data ingestion at national scale."
        ),
    )
    fill_shelves(job)

    assert job.deadline is None
    assert job.shelf_provenance["deadline"]["why"] == "not_stated"


# ---------------------------------------------------------------------------
# 7. THE PIPELINE ROUND TRIP — a fake source through the real run_search, and
#    out the other side as a stored row. This is the test that proves the gate
#    is WIRED: everything above would still pass with fill_shelves sitting
#    unused in a file nobody imports (which is exactly what step 1 shipped).
# ---------------------------------------------------------------------------


_ROUND_TRIP_AD = (
    "We are looking for an AI Engineer to join our platform team in London. "
    "You will build and ship services in Python, work with PyTorch and "
    "LangChain on RAG pipelines, and own your code from design through to "
    "production on Postgres and AWS. Deep Learning and LLM experience is "
    "valued, and you will pair with data scientists across the business. "
    "Closing date: 30 June 2027. Interviews run the following week."
)


def _round_trip_job():
    from src.models import Job as _Job

    return _Job(
        title="AI Engineer",
        company="ShelfCo",
        apply_url="https://example.com/shelf-round-trip",
        source="fake_shelf_source",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London, UK",
        description=_ROUND_TRIP_AD,
        # RAW upstream values, exactly as a dumb-mapper source hands them over:
        # an hourly rate in pounds, an un-normalised employment type, the
        # board's own tag vocabulary.
        salary_min=30.0,
        salary_max=45.0,
        salary_period="per hour",
        salary_currency="GBP",
        employment_type="Full time",
        source_tags=["python", "postgres"],
    )


def test_pipeline_round_trip(migrated_db_path, tmp_path):
    """Fake source -> run_search -> the stored row carries FULL provenance and
    a non-default value that really survived (rule #21: value-presence, not
    schema-presence).

    Four separate things have to be true at once for this to pass, and each one
    was a real gap before this batch:
      * the gate runs at all inside the pipeline (provenance is complete);
      * it normalised a raw upstream token ('Full time' -> 'full_time');
      * it ANNUALISED an hourly rate instead of the old clamp nulling it;
      * the deadline pass still runs now that it moved inside the gate.
    """
    from unittest.mock import patch

    from src.main import run_search
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.sources.base import BaseJobSource

    class _FakeSource(BaseJobSource):
        name = "fake_shelf_source"
        category = "free_json"

        async def fetch_jobs(self):
            return [_round_trip_job()]

    def _fake_build(session, source_filter=None, **kwargs):
        return [_FakeSource(session)]

    profile = UserProfile(
        cv_data=CVData(
            raw_text=(
                "AI Engineer with Python, PyTorch, LangChain, RAG, LLM and "
                "Deep Learning experience."
            ),
            skills=["Python", "PyTorch", "LangChain", "RAG", "LLM", "Deep Learning"],
        ),
        preferences=UserPreferences(target_job_titles=["AI Engineer"]),
    )

    async def _go():
        with (
            patch("src.main._build_sources", _fake_build),
            patch("src.main.load_profile", return_value=profile),
            patch("src.main.EXPORTS_DIR", tmp_path / "exports"),
            patch("src.main.REPORTS_DIR", tmp_path / "reports"),
        ):
            return await run_search(db_path=migrated_db_path, no_notify=True)

    stats = asyncio.run(_go())
    assert stats["new_jobs"] == 1, stats

    async def _read():
        database = JobDatabase(migrated_db_path)
        await database.connect()
        try:
            rows = await database.get_recent_jobs(days=9999)
        finally:
            await database.close()
        return rows

    rows = asyncio.run(_read())
    row = next(r for r in rows if r["company"] == "ShelfCo")

    # 1. Every shelf ACCOUNTED FOR on the stored row — not on an in-memory
    #    object a test built itself.
    provenance = row["shelf_provenance"]
    assert isinstance(provenance, dict)
    assert set(provenance.keys()) == set(UNIVERSAL_SHELF)

    # 2. A raw upstream token was normalised on the way through.
    assert row["employment_type"] == "full_time"
    assert provenance["employment_type"]["raw"] == "Full time"

    # 3. The hourly rate was annualised, not clamped away. Under the OLD
    #    unit-blind clamp this row stored salary_min=NULL (30 < 10,000) and
    #    salary_max=45 — a job advertising "45" a year.
    assert row["salary_min"] == 62400.0       # 30.00/h x 2080 h
    assert row["salary_max"] == 93600.0       # 45.00/h x 2080 h
    assert row["salary_min_gbp_annual"] == 62400.0
    assert row["salary_max_gbp_annual"] == 93600.0
    assert row["salary_period"] == "annual"
    assert provenance["salary"]["raw"]["period"] == "per hour"

    # 4. The deadline pass still runs from its new home inside the gate.
    assert row["deadline"] == "2027-06-30"
    assert row["deadline_source"] == "description"
    assert provenance["deadline"]["how"] == "derived"

    # 5. And the shelves a source filled directly still land.
    assert json.loads(row["source_tags"]) == ["python", "postgres"]
    assert row["description"] == _ROUND_TRIP_AD


# ---------------------------------------------------------------------------
# 6. STEP 3 — the two-pass JOB SOURCE ENRICHMENT sweep
#    (services/shelf_enrichment.py; UNIVERSAL_SHELF.md §2 fill chains,
#    §6 dependency order, §7 cost).
#
#    JOB SOURCE ENRICHMENT = an LLM READING a job ad to extract facts about
#    the JOB. Catalog work: the same facts serve every user, so the bill
#    scales with how many JOBS we hold, never with how many PEOPLE we have.
#
#    Every test below runs OFFLINE against a mocked LLM (rule #4 — the suite
#    must never make a live call, and CI has no LLM keys at all).
# ---------------------------------------------------------------------------


# A real-LENGTH ad, not just a realistic-SOUNDING one. This was ~340 chars,
# which is SHORTER than the 452-char Reed teaser measured inventing
# workplace_mode="onsite" — so once the stub threshold rose to 600 the fixture
# was correctly refused. Live descriptions run ~4,000 chars (median ~997 input
# tokens, dry run 2026-08-17), so a few hundred characters was never
# representative of what the LLM actually reads.
_REAL_AD = (
    "We are hiring a Platform Engineer to join our infrastructure team in "
    "Manchester. You will run our Kubernetes clusters, own the CI pipeline "
    "end to end, and work with product engineers to ship safely. We are "
    "looking for someone comfortable with Terraform, Go and Postgres, who "
    "enjoys making other engineers faster. This is a permanent position. "
    "\n\nWhat you will do: own the reliability of a multi-tenant platform "
    "serving several million requests a day; lead the migration of our "
    "remaining virtual machines onto Kubernetes; build the deployment "
    "tooling other teams use every day; take part in a light-touch on-call "
    "rota with the rest of the platform group. "
    "\n\nWhat we are looking for: solid production experience with a cloud "
    "provider, comfort reading and writing Go, hands-on Terraform, and the "
    "judgement to know when infrastructure work is worth doing and when it "
    "is not. You do not need to have done all of this before — we care more "
    "about how you reason than which tools you have used. "
    "\n\nWhat we offer: a competitive salary reviewed every year, hybrid "
    "working with two days a week in the Manchester office, private medical "
    "cover, a training budget, and a genuinely quiet on-call rota."
)


@pytest.fixture(scope="module")
def _sweep_schema(tmp_path_factory):
    """ONE migrated DB shared by every sweep test in this section.

    Measured on this repo's Windows dev box, the per-test `sweep_db`
    fixture costs ~79s (init_db 9s + 32 migrations 39s + drop_schema 32s).
    Ten sweep tests would have put ~13 minutes onto the canonical pre-commit
    run for no extra coverage — a correctness fix with an operational cost is
    still a regression. Bootstrapping once and wiping between tests keeps every
    assertion and pays the cost once.

    Deliberately NOT imported from conftest: importing a fixture across modules
    double-registers it and breaks the per-test Postgres schema isolation
    (a bug that once surfaced as "invalid credentials" in unrelated tests).
    """
    from migrations import runner
    from src.repositories import pg

    db_path = str(tmp_path_factory.mktemp("sweep") / "sweep.db")

    async def _boot():
        database = JobDatabase(db_path)
        await database.init_db()
        await database.close()
        await runner.up(db_path)

    asyncio.run(_boot())
    yield db_path
    asyncio.run(pg.drop_schema(db_path))


@pytest.fixture
def sweep_db(_sweep_schema):
    """The shared sweep DB, emptied so each test starts from nothing.

    The `current_schema()` assertion is the load-bearing line. `search_path`
    silently falls back to `public` when the per-test schema is missing, so a
    bare `DELETE FROM jobs` in a half-built fixture would delete real rows from
    the shared dev database instead of erroring. Checking WHERE we are before
    deleting anything is the difference between a test and an incident.
    """
    from src.repositories import pg

    async def _wipe():
        async with pg.connect(_sweep_schema) as conn:
            cur = await conn.execute("SELECT current_schema()")
            schema = (await cur.fetchone())[0]
            assert schema and schema.startswith("t_"), (
                f"refusing to wipe: connected to schema {schema!r}, not this test's own"
            )
            for table in ("user_feed", "job_enrichment", "jobs", "run_log"):
                await conn.execute(f"DELETE FROM {table}")  # noqa: S608 — fixed literal list
            await conn.commit()

    asyncio.run(_wipe())
    return _sweep_schema


def _enrichment(**overrides):
    """A `JobEnrichment` with everything `unknown` unless a test says otherwise.

    Mirrors the real contract: the prompt DEMANDS the explicit `unknown` enum
    instead of a guess, so `unknown` is what the model honestly returns most of
    the time, and the sweep has to read it as "leave that shelf absent".
    """
    from src.services.job_enrichment_schema import JobEnrichment

    payload = dict(
        title_canonical="Platform Engineer",
        category="other",
        employment_type="unknown",
        workplace_type="unknown",
        seniority="unknown",
        experience_level="unknown",
        visa_sponsorship="unknown",
        salary={"min": None, "max": None, "currency": None, "frequency": "unknown"},
    )
    payload.update(overrides)
    return JobEnrichment(**payload)


def _fake_llm(enrichment, calls):
    """Stand-in for `llm_extract_validated` that records every prompt it is
    handed. Recording the PROMPT (not just the count) is what lets the stub
    test prove a teaser was never SENT, rather than merely that its answer was
    thrown away afterwards."""

    async def _fn(prompt, schema, system):
        calls.append(prompt)
        return enrichment

    return _fn


async def _insert(db_path, job):
    """Store a job WITHOUT running the gate — the exact shape of every row
    written before the gate existed, which is what pass 1 is for.

    The explicit `commit()` is belt-and-braces, not magic: `insert_job` does
    not commit (its callers batch a whole run), and today `pg.py` connections
    are autocommit so `commit()` is a no-op — but the test would silently stop
    seeing its own rows the day that changes, and that failure would look like
    a bug in the sweep.
    """
    database = JobDatabase(db_path)
    await database.connect()
    try:
        await database.insert_job(job)
        await database.commit()
    finally:
        await database.close()


async def _rows(db_path):
    from src.repositories import pg

    async with pg.connect(db_path) as conn:
        conn.row_factory = pg.Row
        cur = await conn.execute("SELECT * FROM jobs ORDER BY id")
        return {r["title"]: dict(r) for r in await cur.fetchall()}


def _provenance(row):
    value = row["shelf_provenance"]
    return json.loads(value) if isinstance(value, str) else value


def _budget(**overrides):
    from src.services.shelf_enrichment import SweepBudget

    defaults = dict(
        max_jobs=100,
        max_spend_usd=10.0,
        min_absent_shelves=1,
        pass1_max_jobs=1000,
        input_usd_per_1m=0.150,
        output_usd_per_1m=0.600,
        output_tokens_per_job=200,
    )
    defaults.update(overrides)
    return SweepBudget(**defaults)


async def _sweep(db_path, *, budget=None, enrichment=None, calls=None, force=False):
    from src.repositories import pg
    from src.services.shelf_enrichment import run_shelf_enrichment_sweep

    calls = [] if calls is None else calls
    async with pg.connect(db_path) as conn:
        stats = await run_shelf_enrichment_sweep(
            conn,
            budget=budget or _budget(),
            force=force,
            llm_extract_validated_fn=_fake_llm(enrichment or _enrichment(), calls),
        )
    return stats, calls


async def test_a_stub_description_is_never_sent_to_the_llm(sweep_db):
    """THE hard block (§6). `enrich_job` is idempotent per `job_id`, so an
    answer read off a teaser is CACHED PERMANENTLY — not a bad guess we can
    fix later, a bad guess we PAID for and then stored.

    Proven by what reaches the LLM, not by what comes back.
    """
    await _insert(sweep_db, _make_job(title="Stub Job", description="Short teaser."))
    await _insert(sweep_db, _make_job(title="Real Job", description=_REAL_AD))

    stats, calls = await _sweep(sweep_db)

    assert stats["pass2_blocked_stub"] == 1
    assert stats["pass2_enriched"] == 1
    assert len(calls) == 1
    assert "Real Job" in calls[0]
    assert "Stub Job" not in calls[0]

    rows = await _rows(sweep_db)
    assert _provenance(rows["Stub Job"])["description"] == {"how": "absent", "why": "stub"}


async def test_an_llm_value_never_overwrites_a_source_filled_shelf(sweep_db):
    """Trust order, §2: source beats derived beats llm. A shelf the board
    itself filled is a FACT; the model's reading of prose is an inference. An
    inference must never quietly replace a fact.
    """
    job = _make_job(
        title="Source Wins",
        description=_REAL_AD,
        employment_type="Full time",   # raw upstream value — the gate normalises it
        salary_min=50000,
        salary_max=70000,
        salary_currency="GBP",
        salary_period="annual",
    )
    fill_shelves(job)                  # exactly as the ingest path would
    await _insert(sweep_db, job)

    stats, calls = await _sweep(
        sweep_db,
        enrichment=_enrichment(
            employment_type="contract",
            salary={"min": 10.0, "max": 12.0, "currency": "GBP", "frequency": "hourly"},
            seniority="senior",
        ),
    )
    assert len(calls) == 1, "the ad is still read — the refusal is per-SHELF, not per-job"

    row = (await _rows(sweep_db))["Source Wins"]
    provenance = _provenance(row)

    # The source's own values survived untouched, with their provenance.
    assert row["employment_type"] == "full_time"
    assert provenance["employment_type"]["how"] == "source"
    assert row["salary_min"] == 50000
    assert row["salary_max"] == 70000
    assert provenance["salary"]["how"] == "source"

    # And the shelf that really WAS empty did get filled — proving the above is
    # a refusal to overwrite, not the sweep failing to write anything at all.
    assert row["seniority"] == "senior"
    assert provenance["seniority"]["how"] == "llm"


async def test_provenance_says_llm_only_for_shelves_the_llm_filled(sweep_db):
    """`how:"llm"` is a claim about how a value got here. Stamping it on a
    shelf the model left `unknown` makes the audit trail a lie — and
    provenance exists precisely so "nobody looked" and "the ad does not say"
    stay different facts (§3/§4).
    """
    await _insert(sweep_db, _make_job(title="Partial Fill", description=_REAL_AD))

    await _sweep(
        sweep_db,
        enrichment=_enrichment(employment_type="full_time", workplace_type="hybrid"),
    )

    row = (await _rows(sweep_db))["Partial Fill"]
    provenance = _provenance(row)

    llm_shelves = {s for s, e in provenance.items() if e.get("how") == "llm"}
    assert llm_shelves == {"employment_type", "workplace_mode"}

    # What the model declined to answer stays ABSENT — never the literal string
    # "unknown" in a catalog column, never a zero, never a guess (rule #29).
    assert row["seniority"] is None
    assert provenance["seniority"]["how"] == "absent"
    assert row["salary_min"] is None and row["salary_max"] is None
    assert provenance["salary"]["how"] == "absent"


async def test_the_budget_cap_stops_the_sweep_and_says_so_loudly(sweep_db, caplog):
    """A cap that trims silently is a cap nobody can act on. The owner is
    pre-revenue: the run must stop at the ceiling AND leave a record of how
    much went unread.
    """
    import logging

    for i in range(4):
        await _insert(sweep_db, _make_job(title=f"Capped {i}", description=_REAL_AD))

    with caplog.at_level(logging.ERROR, logger="job360.services.shelf_enrichment"):
        stats, calls = await _sweep(sweep_db, budget=_budget(max_jobs=2))

    assert len(calls) == 2, "the cap is a hard stop on CALLS, not a filter on results"
    assert stats["pass2_enriched"] == 2
    assert stats["capped"] is True
    assert stats["cap_reason"] == "max_jobs"
    assert any("JOB SOURCE ENRICHMENT CAPPED" in r.getMessage() for r in caplog.records)


async def test_the_spend_cap_bites_before_the_job_cap_when_it_is_tighter(sweep_db):
    """Two independent ceilings; the sweep stops at whichever comes first. A
    job cap alone cannot bound cost — a batch of very long ads costs several
    times what a batch of short ones does at the same job count.
    """
    for i in range(4):
        await _insert(sweep_db, _make_job(title=f"Spendy {i}", description=_REAL_AD))

    stats, calls = await _sweep(
        sweep_db, budget=_budget(max_jobs=100, max_spend_usd=0.0004)
    )
    assert stats["capped"] is True
    assert stats["cap_reason"] == "max_spend_usd"
    assert 0 < len(calls) < 4
    assert stats["spend_usd"] <= 0.0004


async def test_the_spend_is_written_to_run_log(sweep_db):
    """Before this, NOTHING could answer "what did last night cost?" —
    enrichment spend left no trace anywhere. One run_log row per sweep, with
    the fetch counters honestly zero (this run fetched nothing).
    """
    from src.repositories import pg

    await _insert(sweep_db, _make_job(title="Costed", description=_REAL_AD))
    stats, _ = await _sweep(sweep_db, enrichment=_enrichment(seniority="mid"))

    async with pg.connect(sweep_db) as conn:
        conn.row_factory = pg.Row
        cur = await conn.execute(
            "SELECT run_uuid, total_found, new_jobs, enrichment_stats FROM run_log"
        )
        rows = [dict(r) for r in await cur.fetchall()]

    assert len(rows) == 1
    assert rows[0]["run_uuid"].startswith("shelf-enrichment-")
    assert rows[0]["total_found"] == 0 and rows[0]["new_jobs"] == 0

    logged = json.loads(rows[0]["enrichment_stats"])
    assert logged["pass2_enriched"] == 1
    assert logged["input_tokens"] > 0
    assert logged["spend_usd"] > 0
    assert logged["spend_usd"] == stats["spend_usd"]


async def test_pass1_fills_shelves_without_spending_anything(sweep_db):
    """PASS 1 is FREE. Rows stored before the gate existed never saw the visa
    detector, the deadline extractor or the enum normaliser, and nothing else
    was ever going back for them. Filling those shelves costs zero tokens — so
    it happens BEFORE any ad is read, never instead of it.
    """
    ad = _REAL_AD + " Visa sponsorship is available. Closing date: 30 June 2027."
    job = _make_job(title="Free Fill", description=ad, employment_type="Full time")
    # Deliberately NOT gated: shelf_provenance is the empty default, exactly
    # like every pre-gate row in the live catalog.
    assert not job.shelf_provenance
    await _insert(sweep_db, job)

    # min_absent_shelves impossibly high, so pass 2 cannot run at all —
    # anything filled below was filled for free.
    stats, calls = await _sweep(sweep_db, budget=_budget(min_absent_shelves=99))

    assert calls == [], "pass 1 must never call an LLM"
    assert stats["spend_usd"] == 0
    assert stats["pass1_jobs_updated"] == 1
    # Named, not just counted. `deadline` is the shelf that proves the counter
    # spans ALL 13 universal shelves: pass 1 fills it from the ad text and the
    # LLM is forbidden to touch it, so a counter that only watched the six
    # LLM-fillable shelves would report this free pass as half its real value.
    assert stats["pass1_shelves_by_name"] == {"visa_status": 1, "deadline": 1}
    assert stats["pass1_shelves_filled"] == 2

    row = (await _rows(sweep_db))["Free Fill"]
    provenance = _provenance(row)
    assert row["visa_status"] == "sponsors"
    assert provenance["visa_status"]["how"] == "derived"
    assert row["employment_type"] == "full_time"
    assert row["deadline"] == "2027-06-30"
    assert set(provenance.keys()) == set(UNIVERSAL_SHELF)


async def test_pass1_never_removes_a_value(sweep_db):
    """Re-deriving can produce NULL where a value is stored — here a band that
    the plausibility clamp refuses at BOTH ends. Deleting a value the catalog
    already had is not a maintenance run, it is data loss wearing one. Pass 1
    only ever ADDS.

    And keeping the value means keeping its provenance with it: a restored
    number sitting under a fresh `absent` stamp would be the one state
    provenance exists to make impossible.
    """
    from src.repositories import pg

    await _insert(sweep_db, _make_job(title="Keep Mine", description=_REAL_AD))

    # 5,000 is below the £10k floor and 900,000 above the £500k ceiling, so a
    # re-derivation nulls BOTH ends and the gate stamps `absent:implausible`.
    async with pg.connect(sweep_db) as conn:
        await conn.execute(
            "UPDATE jobs SET salary_min = ?, salary_max = ?, salary_currency = ? WHERE title = ?",
            (5000.0, 900000.0, "GBP", "Keep Mine"),
        )
        await conn.commit()

    await _sweep(sweep_db, budget=_budget(min_absent_shelves=99))

    row = (await _rows(sweep_db))["Keep Mine"]
    assert row["salary_min"] == 5000.0
    assert row["salary_max"] == 900000.0
    assert _provenance(row)["salary"]["how"] != "absent"


async def test_an_already_paid_for_enrichment_is_written_back_for_free(sweep_db):
    """`job_enrichment` is the LLM layer's own store; until its facts reach the
    `jobs` columns no filter, serializer or scorer can see them (§3 "Where LLM
    values land"). Prod holds thousands of such rows — re-reading those ads
    would be paying twice for an answer we already own.
    """
    from src.repositories import pg
    from src.services.job_enrichment import save_enrichment

    await _insert(sweep_db, _make_job(title="Already Paid", description=_REAL_AD))
    job_id = (await _rows(sweep_db))["Already Paid"]["id"]

    async with pg.connect(sweep_db) as conn:
        await save_enrichment(
            conn, job_id, _enrichment(seniority="staff", workplace_type="remote")
        )

    stats, calls = await _sweep(sweep_db)

    assert calls == [], "a job that already has an enrichment row is never re-read"
    assert stats["spend_usd"] == 0

    row = (await _rows(sweep_db))["Already Paid"]
    provenance = _provenance(row)
    assert row["seniority"] == "staff"
    assert row["workplace_mode"] == "remote"
    assert provenance["seniority"]["how"] == "llm"


async def test_re_running_the_gate_does_not_relabel_an_llm_fill_as_source(sweep_db):
    """Pass 1 re-runs the gate over stored rows. A shelf the LLM filled comes
    back off the row as a plain value, and a naive re-run would stamp it
    `how:"source"` — silently upgrading "a model decided this" into "the board
    told us this". Provenance is only worth having if it cannot be laundered.
    """
    await _insert(sweep_db, _make_job(title="Laundering", description=_REAL_AD))

    await _sweep(sweep_db, enrichment=_enrichment(employment_type="contract"))
    first = _provenance((await _rows(sweep_db))["Laundering"])
    assert first["employment_type"]["how"] == "llm"

    # Second sweep: pass 1 meets the row again, now carrying the LLM value.
    await _sweep(sweep_db)
    second = _provenance((await _rows(sweep_db))["Laundering"])
    assert second["employment_type"]["how"] == "llm"
    assert second["employment_type"]["at"] == first["employment_type"]["at"]


async def test_with_the_flags_off_the_nightly_path_never_sweeps(sweep_db, monkeypatch):
    """Rule #18 — the gate is `ENGINE2_ENABLED OR ENRICHMENT_ENABLED`, so BOTH
    names must be off. Merging this must not start spending money: with the
    flags off the sweep is not merely a no-op, it is never CALLED, and
    `refresh_catalog` behaves byte-identically to before it existed.
    """
    from unittest.mock import patch

    import src.core.settings as settings_mod
    import src.services.shelf_enrichment as sweep_mod
    import src.workers.tasks as tasks_mod
    from src.repositories import pg
    from src.services.profile.models import CVData, UserPreferences, UserProfile

    await _insert(sweep_db, _make_job(title="Untouched", description=_REAL_AD))

    called = []

    async def _tripwire(*args, **kwargs):
        called.append(kwargs)
        return {}

    async def _fake_run_search(*args, **kwargs):
        return {"sources_queried": 0, "total_found": 0, "new_jobs": 0}

    profile = UserProfile(
        cv_data=CVData(raw_text="Platform engineer, Kubernetes and Go."),
        preferences=UserPreferences(target_job_titles=["Platform Engineer"]),
    )

    monkeypatch.setattr(sweep_mod, "run_shelf_enrichment_sweep", _tripwire)

    async def _run():
        async with pg.connect(sweep_db) as conn:
            return await tasks_mod.refresh_catalog({"db": conn})

    with (
        patch("src.main.run_search", _fake_run_search),
        patch("src.services.profile.storage.list_profile_user_ids", return_value=["u1"]),
        patch("src.services.profile.storage.load_profile", return_value=profile),
    ):
        for engine2, legacy, expect_called in ((False, False, False), (True, False, True)):
            called.clear()
            monkeypatch.setattr(settings_mod, "ENGINE2_ENABLED", engine2, raising=False)
            monkeypatch.setattr(tasks_mod, "ENRICHMENT_ENABLED", legacy, raising=False)
            await _run()
            assert bool(called) is expect_called, (engine2, legacy)

    # And with the flags off nothing was written to the catalog.
    row = (await _rows(sweep_db))["Untouched"]
    assert row["seniority"] is None
