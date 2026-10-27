"""The Needs-you total in one cheap read: the number on the menu badge (owner 2026-10-27).

The Needs-you page computes its total in the browser (``countNeedsYou`` in
``frontend/src/lib/needs-you-items.ts``): distinct JOBS with anything open, plus the OTHER items.
This module computes the same number on the server, so the badge is exact on every page without
reading the full cards. Read-only, always scoped by ``user_id`` (rules #12/#25).

JOBS (a job counts once however many of these it has):
* an open, non-withdrawn ask tied to a job (a practice / simulation ask never counts);
* a ready-to-send card (``ready.ready_ids``, the same SQL as the card list);
* a CV to approve (``ready.cv_to_approve``, the same service the page reads);
* a person with a drafted note not yet sent, linked to a job (see ``_unsent_contacts``).

OTHER items (each counts once):
* open asks tied to no job; setting requests waiting; outcome suggestions; unmatched mail;
* New connections: people with no job whose newest drafted note is not sent (at most 25, the page cap).

The page caps some lists (50 outcome suggestions, 50 unmatched mail, 25 new connections, 500
contacts); the same caps are applied here so the two totals agree to the last unit.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from src.core import settings
from src.services.applications import inbox_items, outcome_suggestions, ready
from src.services.profile import setting_requests

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

# The page's list of unmatched mail asks for 50 (GET /api/inbox-items default); its New connections
# read at most 25 full records (``MAX_NEW_CONNECTIONS`` in NewConnections.tsx).
MAIL_LIST_MAX = 50
NEW_CONNECTIONS_MAX = 25

# ``isSimulation`` in frontend/src/lib/job-cards.ts: a practice ask never belongs on the real list.
_SIMULATION = re.compile(r"\bsimulation\b|lifecycle test|do not apply", re.IGNORECASE)

_OPEN_ASKS_SQL = (
    "SELECT k.application_id, k.question, k.context, a.job_title, a.job_company, a.status AS app_status "
    "FROM application_asks k LEFT JOIN applications a ON a.id = k.application_id AND a.user_id = k.user_id "
    "WHERE k.user_id = ? AND k.answered_at IS NULL AND k.withdrawn_at IS NULL"
)

# Each contact's newest drafted message, unless it is empty or the newest "sent" mark is at or after it
# (``personToReach`` in job-cards.ts). Only the newest LIST_PEOPLE_MAX contacts, as GET /api/people lists.
_UNSENT_SQL = (
    "SELECT c.id, c.application_id, m.text FROM application_contacts c "
    "JOIN contact_outreach m ON m.id = (SELECT MAX(x.id) FROM contact_outreach x "
    "WHERE x.contact_id = c.id AND x.user_id = c.user_id AND x.entry = 'message') "
    "WHERE c.user_id = ? "
    "AND c.id IN (SELECT n.id FROM application_contacts n WHERE n.user_id = ? ORDER BY n.id DESC LIMIT ?) "
    "AND NOT EXISTS (SELECT 1 FROM contact_outreach s WHERE s.user_id = c.user_id "
    "AND s.id = (SELECT MAX(y.id) FROM contact_outreach y WHERE y.contact_id = c.id "
    "AND y.user_id = c.user_id AND y.entry = 'sent') AND s.occurred_at >= m.occurred_at) "
    # Skipped (#1342): the user skipped the NEWEST drafted note (a `skipped` row newer than it).
    "AND NOT EXISTS (SELECT 1 FROM contact_outreach k WHERE k.contact_id = c.id "
    "AND k.user_id = c.user_id AND k.entry = 'skipped' AND k.id > m.id)"
)


async def _open_ask_jobs(db: JobDatabase, user_id: str) -> tuple[set[int], int]:
    """(job ids with an open ask, open asks tied to no job), practice asks left out. An ask on a job
    that is closed is not counted: the page closes those on load (``tidy_open_asks``), except the
    ``proof_missing:`` ones, which it leaves open."""
    cur = await db._db.execute(_OPEN_ASKS_SQL, (user_id,))
    jobs: set[int] = set()
    general = 0
    for r in await cur.fetchall():
        row = dict(r)
        text = [row["question"], row["context"], row["job_title"] or "", row["job_company"] or ""]
        if any(_SIMULATION.search(str(t or "")) for t in text):
            continue
        if row["application_id"] is None:
            general += 1
            continue
        closed = row["app_status"] in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES
        if closed and not str(row["context"] or "").startswith("proof_missing:"):
            continue
        jobs.add(int(row["application_id"]))
    return jobs, general


async def _unsent_contacts(db: JobDatabase, user_id: str) -> tuple[set[int], int]:
    """(job ids with a person to reach, people with no job to reach): one query, no per-contact read."""
    cur = await db._db.execute(_UNSENT_SQL, (user_id, user_id, settings.LIST_PEOPLE_MAX))
    jobs: set[int] = set()
    cold = 0
    for r in await cur.fetchall():
        if not str(r["text"] or "").strip():
            continue
        if r["application_id"] is None:
            cold += 1
        else:
            jobs.add(int(r["application_id"]))
    return jobs, min(cold, NEW_CONNECTIONS_MAX)


async def needs_you_count(db: JobDatabase, user_id: str) -> dict[str, Any]:
    """``{"n", "jobs", "other"}``: n = jobs + other, the Needs-you page's own total."""
    ask_jobs, general = await _open_ask_jobs(db, user_id)
    ready_jobs = set(await ready.ready_ids(db, user_id))
    cv_jobs = {int(r["application_id"]) for r in await ready.cv_to_approve(db, user_id)}
    person_jobs, connections = await _unsent_contacts(db, user_id)
    jobs = len(ask_jobs | ready_jobs | cv_jobs | person_jobs)
    suggestions, _ = await outcome_suggestions.open_suggestions(db, user_id, settings.OUTCOME_SUGGESTIONS_LIST_MAX)
    other = (
        general
        + len(setting_requests.list_waiting(user_id))
        + len(suggestions)
        + min(await inbox_items.count_open(db, user_id), MAIL_LIST_MAX)
        + connections
    )
    return {"n": jobs + other, "jobs": jobs, "other": other}
