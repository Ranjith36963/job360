"""Fixes for the faults a Fable audit + the data-invariants detector found.

Each test below pins one real production fault, measured on 2026-08-03 against
the live database. None of them threw an exception, none reached Sentry, and
none failed an existing test — which is exactly why they survived so long.
"""

from __future__ import annotations

import pytest

from src.utils.dates import normalize_posted_at

# ── 1. Dates: presence is not validity ──────────────────────────────────────
# The dashboard rendered "Posted NaNw ago" because arbeitnow's epoch integer was
# stored verbatim in a TEXT column and `new Date('1785750903')` is NaN. We then
# stamped date_confidence='high' on it, certifying a value we could not read.

def test_a_unix_epoch_becomes_a_real_date():
    """THE BUG THE OWNER SAW ON HIS PHONE."""
    iso, conf = normalize_posted_at("1785750903")
    assert iso is not None and iso.startswith("2026-08-03"), iso
    assert conf == "high"


def test_epoch_milliseconds_are_not_mistaken_for_seconds():
    """A 13-digit value is milliseconds. Treated as seconds it lands in the year
    58,000 — a date that parses and is absurd, which is worse than one that
    fails, because nothing downstream questions it."""
    iso, conf = normalize_posted_at("1785750903000")
    assert iso is not None and iso.startswith("2026-08-03")
    assert conf == "high"


def test_uk_day_first_dates_parse_as_day_first():
    """reed sends DD/MM/YYYY. Guessing month-first would silently swap day and
    month for the first 12 days of every month — a wrong date that parses."""
    iso, conf = normalize_posted_at("27/07/2026")
    assert iso is not None and iso.startswith("2026-07-27"), iso
    assert conf == "high"


def test_iso_passes_through_untouched():
    iso, conf = normalize_posted_at("2026-07-27T10:00:00+00:00")
    assert iso == "2026-07-27T10:00:00+00:00"
    assert conf == "high"


@pytest.mark.parametrize("bad", ["", None, "next tuesday", "42", "N/A"])
def test_an_unreadable_date_is_never_called_high_confidence(bad):
    """THE SECOND HALF OF THE BUG, and the more dangerous one. If we cannot
    parse it we must store nothing and say low — a null date renders honestly
    as 'Seen 2d ago' from crawler time, while an unparseable one renders NaN."""
    iso, conf = normalize_posted_at(bad)
    assert iso is None
    assert conf == "low"


def test_no_source_derives_confidence_from_mere_presence():
    """The systemic version. Every source used `confidence = "high" if raw else
    "low"` — confidence from PRESENCE, not from parseability. This asserts the
    pattern is gone repo-wide, because fixing four sources by hand would leave
    the other twenty-six to re-introduce it."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "sources"
    offenders = []
    pat = re.compile(r'confidence = "high" if (\w+) else "low"')
    for p in root.rglob("*.py"):
        for m in pat.finditer(p.read_text(encoding="utf-8")):
            if m.group(1) != "posted_at":  # deriving from the RESULT is correct
                offenders.append(f"{p.name}: {m.group(0)}")
    assert not offenders, (
        "these sources still call a date high-confidence just because the field "
        f"was present: {offenders}"
    )


# ── 2. devitjobs: the Apply button that went nowhere ────────────────────────

def test_devitjobs_turns_a_slug_into_a_real_url():
    """2,805 jobs — 43% of the whole catalog — stored 'FBI-TMT-Metadata-Lead'
    where a URL belongs, so Apply went nowhere. A broken link fails only when a
    user clicks it, which we never see."""
    import inspect

    from src.sources.apis_free.devitjobs import DevITJobsSource  # noqa: F401

    src = inspect.getsource(DevITJobsSource)
    assert "devitjobs.uk/jobs/" in src, "the slug is no longer expanded to a URL"


# ── 3. HackerNews: a paragraph is not a company ─────────────────────────────

def test_a_prose_comment_is_not_turned_into_a_job():
    """HN's format is 'Company | Location | Role'. A prose comment has no '|',
    so parts[0] was the ENTIRE paragraph and became both company and title —
    14 titles over 300 chars, longest 1,551. One of them blew Postgres's btree
    index limit and aborted a real user's search twice."""
    from src.sources.other.hackernews import _parse_hn_comment

    prose = (
        "Let's support the country: who's hiring in the coronavirus economy. "
        "Zoom out and consider that many teams are still growing headcount "
        "across engineering, design and operations, and this thread exists to "
        "surface those opportunities for everyone reading along today. "
    ) * 3
    assert _parse_hn_comment(prose) is None, "a prose paragraph was stored as a job"


def test_a_properly_formatted_hn_post_still_parses():
    """The guard must not reject real postings — that would silently delete a
    whole source, which is a worse bug than the one being fixed."""
    from src.sources.other.hackernews import _parse_hn_comment

    ok = "Acme Corp | London, UK | Senior Engineer | https://acme.example/jobs"
    parsed = _parse_hn_comment(ok)
    assert parsed is not None
    assert parsed["company"] == "Acme Corp"


# ── 4. One poison row must not kill the whole search ────────────────────────

def test_the_insert_loop_survives_one_unstorable_job():
    """2026-07-27: both of a real user's searches aborted because ONE
    HackerNews row exceeded Postgres's index limit. His feed then took zero new
    rows for seven days while the catalog grew by ~2,800 jobs."""
    import pathlib
    import re

    main = (pathlib.Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(
        encoding="utf-8"
    )
    block = re.search(
        r"for job in unique_jobs:.*?await db\.commit\(\)", main, re.S
    )
    assert block, "the insert loop moved — re-point this test"
    body = block.group(0)
    assert "try:" in body and "except" in body, (
        "the per-job insert guard is gone: one unstorable row will abort the "
        "entire search again and freeze every affected user's feed"
    )


# ── 5. A user's own application must always open ────────────────────────────

def test_the_detail_lookup_accepts_a_user_so_own_jobs_stay_open():
    """The staleness filter correctly hides ghosts from BROWSING, but it also
    404'd a user's own application (job 56, stage 'applied'). Clicking your own
    application and getting 'not found' reads as data loss."""
    import inspect

    from src.repositories.database import JobDatabase

    sig = inspect.signature(JobDatabase.get_job_by_id_with_enrichment)
    assert "user_id" in sig.parameters, (
        "the detail lookup can no longer tell whose job it is, so a stale job "
        "the user applied to will 404 again"
    )
    src = inspect.getsource(JobDatabase.get_job_by_id_with_enrichment)
    assert "applications" in src and "user_actions" in src


# ── 6. A "full re-score" must be able to reach the whole catalog ────────────

def test_rescore_limit_exceeds_any_plausible_catalog():
    """It was 5,000 while the catalog held 6,457, so the oldest 1,457 jobs were
    never re-scored on a profile change — 43 feed rows still carried scores from
    a profile replaced a month earlier, on a different scale from everything
    around them."""
    import inspect

    from src.repositories.database import JobDatabase

    sig = inspect.signature(JobDatabase.get_catalog_jobs_for_rescore)
    limit = sig.parameters["limit"].default
    assert limit >= 50000, (
        f"rescore limit is {limit}; purge keeps ~30 days of jobs and the catalog "
        "already reached 6,457. A limit near the catalog size makes a 'full' "
        "re-score silently partial."
    )
