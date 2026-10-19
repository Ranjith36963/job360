"""Bring-a-job: the user pastes the ad, we do everything after the click.

Product rule 4 (`docs/product/VISION.md`): Job360 never sources, ranks or
judges a job. The user's own agent finds the ad and decides whether it fits;
this route is the front door where that ad becomes something we REMEMBER.

Since slice 5 (#483) it stores and nothing else: no score, no shelf gate, no
feed row. `POST /jobs/bring` writes the `jobs` row and births the Application
(`services/applications/spine.py`) — the agent records its own verdict
afterwards with `save_fit`.

Why paste is still the fallback (docs/plans/2026-09-04-url-fetch/spec.md):
LinkedIn/Indeed/Workday refuse bots, so `POST /jobs/fetch-url` below fills the
form for the boards that cooperate (JSON-LD job postings — Greenhouse, Lever,
Ashby, Workday, SmartRecruiters, Workable, most company careers pages) and
says one clear sentence for the ones that don't. It never replaces paste: a
fetch pre-fills the form, a human always presses the submit button, and a
failed fetch always lands the user in the paste box with the link kept
(intent constraint 4). We do NOT fetch `robots.txt` — this is a user-agent
action on one URL the user is already reading, not crawling (intent's open
questions); a future crawler would need its own robots decision. The link is
kept as `apply_url` so the receipt can point back.

Storage follows rule #10: the ad goes into the SHARED `jobs` catalog under
`source='user_brought'` (no user_id on `jobs`); "this user is tracking it"
lives in `applications`. Two users pasting the same (company, title) share one
catalog row — `insert_job` is INSERT-OR-IGNORE on the normalized key.

P0 2026-10-14 (bring isolation): the catalog row is a KEY, never the text a
user reads. What a user pasted (title, company, location, apply link, ad text)
is copied onto THEIR application snapshot from THEIR request body, and every
per-user read (`GET /applications/job/{job_id}`, `get_job`, the kit, tailor
provenance, receipts) reads that snapshot — so one user's paste is never
served to another. `existing=True` means "YOU already had this job", never
"someone else brought it". A same-user re-bring of a clearly different posting
under the same title+company (different job link, location or country) is a
409 naming what differs — never a silent overwrite of the application.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.api.auth_deps import AUTH_FIRST, CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.models import JobResponse
from src.core import settings
from src.core.settings import USER_BROUGHT_SOURCE
from src.repositories.database import JobDatabase
from src.services.fetch import outcomes
from src.utils.logger import get_audit_logger, get_logger, safe_log_value

router = APIRouter(tags=["bring"])
logger = get_logger(__name__)


def job_row_to_response(row: dict[str, Any]) -> JobResponse:
    """Build a `JobResponse` straight off a `jobs` row.

    Was `routes/jobs.py::_row_to_job_response`, which merged the row with a
    per-user feed score and an enrichment LEFT JOIN. Both are gone; what is
    left is a plain projection of the stored ad, which is the whole contract
    now (product rule 4 — nothing here is computed about the user).

    Lives here, not on `JobResponse`, because `models.py` is the API's data
    shapes and knows nothing about DB row keys.
    """
    salary = None
    smin, smax = row.get("salary_min"), row.get("salary_max")
    if smin and smax:
        salary = f"{int(smin)}-{int(smax)}"
    elif smin:
        salary = str(int(smin))
    elif smax:
        salary = str(int(smax))

    return JobResponse(
        id=row.get("id", 0),
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", "") or "",
        salary=salary,
        source=row.get("source", ""),
        date_found=row.get("date_found", "") or "",
        apply_url=row.get("apply_url", "") or "",
        experience_level=row.get("experience_level", "") or "",
        description=row.get("description") or None,
        posted_at=row.get("posted_at"),
        first_seen_at=row.get("first_seen_at"),
        last_seen_at=row.get("last_seen_at"),
        date_confidence=row.get("date_confidence"),
        deadline=row.get("deadline"),
        deadline_source=row.get("deadline_source"),
    )


def application_job_response(app_row: dict[str, Any], catalog_row: Optional[dict[str, Any]]) -> JobResponse:
    """The ad as THIS user brought it: their application snapshot, never the
    shared catalog row's pasted fields (P0 2026-10-14 — the catalog row holds
    whichever user pasted first).

    From the snapshot: title, company, location, apply link, ad text, source
    and the per-user dates. From the catalog only what no paste ever writes
    (salary, experience level, deadline, date confidence) — plus title/company
    when an old snapshot left them blank, which is safe because they ARE the
    catalog key every sharer typed. Description, link and location never fall
    back to the catalog.
    """
    cat = dict(catalog_row or {})
    base: dict[str, Any] = job_row_to_response(cat).model_dump() if cat else {"salary": None}
    when = str(app_row.get("created_at") or app_row.get("snapshot_at") or "")
    base.update(
        id=int(app_row.get("job_id") or cat.get("id") or 0),
        title=app_row.get("job_title") or cat.get("title") or "",
        company=app_row.get("job_company") or cat.get("company") or "",
        location=app_row.get("job_location") or "",
        apply_url=app_row.get("job_url") or "",
        description=app_row.get("job_description_snapshot") or None,
        source=app_row.get("job_source") or cat.get("source") or "",
        date_found=when,
        posted_at=when or None,
        first_seen_at=when or None,
        last_seen_at=app_row.get("snapshot_at") or when or None,
    )
    return JobResponse(**base)


def _loose_location(value: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", (value or "").casefold()).split())


def _posting_link(url: str) -> str:
    """Host + path of the ad link (tracking params, ``www.``, a trailing slash
    and the fragment ignored), plus only the query keys that NAME a job
    (``jk``, ``gh_jid``…). '' for a blank or unparseable link."""
    from urllib.parse import parse_qsl  # noqa: PLC0415

    from src.services.applications.job_checks import _JOB_ID_KEYS, canonical_url  # noqa: PLC0415

    canon = canonical_url(url)
    if not canon:
        return ""
    parts = urlsplit(canon)
    query = parse_qsl(parts.query, keep_blank_values=True)
    keep = sorted((k.lower(), v) for k, v in query if k.lower() in _JOB_ID_KEYS)
    return f"{parts.netloc}{parts.path}" + ("?" + "&".join(f"{k}={v}" for k, v in keep) if keep else "")


def posting_differences(
    app_row: dict[str, Any], body: BringJobRequest, country: Optional[str], catalog_url: str = ""
) -> list[str]:
    """What makes this paste a DIFFERENT posting from the one already on the
    caller's application. Only a field both sides actually state can differ
    (rule #29 — blank is "not said", never a conflict). Location is compared
    loosely: "London" and "London, UK" are the same place. ``catalog_url`` is
    the job row's own link: after update_job replaced a dead link with the
    employer's, re-bringing the original ad is still the same posting."""
    diffs: list[str] = []
    have_link, want_link = _posting_link(str(app_row.get("job_url") or "")), _posting_link(body.apply_url)
    if have_link and want_link and have_link != want_link and want_link != _posting_link(catalog_url):
        diffs.append("apply_url")
    have_loc, want_loc = _loose_location(str(app_row.get("job_location") or "")), _loose_location(body.location)
    if have_loc and want_loc and have_loc not in want_loc and want_loc not in have_loc:
        diffs.append("location")
    have_country = str(app_row.get("job_country") or "").upper()
    if have_country and country and have_country != country:
        diffs.append("country")
    return diffs

# Bounds are guardrails, not product limits: a real ad is 500–8,000 chars; the
# cap stops a pasted PDF dump (or a hostile 50 MB body) from becoming a
# catalog row that every later read has to carry.
# C4 — moved to src/core/settings.py so src/services/fetch/extract.py (a
# SERVICE, no FastAPI) can read the same caps without importing this route
# module. Aliased under the old names here so nothing else in this file
# needs to change.
_MAX_TEXT = settings.BRING_MAX_TEXT
_MAX_FIELD = settings.BRING_MAX_FIELD


class BringJobRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=_MAX_FIELD)
    company: str = Field(..., min_length=1, max_length=_MAX_FIELD)
    description: str = Field(..., min_length=1, max_length=_MAX_TEXT)
    location: str = Field("", max_length=_MAX_FIELD)
    apply_url: str = Field("", max_length=2_000)
    # Slice 7 (#514) — the agent's reading of the ad, stored not judged
    # (docs/plans/2026-09-11-visa-signal/spec.md). Omitted = 'unknown' = the
    # ad said nothing; the closed set / alpha-2 / length rules are checked in
    # services/applications/visa.py so every door validates the same way.
    visa_signal: Optional[str] = Field(None, max_length=32)
    visa_detail: Optional[str] = Field(None, max_length=4_000)
    visa_country: Optional[str] = Field(None, max_length=8)
    # Owner decision 2026-10-04 — the assistant's reading of the ad, stored
    # per USER on the application (never on the shared `jobs` row, rule #10):
    # ISO alpha-2 `country`, `remote`, and `found_on` (closed set
    # JOB_FOUND_ON). Omitted = not said (rule #29); a re-bring sets only what
    # it sends and never clears an earlier value. Rules live in
    # services/applications/job_facts.py so every door validates the same way.
    country: Optional[str] = Field(None, max_length=8)
    remote: Optional[bool] = Field(None, strict=True)
    found_on: Optional[str] = Field(None, max_length=40)
    # Ad dates (migration 0054): YYYY-MM-DD the listing says it was posted, and
    # the date on the employer's own posting. Facts the assistant reads off the
    # ad; checked by job_facts.normalize_ad_date (ISO, not in the future).
    posted_at: Optional[str] = Field(None, max_length=10)
    employer_published_at: Optional[str] = Field(None, max_length=10)
    # Owner ask 2026-10-18: where this job came from, shown as a tag on every job - ``ai_hunt`` (your
    # assistant found it), ``user_link`` (the user gave the link), ``user_paste`` (the user pasted the
    # ad). Stored on the ``brought`` event of a NEW application; omitted = not said (rule #29).
    origin: Optional[Literal["ai_hunt", "user_link", "user_paste"]] = None
    # Owner ask 2026-10-18: put it straight into the batch (a ``queued`` event) when it is open.
    queue: bool = Field(False, strict=True)

    @field_validator("title", "company", "location", "apply_url")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("title", "company", "description")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # `min_length` runs on the raw value, so "   " passes it and would be
        # stored as an empty title after the strip above. Check after stripping.
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("apply_url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        # A link we later render as <a href> must be a web link — never
        # javascript:, data:, or a bare path the browser would resolve to us.
        if v and not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("apply_url must start with http:// or https://")
        return v


class BringJobResponse(BaseModel):
    job: JobResponse
    # THIS user already had an application for this job (an exact re-bring).
    # Never "another user brought it" (P0 2026-10-14).
    existing: bool
    # spec 2026-09-04-application-spine R1 — an Application is born HERE.
    application_id: int
    status: str
    # Owner decision 2026-10-04 — the application's CURRENT job facts (null
    # when unset), so the caller sees what was stored.
    country: Optional[str] = None
    remote: Optional[bool] = None
    found_on: Optional[str] = None
    # The two ad dates as stored (null when unset), and the deterministic
    # job checks (services/applications/job_checks.py) - facts about THIS ad.
    ad_posted_at: Optional[str] = None
    employer_published_at: Optional[str] = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    # Slice 5 - set only when this bring birthed a NEW application and the same
    # title at the same company was brought by THIS user earlier under another
    # link (1..90 days ago). Facts only; the user decides.
    repost: Optional[dict[str, Any]] = None
    # ``queue: true`` only: is it in the batch now, and why not when it is not
    # ("not open" = already applied or closed; "already queued" is still true).
    queued: Optional[bool] = None
    queue_note: Optional[str] = None


def _repost_sentence(repost: dict[str, Any]) -> str:
    return (
        f" Among jobs you brought: this title at this company was first brought on {repost['first_seen']} "
        f"({repost['days_since']} days ago) under another link — it may be a repost of the same opening."
    )


async def _job_id_for_same_link(db: JobDatabase, user_id: str, apply_url: str, company: str) -> Optional[int]:
    """The caller's own job at the SAME company whose stored apply link
    canonicalises the same as ``apply_url`` - the re-titled repost the
    title+company key cannot see. Only a job-specific link counts (it carries a
    job id); a careers home page or board root is shared by many jobs and never
    merges two of them. None when nothing matches. Per user, so another user's
    paste is never matched (rule #10 / S2)."""
    from src.services.applications.job_checks import canonical_url, is_job_specific_url  # noqa: PLC0415

    wanted = canonical_url(apply_url)
    if not wanted or not is_job_specific_url(apply_url):
        return None
    want_company = " ".join(company.lower().split())
    # The catalog row's own link counts too: update_job may have replaced a dead
    # aggregator link on the application, and the original ad still names this job.
    cur = await db._db.execute(
        "SELECT a.job_id, a.job_url, a.job_company, j.apply_url AS catalog_url FROM applications a "
        "LEFT JOIN jobs j ON j.id = a.job_id WHERE a.user_id = ? AND a.job_url <> '' ORDER BY a.id",
        (user_id,),
    )
    for r in await cur.fetchall():
        have = " ".join(str(r["job_company"] or "").lower().split())
        # "Parisien" and "Parisien SAS" are the same employer; two blanks never match.
        same_company = bool(have and want_company) and (have.startswith(want_company) or want_company.startswith(have))
        links = {canonical_url(str(r["job_url"] or "")), canonical_url(str(r["catalog_url"] or ""))}
        if same_company and wanted in links:
            return int(r["job_id"])
    return None


@router.post("/jobs/bring", response_model=BringJobResponse, dependencies=AUTH_FIRST)
async def bring_job(
    body: BringJobRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI DI idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> BringJobResponse:
    """Store the pasted ad and birth the Application for it.

    Nothing is scored: the caller (the user's agent) already decided this ad
    is worth keeping, and product rule 4 forbids us judging fit. Returns the
    stored job plus the `application_id` the caller should work against from
    here on.
    """
    # Lazy import (rule #16) — keeps the spine off the module import path.
    from src.models import Job  # noqa: PLC0415
    from src.services.applications import job_facts  # noqa: PLC0415
    from src.services.applications import spine as applications_spine  # noqa: PLC0415
    from src.services.applications.authorship import actor_for  # noqa: PLC0415
    from src.services.applications.spine import SpineError  # noqa: PLC0415

    # Validate the job facts BEFORE anything is written — a bad country or
    # found_on must not leave a half-brought job behind.
    given_facts = {
        k: v
        for k, v in (
            ("country", body.country), ("remote", body.remote), ("found_on", body.found_on),
            ("posted_at", body.posted_at), ("employer_published_at", body.employer_published_at),
        )
        # '' is "not said" on the bring door: a re-bring never clears an
        # earlier value (clearing is PATCH /applications/{id}/job's job).
        if v is not None and v != ""
    }
    try:
        clean_facts = job_facts.validate_job_facts(given_facts)
    except SpineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    now = datetime.now(timezone.utc).isoformat()
    job = Job(
        title=body.title,
        company=body.company,
        apply_url=body.apply_url,
        source=USER_BROUGHT_SOURCE,
        date_found=now,
        location=body.location,
        description=body.description,
        # The user is reading the ad right now: it is live, and "now" is the
        # most honest posting date we have (low confidence, stated as such).
        posted_at=now,
        date_confidence="low",
        first_seen_at=now,
        last_seen_at=now,
    )
    # Same ad link already brought by THIS user (tracking params, www, fragment
    # and a trailing slash ignored)? Reuse that job - the title+company key
    # cannot see a re-titled repost. Pattern: Gsync/jobsync src/lib/jobs/jobDedupe.ts (MIT).
    # Only when the title+company key is NEW: an exact re-bring keeps the old
    # path (it also clears a stale ghost-sweep mark via update_last_seen).
    same_link_job_id = (
        None if await db.get_job_id_by_key(job.normalized_key()) is not None
        else await _job_id_for_same_link(db, user.id, body.apply_url, body.company)
    )
    inserted = False if same_link_job_id is not None else await db.insert_job(job)
    await db.commit()
    if same_link_job_id is None and not inserted:
        # Same key already in the catalog (a legacy scrape, or an earlier
        # paste). The user is reading this ad right now, so it is LIVE: clear
        # the stale mark a long-dead ghost sweep may have left on the row,
        # otherwise `POST /pipeline/{job_id}` still refuses it as expired.
        await db.update_last_seen(job.normalized_key())
    job_id = same_link_job_id if same_link_job_id is not None else await db.get_job_id_by_key(job.normalized_key())
    if job_id is None:
        # insert_job reports "inserted or not", never WHY not; a swallowed
        # insert failure lands here. Say so instead of dying on an assert.
        raise HTTPException(status_code=500, detail="Could not store the job")

    row = await db.get_job_by_id(job_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Could not read the job back")

    # Slice 5 - a repost among the jobs THIS user brought (never another user's
    # row, never the catalog). Computed once; never raises.
    from src.services.applications import reposts  # noqa: PLC0415

    repost = await reposts.repost_of(
        db, user.id, company=body.company, title=body.title, apply_url=body.apply_url,
    )

    # P0 2026-10-14 — a SAME-user re-bring of a clearly different posting
    # (another job link, location or country under the same title+company)
    # must not land silently on the existing application: applications are
    # UNIQUE(user_id, job_id) and the catalog key is (company, title), so a
    # second application would need a second catalog row. Refuse BEFORE any
    # visa/fact write and tell the agent exactly how to proceed.
    mine = await applications_spine.get_application_by_job(db, user.id, job_id)
    if mine is not None:
        mine_row = await applications_spine.get_owned_application(db, user.id, int(mine["id"])) or {}
        diffs = posting_differences(mine_row, body, clean_facts.get("country"), str(row.get("apply_url") or ""))
        if diffs:
            get_audit_logger().info(
                "job_bring_conflict",
                extra={
                    "event": "job_bring_conflict", "user_id": user.id, "job_id": job_id,
                    "application_id": int(mine["id"]), "reason": ",".join(diffs), "status": "refused",
                },
            )
            if repost:
                get_audit_logger().info(
                    "job_bring_repost",
                    extra={
                        "event": "job_bring_repost", "user_id": safe_log_value(user.id),
                        "application_id": int(mine["id"]),
                        "earlier_application_id": repost["application_id"],
                        "days_since": repost["days_since"], "status": "refused",
                    },
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"You already brought this title at this company as application {int(mine['id'])}, "
                    f"and this ad differs from it in: {', '.join(diffs)}. Nothing was changed. "
                    f"If it is the SAME job, keep using application {int(mine['id'])} "
                    "(fix country/remote/found_on/dates with update_job, i.e. PATCH "
                    f"/applications/{int(mine['id'])}/job). If it is a DIFFERENT posting, bring it "
                    "again with a title that tells the two apart, e.g. add the city in brackets."
                    + (_repost_sentence(repost) if repost else "")
                ),
            )

    # R1/R2 — the Application is born HERE, in the same request: upsert the
    # `applications` row for (user, job) with status='considering', copy the
    # ad onto it (the user's own snapshot, independent of the catalog row),
    # append a `brought` event. Bringing the same job twice reuses the row and appends
    # no second event (birth_application reads-before-inserting).
    # P0 2026-10-14 — the snapshot is THIS caller's paste, never the shared
    # catalog row (which holds whichever user brought the key first).
    own_paste = {
        **dict(row),
        "title": body.title, "company": body.company, "location": body.location,
        "apply_url": body.apply_url, "description": body.description, "source": USER_BROUGHT_SOURCE,
    }
    birth = await applications_spine.birth_application(
        db, user_id=user.id, job_id=job_id, job_row=own_paste, recorded_by=actor_for(user),
        origin=body.origin,
    )
    if not birth["existing"] and not inserted:
        # The catalog key already existed (another user's paste, a re-titled
        # same-link repost, or a legacy row) — the new application still
        # carries this caller's own text. Ids only; never ad text or URLs.
        get_audit_logger().info(
            "job_bring_own_snapshot",
            extra={
                "event": "job_bring_own_snapshot", "user_id": user.id, "job_id": job_id,
                "application_id": birth["application_id"], "reason": "shared_catalog_key", "status": "ok",
            },
        )
    flagged_repost = (
        repost if repost is not None and not birth["existing"]
        and repost["application_id"] != birth["application_id"] else None
    )
    if flagged_repost is not None:
        get_audit_logger().info(
            "job_bring_repost",
            extra={
                "event": "job_bring_repost", "user_id": safe_log_value(user.id),
                "application_id": birth["application_id"],
                "earlier_application_id": flagged_repost["application_id"],
                "days_since": flagged_repost["days_since"], "status": "flagged",
            },
        )
    # Slice 7 — the agent's visa reading, when it gave one. Omitted means the
    # ad said nothing: the slot stays 'unknown' and a re-bring never clears a
    # judgement recorded earlier through save_fit or the web.
    if body.visa_signal is not None or body.visa_detail or body.visa_country:
        from src.services.applications import visa as visa_service  # noqa: PLC0415

        try:
            await visa_service.set_visa_signal(
                db, user_id=user.id, application_id=birth["application_id"], recorded_by=actor_for(user),
                signal=body.visa_signal, detail=body.visa_detail, country=body.visa_country,
            )
        except SpineError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    try:
        if given_facts:
            stored = await job_facts.set_job_facts(
                db, user_id=user.id, application_id=birth["application_id"], given=given_facts,
            )
        else:
            app_row = await applications_spine.get_owned_application(db, user.id, birth["application_id"])
            stored = job_facts.job_facts_view(app_row or {})
    except SpineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    facts: dict[str, Any] = {k: stored[k] for k in job_facts.JOB_FACT_FIELDS}
    from src.services.applications.job_checks import checks_for_application  # noqa: PLC0415

    app_now = await applications_spine.get_owned_application(db, user.id, birth["application_id"]) or {}
    checks = checks_for_application(
        app_now, description=str(app_now.get("job_description_snapshot") or body.description)
    )
    # Facts from THIS user's own records: company history, a near-identical ad
    # brought before, and the posting signs (posting_history.py). Never raises.
    from src.services.applications.posting_history import history_checks  # noqa: PLC0415

    if app_now:
        checks += await history_checks(
            db, user.id, app_now, checks,
            description=str(app_now.get("job_description_snapshot") or body.description),
        )

    get_audit_logger().info(
        "job_brought",
        extra={
            "event": "job_brought", "job_id": job_id, "user_id": user.id,
            "application_id": birth["application_id"],
            "existing": bool(birth["existing"]), "catalog_existing": not inserted, "status": "ok",
        },
    )

    # Owner decision, 2026-09-28 — `first_bring`, once per user, web or MCP:
    # both surfaces call this one route function, so capturing here (never
    # duplicated in mcp_server.py) covers both. Never blocks or fails the
    # bring — see src/services/analytics.py. Guarded the same way
    # _CaptureFirstToolCall._capture_once is: a drifted DB (missing column)
    # or a connection blip here must never turn an already-successful bring
    # into a 500.
    from src.services import analytics  # noqa: PLC0415

    try:
        if await analytics.mark_first_bring(db._db, user.id):
            analytics.capture_event(user.id, "first_bring", {"job_id": job_id})
    except Exception:  # noqa: BLE001 — analytics must never break a bring
        logger.debug("analytics_first_bring_failed", extra={"user_id": user.id})

    queued: Optional[bool] = None
    queue_note: Optional[str] = None
    if body.queue:
        queued, queue_note = await applications_spine.queue_if_open(
            db, user.id, birth["application_id"], recorded_by=actor_for(user)
        )
    return BringJobResponse(
        queued=queued,
        queue_note=queue_note,
        # THIS caller's snapshot, never the shared row (P0 2026-10-14).
        job=application_job_response(app_now, dict(row)),
        # Per user: "you already had this job". Whether another user brought
        # the same key is not this caller's business (and was a leak).
        existing=bool(birth["existing"]),
        application_id=birth["application_id"],
        status=birth["status"],
        ad_posted_at=stored.get("ad_posted_at"),
        employer_published_at=stored.get("employer_published_at"),
        checks=checks,
        repost=flagged_repost,
        **facts,
    )


# ---------------------------------------------------------------------------
# POST /jobs/fetch-url — the web fallback's fetch button
# (docs/plans/2026-09-04-url-fetch/spec.md R1-R12).
#
# Deliberately in THIS module, beside the form it serves, not a new one
# (intent.md's own resolved question) — it shares bring's caps and its
# ROUTE_MODULES entry in tests/test_route_auth_coverage.py needs no edit.
#
# No MCP tool exposes this (R10, VISION rule 5): an agent already has its own
# fetch; this is the browser fallback for a human with no fetch of their own.
# Pinned by tests/test_url_fetch.py::test_no_mcp_tool_fetches_a_url.
# ---------------------------------------------------------------------------


class FetchUrlRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2_000)


class FetchUrlResponse(BaseModel):
    # The Literal is built FROM outcomes.OUTCOMES (never re-typed) so the
    # enum has exactly one source — frozen test 37 asserts the two are
    # identical.
    outcome: Literal[outcomes.OUTCOMES]  # type: ignore[valid-type]
    message: str
    final_url: str = ""
    redirects: int = 0
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    found: list[str] = Field(default_factory=list)
    source_hint: str = ""
    bytes_read: int = 0
    elapsed_ms: int = 0


@router.post("/jobs/fetch-url", response_model=FetchUrlResponse)
async def fetch_url_route(
    body: FetchUrlRequest,
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> FetchUrlResponse:
    """Fetch a job-ad URL the user pasted, under the SSRF guard, and hand
    back extracted fields for the form to pre-fill. Never stores anything
    (S2) — the human still presses "Score this job" to actually bring it.

    Always HTTP 200 with a closed ``outcome`` except auth (401), the feature
    switch (404), a rate limit (429), or a malformed body (422) — "the site
    refused us" is a product outcome the form renders a sentence for, not a
    transport error (spec R1).
    """
    if not settings.URL_FETCH_ENABLED:
        raise HTTPException(status_code=404, detail="not found")

    from src.services.auth import rate_limit as auth_rate_limit  # noqa: PLC0415

    # R9 — per user (never per IP: every agent/browser shares the proxy
    # address behind the Next rewrite unless JOB360_TRUST_PROXY=1), plus one
    # GLOBAL bucket so a handful of accounts can't turn Job360 into an open
    # relay for the whole internet — the SSRF-adjacent abuse that survives a
    # perfect per-address deny list (A9).
    if not auth_rate_limit.check_and_record(
        f"url_fetch:{user.id}:min", max_in_window=settings.URL_FETCH_MAX_PER_MINUTE, window_seconds=60,
    ):
        raise HTTPException(status_code=429, detail="Too many link fetches — wait a moment and try again.")
    if not auth_rate_limit.check_and_record(
        f"url_fetch:{user.id}:hour", max_in_window=settings.URL_FETCH_MAX_PER_HOUR, window_seconds=3600,
    ):
        raise HTTPException(status_code=429, detail="Too many link fetches this hour — try again later.")
    if not auth_rate_limit.check_and_record(
        "url_fetch:*", max_in_window=settings.URL_FETCH_MAX_PER_HOUR_GLOBAL, window_seconds=3600,
    ):
        raise HTTPException(status_code=429, detail="Link fetching is busy right now — try again shortly.")

    from src.services.fetch import fetcher  # noqa: PLC0415 — pulls aiohttp (rule #16)

    result = await fetcher.fetch_url(body.url)

    if result.outcome == outcomes.OK and result.html:
        # Extraction lives HERE, not in fetcher.py — scripts/ssrf_drill.py
        # copies only guard.py/fetcher.py into its throwaway package, so
        # fetcher.py must never import extract.py or outcomes.py (see
        # fetcher.py's module docstring for the full reasoning).
        from src.services.fetch import extract  # noqa: PLC0415

        # B4 — three html.parser passes over up to a 2 MiB document measured
        # at ~3s: run inline on `async def`'s own loop this freezes every
        # other user's request for the duration (the exact "Lost contact"
        # shape PR #123 already shipped once). `extract_job_fields` is
        # `@cpu_bound` (loop_guard.py) precisely so this call raises loudly
        # under test/dev if it is ever inlined again.
        extracted = await asyncio.to_thread(
            extract.extract_job_fields,
            result.html,
            max_depth=settings.URL_FETCH_MAX_HTML_DEPTH,
            budget_s=settings.URL_FETCH_EXTRACT_BUDGET_S,
        )
        result.title, result.company = extracted.title, extracted.company
        result.location, result.description = extracted.location, extracted.description
        result.found, result.source_hint = extracted.found, extracted.source_hint

    # A12/S12 — host only, NEVER the query string or the fetched body. The
    # ORIGINAL requested host (not final_url) is what the user actually
    # asked for.
    host = urlsplit(body.url).hostname or ""
    get_audit_logger().info(
        "url_fetched",
        extra={
            "event": "url_fetched", "host": host, "outcome": result.outcome,
            "bytes": result.bytes_read, "ms": result.elapsed_ms,
            "redirects": result.redirects, "user_id": user.id,
        },
    )

    return FetchUrlResponse(
        outcome=result.outcome,
        message=outcomes.MESSAGES.get(result.outcome, ""),
        final_url=result.final_url,
        redirects=result.redirects,
        title=result.title,
        company=result.company,
        location=result.location,
        description=result.description,
        found=result.found,
        source_hint=result.source_hint,
        bytes_read=result.bytes_read,
        elapsed_ms=result.elapsed_ms,
    )
