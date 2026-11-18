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
from typing import Literal, Optional

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


class HuntSourceIn(BaseModel):
    """One hunt source: what it did, or why it was not used (the exact reason or error)."""

    model_config = ConfigDict(extra="forbid")
    calls: Optional[int] = Field(default=None, ge=0)
    ads: Optional[int] = Field(default=None, ge=0)
    brought: Optional[int] = Field(default=None, ge=0)
    skipped: Optional[str] = Field(default=None, max_length=300)


class HuntKitIn(BaseModel):
    """The hunt kit's funnel: results, new after the known drop, passing the title/place pre-filter, read in full."""

    model_config = ConfigDict(extra="forbid")
    results: int = Field(ge=0)
    new: int = Field(ge=0)
    prefilter_passed: int = Field(ge=0)
    read_in_full: int = Field(ge=0)
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


class PeopleScoreOut(BaseModel):
    """2026-11-18: Job360's own count for a people run, never the run's claim: people saved at each job, and new
    network people this run, against the target and the minimum."""

    per_job: dict[str, int] = Field(default_factory=dict)
    network: Optional[int] = None
    target: int
    minimum: int


class MarkDoneOut(BaseModel):
    duty: Duty
    done_at: str
    last_tick_at: str
    outcome: Outcome = "done"
    failures_in_row: int = 0
    # 2026-11-17: a batch stop - queued rows queued after this run's claim; they wait for the next batch.
    next_batch: list[int] = Field(default_factory=list)
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
    _log("schedule_due_read", user, due=",".join(d["duty"] for d in answer["due"]) or "none")
    return WhatsDueOut(**answer)


# 2026-10-30 (17:42 run): refused needs_user, the run stopped with partial/error instead; the 20:26 run
# then stopped with failed/needs_user. A partial or failed batch stop with no outside blocker (rate
# limit, network, sign-in, plan, tool) is the same early stop.
_NOT_A_BLOCKER = frozenset({None, "needs_user", "error", "no_work"})
BATCH_NOT_CLAIMED = 'You did not claim the batch: call whats_due(duties=["batch"]) first. Nothing was recorded.'


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
    for the next batch and are returned."""
    from src.services.applications import queue_preflight  # noqa: PLC0415 - profile <-> spine cycle

    later = await queue_preflight.queued_after_claim(db, user.id, claimed_at)
    note = (" " + _next_batch_note(later)) if later else ""
    rows = await queue_preflight.fillable_rows(db, user.id, queued_by=claimed_at)
    if not rows:
        # 2026-11-01 (31 Oct runs): every run asked one question, then stopped while 22 queued jobs had no
        # CV. A missing CV needs no owner input, whatever else the row waits on.
        cvs = await queue_preflight.cv_rows(db, user.id, queued_by=claimed_at)
        if not cvs:
            return later
        _log("batch_stop_refused", user, reason="cv_missing", rows=str(len(cvs)), later=str(len(later)))
        first = cvs[0]
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(cvs)} queued jobs still have no CV; next: application {first['id']} "
                f"({first['company'] or 'unknown company'}). Keep working: write its tailored CV and save it "
                "with save_artifact kind cv. An open question, a site block or the user's own browser does not "
                "stop CV writing; questions to the user are not a reason to stop. "
                f"Ids: {', '.join(str(r['id']) for r in cvs[:5])}.{note}"
            ),
        )
    _log("batch_stop_refused", user, reason="needs_no_owner_input", rows=str(len(rows)), later=str(len(later)))
    first = rows[0]
    raise HTTPException(
        status_code=409,
        detail=(
            f"{len(rows)} queued jobs still need no owner input; next: application {first['id']} "
            f"({first['company'] or 'unknown company'}). Keep working: write its CV and fill its form. "
            "A partial run ends only when every queued job is waiting on the owner or blocked. Large tool "
            "outputs and outputs saved to a file are normal; they are not a reason to stop. "
            f"Ids: {', '.join(str(r['id']) for r in rows[:5])}.{note}"
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
    """Job360's count (rule #12: scoped by user). reach: everyone saved at each listed job, whoever saved them and
    when. network: new people with no job this assistant saved since the run took the duty."""
    target = settings.NETWORK_MAX_PER_RUN if duty == "network" else settings.REACH_TARGET_PER_JOB
    score = PeopleScoreOut(target=target, minimum=target if duty == "network" else settings.REACH_MIN_PER_JOB)
    if duty == "reach":
        cur = await db._db.execute(
            "SELECT application_id, COUNT(*) AS n FROM application_contacts "  # noqa: S608 - placeholders only
            f"WHERE user_id = ? AND application_id IN ({', '.join('?' for _ in ids)}) GROUP BY application_id",
            (user.id, *ids),
        )
        got = {int(dict(r)["application_id"]): int(dict(r)["n"]) for r in await cur.fetchall()}
        score.per_job = {str(i): got.get(i, 0) for i in ids}
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


def _below_target(score: PeopleScoreOut) -> list[str]:
    """Each job under the minimum ("<id>: n of 5"), or the network run under its target."""
    if score.network is not None:
        return [f"network: {score.network} of {score.target}"] if score.network < score.minimum else []
    return [f"{i}: {n} of {score.target}" for i, n in score.per_job.items() if n < score.minimum]


async def _refuse_undrafted_people(
    db: JobDatabase, user: CurrentUser, duty: str, ids: list[int], now: datetime
) -> None:
    """A people run (reach or network) ends only when every person it saved has an outreach draft. The people
    this assistant saved since the run took the duty (or in the last ``DUTY_CLAIM_MINUTES``) with no
    ``message`` row are listed in a 409 that says what to do next (rule #12: scoped by user)."""
    since = _run_since(await schedule.load_stamps(db, user.id), duty, now)
    scope, args = ("ac.application_id IS NULL", ()) if duty == "network" else (
        f"ac.application_id IN ({', '.join('?' for _ in ids)})", tuple(ids)
    )
    cur = await db._db.execute(
        "SELECT ac.id, ac.name, ac.created_at FROM application_contacts ac "  # noqa: S608 - placeholders only
        f"WHERE ac.user_id = ? AND ac.added_by <> 'web' AND {scope} "
        "AND NOT EXISTS (SELECT 1 FROM contact_outreach o WHERE o.contact_id = ac.id "
        "AND o.entry = 'message') ORDER BY ac.id",
        (user.id, *args),
    )
    missing = []
    for row in await cur.fetchall():
        r = dict(row)
        try:
            at = datetime.fromisoformat(str(r["created_at"]))
        except ValueError:
            continue
        if (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) >= since:
            missing.append(r)
    if not missing:
        return
    _log("people_run_refused", user, duty=duty, reason="no_draft", contacts=str(len(missing)))
    first = missing[0]
    raise HTTPException(
        status_code=409,
        detail=(
            f"{len(missing)} people saved this run have no outreach note; next: contact {first['id']} "
            f"({first['name']}). Write one note per person (get_recipe(\"write\"); a LinkedIn connection note of "
            "200 characters or fewer), have the Doctor check it (get_recipe(\"doctor\")), save it with save_artifact "
            "(kind outreach, contact_id, channel linkedin), call check_outreach, then mark_done again. "
            f"Contact ids: {', '.join(str(r['id']) for r in missing[:10])}."
        ),
    )


# 2026-11-17 hunt run (the first on recipe hunt.md): it never used web search, connectors or a browser and said
# nothing about them, and read 52 of its 370 new kit ads in full. Only Job360's reply is obeyed, so an
# assistant's hunt ends with a report that names every source and the kit funnel.
HUNT_SOURCES = ("mail", "kit", "connectors", "web", "browser")
HUNT_REPORT_SHAPE = (
    'report: {"sources": {"mail": S, "kit": S, "connectors": S, "web": S, "browser": S}, "kit": {"results": N, '
    '"new": N, "prefilter_passed": N, "read_in_full": N}}, each S = {calls, ads, brought} or '
    '{skipped: "<exact reason or error>"}'
)


def _source_state(src: HuntSourceIn) -> Optional[str]:
    counted = None not in (src.calls, src.ads, src.brought)
    said = bool((src.skipped or "").strip())
    if counted and src.skipped is None:
        return "ran"
    if said and src.calls is None and src.ads is None and src.brought is None:
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
                        '{skipped: "<exact reason or error>"}')
    unknown = sorted(k for k in report.sources if k not in HUNT_SOURCES)
    if unknown:
        codes.append("source_unknown")
        # Never echo the caller's key text (the 422 detail is logged): a count and the closed set only.
        problems.append(f"{len(unknown)} unknown source keys: count them under one of {', '.join(HUNT_SOURCES)}")
    states = {k: _source_state(v) for k, v in report.sources.items() if k in HUNT_SOURCES}
    unclear = [k for k in HUNT_SOURCES if k in states and states[k] is None]
    if unclear:
        codes.append("source_unclear")
        problems.append(f"sources {', '.join(unclear)} need all of calls, ads, brought, or only a non-empty skipped")
    kit = report.kit
    if kit is None and states.get("kit") == "ran":
        codes.append("kit_missing")
        problems.append("kit: {results, new, prefilter_passed, read_in_full} is missing")
    if kit is not None and not (kit.read_in_full <= kit.prefilter_passed <= kit.new <= kit.results):
        codes.append("kit_counts")
        problems.append("kit counts must keep read_in_full <= prefilter_passed <= new <= results")
    elif kit is not None and kit.read_in_full < kit.prefilter_passed and not (kit.unread_reason or "").strip():
        codes.append("kit_unread")
        problems.append(f"you read {kit.read_in_full} of {kit.prefilter_passed} kit ads that passed the pre-filter: "
                        "read every one in full (batches of at most 40, up to 8 helpers), or say why in "
                        "kit.unread_reason")
    if codes:
        _log("hunt_report_refused", user, codes=",".join(codes), missing=",".join(missing))
        raise HTTPException(
            status_code=422, detail=f"Hunt report refused: {'; '.join(problems)}. The full shape: {HUNT_REPORT_SHAPE}. "
                                    "Nothing was recorded; fix it, then mark_done again.",
        )
    get_audit_logger().info("hunt_report_ok", extra={
        "event": "hunt_report_ok", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
        "sources": ",".join(f"{k}:{states[k]}" for k in HUNT_SOURCES), "result": "ok",
        **({f"kit_{k}": getattr(kit, k) for k in ("results", "new", "prefilter_passed", "read_in_full")}
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
    if body.duty == "batch" and body.outcome in ("partial", "failed") and body.reason in _NOT_A_BLOCKER:
        # The website never claims (None: every queued row counts); an assistant's claim was checked above.
        claimed_at = None
        if actor_for(user) != "web":
            claimed_at = (await schedule.load_stamps(db, user.id)).get(schedule.RUNNING + "batch")
        later = await _refuse_early_batch_stop(db, user, claimed_at)
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
        await _refuse_undrafted_people(db, user, body.duty, ids, now)
    if body.reviewed and body.duty not in ("reach", "network"):
        raise HTTPException(status_code=422, detail="reviewed is only for duty reach or network")
    outcome, reason, said = body.outcome, body.reason, ""
    score: Optional[PeopleScoreOut] = None
    if body.duty in ("reach", "network") and actor_for(user) != "web":
        # Owner 2026-11-18: Job360 judges the count, never the run's own claim; the call is never refused for it.
        score = await _people_score(db, user, body.duty, ids, now)
        short = _below_target(score)
        _log("people_run_score", user, duty=body.duty, jobs=",".join(f"{i}={n}" for i, n in score.per_job.items()),
             network=str(score.network if score.network is not None else ""), below=str(len(short)))
        if short and outcome != "failed":
            outcome, reason = "partial", "below_target"
            said = (f"Recorded as partial (below_target): {'; '.join(short)}. "
                    f"Minimum {score.minimum}, target {score.target}.")
    if body.duty in ("reach", "network") and actor_for(user) != "web" and outcome == "done" and not body.reviewed:
        # 2026-11-18 (09:13 run): the run reviewed its own notes, with no Doctor check and no separate reviewer.
        outcome, reason = "partial", "no_review"
        said = NO_REVIEW_NOTE
        _log("people_run_unreviewed", user, duty=body.duty, reason="no_review")
    await schedule.mark(db, user.id, body.duty, now, ids or None, outcome=outcome, reason=reason or "")
    _log("schedule_duty_done", user, duty=body.duty, outcome=outcome, reason=reason or "",
         next_batch=str(len(later)))
    failures = int((await schedule.load_runs(db, user.id)).get(body.duty, {}).get("failures") or 0)
    if body.duty == "hunt" and body.outcome != "failed":  # Ignition: a finished hunt starts reach and the batch
        from src.services.browser_agent import duty_trigger  # noqa: PLC0415

        await duty_trigger.ignite(user.id, ("reach", "batch"), "hunt_done", db=db)
    stamp = now.isoformat(timespec="seconds")
    return MarkDoneOut(
        duty=body.duty, done_at=stamp, last_tick_at=stamp, outcome=outcome, failures_in_row=failures,
        next_batch=later, note=said or _next_batch_note(later), score=score,
    )


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
