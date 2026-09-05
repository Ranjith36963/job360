"""Bring-a-job: the user pastes the ad, we do everything after the click.

Product rule (docs/plans/2026-08-27-exponential-product-research.md §8): Job360
never sources or recommends jobs. Matching runs ONLY on a job the user brings.
This is that front door.

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
`source='user_brought'` (no user_id on `jobs`); "this user brought / is
tracking it" lives in `user_feed`, exactly like a search hit. Two users
pasting the same (company, title) share one row — `insert_job` is
INSERT-OR-IGNORE on the normalized key, and `existing=True` tells the caller.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_request_db
from src.api.models import JobResponse
from src.api.routes.jobs import _compute_bucket, _personalize_dims, _row_to_job_response
from src.core import settings
from src.core.settings import USER_BROUGHT_SOURCE
from src.repositories.database import JobDatabase
from src.services.fetch import outcomes
from src.utils.logger import get_audit_logger

router = APIRouter(tags=["bring"])

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
    existing: bool   # the same (company, title) was already in the catalog
    scored: bool     # False when the user has no complete profile yet
    # spec 2026-09-04-application-spine R1 — an Application is born HERE.
    application_id: int
    status: str


@router.post("/jobs/bring", response_model=BringJobResponse)
async def bring_job(
    body: BringJobRequest,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI DI idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> BringJobResponse:
    """Store the pasted ad, score it against the caller's profile, put it in
    their feed. Returns the job exactly as GET /jobs/{id} would, so the
    frontend can route straight to the detail page.
    """
    # Lazy: Job + the shelf gate pull the scoring stack (rule #16).
    from src.models import Job  # noqa: PLC0415
    from src.services.applications import spine as applications_spine  # noqa: PLC0415
    from src.services.applications.authorship import actor_for  # noqa: PLC0415
    from src.services.feed import FeedService  # noqa: PLC0415
    from src.services.profile.storage import current_profile_version_id  # noqa: PLC0415
    from src.services.shelf_gate import fill_shelves  # noqa: PLC0415
    from src.services.skill_matcher import SCORER_VERSION  # noqa: PLC0415

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
    # Same chokepoint every catalog row passes (Universal Shelf §5): salary
    # band, workplace mode, visa text, deadline — read from the pasted ad.
    fill_shelves(job)

    inserted = await db.insert_job(job)
    await db.commit()
    if not inserted:
        # Same key already in the catalog (a scrape, or an earlier paste). The
        # user is reading this ad right now, so it is LIVE: reset the ghost
        # detector, otherwise a row it had marked stale stays invisible to
        # the detail read below (which filters on staleness_state) and the
        # page the user is sent to is empty.
        await db.update_last_seen(job.normalized_key())
    job_id = await db.get_job_id_by_key(job.normalized_key())
    if job_id is None:
        # insert_job reports "inserted or not", never WHY not; a swallowed
        # insert failure lands here. Say so instead of dying on an assert.
        raise HTTPException(status_code=500, detail="Could not store the job")

    row = await db.get_job_by_id_with_enrichment(job_id, user_id=user.id)
    if row is None:
        raise HTTPException(status_code=500, detail="Could not read the job back")
    personalised = await _personalize_dims(dict(row), db, user)
    scored = "feed_score" in personalised
    score = int(personalised.get("feed_score") or 0)

    # Put it in THIS user's feed so the dashboard shows it beside search hits.
    # Version stamps mirror run_search (main.py) so a later backfill treats the
    # row like any other.
    await FeedService(db._db).upsert_feed_row(
        user_id=user.id,
        job_id=job_id,
        score=max(0, min(100, score)),
        bucket=_compute_bucket(row.get("date_found") or now),
        profile_version=current_profile_version_id(user.id),
        scorer_version=SCORER_VERSION,
    )

    # R1/R2 — the Application is born HERE, in the same request: upsert the
    # `applications` row for (user, job) with status='considering', copy the
    # ad onto it (the snapshot purge_old_jobs can no longer erase), append a
    # `brought` event. Bringing the same job twice reuses the row and appends
    # no second event (birth_application reads-before-inserting).
    birth = await applications_spine.birth_application(
        db, user_id=user.id, job_id=job_id, job_row=dict(row), recorded_by=actor_for(user),
    )

    get_audit_logger().info(
        "job_brought",
        extra={
            "event": "job_brought", "job_id": job_id, "user_id": user.id,
            "existing": not inserted, "scored": scored, "status": "ok",
        },
    )
    job_action = await db.get_action_for_job(job_id, user.id)
    resp = _row_to_job_response(personalised, job_action)
    resp.description = personalised.get("description") or None
    return BringJobResponse(
        job=resp, existing=not inserted, scored=scored,
        application_id=birth["application_id"], status=birth["status"],
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
