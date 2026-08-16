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
