"""A background re-score that dies must say so — issue #271.

``_maybe_trigger_rescore`` fires ``rescore_user_feed`` with
``asyncio.create_task``. The old done-callback was ``_rescore_bg_tasks.discard``:
it dropped the reference and never read ``task.exception()``. asyncio only
surfaces an unretrieved exception when the task object is garbage-collected, and
by then the message has no user attached to it. The ``except`` around the call
covers SCHEDULING, not execution.

So a re-score that started and then failed left the user's feed permanently on a
stale ``profile_version``, with nothing in the logs to say it had happened.
Measured in production 2026-08-11: 9,708 such rows, all pointing at jobs still in
the catalog (0 orphans), one user stuck at version 10 with 15 current.

These tests pin the visibility, not the scheduling — the durable fix is to move
the work onto the ARQ queue, which is a separate change.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.api.routes.profile import _rescore_bg_tasks, _rescore_finished


@pytest.mark.asyncio
async def test_a_failed_rescore_is_logged_at_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom() -> None:
        raise RuntimeError("scorer exploded")

    task = asyncio.ensure_future(boom())
    _rescore_bg_tasks.add(task)
    with pytest.raises(RuntimeError):
        await task

    with caplog.at_level(logging.ERROR, logger="src.api.routes.profile"):
        _rescore_finished(task)

    assert any(
        r.levelno == logging.ERROR and "stale" in r.getMessage()
        for r in caplog.records
    ), (
        "A failed background re-score produced no ERROR log. That is the exact "
        "silence behind issue #271: the feed is left on an old profile_version "
        f"and nobody finds out. Records: {[r.getMessage() for r in caplog.records]}"
    )
    assert task not in _rescore_bg_tasks, "the task reference must still be released"


@pytest.mark.asyncio
async def test_a_cancelled_rescore_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A deploy mid-run cancels the task. `main` auto-deploys on every merge."""

    async def slow() -> None:
        await asyncio.sleep(30)

    task = asyncio.ensure_future(slow())
    _rescore_bg_tasks.add(task)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with caplog.at_level(logging.WARNING, logger="src.api.routes.profile"):
        _rescore_finished(task)

    assert any("CANCELLED" in r.getMessage() for r in caplog.records), (
        "A cancelled re-score logged nothing. Restarts are the common case here — "
        "every merge to main redeploys and kills anything in flight."
    )
    assert task not in _rescore_bg_tasks


@pytest.mark.asyncio
async def test_a_successful_rescore_stays_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The guard must not cry wolf: a clean run logs no error or warning."""

    async def fine() -> None:
        return None

    task = asyncio.ensure_future(fine())
    _rescore_bg_tasks.add(task)
    await task

    with caplog.at_level(logging.WARNING, logger="src.api.routes.profile"):
        _rescore_finished(task)

    noisy = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not noisy, f"A successful re-score should be silent, but logged: {noisy}"
    assert task not in _rescore_bg_tasks
