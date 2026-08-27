"""W-19 / W-20 — the chase cron: the product finally speaks about the user's OWN applications.

Before this, every message Job360 could send was about a job the user had NOT applied
to. The moment they applied, the product went silent about the thing they now cared
about most. The dormancy query already existed (``get_stale_applications``) and reached
exactly one consumer: an in-app banner they only saw if they went looking.

The negative cases here are the point. A notification feature is easy to make work and
easy to make *hostile* — the tests that matter are the ones proving it stays quiet:
already-chased, already-resolved, already-ghosted, no channel, and the per-user cap.

HTTP is never touched: the cron uses the same ``ctx['dispatcher']`` test hook that
send_notification uses (tasks.py:365).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.repositories import pg
from src.workers import tasks as worker_tasks

USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class _RecordingDispatcher:
    """Stands in for dispatcher.dispatch — records calls, never sends anything."""

    def __init__(self, results: list[Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results if results is not None else [_Ok()]

    async def __call__(self, db: Any, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        return self._results


class _Ok:
    ok = True


class _Failed:
    ok = False


@pytest.fixture
async def db(tmp_path):
    """A connection with just the tables the cron touches."""
    async with pg.connect(str(tmp_path / "chase.db")) as conn:
        await conn.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_chased_at TEXT,
                UNIQUE(user_id, job_id)
            );
            CREATE TABLE notification_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        await conn.commit()
        yield conn


async def _add_job(conn, title: str, company: str) -> int:
    cur = await conn.execute(
        "INSERT INTO jobs (title, company) VALUES (?, ?)", (title, company)
    )
    await conn.commit()
    return cur.lastrowid


async def _add_application(
    conn,
    job_id: int,
    *,
    user_id: str = USER,
    stage: str = "applied",
    quiet_days: float = 30,
    last_chased_at: str | None = None,
) -> None:
    await conn.execute(
        """INSERT INTO applications
           (user_id, job_id, stage, created_at, updated_at, last_chased_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, job_id, stage, _iso(quiet_days), _iso(quiet_days), last_chased_at),
    )
    await conn.commit()


async def _enable_rule(conn, user_id: str = USER) -> None:
    await conn.execute(
        "INSERT INTO notification_rules (user_id, enabled) VALUES (?, 1)", (user_id,)
    )
    await conn.commit()


async def _run(conn, dispatcher: _RecordingDispatcher) -> dict[str, int]:
    return await worker_tasks.chase_stale_applications(
        {"db": conn, "dispatcher": dispatcher}
    )


# ── it actually chases ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_quiet_application_produces_a_message_naming_the_job(db):
    await _enable_rule(db)
    job_id = await _add_job(db, "Platform Engineer", "Northwind")
    await _add_application(db, job_id, quiet_days=30)
    disp = _RecordingDispatcher()

    result = await _run(db, disp)

    assert len(disp.calls) == 1, "the quiet application produced no message"
    call = disp.calls[0]
    assert call["user_id"] == USER
    # Rule #21: assert the real content, not that a key exists. A count-only
    # message ("1 application is quiet") is useless in an inbox.
    assert "Platform Engineer" in call["body"]
    assert "Northwind" in call["body"]
    assert result["chased"] == 1


@pytest.mark.parametrize(
    "company", ["Meta", "Monzo", "Wise", "Octopus Energy", "Deloitte", "Starling Bank"]
)
def test_company_names_ending_in_a_or_t_are_not_truncated(company: str) -> None:
    """Regression: the first version used ``f"{t} at {c}".strip(" at")``.

    ``str.strip`` takes a SET OF CHARACTERS, not a suffix — so it ate any trailing
    space/'a'/'t' and "Data Engineer at Meta" went out as "Data Engineer at Me".
    The original tests used "Northwind" and "Acme", which both end in safe letters,
    so the bug shipped. This parametrisation exists specifically to include names
    that end in the stripped characters.
    """
    label = worker_tasks._job_label(
        {"title": "Data Engineer", "company": company, "job_id": 1}
    )
    assert label == f"Data Engineer at {company}"
    assert label.endswith(company), f"company name truncated: {label!r}"


def test_job_label_degrades_when_the_catalog_row_is_gone() -> None:
    """get_applications_to_chase LEFT JOINs jobs, so title/company can be empty."""
    assert worker_tasks._job_label({"title": "Data Engineer", "company": "", "job_id": 7}) == "Data Engineer"
    assert worker_tasks._job_label({"title": "", "company": "Acme", "job_id": 7}) == "Acme"
    # Never render a dangling "at" with a hole in it.
    assert worker_tasks._job_label({"title": "", "company": "", "job_id": 7}) == "job #7"
    assert " at " not in worker_tasks._job_label({"title": "", "company": "Acme", "job_id": 7})


@pytest.mark.asyncio
async def test_the_message_names_a_company_that_ends_in_a_stripped_character(db):
    """End-to-end version of the above, through the real cron."""
    await _enable_rule(db)
    job_id = await _add_job(db, "Data Engineer", "Meta")
    await _add_application(db, job_id, quiet_days=30)
    disp = _RecordingDispatcher()

    await _run(db, disp)

    assert "Meta" in disp.calls[0]["body"]
    assert "at Me\n" not in disp.calls[0]["body"]


@pytest.mark.asyncio
async def test_the_score_gate_is_bypassed_and_quiet_hours_are_not(db):
    """match_score must be absent; force must not be set.

    The score threshold asks "is this job good enough to mention?" — the wrong
    question for a job they already applied to. force=True would bypass quiet
    hours, and a chase is never worth waking someone at 3am.
    """
    await _enable_rule(db)
    job_id = await _add_job(db, "Data Engineer", "Acme")
    await _add_application(db, job_id, quiet_days=30)
    disp = _RecordingDispatcher()

    await _run(db, disp)

    call = disp.calls[0]
    assert call.get("match_score") is None, "score gate would silently drop the chase"
    assert not call.get("force"), "a chase must respect quiet hours"


# ── the negative cases — proving it stays quiet ──────────────────────────────


@pytest.mark.asyncio
async def test_a_recently_active_application_is_left_alone(db):
    await _enable_rule(db)
    job_id = await _add_job(db, "Fresh Role", "NewCo")
    await _add_application(db, job_id, quiet_days=1)
    disp = _RecordingDispatcher()

    result = await _run(db, disp)

    assert disp.calls == []
    assert result["chased"] == 0


@pytest.mark.asyncio
async def test_it_never_chases_the_same_application_twice_in_the_cooldown(db):
    """The whole reason last_chased_at exists.

    Without the cooldown, a daily cron re-chases every dormant row every day: an
    application quiet for 30 days would generate ~23 emails.
    """
    await _enable_rule(db)
    job_id = await _add_job(db, "Repeat Role", "SpamCo")
    await _add_application(db, job_id, quiet_days=30)

    first = _RecordingDispatcher()
    await _run(db, first)
    assert len(first.calls) == 1

    second = _RecordingDispatcher()
    result = await _run(db, second)

    assert second.calls == [], "chased the same application twice inside the cooldown"
    assert result["chased"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["offer", "rejected", "ghosted"])
async def test_resolved_and_ghosted_applications_are_never_chased(db, stage: str):
    """The conversation already ended — in either direction, or the user told us."""
    await _enable_rule(db)
    job_id = await _add_job(db, f"{stage.title()} Role", "DoneCo")
    await _add_application(db, job_id, stage=stage, quiet_days=60)
    disp = _RecordingDispatcher()

    await _run(db, disp)

    assert disp.calls == [], f"chased an application already in '{stage}'"


@pytest.mark.asyncio
async def test_a_user_with_notifications_disabled_is_never_chased(db):
    job_id = await _add_job(db, "Quiet Role", "NoNotifyCo")
    await _add_application(db, job_id, quiet_days=30)
    await db.execute(
        "INSERT INTO notification_rules (user_id, enabled) VALUES (?, 0)", (USER,)
    )
    await db.commit()
    disp = _RecordingDispatcher()

    await _run(db, disp)

    assert disp.calls == []


@pytest.mark.asyncio
async def test_one_users_applications_never_leak_into_another_users_chase(db):
    """Rule #12/#25 — every query scoped by user.id."""
    await _enable_rule(db, USER)
    mine = await _add_job(db, "Mine", "MineCo")
    theirs = await _add_job(db, "Theirs", "TheirCo")
    await _add_application(db, mine, user_id=USER, quiet_days=30)
    await _add_application(db, theirs, user_id=OTHER, quiet_days=30)
    disp = _RecordingDispatcher()

    await _run(db, disp)

    assert len(disp.calls) == 1
    body = disp.calls[0]["body"]
    assert "Mine" in body
    assert "Theirs" not in body, "another user's application leaked into the chase"


@pytest.mark.asyncio
async def test_the_batch_is_capped_so_a_holiday_does_not_produce_an_inbox(db):
    await _enable_rule(db)
    for i in range(worker_tasks.CHASE_MAX_PER_USER + 4):
        job_id = await _add_job(db, f"Role {i}", f"Co {i}")
        await _add_application(db, job_id, quiet_days=30)
    disp = _RecordingDispatcher()

    result = await _run(db, disp)

    assert len(disp.calls) == 1, "one message per user per run, not one per application"
    assert result["chased"] == worker_tasks.CHASE_MAX_PER_USER


@pytest.mark.asyncio
async def test_uncapped_leftovers_stay_eligible_for_the_next_run(db):
    """Only what we actually sent gets stamped, so the rest are not silently skipped."""
    await _enable_rule(db)
    total = worker_tasks.CHASE_MAX_PER_USER + 3
    for i in range(total):
        job_id = await _add_job(db, f"Role {i}", f"Co {i}")
        await _add_application(db, job_id, quiet_days=30)

    await _run(db, _RecordingDispatcher())

    cur = await db.execute(
        "SELECT COUNT(*) FROM applications WHERE user_id = ? AND last_chased_at IS NULL",
        (USER,),
    )
    remaining = (await cur.fetchone())[0]
    assert remaining == total - worker_tasks.CHASE_MAX_PER_USER


@pytest.mark.asyncio
async def test_a_user_with_no_channel_keeps_a_clean_cooldown(db):
    """No channels -> dispatch returns []. Don't burn the cooldown on a non-send.

    Otherwise a user who sets up email a week after signing up would silently
    never be chased about anything that went quiet in that first week.
    """
    await _enable_rule(db)
    job_id = await _add_job(db, "Unreachable Role", "NoChannelCo")
    await _add_application(db, job_id, quiet_days=30)
    disp = _RecordingDispatcher(results=[])

    result = await _run(db, disp)

    assert result["chased"] == 0
    cur = await db.execute(
        "SELECT last_chased_at FROM applications WHERE user_id = ? AND job_id = ?",
        (USER, job_id),
    )
    assert (await cur.fetchone())[0] is None, "cooldown burned without delivering anything"


@pytest.mark.asyncio
async def test_chasing_does_not_reset_the_dormancy_clock(db):
    """updated_at means "the user did something". Us emailing is not that.

    If the chase bumped updated_at, the application would look freshly active and
    the chase would silence the very signal it exists to report.
    """
    await _enable_rule(db)
    job_id = await _add_job(db, "Clock Role", "ClockCo")
    await _add_application(db, job_id, quiet_days=30)
    cur = await db.execute(
        "SELECT updated_at FROM applications WHERE user_id = ? AND job_id = ?",
        (USER, job_id),
    )
    before = (await cur.fetchone())[0]

    await _run(db, _RecordingDispatcher())

    cur = await db.execute(
        "SELECT updated_at, last_chased_at FROM applications WHERE user_id = ? AND job_id = ?",
        (USER, job_id),
    )
    after, chased_at = await cur.fetchone()
    assert after == before, "the chase reset the dormancy clock"
    assert chased_at is not None, "last_chased_at was never stamped"


@pytest.mark.asyncio
async def test_a_dispatcher_failure_does_not_kill_the_run(db):
    """One bad user must not stop the rest — the worker keeps going."""
    await _enable_rule(db)
    job_id = await _add_job(db, "Boom Role", "BoomCo")
    await _add_application(db, job_id, quiet_days=30)

    class _Boom(_RecordingDispatcher):
        async def __call__(self, db_: Any, **kwargs: Any) -> list[Any]:
            raise RuntimeError("smtp exploded")

    result = await _run(db, _Boom())

    assert result["chased"] == 0  # no crash, no stamp


@pytest.mark.asyncio
async def test_a_failed_send_is_reported_but_still_starts_the_cooldown(db):
    """A hard delivery failure must not become an infinite retry loop by proxy."""
    await _enable_rule(db)
    job_id = await _add_job(db, "Failing Role", "FailCo")
    await _add_application(db, job_id, quiet_days=30)
    disp = _RecordingDispatcher(results=[_Failed()])

    result = await _run(db, disp)

    assert result["sent"] == 0
    assert result["chased"] == 1
