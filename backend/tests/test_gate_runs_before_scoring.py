"""The shelf gate runs BEFORE the scorer — pinned, not merely documented.

WHY THIS FILE EXISTS. Two LIVING docs state this ordering as a fact:
`docs/product/pillars/UNIVERSAL_SHELF.md` §5 ("The gate runs FIRST in that
function, before scoring, so the scorer reads normalised shelves") and
`docs/product/pillars/CATALOG_STATE.md` §3 step 5. Nothing asserted it.

Every other Universal Shelf test calls `fill_shelves()` itself, so all of
them keep passing if someone moves the `fill_shelves(job)` loop in
`src/main.py::_score_dedup_and_filter` below the scoring loop — or deletes
it and re-derives shelves later. The failure would be silent and expensive:
the scorer would read RAW upstream tokens ('Full time', an un-annualised
foreign salary) on the exact code path the multi-dim scorer exists to
consume, and the docs would be quietly false.

A doc parser cannot check ordering. A test can, by looking at the job at the
one moment that matters: the instant the scorer is handed it.
"""
from datetime import datetime, timezone

from src.main import _score_dedup_and_filter
from src.models import UNIVERSAL_SHELF, Job
from src.services.skill_matcher import ScoreBreakdown


class _RecordingScorer:
    """Stands in for `JobScorer` and photographs each job as it arrives.

    Deliberately NOT a subclass or a mock of the real scorer: the claim under
    test is about the ORDER of two loops in the orchestrator, and a stub that
    only implements the two methods the orchestrator calls keeps this test
    from failing for unrelated scoring reasons.
    """

    def __init__(self):
        self.seen = []

    def score(self, job: Job) -> ScoreBreakdown:
        self.seen.append({
            "employment_type": job.employment_type,
            "provenance_keys": set(job.shelf_provenance or {}),
        })
        return ScoreBreakdown(
            title_score=40,
            skill_score=40,
            location_score=10,
            recency_score=10,
            seniority_score=0,
            salary_score=0,
            visa_score=0,
            workplace_score=0,
            match_score=100,
        )

    def check_visa_flag(self, job: Job) -> bool:
        return False


def _raw_job() -> Job:
    """A job as a SOURCE hands it over: an un-normalised upstream token.

    'Full time' is the raw shape; `full_time` is what the closed enum stores.
    The gate is the only thing that converts one into the other, so the value
    the scorer sees answers "did the gate already run?" with no ambiguity.
    """
    return Job(
        title="AI Engineer",
        company="DeepMind",
        apply_url="https://example.com/job",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London",
        description="An AI role in London.",
        employment_type="Full time",
    )


def test_the_scorer_never_sees_an_unnormalised_shelf():
    """The gate ran before `score()` was called, not after it."""
    scorer = _RecordingScorer()

    _score_dedup_and_filter([_raw_job()], scorer)

    assert scorer.seen, "the scorer was never called — this test proves nothing"
    snapshot = scorer.seen[0]
    assert snapshot["employment_type"] == "full_time", (
        "the scorer was handed the RAW upstream token 'Full time'. "
        "fill_shelves() must run before the scoring loop in "
        "_score_dedup_and_filter (UNIVERSAL_SHELF.md section 5)."
    )


def test_every_shelf_is_already_accounted_for_when_the_scorer_runs():
    """Provenance is complete at score time — not stamped on afterwards.

    Guards the weaker way this could regress: the gate still runs, but late
    enough that some shelves are unaccounted-for while scoring reads them.
    """
    scorer = _RecordingScorer()

    _score_dedup_and_filter([_raw_job()], scorer)

    assert scorer.seen[0]["provenance_keys"] == set(UNIVERSAL_SHELF)
