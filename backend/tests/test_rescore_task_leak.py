"""A re-score task must not outlive the test whose schema it queries.

ISSUE #369. Three gate runs on 2026-08-23 failed with `relation "users" does
not exist` / `relation "user_profiles" does not exist`, each blaming a DIFFERENT
test in `test_profile_upload.py`, each passing when that file ran alone. The
gate log carries the whole chain:

    queue.py:67    enqueue skipped: REDIS_URL is not set, so
                   rescore_user_feed_task cannot be queued
    profile.py:94  rescore: background re-score FAILED, feed left on a stale
                   profile_version: OperationalError('relation
                   "user_profiles" does not exist')

No Redis in tests, so `_maybe_trigger_rescore` falls back to
`_run_rescore_in_process`, which does `asyncio.create_task(...)` and never
awaits it. The test ends, conftest drops the per-test schema, and the orphan
then queries a schema that is gone — attributed to whatever test is running by
then. The blamed test is innocent, which is why it looked random.

It is a RACE: a task that finishes inside its own request is harmless. That is
why the same batch passes on one run and fails on the next, and why a plain
"run it twice and see" proves nothing. These tests make the race deterministic
by giving the re-score something slow to do.

Rule #4-safe: no HTTP, no DB, no network — this exercises the teardown helper
against real asyncio tasks.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import cancel_pending_rescores


def _make_pending_task(loop: asyncio.AbstractEventLoop) -> asyncio.Task:
    """A task that will not finish on its own inside a test."""

    async def _never():
        # NOT asyncio.sleep: conftest's `_instant_asyncio_sleep` makes every
        # sleep return immediately, so a "task that never finishes" built on
        # sleep finishes on the first tick and this test would assert nothing.
        # A test helper defeated by another test helper.
        await asyncio.Event().wait()

    return loop.create_task(_never())


class TestTheTeardownActuallyCollectsThem:
    @pytest.mark.asyncio
    async def test_a_pending_task_is_cancelled_and_the_set_emptied(self) -> None:
        loop = asyncio.get_running_loop()
        tasks = {_make_pending_task(loop) for _ in range(3)}
        held = list(tasks)

        assert cancel_pending_rescores(tasks) == 3
        assert tasks == set(), "the tracking set must not keep growing across tests"

        # The cancel is POSTED to the loop, so it lands on the next tick.
        await asyncio.sleep(0)
        await asyncio.gather(*held, return_exceptions=True)
        assert all(t.cancelled() for t in held), (
            "THE LEAK: a task survived teardown and will run against a schema "
            "that has already been dropped"
        )

    @pytest.mark.asyncio
    async def test_a_finished_task_is_left_alone(self) -> None:
        """NEGATIVE CONTROL. A re-score that completed inside its own request is
        the HARMLESS case — cancelling it would turn a success into a logged
        `was CANCELLED — feed left stale`, i.e. manufacture the alarm."""
        loop = asyncio.get_running_loop()

        async def _quick():
            return "done"

        done = loop.create_task(_quick())
        await done
        tasks = {done}

        assert cancel_pending_rescores(tasks) == 0, "a finished task was cancelled"
        assert done.result() == "done"
        assert not done.cancelled()

    def test_an_empty_or_absent_set_is_not_an_error(self) -> None:
        """The fixture runs after EVERY test, including the thousands that never
        touch a profile route. It must be free and silent there."""
        assert cancel_pending_rescores(None) == 0
        assert cancel_pending_rescores(set()) == 0


class TestTheRealRouteLeaksWithoutIt:
    """The end-to-end half: drive the actual fallback and show it leaves a task."""

    @pytest.mark.asyncio
    async def test_the_in_process_fallback_leaves_a_tracked_task(
        self, monkeypatch
    ) -> None:
        import src.api.routes.profile as profile_route

        started = asyncio.Event()

        async def _slow_rescore(user_id):
            started.set()
            await asyncio.Event().wait()  # see _make_pending_task on why not sleep

        # Patch the symbol `_run_rescore_in_process` imports at call time.
        import src.services.rescore as rescore_mod

        monkeypatch.setattr(rescore_mod, "rescore_user_feed", _slow_rescore)
        profile_route._rescore_bg_tasks.clear()

        profile_route._run_rescore_in_process("user-369")
        await asyncio.wait_for(started.wait(), timeout=5)

        pending = [t for t in profile_route._rescore_bg_tasks if not t.done()]
        assert pending, (
            "the fallback did not leave a tracked task — if this ever fails, the "
            "leak is gone and this whole file can go with it"
        )

        held = list(profile_route._rescore_bg_tasks)
        assert cancel_pending_rescores(profile_route._rescore_bg_tasks) == len(pending)
        await asyncio.sleep(0)
        await asyncio.gather(*held, return_exceptions=True)
        assert all(t.cancelled() for t in held)
