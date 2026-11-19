"""What is due now? - the per-user door the scheduled assistant reads (owner decision 2026-10-23).

``GET /api/schedule/due`` answers which duties (hunt, inbox, batch) are due for THIS user, from
their own settings and the last time each was done; ``POST /api/schedule/done`` stamps one. The
MCP tools ``whats_due`` and ``mark_done`` call these functions (VISION M5: same route the web
uses). Both take the user from the session / token / OAuth subject, never from input (rules
#12/#25), are rate-limited per user, and log only closed values. Rules:
``services/profile/schedule.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.routes.own_browser import PcIdleOut
from src.core import settings
from src.repositories.database import JobDatabase
from src.services.applications.authorship import actor_for
from src.services.auth import rate_limit
from src.services.profile import schedule
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["schedule"])

Duty = Literal["hunt", "inbox", "batch", "reach", "network"]
Outcome = Literal["done", "partial", "failed"]
RunReason = Literal[
    "no_work", "tool_unavailable", "sign_in_needed", "paid_plan_needed", "network_blocked", "rate_limited",
    "needs_user", "error", "no_review", "below_target",
]


class InboxSearchOut(BaseModel):
    after: str  # ISO-8601; "" when the user has no open application
    gmail_after: str  # the same day as Gmail's after: YYYY/MM/DD
    application_ids: list[int]  # every open application: mark_inbox_checked these after the search
    companies: list[str]
    email_senders: list[str]
    skip_message_ids: list[str]  # open unmatched mail: never read or park it again


class DueItemOut(BaseModel):
    duty: Duty
    recipe: Literal["hunt", "inbox", "batch", "reach"]
    reason: Literal["never_done", "interval_elapsed", "queue_waiting", "new_applications", "run_now"]
    # Only on `reach`: the applications to find people for this run (newest first).
    application_ids: Optional[list[int]] = None
    # Only on `network`: at most this many new people who fit the user's profile (cold contacts) this run.
    max_people: Optional[int] = None
    # Only on `inbox`: what one Gmail search needs (2026-10-31), see services/applications/inbox.search_plan.
    search: Optional[InboxSearchOut] = None
    # Only on `hunt` (2026-11-18): profile paths the hunt needs and the user has not filled (closed:
    # preferences.target_job_titles). Stop and ask the user; never invent them.
    needs: Optional[list[Literal["preferences.target_job_titles"]]] = None


class KnownListOut(BaseModel):
    """2026-11-17 hunt: the user's known jobs as two plain-text downloads (curl them; no copying by hand).
    ``urls_url``: one job link per line (``urls`` lines, the kit's ``--known``); ``ct_url``:
    ``company ||| title ||| id`` per application (``total`` lines). Signed, read-only, this user only."""

    urls_url: str
    ct_url: str
    total: int
    urls: int
    expires_at: str


class WhatsDueOut(BaseModel):
    due: list[DueItemOut]
    nothing_due: bool
    next_due_at: dict[str, Optional[str]]
    batch_hold: Optional[str] = None
    # Owner 2026-10-25: queued jobs that wait on the user (an open ask, a block, a CV not seen yet). When
    # all of them do, `batch_hold` is `waiting_on_you` and the batch is not due.
    waiting_on_you: int = 0
    # Owner 2026-10-25: why the hunt is not due although its time came (closed set: queue_full).
    hunt_hold: Optional[Literal["queue_full"]] = None
    # Owner 2026-10-27: duties due because the user pressed Run now in Job360 (reason `run_now`).
    run_now: list[Duty] = Field(default_factory=list)
    last_done_at: dict[str, Optional[str]]
    last_tick_at: Optional[str] = None
    # Owner 2026-10-25: failed runs in a row per duty (a failing duty waits 2h, 4h, 8h... at most 24h).
    failures_in_row: dict[str, int] = Field(default_factory=dict)
    # Owner 2026-10-25: duties another run has now (not listed in `due`): {duty: taken_at}.
    running: dict[str, Optional[str]] = Field(default_factory=dict)
    # Duties that are due but another run has them now (so `nothing_due` is never silent about it).
    held_by_running: list[Duty] = Field(default_factory=list)
    expected_every_hours: int
    checked_at: str
    # Owner #12: own-browser tasks wait and no session in the user's own browser worked them for over
    # PC_IDLE_WARN_HOURS (or ever): tell the user to start their PC browser session. A scheduled run never works them.
    pc_idle: Optional[PcIdleOut] = None
    # An assistant's read for the hunt (no `duties`, or `duties` with hunt): its known list (KnownListOut).
    known_list: Optional[KnownListOut] = None


class HuntSkipIn(BaseModel):
    """Why a hunt source was not used: a closed ``code`` (HUNT_SKIP_CODES) and its ``detail`` (the exact error)."""

    model_config = ConfigDict(extra="forbid")
    code: str = Field(max_length=40)
    detail: Optional[str] = Field(default=None, max_length=300)


class HuntSourceIn(BaseModel):
    """One hunt source: what it did, or why it was not used ({code, detail}; free text alone is refused)."""

    model_config = ConfigDict(extra="forbid")
    calls: Optional[int] = Field(default=None, ge=0)
    ads: Optional[int] = Field(default=None, ge=0)
    brought: Optional[int] = Field(default=None, ge=0)
    # A str is accepted by the model only so the refusal can name the source (2026-11-18: excuses passed).
    skipped: Optional[Union[HuntSkipIn, str]] = Field(default=None)


class HuntKitIn(BaseModel):
    """The hunt kit's funnel: results, new after the known drop, passing the title/place pre-filter, read in full."""

    model_config = ConfigDict(extra="forbid")
    results: int = Field(ge=0)
    new: int = Field(ge=0)
    prefilter_passed: int = Field(ge=0)
    read_in_full: int = Field(ge=0)
    # 2026-11-18: every ad that passed counts once in read_in_full, unreadable (no page opened after Job360's
    # browser, TinyFish and Apify) or snippet_only (judged on a snippet). Required: missing = refused 422.
    unreadable: Optional[int] = Field(default=None, ge=0)
    snippet_only: Optional[int] = Field(default=None, ge=0)
    # Prose about the unread ads (never logged); it never replaces the two counts.
    unread_reason: Optional[str] = Field(default=None, max_length=300)


class HuntReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: dict[str, HuntSourceIn] = Field(default_factory=dict)
    kit: Optional[HuntKitIn] = None


class MarkDoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duty: Duty
    # Only with `reach`: the applications this run worked (each stamped so it is not listed again).
    application_ids: Optional[list[int]] = Field(default=None, max_length=20)
    # How the run ended (owner 2026-10-25). `failed` needs a `reason`; done/partial may carry one.
    outcome: Outcome = "done"
    reason: Optional[RunReason] = None
    # Prose about the run (at most 300 characters); a people run that stops short names each free lookup here.
    note: str = Field(default="", max_length=300)
    # Only with `hunt` (2026-11-17): every source's counts or exact skip reason, and the kit funnel (HUNT_SOURCES).
    report: Optional[HuntReportIn] = None
    # Only with reach/network (2026-11-18): true when the Doctor check AND a separate reviewer checked every person
    # and note this run saved. An assistant's people run marked done without it is recorded partial, no_review.
    reviewed: bool = False
    # Only with reach (2026-11-18 22:07 run: five ids listed, one company searched): application id -> what each
    # source found at that company ({"sprouts": 0, "clay": 1, "team_page": 2}). Needed for each job left under 3.
    searched: Optional[dict[str, dict[str, int]]] = Field(default=None, max_length=20)
    # Only with reach (owner 18 Nov; Kits 19 Nov, job 833 scored 0): the jobs whose company is a recruitment agency or
    # whose ad names no employer. The run judges it; there the named recruiter is the target (1 to 2 people).
    agency: Optional[list[int]] = Field(default=None, max_length=20)


class PeopleScoreOut(BaseModel):
    """2026-11-18: Job360's own count for a people run, never the run's claim: people saved at each job, and new
    network people this run, against the target and the minimum."""

    per_job: dict[str, int] = Field(default_factory=dict)
    network: Optional[int] = None
    target: int
    minimum: int
    # The agency jobs of this run (MarkDoneIn.agency): full at AGENCY_MINIMUM people, target AGENCY_TARGET.
    agency: list[str] = Field(default_factory=list)


# Owner 18 Nov: for an agency job the named recruiter IS the target, 1 to 2 people.
AGENCY_MINIMUM = 1
AGENCY_TARGET = 2


def _job_minimum(score: PeopleScoreOut, job: str) -> int:
    return AGENCY_MINIMUM if job in score.agency else score.minimum


class MarkDoneOut(BaseModel):
    duty: Duty
    done_at: str
    last_tick_at: str
    outcome: Outcome = "done"
    failures_in_row: int = 0
    # 2026-11-17: a batch stop - queued rows queued after this run's claim; they wait for the next batch.
    next_batch: list[int] = Field(default_factory=list)
    # 2026-11-18 (12:41 run, application 361): a batch stop - this batch's queued rows still missing their job
    # country (set it from the ad with update_job, or name it under Not submitted).
    country_missing: list[int] = Field(default_factory=list)
    note: str = ""
    score: Optional[PeopleScoreOut] = None


class RunProblemOut(BaseModel):
    duty: Duty
    label: str
    failures_in_row: int
    reason: RunReason
    title: str
    what_to_do: str
    last_failed_at: Optional[str] = None


class RunProblemsOut(BaseModel):
    problems: list[RunProblemOut]


class DutyStatusOut(BaseModel):
    duty: Duty
    label: str
    state: Literal["off", "running", "due", "failing", "held", "waiting"]
    # Why a duty that is on does not run now (hunt: queue_full; batch: one of the batch holds).
    hold: Optional[str] = None
    last_run_at: Optional[str] = None
    last_outcome: Optional[Outcome] = None
    last_reason: Optional[RunReason] = None
    failures_in_row: int = 0
    next_due_at: Optional[str] = None


class ScheduleStatusOut(BaseModel):
    duties: list[DutyStatusOut]
    last_tick_at: Optional[str] = None


def _limit(user: CurrentUser, kind: str, per_hour: int) -> None:
    if per_hour > 0 and not rate_limit.check_and_record(
        f"schedule-{kind}:{user.id}", max_in_window=per_hour, window_seconds=3600
    ):
        raise HTTPException(status_code=429, detail=f"too many schedule {kind}s this hour; try again later")


def _log(event: str, user: CurrentUser, **fields: str) -> None:
    get_audit_logger().info(
        event,
        extra={"event": event, "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
               "result": "ok", **fields},
    )


@router.get("/schedule/due", response_model=WhatsDueOut, dependencies=AUTH_FIRST)
async def whats_due(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
    duties: Optional[list[Duty]] = Query(default=None),  # noqa: B008
) -> WhatsDueOut:
    """The duties due NOW for the signed-in user, each with its recipe. Changes no setting. ``duties``
    (a routine that runs one job passes its own) limits the answer AND what it takes to those; a read
    without ``duties`` only looks. An assistant's read with ``duties`` takes each listed duty for this run
    (one run per duty at a time, owner 2026-10-25):
    another run in the next ``DUTY_CLAIM_MINUTES`` sees it under ``running`` / ``held_by_running`` until
    ``mark_done``. The website's own read only looks."""
    _limit(user, "read", settings.SCHEDULE_READS_MAX_PER_HOUR)
    now = datetime.now(timezone.utc)
    await schedule.sweep_lapsed(db, user.id, now)  # a claim that ran out with no mark_done counts, once
    answer = schedule.due_now(await schedule.load_facts(db, user.id, now), now)
    if duties:
        wanted = set(duties)
        answer["due"] = [d for d in answer["due"] if d["duty"] in wanted]
        answer["held_by_running"] = [d for d in answer["held_by_running"] if d in wanted]
        answer["nothing_due"] = not answer["due"]
    if duties and actor_for(user) != "web" and answer["due"]:
        got = await schedule.claim(db, user.id, [d["duty"] for d in answer["due"]], now)
        lost = [d["duty"] for d in answer["due"] if d["duty"] not in got]
        answer["due"] = [d for d in answer["due"] if d["duty"] in got]
        answer["nothing_due"] = not answer["due"]
        answer["running"] = {**answer["running"], **{d: now.isoformat(timespec="seconds") for d in lost}}
        answer["held_by_running"] = [*answer["held_by_running"], *lost]
    for item in answer["due"]:
        if item["duty"] == "inbox":
            from src.services.applications.inbox import search_plan  # noqa: PLC0415 - profile <-> spine cycle

            item["search"] = await search_plan(db, user.id)
    from src.services.applications import own_browser  # noqa: PLC0415 - profile <-> spine cycle

    waiting = len(await own_browser.queue_tasks(db, user, now))
    answer["pc_idle"] = await own_browser.pc_idle(db, user.id, now, waiting)
    if actor_for(user) != "web" and (not duties or "hunt" in duties):
        from src.services.applications import known_list  # noqa: PLC0415 - profile <-> spine cycle

        try:
            answer["known_list"] = await known_list.mint(db, user.id, now)
        except known_list.KnownListError:  # no SESSION_SECRET: the run falls back to list_applications
            answer["known_list"] = None
        if answer["known_list"]:
            _log("known_list_minted", user, total=str(answer["known_list"]["total"]))
    hunt_needs = ",".join(n for d in answer["due"] if d["duty"] == "hunt" for n in d.get("needs") or [])
    _log("schedule_due_read", user, due=",".join(d["duty"] for d in answer["due"]) or "none",
         hunt_needs=hunt_needs or "none")
    return WhatsDueOut(**answer)


# 2026-10-30 (17:42 run): refused needs_user, the run stopped with partial/error instead; the 20:26 run
# then stopped with failed/needs_user. A partial or failed batch stop with no outside blocker (rate
# limit, network, sign-in, plan, tool) is the same early stop.
_NOT_A_BLOCKER = frozenset({None, "needs_user", "error", "no_work"})
BATCH_NOT_CLAIMED = 'You did not claim the batch: call whats_due(duties=["batch"]) first. Nothing was recorded.'


_STEP_WORDS = {"refill": "refill the form", "fill": "fill the form"}


def _row_steps(rows: list[dict[str, Any]]) -> str:
    """``125 (refill the form); 133 (fill the form)``: what each row the batch stop names still needs."""
    return "; ".join(f"{r['id']} ({_STEP_WORDS.get(str(r.get('step')), 'fill the form')})" for r in rows)


def _next_batch_note(later: list[int]) -> str:
    if not later:
        return ""
    return (f"{len(later)} jobs queued after this run claimed the batch wait for the next batch: "
            f"{', '.join(str(i) for i in later[:20])}.")


async def _refuse_early_batch_stop(db: JobDatabase, user: CurrentUser, claimed_at: Optional[datetime]) -> list[int]:
    """2026-10-30: a batch run stops early (partial, no outside blocker) only when every queued job waits
    on the owner or is blocked. While a queued row still needs no owner input (own browser, no hold, ask,
    block or filled form) the run is refused with 409 and told which row to work next. 2026-11-17: only rows
    queued at or before this run's claim (``claimed_at``; None = the website, every row) count; the rest wait
    for the next batch and are returned. 2026-11-18: only rows that already have their CV count; a row with
    no CV is the CV run's (``queue_preflight.cv_rows`` / ``cv_work``), and the batch skips it as ``no_cv``."""
    from src.services.applications import queue_preflight  # noqa: PLC0415 - profile <-> spine cycle

    later = await queue_preflight.queued_after_claim(db, user.id, claimed_at)
    note = (" " + _next_batch_note(later)) if later else ""
    rows = await queue_preflight.fillable_rows(db, user.id, queued_by=claimed_at, with_cv=True)
    if not rows:
        # 2026-11-18 (application 1033): a 409 for a CV-less row made the batch run write that CV itself.
        # Those rows wait for the CV run; logged so a stop that left them is visible.
        no_cv = await queue_preflight.cv_rows(db, user.id, queued_by=claimed_at)
        if no_cv:
            _log("batch_stop_no_cv_left", user, rows=str(len(no_cv)), ids=",".join(str(r["id"]) for r in no_cv[:20]))
        return later
    # 2026-11-18 (20:55 run): the log named no rows, so the queue/gate disagreement on 125 and 136 was invisible.
    _log("batch_stop_refused", user, reason="needs_no_owner_input", rows=str(len(rows)), later=str(len(later)),
         ids=safe_log_value(",".join(f"{r['id']}:{r['step']}" for r in rows[:20])))
    first = rows[0]
    raise HTTPException(
        status_code=409,
        detail=(
            f"{len(rows)} queued jobs still need no owner input (no open ask, no block); next: application "
            f"{first['id']} ({first['company'] or 'unknown company'}). Keep working: {first['step']} its form (its CV "
            "is saved), then check_submit. A partial run ends only when every queued job is waiting on the owner or "
            "blocked. Large tool outputs and outputs saved to a file are normal; they are not a reason to stop. "
            f"Ids: {', '.join(str(r['id']) for r in rows[:5])}. "
            f"Each: {_row_steps(rows[:5])}.{note}"
        ),
    )


# 2026-11-17 (20:08 people run): the run saved 10 people, wrote no outreach note for any of them, and marked
# network failed with tool_unavailable because one lookup (Crustdata) was out of credits, although web search,
# company pages and three other lookups were there. The recipe said both; only Job360's reply is obeyed.
# 2026-11-17 (20:48 run, after that fix): five jobs due, two minutes of work, Sprouts loaded but never called,
# Clay and Vibe never loaded, then failed/no_work. A people run that was given work never has "no work", never
# asks the user, and does not stop short without saying what each free lookup returned.
_ONE_TOOL_DOWN = frozenset({"tool_unavailable", "paid_plan_needed", "sign_in_needed", "no_work", "needs_user"})
# 2026-11-18: Vibe Prospecting, Crustdata and Apollo people search need credits or a paid plan; the owner pays nothing.
_FREE_LOOKUPS = ("sprouts", "clay")
# 2026-11-18 22:56 run (Kits orders, 01:10): a failed people mark backs the duty off two hours, and that cost a
# whole hour of search. A site or tool the run could not reach leaves the others: recorded partial, never failed.
_TOOL_NOT_REACHED = frozenset({"network_blocked", "rate_limited"})
NO_REVIEW_NOTE = (
    "Recorded as partial (no_review): a people run is done only after the Doctor check (get_recipe(\"doctor\")) and "
    "a separate reviewer checked every person and note. Next run: do both, then mark_done with reviewed=true."
)


def _run_since(stamps: dict[str, datetime], duty: str, now: datetime) -> datetime:
    """When this people run started: its live claim, else ``DUTY_CLAIM_MINUTES`` ago."""
    window = timedelta(minutes=settings.DUTY_CLAIM_MINUTES)
    since = stamps.get(schedule.RUNNING + duty)
    return now - window if since is None or now - since >= window else since


async def _people_score(db: JobDatabase, user: CurrentUser, duty: str, ids: list[int], now: datetime) -> PeopleScoreOut:
    """Job360's count (rule #12: scoped by user). reach: everyone saved at each listed job's COMPANY (on that job,
    another job there, or no job), whoever saved them and when, each person once (2026-11-18: Robert Walters 954/955
    and an existing YAL contact were not counted). network: new people with no job this assistant saved since the
    run took the duty."""
    target = settings.NETWORK_MAX_PER_RUN if duty == "network" else settings.REACH_TARGET_PER_JOB
    score = PeopleScoreOut(target=target, minimum=target if duty == "network" else settings.REACH_MIN_PER_JOB)
    if duty == "reach":
        from src.services.applications import contacts  # noqa: PLC0415 - profile <-> spine cycle

        cur = await db._db.execute(
            "SELECT id, job_company FROM applications "  # noqa: S608 - placeholders only
            f"WHERE user_id = ? AND id IN ({', '.join('?' for _ in ids)})",
            (user.id, *ids),
        )
        job_key = {
            int(r["id"]): contacts.company_key(str(r["job_company"] or "")) for r in map(dict, await cur.fetchall())
        }
        cur = await db._db.execute(
            "SELECT ac.application_id, ac.name, ac.linkedin_url, ac.company, a.job_company "
            "FROM application_contacts ac "
            "LEFT JOIN applications a ON a.id = ac.application_id WHERE ac.user_id = ?",
            (user.id,),
        )
        people: dict[str, set[str]] = {}
        # 2026-11-19 04:07 run: two recruiters saved ON agency job 1110 with company "David Joseph & Company" scored 0,
        # because the job's company read "David Joseph & Company (recruiting for ...)". Everyone saved on a job
        # counts for it, as well as everyone at its company.
        on_job: dict[int, set[str]] = {}
        for row in map(dict, await cur.fetchall()):
            who = (contacts.linkedin_identity_key(str(row["linkedin_url"] or ""))
                   or contacts.name_identity_key(str(row["name"] or "")))
            if row["application_id"] is not None:
                on_job.setdefault(int(row["application_id"]), set()).add(who)
            key = contacts.contact_company_key(row)
            if key:
                people.setdefault(key, set()).add(who)
        per_job = {}
        for i in ids:
            key = job_key.get(i, "")
            per_job[str(i)] = len((people.get(key, set()) if key else set()) | on_job.get(i, set()))
        score.per_job = per_job
        return score
    since = _run_since(await schedule.load_stamps(db, user.id), duty, now)
    cur = await db._db.execute(
        "SELECT created_at FROM application_contacts WHERE user_id = ? AND application_id IS NULL "
        "AND added_by <> 'web'",
        (user.id,),
    )
    n = 0
    for row in await cur.fetchall():
        try:
            at = datetime.fromisoformat(str(dict(row)["created_at"]))
        except ValueError:
            continue
        n += (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) >= since
    score.network = n
    return score


def _refuse_unsearched_jobs(user: CurrentUser, score: PeopleScoreOut, searched: dict[str, dict[str, int]]) -> None:
    """2026-11-18 22:07 run: five ids listed, one company searched, Sprouts and Clay never called for the other four.
    Each job left under the minimum carries in ``searched`` what each free lookup found at its company; Job360 only
    checks that the record is there and logs its counts, never whether the run tried hard enough."""
    short = [i for i, n in score.per_job.items() if n < _job_minimum(score, i)]
    missing = [i for i in short if i not in searched]
    unsaid: dict[str, list[str]] = {}
    for i in (i for i in short if i in searched):
        _log("people_search_counts", user, job=i, sources=",".join(f"{k}={v}" for k, v in searched[i].items()))
        names = [n for n in _FREE_LOOKUPS if n not in {k.lower() for k in searched[i]}]
        if names:
            unsaid[i] = names
    if not missing and not unsaid:
        return
    _log("people_run_refused", user, duty="reach", reason="jobs_unsearched",
         jobs=",".join(missing + list(unsaid)))
    parts = [f"no searched entry: {', '.join(missing)}"] if missing else []
    parts += [f"{i}: {', '.join(names)} missing" for i, names in unsaid.items()]
    raise HTTPException(
        status_code=409,
        detail=(
            f"Jobs under {score.minimum} people need what each source found at their company; {'; '.join(parts)}. "
            "Work each company now (step 1: Sprouts and Clay with its domain, then its pages and a web search), "
            'then mark_done again with searched {"<application id>": {"sprouts": n, "clay": n, "<source>": n}}. '
            "An agency job (the company is a recruitment agency, or the ad names no employer) is full with its named "
            "recruiter: list it in agency."
        ),
    )


def _below_target(score: PeopleScoreOut) -> list[str]:
    """Each job under the minimum ("<id>: n of 5"), or the network run under its target."""
    if score.network is not None:
        return [f"network: {score.network} of {score.target}"] if score.network < score.minimum else []
    return [f"{i}: {n} of {AGENCY_TARGET if i in score.agency else score.target}"
            for i, n in score.per_job.items() if n < _job_minimum(score, i)]


async def _refuse_unfinished_people(
    db: JobDatabase, user: CurrentUser, duty: str, ids: list[int], now: datetime
) -> int:
    """A people run (reach or network) ends only when every person it saved has an outreach draft AND a stored
    ``outreach_reviewed`` (the Doctor, a fresh reviewer: 2026-11-18, three runs said "self-review only"). The people
    this assistant saved since the run took the duty (or in the last ``DUTY_CLAIM_MINUTES``) that miss either are
    listed in a 409 that says what to do next (rule #12: scoped by user). Returns how many people it checked."""
    since = _run_since(await schedule.load_stamps(db, user.id), duty, now)
    scope, args = ("ac.application_id IS NULL", ()) if duty == "network" else (
        f"ac.application_id IN ({', '.join('?' for _ in ids)})", tuple(ids)
    )
    cur = await db._db.execute(
        "SELECT ac.id, ac.name, ac.created_at, "  # noqa: S608 - placeholders only
        "EXISTS (SELECT 1 FROM contact_outreach o WHERE o.contact_id = ac.id AND o.entry = 'message') AS drafted, "
        "EXISTS (SELECT 1 FROM contact_outreach o WHERE o.contact_id = ac.id AND o.entry = 'reviewed') AS reviewed "
        f"FROM application_contacts ac WHERE ac.user_id = ? AND ac.added_by <> 'web' AND {scope} ORDER BY ac.id",
        (user.id, *args),
    )
    saved = []
    for row in await cur.fetchall():
        r = dict(row)
        try:
            at = datetime.fromisoformat(str(r["created_at"]))
        except ValueError:
            continue
        if (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) >= since:
            saved.append(r)
    undrafted = [r for r in saved if not r["drafted"]]
    unreviewed = [r for r in saved if r["drafted"] and not r["reviewed"]]
    if undrafted:
        _log("people_run_refused", user, duty=duty, reason="no_draft", contacts=str(len(undrafted)))
        first = undrafted[0]
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(undrafted)} people saved this run have no outreach note; next: contact {first['id']} "
                f"({first['name']}). Write one note per person (get_recipe(\"write\"); a LinkedIn connection note "
                "of 200 characters or fewer), save it with save_artifact (kind outreach, contact_id, channel "
                "linkedin), have the Doctor review it, call check_outreach, then mark_done again. "
                f"Contact ids: {', '.join(str(r['id']) for r in undrafted[:10])}."
            ),
        )
    if unreviewed:
        _log("people_run_refused", user, duty=duty, reason="no_review", contacts=str(len(unreviewed)))
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(unreviewed)} notes saved this run have no Doctor review. get_recipe(\"doctor\"), row Outreach "
                "review: hand ALL of them to ONE fresh helper agent (the Agent tool, read only; never yourself), then "
                "record_event outreach_reviewed for each (contact_id, review.message_id, review.sha256, confidence, "
                "lines, reviewer), then mark_done again. "
                f"Contact ids: {', '.join(str(r['id']) for r in unreviewed[:10])}."
            ),
        )
    return len(saved)


async def _refuse_left_out_jobs(db: JobDatabase, user: CurrentUser, ids: list[int], now: datetime) -> None:
    """2026-11-18 13:56 run: whats_due listed 5 applications, the run worked one and marked only that one, so
    Job360 judged one job. Every application still due for reach that was brought before this run took the duty
    must be in ``application_ids``: worked, or listed as tried (its count then shows below_target)."""
    since = _run_since(await schedule.load_stamps(db, user.id), "reach", now)
    due = (await schedule.load_facts(db, user.id, now)).reach_ids
    left = [a for a in due if a not in ids]
    if not left:
        return
    cur = await db._db.execute(
        f"SELECT id, created_at FROM applications WHERE user_id = ? AND id IN ({', '.join('?' for _ in left)})",  # noqa: S608
        (user.id, *left),
    )
    older = []
    for row in await cur.fetchall():
        r = dict(row)
        try:
            at = datetime.fromisoformat(str(r["created_at"]))
        except ValueError:
            continue
        if (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) < since:
            older.append(int(r["id"]))
    if not older:
        return
    _log("people_run_refused", user, duty="reach", reason="jobs_left_out", jobs=",".join(str(i) for i in older))
    raise HTTPException(
        status_code=409,
        detail=(
            f"{len(older)} applications this run was given are not in application_ids: {', '.join(map(str, older))}. "
            "Work each one next, in turn (step 1 for its company, until 5 or every source is tried), then mark_done "
            "with all of them; a job that truly found nobody is still listed (Job360 counts it)."
        ),
    )


# 2026-11-17 hunt run (the first on recipe hunt.md): it never used web search, connectors or a browser and said
# nothing about them, and read 52 of its 370 new kit ads in full. Only Job360's reply is obeyed, so an
# assistant's hunt ends with a report that names every source and the kit funnel.
HUNT_SOURCES = ("mail", "kit", "connectors", "web", "browser")
# 2026-11-18 hunt run: `skipped` said "not run: WebSearch/TinyFish/Apify not called this run" and was accepted.
# A source is skipped only for one of these (closed set); `detail` is the exact error (never logged).
HUNT_SKIP_CODES = ("tool_missing", "tool_error", "no_results", "paid_needs_yes", "login_wall")
HUNT_REPORT_SHAPE = (
    'report: {"sources": {"mail": S, "kit": S, "connectors": S, "web": S, "browser": S}, "kit": {"results": N, '
    '"new": N, "prefilter_passed": N, "read_in_full": N, "unreadable": N, "snippet_only": N}}, each S = '
    '{calls, ads, brought} or {skipped: {code, detail}} with code one of ' + " | ".join(HUNT_SKIP_CODES)
    + " and detail the exact error"
)
# Word runs that mean "I did not try it": an excuse, not a reason (matched on words, never a regex).
_SKIP_EXCUSES = (
    ("not", "run"), ("not", "called"), ("not", "used"), ("not", "tried"), ("not", "needed"), ("not", "attempted"),
    ("no", "time"), ("out", "of", "time"), ("did", "not", "run"), ("did", "not", "call"),
    ("did", "not", "use"), ("didn", "t", "run"), ("didn", "t", "call"), ("didn", "t", "use"),
    ("wasn", "t", "run"), ("wasn", "t", "called"), ("wasn", "t", "used"),
)


def _words(text: str) -> list[str]:
    """Lower-case letter/digit runs of ``text`` (a character scan; no regex on caller text)."""
    return "".join(ch if ch.isalnum() else " " for ch in text.casefold()).split()


def _is_excuse(text: str) -> bool:
    words = _words(text)
    for run in _SKIP_EXCUSES:
        n = len(run)
        if any(tuple(words[i:i + n]) == run for i in range(len(words) - n + 1)):
            return True
    return False


def _skip_problem(skipped: Union[HuntSkipIn, str, None]) -> Optional[str]:
    """The refusal code for a source's ``skipped`` (None = an honest skip, or no skip)."""
    if skipped is None:
        return None
    if isinstance(skipped, str):
        return "skip_free_text"
    if skipped.code not in HUNT_SKIP_CODES:
        return "skip_code"
    if not (skipped.detail or "").strip():
        return "skip_detail"
    if _is_excuse(skipped.detail or ""):
        return "skip_excuse"
    return None


_SKIP_FIX = {
    "skip_free_text": "send skipped as {code, detail}, never free text alone",
    "skip_code": "skipped.code must be one of " + ", ".join(HUNT_SKIP_CODES),
    "skip_detail": "skipped.detail must be the exact error or what the tool said",
    "skip_excuse": "\"not run\", \"not called\" or \"no time\" is not a reason: run that source now (start it "
                   "while the kit runs), then report its counts",
}


def _source_state(src: HuntSourceIn) -> Optional[str]:
    counted = None not in (src.calls, src.ads, src.brought)
    if counted and src.skipped is None:
        return "ran"
    if src.skipped is not None and src.calls is None and src.ads is None and src.brought is None:
        if isinstance(src.skipped, str) and not src.skipped.strip():
            return None
        return "skipped"
    return None


def _check_hunt_report(user: CurrentUser, report: Optional[HuntReportIn]) -> None:
    """Refuse (422, saying exactly what is missing) an assistant's hunt report that is short; log the counts."""
    if report is None:
        _log("hunt_report_refused", user, codes="report_missing", missing="report")
        raise HTTPException(
            status_code=422, detail=f"A hunt run ends with a {HUNT_REPORT_SHAPE}. Nothing was recorded."
        )
    codes: list[str] = []
    problems: list[str] = []
    missing = [k for k in HUNT_SOURCES if k not in report.sources]
    if missing:
        codes.append("source_missing")
        problems.append(f"sources {', '.join(missing)} are missing: use each one, or send it as "
                        "{skipped: {code, detail}}")
    unknown = sorted(k for k in report.sources if k not in HUNT_SOURCES)
    if unknown:
        codes.append("source_unknown")
        # Never echo the caller's key text (the 422 detail is logged): a count and the closed set only.
        problems.append(f"{len(unknown)} unknown source keys: count them under one of {', '.join(HUNT_SOURCES)}")
    states = {k: _source_state(v) for k, v in report.sources.items() if k in HUNT_SOURCES}
    unclear = [k for k in HUNT_SOURCES if k in states and states[k] is None]
    if unclear:
        codes.append("source_unclear")
        problems.append(f"sources {', '.join(unclear)} need all of calls, ads, brought, or only skipped")
    # One code per kind of bad skip, each naming its sources (closed names only; never the caller's text).
    bad_skips: dict[str, list[str]] = {}
    for k in HUNT_SOURCES:
        if states.get(k) == "skipped":
            why = _skip_problem(report.sources[k].skipped)
            if why:
                bad_skips.setdefault(why, []).append(k)
    for why, names in bad_skips.items():
        codes.append(why)
        problems.append(f"sources {', '.join(names)}: {_SKIP_FIX[why]}")
    kit = report.kit
    if kit is None and states.get("kit") == "ran":
        codes.append("kit_missing")
        problems.append("kit: {results, new, prefilter_passed, read_in_full, unreadable, snippet_only} is missing")
    if kit is not None and (kit.unreadable is None or kit.snippet_only is None):
        codes.append("kit_unaccounted")
        problems.append("kit.unreadable and kit.snippet_only are required (0 when none): unreadable = ads no page "
                        "would open after Job360's browser, TinyFish and Apify; snippet_only = ads judged on a "
                        "snippet, not the full ad")
    elif kit is not None and not (kit.read_in_full <= kit.prefilter_passed <= kit.new <= kit.results):
        codes.append("kit_counts")
        problems.append("kit counts must keep read_in_full <= prefilter_passed <= new <= results")
    elif kit is not None and kit.unreadable is not None and kit.snippet_only is not None:
        total = kit.read_in_full + kit.unreadable + kit.snippet_only
        if total != kit.prefilter_passed:
            codes.append("kit_unread")
            problems.append(
                f"you read {kit.read_in_full} of {kit.prefilter_passed} kit ads that passed the pre-filter in full: "
                f"read_in_full + unreadable + snippet_only = {kit.read_in_full} + {kit.unreadable} + "
                f"{kit.snippet_only} = {total}, but prefilter_passed is {kit.prefilter_passed}. Count every ad once "
                "(read the rest in full: batches of at most 40, up to 8 helpers; a 403 page is tried with Job360's "
                "browser, TinyFish, then Apify first); kit.unread_reason never replaces a count"
            )
    if codes:
        _log("hunt_report_refused", user, codes=",".join(codes), missing=",".join(missing))
        raise HTTPException(
            status_code=422, detail=f"Hunt report refused: {'; '.join(problems)}. The full shape: {HUNT_REPORT_SHAPE}. "
                                    "Nothing was recorded; fix it, then mark_done again.",
        )
    skips = ",".join(
        f"{k}:{report.sources[k].skipped.code}" for k in HUNT_SOURCES  # type: ignore[union-attr]
        if states.get(k) == "skipped"
    )
    get_audit_logger().info("hunt_report_ok", extra={
        "event": "hunt_report_ok", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
        "sources": ",".join(f"{k}:{states[k]}" for k in HUNT_SOURCES), "skips": skips or "none", "result": "ok",
        **({f"kit_{k}": getattr(kit, k) for k in ("results", "new", "prefilter_passed", "read_in_full",
                                                  "unreadable", "snippet_only")}
           if kit else {}),
    })


@router.post("/schedule/done", response_model=MarkDoneOut, dependencies=AUTH_FIRST)
async def mark_done(
    body: MarkDoneIn,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> MarkDoneOut:
    """Stamp one duty (hunt | inbox | batch) as done now, and the tick. A slot, never history."""
    _limit(user, "mark", settings.SCHEDULE_MARKS_MAX_PER_HOUR)
    ids = sorted(set(body.application_ids or []))
    if body.duty == "reach" and not ids:
        raise HTTPException(status_code=422, detail="reach needs application_ids: the applications this run worked")
    if body.duty != "reach" and body.application_ids is not None:
        raise HTTPException(status_code=422, detail="application_ids is only for duty reach")
    if body.duty != "hunt" and body.report is not None:
        raise HTTPException(status_code=422, detail="report is only for duty hunt")
    if body.outcome == "failed" and body.reason is None:
        raise HTTPException(status_code=422, detail="a failed run needs a reason (one of the closed list)")
    if ids and await schedule.owned_application_ids(db, user.id, ids) != set(ids):
        raise HTTPException(status_code=404, detail="application not found")
    # A failed hunt (closed reason, backed off, carded) may end before any source ran; a report it sends is checked.
    if body.duty == "hunt" and actor_for(user) != "web" and (body.outcome != "failed" or body.report is not None):
        _check_hunt_report(user, body.report)
    now = datetime.now(timezone.utc)
    # S5 (prod, 4 runs): a run closed a batch it never claimed (a plain whats_due, or none), pushing the next
    # batch back. Only an assistant's whats_due(duties=["batch"]) claims, so only an assistant is refused.
    if body.duty == "batch" and actor_for(user) != "web" and not await schedule.claim_live(db, user.id, "batch", now):
        raise HTTPException(status_code=409, detail=BATCH_NOT_CLAIMED)
    later: list[int] = []
    no_country: list[int] = []
    # The website never claims (None: every queued row counts); an assistant's claim was checked above.
    claimed_at = None
    if body.duty == "batch" and actor_for(user) != "web":
        claimed_at = (await schedule.load_stamps(db, user.id)).get(schedule.RUNNING + "batch")
    # 2026-11-18 (23:41 run): "done" (the default) skipped this check, so a run closed the batch before it opened
    # its one workable row (1087). "done" means nothing is left: it is checked like a partial stop.
    if body.duty == "batch" and (body.outcome == "done" or (
            body.outcome in ("partial", "failed") and body.reason in _NOT_A_BLOCKER)):
        later = await _refuse_early_batch_stop(db, user, claimed_at)
    if body.duty == "batch":
        from src.services.applications import queue_preflight  # noqa: PLC0415 - profile <-> spine cycle

        no_country = await queue_preflight.country_missing_ids(db, user.id, queued_by=claimed_at)
        if no_country:
            _log("batch_country_missing", user, rows=str(len(no_country)), ids=",".join(map(str, no_country[:20])))
    reviewed = body.reviewed
    if body.duty in ("reach", "network") and actor_for(user) != "web":
        if body.outcome == "failed" and body.reason in _ONE_TOOL_DOWN:
            _log("people_run_refused", user, duty=body.duty, reason=body.reason)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{body.reason} is not a failed people run: the jobs were due, and web search, the company's own "
                    "team, about, blog and press pages, GitHub, speaker pages, Sprouts, Clay and Vibe still "
                    "work. Find people with them and save them; when they truly found nobody, mark_done with outcome "
                    "partial and say in note what each source returned."
                ),
            )
        unsaid = [n for n in _FREE_LOOKUPS if n not in (body.note or "").lower()]
        if body.outcome in ("partial", "failed") and unsaid:
            _log("people_run_refused", user, duty=body.duty, reason="lookups_unreported")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A people run that stops short says in note what each free lookup returned; missing: "
                    f"{', '.join(unsaid)}. Load their tools (search your tool list for Sprouts, Clay and Vibe), call "
                    "each once per company with the company's domain, save who they prove, then mark_done again."
                ),
            )
        if body.duty == "reach":
            await _refuse_left_out_jobs(db, user, ids, now)
        # Every person it saved has a stored Doctor review (else 409 above): that, not the flag, is the evidence.
        checked = await _refuse_unfinished_people(db, user, body.duty, ids, now)  # always: the flag proves nothing
        reviewed = reviewed or checked > 0
    if body.reviewed and body.duty not in ("reach", "network"):
        raise HTTPException(status_code=422, detail="reviewed is only for duty reach or network")
    if body.agency is not None and (body.duty != "reach" or set(body.agency) - set(body.application_ids or [])):
        raise HTTPException(status_code=422, detail="agency is only for duty reach, and only ids in application_ids")
    if body.searched is not None and body.duty != "reach":
        raise HTTPException(status_code=422, detail="searched is only for duty reach")
    outcome, reason, said = body.outcome, body.reason, ""
    score: Optional[PeopleScoreOut] = None
    if body.duty in ("reach", "network") and actor_for(user) != "web":
        # Owner 2026-11-18: Job360 judges the count, never the run's own claim; the call is never refused for it.
        score = await _people_score(db, user, body.duty, ids, now)
        score.agency = [str(i) for i in body.agency or [] if i in ids]
        if body.duty == "reach" and outcome != "failed":
            _refuse_unsearched_jobs(user, score, body.searched or {})
        short = _below_target(score)
        _log("people_run_score", user, duty=body.duty, jobs=",".join(f"{i}={n}" for i, n in score.per_job.items()),
             network=str(score.network if score.network is not None else ""), below=str(len(short)))
        if short and outcome != "failed":
            outcome, reason = "partial", "below_target"
            said = (f"Recorded as partial (below_target): {'; '.join(short)}. "
                    f"Minimum {score.minimum}, target {score.target}.")
            if not reviewed:  # 2026-11-18 12:05 run: below_target hid that it also skipped the review
                said += " Also no_review: " + NO_REVIEW_NOTE.split(": ", 1)[1]
    if body.duty in ("reach", "network") and actor_for(user) != "web" and outcome == "failed" and (
            reason in _TOOL_NOT_REACHED):
        outcome = "partial"
        said = (f"Recorded as partial ({reason}): a people run is never failed for one site or tool it could not "
                "reach, so nothing waits two hours; the next hourly run tries again. "
                + said).strip()
        _log("people_run_not_failed", user, duty=body.duty, reason=reason)
    if body.duty in ("reach", "network") and actor_for(user) != "web" and outcome == "done" and not reviewed:
        # 2026-11-18 (09:13 run): the run reviewed its own notes, with no Doctor check and no separate reviewer.
        outcome, reason = "partial", "no_review"
        said = NO_REVIEW_NOTE
        _log("people_run_unreviewed", user, duty=body.duty, reason="no_review")
    await schedule.mark(db, user.id, body.duty, now, ids or None, outcome=outcome, reason=reason or "")
    _log("schedule_duty_done", user, duty=body.duty, outcome=outcome, reason=reason or "",
         next_batch=str(len(later)))
    failures = int((await schedule.load_runs(db, user.id)).get(body.duty, {}).get("failures") or 0)
    if body.duty == "hunt" and body.outcome != "failed":  # Ignition: a finished hunt starts CV review and reach
        from src.services.browser_agent import duty_trigger  # noqa: PLC0415

        # (no CV review link, or its start failed: the batch, as before; the CV run's end starts the batch)
        await duty_trigger.ignite(user.id, ("cv", "reach"), "hunt_done", db=db)
    stamp = now.isoformat(timespec="seconds")
    note = said or _next_batch_note(later)
    if no_country:
        note = " ".join(x for x in (note, _country_note(no_country)) if x)
    return MarkDoneOut(
        duty=body.duty, done_at=stamp, last_tick_at=stamp, outcome=outcome, failures_in_row=failures,
        next_batch=later, country_missing=no_country, note=note, score=score,
    )


def _country_note(ids: list[int]) -> str:
    shown = ", ".join(map(str, ids[:20])) + (f" and {len(ids) - 20} more" if len(ids) > 20 else "")
    return (f"{len(ids)} queued jobs still have no job country: {shown}. Set each from its ad with update_job "
            "(country), or name it under Not submitted if the ad does not say.")


@router.get("/schedule/problems", response_model=RunProblemsOut, dependencies=AUTH_FIRST)
async def run_problems(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> RunProblemsOut:
    """Owner 2026-10-25: one plain card per duty whose scheduled run failed ``FAILURE_CARD_AT`` times in a
    row, saying what happened and what to do (Home and Needs you show them). Read-only; closed values."""
    runs = await schedule.load_runs(db, user.id)
    return RunProblemsOut(problems=[RunProblemOut(**p) for p in schedule.problems(runs)])


@router.get("/schedule/status", response_model=ScheduleStatusOut, dependencies=AUTH_FIRST)
async def schedule_status(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ScheduleStatusOut:
    """Owner 2026-10-25: the Home status card - for each duty, is it on, when it last ran and how that
    ended, and when it runs next. Read-only (takes no duty); closed values only."""
    _limit(user, "read", settings.SCHEDULE_READS_MAX_PER_HOUR)
    now = datetime.now(timezone.utc)
    facts = await schedule.load_facts(db, user.id, now)
    answer = schedule.due_now(facts, now)
    rows = schedule.status(facts, answer, await schedule.load_runs(db, user.id))
    return ScheduleStatusOut(duties=[DutyStatusOut(**r) for r in rows], last_tick_at=answer["last_tick_at"])
