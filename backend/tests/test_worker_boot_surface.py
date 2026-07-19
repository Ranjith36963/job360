"""Worker-boot surface: the things that fail SILENTLY when they break.

Both bugs pinned here share one shape — nothing raises, so a green suite proves
nothing:

* **H1**: `score_and_ingest` built its Job without an `id`, so the enrichment
  lookup did `dict.get(None)` — which always misses. Every enrichment dimension
  (seniority / salary / visa / workplace) silently scored 0 for every job the
  worker ingested. `dict.get` on a missing key returns None, and the scorer
  reads None as "this job has no enrichment", which is indistinguishable from
  the real thing.
* **cron_jobs**: `WorkerSettings.cron_jobs` must be a REAL iterable in the class
  body. A previous implementation used a descriptor that returned itself, and
  newer arq reads `__dict__['cron_jobs']` directly without triggering
  descriptors — so the worker crashed at boot with
  "'_CronJobsDescriptor' object is not iterable" and every cron
  (notification_tick, nightly_ghost_sweep) stopped. That path had NO test.

The related dead-worker P0 (`ctx['db']`/`ctx['enqueue']` never populated) is
already covered by test_worker_tasks.py::test_worker_startup_populates_ctx.
"""

from datetime import datetime, timezone

import pytest


def test_job_from_row_carries_the_row_id() -> None:
    """H1: without `id`, the enrichment lookup can only ever miss."""
    from src.workers.tasks import _job_from_row

    row = {
        "id": 4242,
        "title": "Data Engineer",
        "company": "Acme",
        "apply_url": "https://example.test/1",
        "source": "test",
        "date_found": datetime.now(timezone.utc),
        "location": "London",
        "description": "",
        "match_score": 0,
    }

    job = _job_from_row(row)
    assert job.id == 4242, (
        "Job.id must come from the row — the enrichment lookup is keyed on it, "
        "so an unset id silently zeroes every enrichment dimension"
    )


def test_enrichment_lookup_actually_hits_with_a_real_id() -> None:
    """The end-to-end consequence: lookup by job.id must FIND the enrichment.

    Asserts the value, not just that the call succeeds (rule #21) — the bug's
    whole signature was a call that succeeded and returned None.
    """
    from src.workers.tasks import _job_from_row

    row = {
        "id": 77,
        "title": "Data Engineer",
        "company": "Acme",
        "apply_url": "https://example.test/1",
        "source": "test",
        "date_found": datetime.now(timezone.utc),
        "location": "London",
        "description": "",
        "match_score": 0,
    }
    job = _job_from_row(row)

    enrichment_lookup_dict = {77: {"seniority": "senior"}}
    # the exact lambda shape score_and_ingest builds
    lookup = lambda j: enrichment_lookup_dict.get(j.id)  # noqa: E731

    assert lookup(job) == {"seniority": "senior"}, (
        "enrichment lookup missed for a job that HAS enrichment — this is the "
        "dim-scoring-id bug: every dimension would score 0 with no error"
    )


def test_cron_jobs_is_a_real_iterable_in_the_class_body() -> None:
    """arq reads WorkerSettings.__dict__['cron_jobs'] WITHOUT triggering descriptors.

    A descriptor here returned itself and the worker died at boot with
    "'_CronJobsDescriptor' object is not iterable" — killing notification_tick
    and nightly_ghost_sweep. Read it the way arq does, not via attribute access,
    or a descriptor would pass this test while still breaking the worker.
    """
    from src.workers.settings import WorkerSettings

    raw = WorkerSettings.__dict__.get("cron_jobs")
    assert raw is not None, "cron_jobs missing from the class body"
    assert isinstance(raw, (list, tuple)), (
        f"cron_jobs must be a real list/tuple in the class body, got {type(raw).__name__} — "
        "arq does not trigger descriptors and the worker will not boot"
    )
    # must survive iteration: the exact operation that crashed
    assert list(raw) is not None


def test_every_registered_function_is_callable() -> None:
    """A name in `functions` that isn't a real coroutine fn breaks dispatch silently."""
    import inspect

    from src.workers.settings import WorkerSettings

    assert WorkerSettings.functions, "no tasks registered — the worker would idle forever"
    for fn in WorkerSettings.functions:
        assert callable(fn), f"registered task {fn!r} is not callable"
        assert inspect.iscoroutinefunction(fn), (
            f"registered task {getattr(fn, '__name__', fn)!r} is not async — arq requires a coroutine"
        )


def test_send_notification_is_registered() -> None:
    """SI1 depends on this exact name being enqueueable.

    `_enqueue_notifications` calls enqueue("send_notification", ...) by STRING.
    If the function were renamed or dropped from the registry, the enqueue would
    succeed and the job would never run — silent, again.
    """
    from src.workers.settings import WorkerSettings

    names = {getattr(f, "__name__", "") for f in WorkerSettings.functions}
    assert "send_notification" in names, (
        f"send_notification missing from the ARQ registry: {sorted(names)} — "
        "notifications would be enqueued into a queue nobody consumes"
    )


@pytest.mark.parametrize("attr,expected", [("max_jobs", 1), ("job_timeout", 600)])
def test_worker_concurrency_and_timeout_pinned(attr: str, expected: int) -> None:
    """max_jobs=1 is load-bearing: tasks share ONE ctx['db'] connection.

    Raising it lets two coroutines use one psycopg connection concurrently
    ("another operation in progress"). job_timeout bounds a hung LLM call.
    """
    from src.workers.settings import WorkerSettings

    assert getattr(WorkerSettings, attr) == expected


def test_health_check_interval_is_short_enough_to_notice_death() -> None:
    """arq's default is 3600 — a worker dead at 00:01 still looks alive at 00:59.

    The heartbeat key expires at interval+1, so this bounds how long a dead
    worker can masquerade as healthy. This worker HAS died silently in
    production before (commit 7f906c6).
    """
    from src.workers.settings import WorkerSettings

    interval = getattr(WorkerSettings, "health_check_interval", 3600)
    assert interval <= 60, (
        f"health_check_interval={interval}s is too coarse to detect a dead worker; "
        "arq's 3600 default is what let one die unnoticed"
    )
