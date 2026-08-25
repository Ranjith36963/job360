"""A source that never fetches freezes its jobs as ACTIVE forever — say so.

PRODUCTION EVIDENCE (prod Postgres, read-only, 2026-08-13):

    SELECT source, consecutive_misses, count(*) , min(left(last_seen_at,10))
    FROM jobs
    WHERE (staleness_state='active' OR staleness_state IS NULL)
      AND left(last_seen_at,10) < to_char(now() - interval '2 days','YYYY-MM-DD')
    GROUP BY 1,2
      ('workday',  0, 76, '2026-08-08')
      ('linkedin', 0, 33, '2026-08-06')
      ('linkedin', 1, 21, '2026-08-05')
      ('adzuna',   1, 10, '2026-08-10')

Every one of those 140 zombie rows belongs to one of the exactly three sources
that raise on every run (``run_log.per_source_errors`` = {adzuna, linkedin,
workday}; workday's duration is pinned at 240.0s, a timeout). Their
``consecutive_misses`` never got past 1, so:

    SELECT count(*) FROM jobs WHERE staleness_state='active'
      AND consecutive_misses >= 2 AND now() - last_seen_at::timestamptz > '12h'
      -> 0

which is why ``nightly_ghost_sweep`` correctly reports
``{'evaluated': 8783, 'transitioned': 0}``. The nightly sweep is NOT broken.
The absence sweep upstream never ran for those sources: ``_ghost_detection_pass``
skips a source whose fetch raised — deliberately, so a rate-limited scrape is
not read as "the jobs vanished" — but it does it with a bare ``continue``, no
log, no counter. A source can therefore fail forever and its listings are
served as live forever, and nothing anywhere says so.

This test does not change the skip decision (that is a product call: aging a
job out removes it from what users see). It pins the VISIBILITY of the skip
and of the number of live rows it froze.
"""
from __future__ import annotations

import asyncio
import logging

import pytest


class _FakeSource:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture
def db():
    from src.repositories.database import JobDatabase

    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def _seed(db, source: str, n: int) -> None:
    from src.models import Job

    async def _run() -> None:
        for i in range(n):
            await db.insert_job(
                Job(
                    title=f"T{i}",
                    company=f"C{i}",
                    apply_url=f"https://x/{source}/{i}",
                    source=source,
                    date_found="2026-08-01T00:00:00+00:00",
                )
            )

    asyncio.run(_run())


@pytest.mark.parametrize("bad_result", [RuntimeError("boom"), None], ids=["raised", "none"])
def test_failed_source_logs_how_many_live_jobs_it_froze(db, caplog, bad_result):
    from src.main import _ghost_detection_pass

    _seed(db, "workday", 3)

    async def _run():
        with caplog.at_level(logging.WARNING):
            return await _ghost_detection_pass(db, [_FakeSource("workday")], [bad_result], {})

    missed = asyncio.run(_run())

    # Unchanged decision: a failed scrape is still NOT absence evidence.
    assert missed == {}

    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "workday" in text, "the skipped source was not named in any log line"
    assert "3" in text, (
        "the log did not say how many live jobs the skip froze — "
        "'a source failed' is not actionable, 'N live jobs stopped ageing' is"
    )


def test_healthy_source_does_not_log_a_freeze_warning(db, caplog):
    """No false alarm: a source that fetched fine must not produce the warning."""
    from src.main import _ghost_detection_pass
    from src.models import Job

    job = Job(
        title="A",
        company="X",
        apply_url="https://a",
        source="reed",
        date_found="2026-08-01T00:00:00+00:00",
    )
    asyncio.run(db.insert_job(job))

    async def _run():
        with caplog.at_level(logging.WARNING):
            return await _ghost_detection_pass(db, [_FakeSource("reed")], [[job]], {})

    missed = asyncio.run(_run())
    assert missed.get("reed") == 0
    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "froze" not in text and "stopped ageing" not in text


# ---------------------------------------------------------------------------
# The COUNT must mean what the warning says it means
# ---------------------------------------------------------------------------


def test_the_freeze_count_counts_only_jobs_the_app_actually_serves(db):
    """`possibly_stale` and `likely_stale` rows are NOT live listings.

    The warning above says "N live job(s) ... are still served as active", and
    the number behind it came from `count_unexpired_jobs_for_source`, which
    counted everything *not* `confirmed_expired`. But `get_recent_jobs` serves
    only `staleness_state IS NULL OR = 'active'` — so the two mid-states were
    counted as live while no user could see them, and the warning overstated
    the damage.

    That is this repo's recorded failure mode in miniature: an instrument that
    does not count the way its consumer counts. It is worth pinning here rather
    than trusting the SQL to stay in step, because the two predicates live in
    different files and nothing else forces them to agree.
    """
    from src.repositories.database import JobDatabase  # noqa: F401 — fixture type

    _seed(db, "reed", 4)

    async def _mark(state: str, apply_url: str) -> None:
        await db._db.execute(
            "UPDATE jobs SET staleness_state = ? WHERE apply_url = ?",
            (state, apply_url),
        )
        await db._db.commit()

    # 4 seeded rows: leave one 'active', set one NULL (never classified — still
    # served, by design), and move the other two into the mid-states.
    asyncio.run(_mark("active", "https://x/reed/0"))
    asyncio.run(_mark(None, "https://x/reed/1"))
    asyncio.run(_mark("possibly_stale", "https://x/reed/2"))
    asyncio.run(_mark("likely_stale", "https://x/reed/3"))

    counted = asyncio.run(db.count_unexpired_jobs_for_source("reed"))
    assert counted == 2, (
        f"counted {counted}, expected 2 (the 'active' row and the NULL row). "
        f"A count that includes possibly_stale/likely_stale reports listings the "
        f"app does not serve, so the freeze warning overstates the live damage."
    )


def test_the_freeze_count_still_excludes_expired_rows(db):
    """The other direction: a confirmed-expired row is not live either.

    Tightening a predicate until it stops over-counting can quietly turn into
    under-counting, so both edges are pinned.
    """
    _seed(db, "adzuna", 2)

    async def _expire(apply_url: str) -> None:
        await db._db.execute(
            "UPDATE jobs SET staleness_state = 'confirmed_expired' WHERE apply_url = ?",
            (apply_url,),
        )
        await db._db.commit()

    asyncio.run(_expire("https://x/adzuna/0"))

    counted = asyncio.run(db.count_unexpired_jobs_for_source("adzuna"))
    assert counted == 1, f"counted {counted}, expected 1 — the expired row must not count"
