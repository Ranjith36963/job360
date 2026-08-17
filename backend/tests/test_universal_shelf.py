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


def test_visa_unknown_is_a_real_absent_value_not_a_missing_one():
    """Rule #31: `unknown` IS the third visa state, stored as a literal
    value — but its provenance `how` is still "absent" (the ad never said),
    distinct from a shelf nobody looked at.
    """
    job = _make_job(description="We are hiring a great engineer to join our team.")
    fill_shelves(job)

    assert job.visa_status == "unknown"
    assert job.shelf_provenance["visa_status"] == {"how": "absent", "why": "not_stated"}


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
    real_job = _make_job(
        title="Backend Engineer",
        description=(
            "We are looking for a Backend Engineer to join our platform team. "
            "You will design and build services in Python, work closely with "
            "product and design, and own your code from design through to "
            "production. Experience with Postgres and distributed systems is a plus."
        ),
    )
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
