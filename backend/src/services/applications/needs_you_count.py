"""The Needs-you total in one cheap read: the number on the menu badge, the dashboard headline and the page.

JOBS come from ``blockers.blockers_for`` (one batch call): a job counts once, under the actor of its FIRST
blocker. ``jobs`` is the jobs whose first blocker is the user, and ``job_ids`` are the page's rows. The nav
badge, the Progress-dashboard headline and the Needs-you summary and chips all read this one answer; the
frontend counts nothing itself. Read-only, always scoped by ``user_id`` (rules #12/#25).

OTHER items (each counts once):
* open asks tied to no job, and open asks tied to a job that is not one of the rows (for example a
  ``proof_missing:`` ask on an applied or ghosted job; ``other_ask_ids`` lists them so the page can show them);
* setting requests waiting; outcome suggestions; unmatched mail;
* New connections: people with no job whose newest drafted note is not sent (at most 25, the page cap).

The page caps some lists (50 outcome suggestions, 50 unmatched mail, 25 new connections, 500
contacts); the same caps are applied here so the two totals agree to the last unit.

A TEST application (``test_marker.py``, owner 2026-10-29) counts nowhere: its asks, ready card, CV
and people are left out. Nothing is judged by its words: a real "Simulation Engineer" job counts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core import settings
from src.services.applications import inbox_items, outcome_suggestions, test_marker
from src.services.profile import setting_requests

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

# The page's list of unmatched mail asks for 50 (GET /api/inbox-items default); its New connections
# read at most 25 full records (``MAX_NEW_CONNECTIONS`` in NewConnections.tsx).
MAIL_LIST_MAX = 50
NEW_CONNECTIONS_MAX = 25

_OPEN_ASKS_SQL = (
    "SELECT k.id, k.application_id, k.question, k.context, a.job_title, a.job_company, a.status AS app_status "
    "FROM application_asks k LEFT JOIN applications a ON a.id = k.application_id AND a.user_id = k.user_id "
    "WHERE k.user_id = ? AND k.answered_at IS NULL AND k.withdrawn_at IS NULL"
)

# Each contact's newest drafted message, unless it is empty or the newest "sent" mark is at or after it
# (``personToReach`` in job-cards.ts). Only the newest LIST_PEOPLE_MAX contacts, as GET /api/people lists.
_UNSENT_HEAD = (
    "SELECT c.id, c.application_id, m.text, m.occurred_at FROM application_contacts c "
    "JOIN contact_outreach m ON m.id = (SELECT MAX(x.id) FROM contact_outreach x "
    "WHERE x.contact_id = c.id AND x.user_id = c.user_id AND x.entry = 'message') "
    "WHERE c.user_id = ? "
)
_UNSENT_NEWEST = "AND c.id IN (SELECT n.id FROM application_contacts n WHERE n.user_id = ? ORDER BY n.id DESC LIMIT ?) "
_UNSENT_TAIL = (
    "AND NOT EXISTS (SELECT 1 FROM contact_outreach s WHERE s.user_id = c.user_id "
    "AND s.id = (SELECT MAX(y.id) FROM contact_outreach y WHERE y.contact_id = c.id "
    "AND y.user_id = c.user_id AND y.entry = 'sent') AND s.occurred_at >= m.occurred_at) "
    # S11b: a person the user marked a duplicate (the newest duplicate_of row is not '') is no one to reach.
    "AND COALESCE((SELECT d.value FROM contact_edits d WHERE d.contact_id = c.id AND d.user_id = c.user_id "
    "AND d.field = 'duplicate_of' ORDER BY d.id DESC LIMIT 1), '') = '' "
    # Skipped (#1342): the user skipped the NEWEST drafted note (a `skipped` row newer than it).
    "AND NOT EXISTS (SELECT 1 FROM contact_outreach k WHERE k.contact_id = c.id "
    "AND k.user_id = c.user_id AND k.entry = 'skipped' AND k.id > m.id)"
)
_UNSENT_SQL = _UNSENT_HEAD + _UNSENT_NEWEST + _UNSENT_TAIL


async def unsent_for_apps(db: JobDatabase, user_id: str, app_ids: list[int]) -> list[dict[str, Any]]:
    """The same people as ``_unsent_contacts`` (``id, application_id, text, occurred_at``), narrowed to these
    applications in one query, with the same newest-people cap. Read-only; scoped by ``user_id``."""
    if not app_ids:
        return []
    marks = ",".join("?" for _ in app_ids)
    cur = await db._db.execute(
        _UNSENT_HEAD + _UNSENT_NEWEST + f"AND c.application_id IN ({marks}) " + _UNSENT_TAIL,  # noqa: S608
        (user_id, user_id, settings.LIST_PEOPLE_MAX, *app_ids),
    )
    return [dict(r) for r in await cur.fetchall()]


async def _open_job_asks(db: JobDatabase, user_id: str, tests: set[int]) -> tuple[list[tuple[int, int]], int]:
    """([(ask id, job id)] open asks tied to a job, open asks tied to no job), a test job's asks left out. An
    ask on a job that is closed is not counted: the page closes those on load (``tidy_open_asks``), except the
    ``proof_missing:`` ones, which it leaves open."""
    cur = await db._db.execute(_OPEN_ASKS_SQL, (user_id,))
    tied: list[tuple[int, int]] = []
    general = 0
    for r in await cur.fetchall():
        row = dict(r)
        if row["application_id"] is None:
            general += 1
            continue
        if int(row["application_id"]) in tests:
            continue
        closed = row["app_status"] in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES
        if closed and not str(row["context"] or "").startswith("proof_missing:"):
            continue
        tied.append((int(row["id"]), int(row["application_id"])))
    return tied, general


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


ACTORS = ("you", "your_pc", "assistant", "job360", "employer")


async def jobs_by_first_blocker(db: JobDatabase, user_id: str) -> dict[str, Any]:
    """Every open application counted ONCE, under the actor and kind of its first blocker (``blockers_for``
    orders them, so the first is what waits next): ``by_actor``, ``by_kind``, and for the jobs that wait on the
    user ``job_ids`` (oldest first blocker first) and ``job_ids_by_kind``. One batch call, no per-job loop."""
    from src.services.applications import blockers  # lazy: blockers imports this module

    closed = tuple(settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES)
    marks = ",".join("?" for _ in closed)
    cur = await db._db.execute(
        f"SELECT id FROM applications WHERE user_id = ? AND status NOT IN ({marks})",  # noqa: S608
        (user_id, *closed),
    )
    ids = [int(r["id"]) for r in await cur.fetchall()]
    by_actor = dict.fromkeys(ACTORS, 0)
    by_kind: dict[str, int] = {}
    mine: list[tuple[str, int, str]] = []
    for app_id, lst in (await blockers.blockers_for(db, user_id, ids)).items():
        if not lst:
            continue
        first = lst[0]
        by_actor[first["actor"]] = by_actor.get(first["actor"], 0) + 1
        by_kind[first["kind"]] = by_kind.get(first["kind"], 0) + 1
        if first["actor"] == "you":
            mine.append((first["since"], app_id, first["kind"]))
    mine.sort()
    by_ids: dict[str, list[int]] = {}
    for _, app_id, kind in mine:
        by_ids.setdefault(kind, []).append(app_id)
    return {"by_actor": by_actor, "by_kind": by_kind, "job_ids": [i for _, i, _ in mine], "job_ids_by_kind": by_ids}


async def needs_you_count(db: JobDatabase, user_id: str) -> dict[str, Any]:
    """``n`` = jobs + other. ``jobs`` = the jobs whose first blocker is the user (= ``jobs_waiting_on_you``);
    ``by_actor`` / ``by_kind`` count every open job once by its first blocker, so the badge, the dashboard
    headline and the Needs-you chips all read this one answer."""
    tests = await test_marker.test_app_ids(db, user_id)
    tied, general = await _open_job_asks(db, user_id, tests)
    _, connections = await _unsent_contacts(db, user_id)
    first = await jobs_by_first_blocker(db, user_id)
    jobs = first["by_actor"]["you"]
    rows = set(first["job_ids"])
    other_ask_ids = [ask_id for ask_id, job in tied if job not in rows]  # no row to answer them in: Other
    suggestions, _ = await outcome_suggestions.open_suggestions(db, user_id, settings.OUTCOME_SUGGESTIONS_LIST_MAX)
    other = (
        general
        + len(other_ask_ids)
        + len(setting_requests.list_waiting(user_id))
        + len(suggestions)
        + min(await inbox_items.count_open(db, user_id), MAIL_LIST_MAX)
        + connections
    )
    return {
        "n": jobs + other, "jobs": jobs, "other": other, "jobs_waiting_on_you": jobs,
        "other_ask_ids": other_ask_ids, **first,
    }
