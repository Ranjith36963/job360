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

ANSWER ONCE: the questionnaire card (``ask_once.questionnaire``) counts as one item while any of its rows
waits; ``answer_once`` is how many rows. It shows under the "Answer once" chip with the jobs whose first
blocker is ``answers``.

The page caps some lists (50 outcome suggestions, 50 unmatched mail, 25 new connections, 500
contacts); the same caps are applied here so the two totals agree to the last unit.

A TEST application (``test_marker.py``, owner 2026-10-29) counts nowhere: its asks, ready card, CV
and people are left out. Nothing is judged by its words: a real "Simulation Engineer" job counts.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import asks as asks_service
from src.services.applications import inbox_items, outcome_suggestions, site_signin, spine, test_marker
from src.services.profile import ask_once, setting_requests
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase
    from src.services.applications.blockers import Blocker

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
        if asks_service.answered_by_applying(row, row["app_status"]):  # "I applied" answered it; the page closes it
            continue
        tied.append((int(row["id"]), int(row["application_id"])))
    return tied, general


async def _unsent_contacts(db: JobDatabase, user_id: str) -> tuple[set[int], int]:
    """(job ids with a person to reach, people with no job to reach): one query, no per-contact read. A cold
    person whose LinkedIn note is over the limit is the assistant's to rewrite, so it is not counted."""
    from src.services.applications import contacts  # noqa: PLC0415 - contacts imports the spine package

    cur = await db._db.execute(_UNSENT_SQL, (user_id, user_id, settings.LIST_PEOPLE_MAX))
    jobs: set[int] = set()
    cold = 0
    found = [dict(r) for r in await cur.fetchall()]
    too_long = await contacts.over_limit_notes(
        db, user_id, [int(r["id"]) for r in found if r["application_id"] is None and str(r["text"] or "").strip()]
    )
    for r in found:
        if not str(r["text"] or "").strip():
            continue
        if r["application_id"] is None:
            cold += int(int(r["id"]) not in too_long)
        else:
            jobs.add(int(r["application_id"]))
    return jobs, min(cold, NEW_CONNECTIONS_MAX)


ACTORS = ("you", "your_pc", "assistant", "job360", "employer")

# The FIXED filters (owner 2026-11-13): always all present, in this order, at zero too. ``todo`` = something the
# owner does; ``view`` = jobs the assistant settled (never counted in All or the badge); ``stat`` = a count only.
FILTERS: tuple[tuple[str, str, str], ...] = (
    ("all", "All", "todo"), ("signin_captcha", "Sign in / CAPTCHA", "todo"), ("check_send", "Check & send", "todo"),
    ("answer_once", "Answer once", "todo"), ("cv_weak", "CV still weak", "todo"),
    ("finish_by_hand", "Finish by hand", "todo"), ("went_out", "Did it go out?", "todo"),
    ("strong_cvs", "Strong CVs (AI reviewed)", "view"), ("posting_closed", "Posting closed", "stat"),
    ("other", "Other", "todo"),
)
# ONE table: a blocker's kind (and, for a site step, the word of its label) -> the filter it belongs to.
_SITE_FILTER = {
    "sign in": "signin_captcha", "create an account": "signin_captcha", "solve the CAPTCHA": "signin_captcha",
    "attach your CV": "signin_captcha", "enter the code": "signin_captcha",
    "finish on the site": "finish_by_hand", "mark it applied": "went_out",
}
_KIND_FILTER = {
    "confirm_sent": "went_out", "send_yes": "check_send", "check_send": "check_send", "approve_cv": "cv_weak",
    "apply_by_hand": "finish_by_hand", "outreach_send": "other", "posting_closed": "other",
}
_UNKNOWN_LOGGED = "other"  # reason code written when an owner kind is in no row of the table


def filter_for(blocker: Mapping[str, object]) -> Optional[str]:
    """The filter ONE blocker belongs to, or None. Only the owner's blockers (actor ``you``) map: the assistant's
    (``review_cv``, ``fix_cv``, ``assistant_step`` ...), the PC's and the employer's land nowhere. An owner kind
    the table does not know lands in Other, never nowhere. Pure: nothing is read, nothing hidden here (hiding a
    blocker belongs to ``blockers.py``, so the badge, All and the chips agree)."""
    if blocker["actor"] != "you":
        return None
    kind = blocker["kind"]
    if kind == "site_step":
        return _SITE_FILTER.get(str(blocker["label"]), _UNKNOWN_LOGGED)
    if kind == "answers":
        ref = blocker.get("ref")
        return "check_send" if isinstance(ref, dict) and ref.get("fill_event_id") is not None else "answer_once"
    return _KIND_FILTER.get(str(kind), _UNKNOWN_LOGGED)


def filters_of(blockers: Sequence[Mapping[str, object]]) -> set[str]:
    """Every filter a job's blockers put it in (a job is in each filter it has an owner need in)."""
    return {key for b in blockers if (key := filter_for(b)) is not None}


def _is_known(blocker: Mapping[str, object]) -> bool:
    kind = blocker["kind"]
    return kind in _KIND_FILTER or kind in ("site_step", "answers")


async def jobs_by_first_blocker(db: JobDatabase, user_id: str, tests: Optional[set[int]] = None) -> dict[str, Any]:
    """Every open application counted ONCE, under the actor and kind of its first blocker (``blockers_for``
    orders them, so the first is what waits next): ``by_actor``, ``by_kind``, and for the jobs that wait on the
    user ``job_ids`` (oldest first blocker first) and ``job_ids_by_kind``. One batch call, no per-job loop.
    ``tests``: the caller's own ``test_marker.test_app_ids`` answer (None = read it once, in ``blockers_for``)."""
    from src.services.applications import auto_close, blockers  # lazy: blockers imports this module

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
    firsts: dict[int, Blocker] = {}
    members: dict[str, list[int]] = {key: [] for key, _, _ in FILTERS}
    unknown: dict[int, list[str]] = {}
    cleared: set[int] = set()
    token = blockers.CLEARED.set(cleared)
    try:
        found = await blockers.blockers_for(db, user_id, ids, tests=tests)
    finally:
        blockers.CLEARED.reset(token)
    for app_id, lst in found.items():
        if not lst:
            continue
        for key in sorted(filters_of(lst)):
            members[key].append(app_id)
        if mine_all := [b for b in lst if b["actor"] == "you"]:
            members["all"].append(app_id)
            if odd := [b["kind"] for b in mine_all if not _is_known(b)]:
                unknown[app_id] = odd
        if any(b["kind"] == "posting_closed" and b["actor"] == "you" for b in lst):
            members["posting_closed"].append(app_id)
        first = lst[0]
        by_actor[first["actor"]] = by_actor.get(first["actor"], 0) + 1
        by_kind[first["kind"]] = by_kind.get(first["kind"], 0) + 1
        if first["actor"] == "you":
            mine.append((first["since"], app_id, first["kind"]))
            firsts[app_id] = first
    mine.sort()
    # Diagnosis D (2026-11-13): one sign-in on a site clears every job on it, so those jobs are ONE item.
    signins, grouped = site_signin.group([(i, firsts[i]) for _, i, _ in mine])
    by_ids: dict[str, list[int]] = {}
    for _, app_id, kind in mine:
        if app_id not in grouped:
            by_ids.setdefault(kind, []).append(app_id)
    for key, _, kind in FILTERS:  # a grouped job is no row: its host card is the item (counted in signin_captcha)
        if kind == "todo":
            members[key] = [i for i in members[key] if i not in grouped]
    members["strong_cvs"] = sorted(cleared)
    members["posting_closed"] = sorted(
        set(members["posting_closed"]) | set(await auto_close.proven_closed_ids(db, user_id)))
    order = {app_id: n for n, (_, app_id, _) in enumerate(mine)}  # the page's order: oldest first blocker first
    for key in members:
        members[key].sort(key=lambda i: (order.get(i, len(order)), i))
    if unknown:  # an owner kind the table does not know: filed under Other, and said here so it can be added
        get_audit_logger().info("needs_you_filter_unknown_kind", extra={
            "event": "needs_you_filter_unknown_kind", "user_id": safe_log_value(user_id), "reason": "kind_not_in_table",
            "jobs": {str(a): [safe_log_value(k, max_len=40) for k in ks] for a, ks in sorted(unknown.items())[:50]}})
    return {
        "by_actor": by_actor, "by_kind": by_kind, "job_ids": [i for _, i, _ in mine if i not in grouped],
        "job_ids_by_kind": by_ids, "site_signins": signins, "signin_job_ids": sorted(grouped), "members": members,
    }


PHASES = ("tests", "asks", "contacts", "blockers", "suggestions", "settings", "inbox", "answer_once", "pc_idle")


async def needs_you_count(
    db: JobDatabase, user_id: str, timings: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """``n`` = the page's job rows + one per sign-in site (``site_signins``) + other. ``jobs`` = the jobs whose
    first blocker is the user (= ``jobs_waiting_on_you``);
    ``by_actor`` / ``by_kind`` count every open job once by its first blocker, so the badge, the dashboard
    headline and the Needs-you chips all read this one answer.

    ``timings``: when given, it is filled with the milliseconds each step took (the keys are ``PHASES``).
    Durations only, never a value: the route sends them as a ``Server-Timing`` header, so a slow badge
    (2026-11-12: 6 s on a large account) can be read step by step from outside."""
    lap = time.perf_counter()

    def mark(phase: str) -> None:
        nonlocal lap
        now = time.perf_counter()
        if timings is not None:
            timings[phase] = round((now - lap) * 1000, 1)
        lap = now

    tests = await test_marker.test_app_ids(db, user_id)
    mark("tests")
    tied, general = await _open_job_asks(db, user_id, tests)
    mark("asks")
    _, connections = await _unsent_contacts(db, user_id)
    mark("contacts")
    # The blockers step also reports its own parts (``b_<part>``), so a slow one can be named.
    from src.services.applications import blockers  # noqa: PLC0415 - lazy: blockers imports this module

    parts: dict[str, float] = {}
    token = blockers.SUBSTEPS.set(parts if timings is not None else None)
    try:
        first = await jobs_by_first_blocker(db, user_id, tests)
    finally:
        blockers.SUBSTEPS.reset(token)
    mark("blockers")
    if timings is not None:
        timings.update({f"b_{part}": ms for part, ms in parts.items()})
    members = first.pop("members")
    jobs = first["by_actor"]["you"]
    rows = {*first["job_ids"], *first.pop("signin_job_ids")}  # a grouped job's asks wait on its host card
    other_ask_ids = [ask_id for ask_id, job in tied if job not in rows]  # no row to answer them in: Other
    suggestions, _ = await outcome_suggestions.open_suggestions(db, user_id, settings.OUTCOME_SUGGESTIONS_LIST_MAX)
    mark("suggestions")
    waiting_settings = len(await asyncio.to_thread(setting_requests.list_waiting, user_id))
    mark("settings")
    open_mail = min(await inbox_items.count_open(db, user_id), MAIL_LIST_MAX)
    mark("inbox")
    other = general + len(other_ask_ids) + waiting_settings + len(suggestions) + open_mail + connections
    # Answer once (owner 2026-11-02): the questionnaire is ONE card, counted once while any row waits.
    answer_once = await asyncio.to_thread(ask_once.open_questions, user_id, await spine.queued_countries(db, user_id))
    mark("answer_once")
    # Owner #12: one line for the jobs on the user's PC when no session in their own browser worked the queue.
    from src.services.applications import own_browser  # noqa: PLC0415 - own_browser -> contacts -> this package

    pc_idle = await own_browser.pc_idle(db, user_id, datetime.now(timezone.utc), first["by_actor"]["your_pc"])
    mark("pc_idle")
    n = len(first["job_ids"]) + len(first["site_signins"]) + other + (1 if answer_once else 0)
    filters = _filters(members, n, general, other, answer_once, len(first["site_signins"]))
    get_audit_logger().info("needs_you_filters", extra={
        "event": "needs_you_filters", "user_id": safe_log_value(user_id), "n": n, "result": "ok",
        "counts": {f["key"]: f["count"] for f in filters}})
    return {
        "n": n, "jobs": jobs, "other": other, "jobs_waiting_on_you": jobs,
        "answer_once": answer_once, "other_ask_ids": other_ask_ids, **first, "pc_idle": pc_idle, "filters": filters,
    }


def _filters(
    members: dict[str, list[int]], n: int, general: int, other: int, answer_once: int, site_cards: int = 0,
) -> list[dict[str, Any]]:
    """The fixed filters, each with its own count. A job counts in every filter it has an owner need in; All
    counts each job once and equals ``n``. The items that are no job are PARTITIONED, never added twice: the
    job-less asks and the Answer-once card show under "Answer once", the rest of Other under "Other", so
    the non-job items across the filters add up to ``other + card`` (``n`` minus the jobs). The view and the
    stat never enter ``n``. A site sign-in card (one per host, its jobs are no rows) counts once in
    "Sign in / CAPTCHA"."""
    extra = {"answer_once": general + (1 if answer_once else 0), "other": other - general, "signin_captcha": site_cards}
    out: list[dict[str, Any]] = []
    for key, label, kind in FILTERS:
        ids = members[key]
        count = n if key == "all" else len(ids) + extra.get(key, 0)
        out.append({"key": key, "label": label, "count": count, "job_ids": ids, "kind": kind})
    return out
