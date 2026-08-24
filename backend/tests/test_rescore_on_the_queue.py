"""Re-scoring must survive a deploy — issue #271.

`_maybe_trigger_rescore` used to fire `rescore_user_feed` with
`asyncio.create_task` INSIDE THE WEB PROCESS. There was no queue entry, no
retry, and no completion record: `grep -c rescore src/workers/tasks.py` was 0.
`main` auto-deploys on every merge, so a deploy alone killed anything in flight.

Measured in production 2026-08-11: 9,708 `user_feed` rows carry a
`profile_version` older than their user's current one, and ALL 9,708 point at
jobs still in the catalog (0 orphans) — every one was reachable and simply never
re-scored. One user sat at version 10 with 15 current.

These tests pin the durable fix:
  * PART A — the work goes on the ARQ queue, with a retry, and the profile save
    never 500s when Redis is down (it falls back in-process and SAYS SO).
  * PART B — a throttled, resumable backfill job that does not monopolise the
    event loop and does not fire on boot.

The event-loop test is the load-bearing one. This repo's own recorded lesson is
"a correctness fix with an operational cost is still a regression" — a previous
backfill froze the whole loop and 5-row tests could not see it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any, Optional

import pytest

from migrations import runner
from src.repositories import pg

# ---------------------------------------------------------------------------
# PART A — the ARQ task
# ---------------------------------------------------------------------------


def test_the_rescore_task_is_registered_with_the_worker() -> None:
    """A task ARQ does not know about can never run. This was the whole bug."""
    from src.workers.settings import WorkerSettings
    from src.workers.tasks import rescore_user_feed_task

    names = [getattr(f, "__name__", "") for f in WorkerSettings.functions]
    assert "rescore_user_feed_task" in names, (
        "rescore_user_feed_task is not in WorkerSettings.functions, so enqueueing "
        f"it would sit in Redis forever. Registered: {names}"
    )
    assert rescore_user_feed_task is not None


@pytest.mark.asyncio
async def test_the_task_rescores_the_user_it_was_given() -> None:
    from src.services import rescore as rescore_mod
    from src.workers.tasks import rescore_user_feed_task

    seen: list[str] = []

    async def _fake(user_id: str, db_path: Optional[str] = None) -> dict[str, Any]:
        seen.append(user_id)
        return {"rescored": 7, "version": 15}

    original = rescore_mod.rescore_user_feed
    rescore_mod.rescore_user_feed = _fake  # type: ignore[assignment]
    try:
        result = await rescore_user_feed_task({}, "user-abc")
    finally:
        rescore_mod.rescore_user_feed = original  # type: ignore[assignment]

    assert seen == ["user-abc"]
    assert result["rescored"] == 7


@pytest.mark.asyncio
async def test_a_failing_task_asks_arq_to_retry() -> None:
    """No retry = one transient DB blip permanently strands a user's feed."""
    from arq.worker import Retry

    from src.services import rescore as rescore_mod
    from src.workers.tasks import rescore_user_feed_task

    async def _boom(user_id: str, db_path: Optional[str] = None) -> dict[str, Any]:
        raise RuntimeError("scorer exploded")

    original = rescore_mod.rescore_user_feed
    rescore_mod.rescore_user_feed = _boom  # type: ignore[assignment]
    try:
        with pytest.raises(Retry) as first:
            await rescore_user_feed_task({"job_try": 1}, "user-abc")
        with pytest.raises(Retry) as later:
            await rescore_user_feed_task({"job_try": 3}, "user-abc")
    finally:
        rescore_mod.rescore_user_feed = original  # type: ignore[assignment]

    assert first.value.defer_score is not None
    assert later.value.defer_score > first.value.defer_score, (
        "retries must back off, otherwise a poisoned job hammers the single "
        "worker slot (max_jobs=1)"
    )


# ---------------------------------------------------------------------------
# PART A — the profile save now ENQUEUES
# ---------------------------------------------------------------------------


@pytest.fixture
def _profile_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.profile import storage

    monkeypatch.setattr(
        storage, "profile_content_changed_since_previous", lambda user_id: True
    )


@pytest.mark.asyncio
async def test_a_profile_save_enqueues_instead_of_running_in_the_web_process(
    monkeypatch: pytest.MonkeyPatch, _profile_changed: None
) -> None:
    from src.api.routes import profile as profile_route
    from src.workers import queue as queue_mod

    calls: list[tuple[str, tuple[Any, ...]]] = []

    async def _fake_enqueue(function_name: str, *args: Any, **kwargs: Any) -> bool:
        calls.append((function_name, args))
        return True

    monkeypatch.setattr(queue_mod, "enqueue_job", _fake_enqueue)

    in_process: list[str] = []
    monkeypatch.setattr(
        profile_route, "_run_rescore_in_process", lambda uid: in_process.append(uid)
    )

    await profile_route._maybe_trigger_rescore("user-abc")

    assert calls == [("rescore_user_feed_task", ("user-abc",))], (
        "the profile save did not put the re-score on the queue — that is issue "
        f"#271 exactly. Enqueue calls: {calls}"
    )
    assert in_process == [], (
        "the in-process fallback ran even though the queue accepted the job; the "
        "work would then be done twice and one copy dies on the next deploy"
    )


@pytest.mark.asyncio
async def test_a_dead_redis_falls_back_in_process_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _profile_changed: None,
) -> None:
    """Redis down must never lose the work AND never be silent about it."""
    from src.api.routes import profile as profile_route
    from src.workers import queue as queue_mod

    async def _dead(function_name: str, *args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(queue_mod, "enqueue_job", _dead)

    in_process: list[str] = []
    monkeypatch.setattr(
        profile_route, "_run_rescore_in_process", lambda uid: in_process.append(uid)
    )

    with caplog.at_level(logging.WARNING, logger="job360.api.profile"):
        await profile_route._maybe_trigger_rescore("user-abc")

    assert in_process == ["user-abc"], (
        "with the queue unreachable the re-score was dropped entirely — worse "
        "than the bug we are fixing"
    )
    assert any(
        "QUEUE UNAVAILABLE" in r.getMessage() for r in caplog.records
    ), f"the fallback was silent. Records: {[r.getMessage() for r in caplog.records]}"


@pytest.mark.asyncio
async def test_an_exploding_enqueue_never_500s_the_profile_save(
    monkeypatch: pytest.MonkeyPatch, _profile_changed: None
) -> None:
    from src.api.routes import profile as profile_route
    from src.workers import queue as queue_mod

    async def _explode(function_name: str, *args: Any, **kwargs: Any) -> bool:
        raise ConnectionError("redis is gone")

    monkeypatch.setattr(queue_mod, "enqueue_job", _explode)

    in_process: list[str] = []
    monkeypatch.setattr(
        profile_route, "_run_rescore_in_process", lambda uid: in_process.append(uid)
    )

    # Must not raise — the profile save is the user-visible action.
    await profile_route._maybe_trigger_rescore("user-abc")
    assert in_process == ["user-abc"]


@pytest.mark.asyncio
async def test_an_unchanged_profile_still_queues_nothing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old cheap guard must survive the move to the queue."""
    from src.api.routes import profile as profile_route
    from src.services.profile import storage
    from src.workers import queue as queue_mod

    monkeypatch.setattr(
        storage, "profile_content_changed_since_previous", lambda user_id: False
    )
    calls: list[str] = []

    async def _fake_enqueue(function_name: str, *args: Any, **kwargs: Any) -> bool:
        calls.append(function_name)
        return True

    monkeypatch.setattr(queue_mod, "enqueue_job", _fake_enqueue)
    monkeypatch.setattr(profile_route, "_run_rescore_in_process", lambda uid: calls.append("inproc"))

    await profile_route._maybe_trigger_rescore("user-abc")
    assert calls == []


# ---------------------------------------------------------------------------
# PART B — the backfill
# ---------------------------------------------------------------------------


@pytest.fixture
async def backfill_db():
    """A migrated DB with users, feed rows and profile-version history."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Same preamble as tests/test_worker_tasks.py: the migration chain assumes
    # the legacy single-user tables already exist.
    async with pg.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT DEFAULT '',
                salary_min REAL,
                salary_max REAL,
                description TEXT DEFAULT '',
                apply_url TEXT NOT NULL,
                source TEXT NOT NULL,
                date_found TEXT NOT NULL,
                match_score INTEGER DEFAULT 0,
                visa_flag INTEGER DEFAULT 0,
                experience_level TEXT DEFAULT '',
                normalized_company TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                first_seen_at TEXT,
                UNIQUE(normalized_company, normalized_title)
            );
            CREATE TABLE user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            """
        )
        await db.commit()
    await runner.up(path)
    async with pg.connect(path) as db:
        db.row_factory = pg.Row
        yield db


async def _seed(db: Any, user_id: str, *, versions: int, stamped: Optional[int]) -> None:
    """Give ``user_id`` ``versions`` profile snapshots and one feed row.

    ``stamped`` is the profile_version written onto the feed row — None or an
    older number means the row is stale.
    """
    await db.execute(
        "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
        (user_id, f"{user_id}@x", "!"),
    )
    for _ in range(versions):
        await db.execute(
            "INSERT INTO user_profile_versions(user_id, cv_data, preferences) "
            "VALUES(?, ?, ?)",
            (user_id, "{}", "{}"),
        )
    await db.execute(
        "INSERT INTO user_feed(user_id, job_id, score, bucket, profile_version) "
        "VALUES(?, ?, ?, ?, ?)",
        (user_id, abs(hash(user_id)) % 100000, 50, "24h", stamped),
    )
    await db.commit()


def _current_version(rows: list[Any], user_id: str) -> int:
    for r in rows:
        if r["user_id"] == user_id:
            return int(r["current_version"])
    raise AssertionError(f"{user_id} not selected")


@pytest.mark.asyncio
async def test_the_backfill_only_picks_users_whose_feed_is_stale(backfill_db) -> None:
    from src.workers.tasks import _stale_feed_users

    await _seed(backfill_db, "stale-user", versions=3, stamped=1)
    await _seed(backfill_db, "never-stamped", versions=2, stamped=None)
    await _seed(backfill_db, "current-user", versions=1, stamped=None)
    # current-user's only version id is unknown here; stamp the feed row with it.
    cur = await backfill_db.execute(
        "SELECT MAX(id) AS v FROM user_profile_versions WHERE user_id = ?",
        ("current-user",),
    )
    row = await cur.fetchone()
    await backfill_db.execute(
        "UPDATE user_feed SET profile_version = ? WHERE user_id = ?",
        (row["v"], "current-user"),
    )
    await backfill_db.commit()

    rows = await _stale_feed_users(backfill_db, after="", limit=50)
    picked = {r["user_id"] for r in rows}
    assert picked == {"stale-user", "never-stamped"}, (
        "the backfill must select exactly the users whose feed rows are behind "
        f"their current profile version. Got: {picked}"
    )


@pytest.mark.asyncio
async def test_the_backfill_enqueues_one_durable_job_per_stale_user(
    backfill_db,
) -> None:
    from src.workers.tasks import rescore_backfill

    for i in range(5):
        await _seed(backfill_db, f"u{i}", versions=2, stamped=None)

    enqueued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _enqueue(fn: str, *args: Any, **kwargs: Any) -> None:
        enqueued.append((fn, args, kwargs))

    result = await rescore_backfill(
        {"db": backfill_db, "enqueue": _enqueue}, batch_size=2, throttle_seconds=0
    )

    assert result["enqueued"] == 5, result
    assert {a[0] for _, a, _ in enqueued} == {f"u{i}" for i in range(5)}
    assert all(fn == "rescore_user_feed_task" for fn, _, _ in enqueued)
    assert result["batches"] >= 3, (
        "5 users at batch_size=2 must be at least 3 batches — one giant "
        f"transaction is exactly what the recorded lesson forbids: {result}"
    )


@pytest.mark.asyncio
async def test_a_rerun_does_not_double_work(backfill_db) -> None:
    """Resumable, not restartable: the same user gets the same job id.

    ARQ refuses a second job with an existing ``_job_id``, so a backfill that
    dies half-way and is re-run does not re-enqueue what is already owed.
    """
    from src.workers.tasks import rescore_backfill

    await _seed(backfill_db, "u1", versions=4, stamped=1)

    seen: list[dict[str, Any]] = []

    def _enqueue(fn: str, *args: Any, **kwargs: Any) -> None:
        seen.append(kwargs)

    ctx = {"db": backfill_db, "enqueue": _enqueue}
    await rescore_backfill(ctx, batch_size=10, throttle_seconds=0)
    await rescore_backfill(ctx, batch_size=10, throttle_seconds=0)

    assert len(seen) == 2
    assert seen[0]["_job_id"] == seen[1]["_job_id"], (
        "the job id changed between runs, so ARQ would queue the same expensive "
        f"re-score twice: {seen}"
    )
    assert "u1" in seen[0]["_job_id"] and "4" in seen[0]["_job_id"], (
        "the job id must be keyed on (user, current profile version) so a NEW "
        f"profile change is still allowed through: {seen[0]['_job_id']}"
    )


@pytest.mark.asyncio
async def test_the_backfill_is_not_wired_to_a_cron() -> None:
    """It must be deliberate. Nothing that expensive fires on boot."""
    from src.workers.settings import WorkerSettings

    names = [getattr(f, "__name__", "") for f in WorkerSettings.functions]
    assert "rescore_backfill" in names, "it must be enqueueable on purpose"

    cron_names = {
        getattr(getattr(c, "coroutine", None), "__name__", "") for c in WorkerSettings.cron_jobs
    }
    assert "rescore_backfill" not in cron_names, (
        f"the backfill is on a cron — it must be run deliberately. {cron_names}"
    )


# --- the load-bearing one: does it actually yield? -------------------------


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _NeverYieldingDB:
    """A DB whose ``execute`` awaits nothing real.

    That is the point: awaiting a coroutine that never awaits a future does NOT
    hand control back to the event loop. So the ONLY place this test can yield
    is the backfill's own throttle. Remove that ``await`` and the competing
    coroutine below never runs at all.
    """

    row_factory: Any = None

    def __init__(self, users: list[str]) -> None:
        self.users = sorted(users)
        self.queries = 0

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self.queries += 1
        after, limit = params[0], params[1]
        rows = [u for u in self.users if u > after][: int(limit)]
        return _FakeCursor(
            [{"user_id": u, "current_version": 2, "stale_rows": 3} for u in rows]
        )


@pytest.mark.asyncio
async def test_the_backfill_lets_other_work_run_between_batches() -> None:
    """A backfill that monopolises the loop is a regression, not a fix.

    Recorded lesson (this repo): "a correctness fix with an operational cost is
    still a regression" — a previous backfill froze the whole event loop and
    five-row tests could not see it. So: run something else at the same time and
    prove it got CPU while the backfill was still going.
    """
    from src.workers.tasks import rescore_backfill

    db = _NeverYieldingDB([f"user-{i:03d}" for i in range(40)])
    ticks = {"n": 0}
    backfill_done = {"v": False}

    async def competitor() -> None:
        while not backfill_done["v"]:
            ticks["n"] += 1
            await asyncio.sleep(0)

    task = asyncio.ensure_future(competitor())
    try:
        result = await rescore_backfill(
            {"db": db, "enqueue": lambda *a, **k: None},
            batch_size=10,
            throttle_seconds=0.01,
        )
    finally:
        backfill_done["v"] = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert result["enqueued"] == 40
    assert result["batches"] == 4
    assert ticks["n"] >= 4, (
        "the competing coroutine got NO time while the backfill ran — the "
        "backfill is holding the event loop across all 4 batches. Ticks: "
        f"{ticks['n']}"
    )


def test_the_cli_entrance_queues_the_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Run it deliberately" needs a door a human can actually open."""
    from click.testing import CliRunner

    from src.cli import cli
    from src.workers import queue as queue_mod

    seen: list[tuple[str, dict[str, Any]]] = []

    async def _fake(function_name: str, *args: Any, **kwargs: Any) -> bool:
        seen.append((function_name, kwargs))
        return True

    monkeypatch.setattr(queue_mod, "enqueue_job", _fake)
    result = CliRunner().invoke(
        cli, ["rescore-backfill", "--batch-size", "10", "--max-users", "50"]
    )

    assert result.exit_code == 0, result.output
    assert seen == [
        ("rescore_backfill", {"batch_size": 10, "max_users": 50, "throttle_seconds": 2.0})
    ], seen


def test_the_cli_fails_loudly_when_the_queue_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 0 on a job that was never queued is the false-green this repo keeps hitting."""
    from click.testing import CliRunner

    from src.cli import cli
    from src.workers import queue as queue_mod

    async def _dead(function_name: str, *args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(queue_mod, "enqueue_job", _dead)
    result = CliRunner().invoke(cli, ["rescore-backfill"])

    assert result.exit_code != 0, (
        "the CLI reported success for a backfill that was never queued: "
        f"{result.output}"
    )
    assert "Nothing was run" in result.output


@pytest.mark.asyncio
async def test_the_backfill_respects_a_hard_ceiling() -> None:
    """Throttled means bounded: the owner can cap one run."""
    from src.workers.tasks import rescore_backfill

    db = _NeverYieldingDB([f"user-{i:03d}" for i in range(40)])
    result = await rescore_backfill(
        {"db": db, "enqueue": lambda *a, **k: None},
        batch_size=10,
        max_users=15,
        throttle_seconds=0,
    )
    assert result["enqueued"] == 15, result
