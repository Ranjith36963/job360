"""Worker/ARQ task logging (full-lifecycle logging gap H).

Every background task now emits task_start / task_done(+duration) / task_error
(+traceback) on job360.worker, so the previously-dark worker layer is traceable.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from src.workers.tasks import _logged_task


def test_logged_task_logs_start_and_done(caplog):
    @_logged_task
    async def sample(ctx, x):
        return x * 2

    with caplog.at_level(logging.INFO, logger="job360.worker"):
        result = asyncio.run(sample({}, 5))

    assert result == 10  # wrapper preserves the return value
    events = [getattr(r, "event", "") for r in caplog.records]
    assert "task_start" in events
    assert "task_done" in events
    done = [r for r in caplog.records if getattr(r, "event", "") == "task_done"]
    assert done[-1].task == "sample"
    assert isinstance(done[-1].duration_ms, (int, float))


def test_logged_task_logs_error_and_reraises(caplog):
    @_logged_task
    async def boom(ctx):
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.ERROR, logger="job360.worker"):
        with pytest.raises(RuntimeError):
            asyncio.run(boom({}))

    errs = [r for r in caplog.records if getattr(r, "event", "") == "task_error"]
    assert errs, "task_error not logged"
    assert errs[-1].exc_info is not None  # traceback captured


def test_decorated_tasks_preserve_name_for_arq():
    # ARQ registers tasks by __name__ — the decorator must not rename them.
    from src.workers import tasks

    for name in ("score_and_ingest", "send_notification", "notification_tick", "send_bundle", "nightly_ghost_sweep", "enrich_job_task"):
        assert getattr(tasks, name).__name__ == name
