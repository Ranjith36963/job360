"""W-17 / W-18 — the instant email must say what the dashboard says, and link to us.

Instant is the DEFAULT notify mode, and it was the worst message the product could
produce: `SELECT title, company, apply_url` and a body of
``f"Job360 match: {title}\\n{apply_url}"``. Two separate failures in one line:

  W-17  No score, no verdict, no reason, no salary — everything that answers "why am
        I being told about this?" The digest path already had all of it via
        build_decision_card. Two users on different settings received different
        products, and the default one was the poor one.

  W-18  The link went STRAIGHT TO THE EMPLOYER. The digest links to
        job360.uk/jobs/{id}. So in the default mode the click could never come back —
        no attribution, no staleness guard, no "this one closed" page.

A third bug falls out of the same query: `job_row.get("match_score")` was passed to
dispatch() as the score-threshold gate input, but the SELECT never fetched that column,
so it was ALWAYS None and the gate never actually gated anything.

The anti-drift test is the important one here. Both modes must be rendered from ONE
definition (build_decision_card), so a future edit cannot make them disagree again.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.repositories import pg
from src.workers import tasks as worker_tasks

USER = "instant-user"
EMPLOYER_URL = "https://boards.example.com/apply/platform-engineer"
# The REAL configured value, not a hardcoded guess: this worktree's backend/.env
# sets SITE_BASE_URL=http://localhost:3000, and the anti-drift test below must
# compare the instant body against the same base the code actually used.
from src.core.settings import SITE_BASE_URL as SITE  # noqa: E402


def iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class Recorder:
    """Stands in for dispatcher.dispatch at the Apprise boundary."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, db: Any, **kw: Any) -> list[Any]:
        self.calls.append(kw)
        return [type("R", (), {"ok": True, "queued_digest": False, "skipped": False,
                               "channel_type": "email", "channel_id": 1, "error": None})()]


@pytest.fixture
async def db(tmp_path):
    async with pg.connect(str(tmp_path / "instant.db")) as conn:
        await conn.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT DEFAULT '',
                apply_url TEXT DEFAULT '',
                match_score INTEGER DEFAULT 0
            );
            CREATE TABLE user_feed (
                user_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                score INTEGER DEFAULT 0,
                llm_fit_score INTEGER,
                llm_verdict TEXT,
                llm_reason TEXT,
                notified_at TEXT,
                UNIQUE(user_id, job_id)
            );
            CREATE TABLE job_enrichment (
                job_id INTEGER PRIMARY KEY,
                salary TEXT
            );
            CREATE TABLE notification_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                sent_at TEXT,
                error_message TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '2026-01-01',
                UNIQUE(user_id, job_id, channel)
            );
            """
        )
        await conn.commit()
        yield conn


async def seed(
    conn,
    *,
    judged: bool = True,
    salary: str | None = '{"currency": "GBP", "min": 70000, "max": 85000, "frequency": "yearly"}',
    location: str = "London, UK",
    feed_row: bool = True,
    score: int = 61,
) -> int:
    cur = await conn.execute(
        "INSERT INTO jobs (title, company, location, apply_url, match_score) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Platform Engineer", "Meta", location, EMPLOYER_URL, score),
    )
    job_id = cur.lastrowid
    if feed_row:
        await conn.execute(
            "INSERT INTO user_feed (user_id, job_id, score, llm_fit_score, llm_verdict, llm_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                USER,
                job_id,
                score,
                82 if judged else None,
                "strong fit" if judged else None,
                "Your Kubernetes and Terraform work maps directly to their platform team."
                if judged
                else None,
            ),
        )
    if salary is not None:
        await conn.execute(
            "INSERT INTO job_enrichment (job_id, salary) VALUES (?, ?)", (job_id, salary)
        )
    await conn.commit()
    return job_id


async def send(conn, job_id: int, rec: Recorder) -> dict[str, int]:
    return await worker_tasks.send_notification(
        {"db": conn, "dispatcher": rec}, USER, job_id
    )


# ── W-17: the email must say WHY ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_instant_body_carries_the_score_the_verdict_and_the_reason(db):
    job_id = await seed(db)
    rec = Recorder()

    await send(db, job_id, rec)

    assert rec.calls, "no message was dispatched at all"
    body = rec.calls[0]["body"]
    assert "82" in body, f"the fit score is missing:\n{body}"
    assert "strong fit" in body, f"the verdict is missing:\n{body}"
    assert "Kubernetes" in body, f"the reason is missing:\n{body}"


@pytest.mark.asyncio
async def test_instant_body_carries_salary_and_location(db):
    job_id = await seed(db)
    rec = Recorder()

    await send(db, job_id, rec)

    body = rec.calls[0]["body"]
    assert "London" in body, f"location missing:\n{body}"
    # The dashboard's own words — "£70k–£85k", not "£70,000". Parity of WORDING is
    # the point of the shared card: a different format is a visible difference to
    # the only person who matters.
    assert "70k" in body, f"salary missing, or worded differently to the dashboard:\n{body}"


@pytest.mark.asyncio
async def test_an_unjudged_job_says_not_reviewed_rather_than_inventing_a_verdict(db):
    """Rule #29's spirit: say what we know, never manufacture what we don't."""
    job_id = await seed(db, judged=False)
    rec = Recorder()

    await send(db, job_id, rec)

    body = rec.calls[0]["body"]
    assert "not yet reviewed" in body.lower(), f"unjudged job did not say so:\n{body}"
    assert "strong fit" not in body.lower()


# ── W-18: the click must be able to come back ────────────────────────────────


@pytest.mark.asyncio
async def test_instant_body_links_to_job360_not_straight_to_the_employer(db):
    """THE W-18 assertion. The digest already does this; instant did not."""
    job_id = await seed(db)
    rec = Recorder()

    await send(db, job_id, rec)

    body = rec.calls[0]["body"]
    assert f"/jobs/{job_id}" in body, f"no Job360 job link in the body:\n{body}"
    assert EMPLOYER_URL not in body, (
        "the raw employer apply_url is still in the instant email — the click "
        f"cannot come back:\n{body}"
    )


# ── the two modes must not drift apart again ─────────────────────────────────


@pytest.mark.asyncio
async def test_instant_and_digest_describe_one_job_identically(db):
    """Both modes must render from ONE definition, so they cannot disagree.

    This is the guard that outlives the fix: if someone later hand-rolls the
    instant body again, the score/verdict/reason/link will drift and this fails.
    """
    from src.services.delivery.decision_card import build_decision_card
    from src.services.delivery.email_body import render_digest_text

    job_id = await seed(db)
    rec = Recorder()
    await send(db, job_id, rec)
    instant_body = rec.calls[0]["body"]

    cur = await db.execute(
        "SELECT j.id AS job_id, j.title, j.company, j.location, e.salary AS enr_salary, "
        "       f.score, f.llm_fit_score, f.llm_verdict, f.llm_reason "
        "FROM jobs j JOIN user_feed f ON f.job_id = j.id AND f.user_id = ? "
        "LEFT JOIN job_enrichment e ON e.job_id = j.id WHERE j.id = ?",
        (USER, job_id),
    )
    db.row_factory = pg.Row
    row = dict(await cur.fetchone())
    card = build_decision_card(row, site_base_url=SITE)
    digest_body = render_digest_text([card], considered=1, dropped_reasons=[])

    for fact in (str(card.primary_score), card.verdict or "", card.url):
        assert fact in instant_body, f"instant is missing {fact!r} that the digest states"
        assert fact in digest_body, f"digest is missing {fact!r} (test setup wrong)"


# ── the gate that never gated ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_real_match_score_reaches_the_dispatcher(db):
    """`job_row.get("match_score")` was always None — the SELECT never fetched it.

    So dispatch()'s score-threshold gate (dispatcher.py Gate 2) received None for
    every instant notification and could never gate anything.
    """
    job_id = await seed(db, score=61)
    rec = Recorder()

    await send(db, job_id, rec)

    passed = rec.calls[0].get("match_score")
    assert passed is not None, "match_score is still None — the threshold gate is dead"
    assert passed == 82, f"expected the judged fit score the user is ranked by, got {passed}"


@pytest.mark.asyncio
async def test_a_job_we_cannot_explain_is_not_sent(db):
    """No user_feed row → we cannot score or explain it, so we do not send it.

    Same rule the digest already states for its INNER join. Consistency matters
    more than reach: an unexplainable alert is exactly the spam we are avoiding.
    """
    job_id = await seed(db, feed_row=False)
    rec = Recorder()

    result = await send(db, job_id, rec)

    assert rec.calls == [], "sent a job we have no feed row for"
    assert result.get("sent", 0) == 0
