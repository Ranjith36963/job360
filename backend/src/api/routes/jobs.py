"""Jobs list, export, and detail endpoints."""

import asyncio
import csv
import io
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.api.auth_deps import CurrentUser, optional_user, require_user
from src.api.dependencies import get_request_db
from src.api.models import JobListResponse, JobResponse
from src.repositories.database import JobDatabase
from src.services.job_signals import signal_backed_lookup
from src.services.visa_signal import VisaStatus, detect_visa_status

if TYPE_CHECKING:  # annotation-only; the real import stays lazy (rule #16)
    from src.models import Job

logger = logging.getLogger("job360.api.jobs")

router = APIRouter(tags=["jobs"])


# Enum-string → bool mapping for the visa_sponsorship enrichment field.
# JobEnrichment uses {"yes", "no", "unknown"} — JobResponse exposes a
# tri-state Optional[bool] (None for "unknown" or absent enrichment).
_VISA_TO_BOOL = {"yes": True, "no": False}


def _parse_enr_salary(raw: object) -> tuple[Optional[int], Optional[int], Optional[str], Optional[str]]:
    """Decode a `job_enrichment.salary` JSON blob into the four
    JobResponse-flat fields. Returns ``(min_gbp, max_gbp, period, currency)``;
    every element is None when the blob is missing/malformed/empty."""
    if not raw:
        return (None, None, None, None)
    try:
        obj = json.loads(raw) if isinstance(raw, (str, bytes)) else dict(cast(Any, raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return (None, None, None, None)
    if not isinstance(obj, dict):
        return (None, None, None, None)
    smin = obj.get("min")
    smax = obj.get("max")
    freq = obj.get("frequency")
    currency = obj.get("currency")
    return (
        int(smin) if smin is not None else None,
        int(smax) if smax is not None else None,
        freq if freq and freq != "unknown" else None,
        currency or None,
    )


def _parse_json_list(raw: object) -> Optional[list[str]]:
    """Decode a JSON list column into list[str], None on missing/invalid."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        decoded = json.loads(cast(Any, raw))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, list):
        return None
    return [str(x) for x in decoded]


def _compute_bucket(date_found: str) -> str:
    """Return time bucket string from date_found ISO string."""
    if not date_found:
        return "7d"
    try:
        # Parse with or without timezone info
        dt = datetime.fromisoformat(date_found)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        hours_ago = (now - dt).total_seconds() / 3600
        if hours_ago <= 24:
            return "24h"
        elif hours_ago <= 48:
            return "48h"
        elif hours_ago <= 72:
            return "3d"
        elif hours_ago <= 120:
            return "5d"
        else:
            return "7d"
    except (ValueError, TypeError):
        return "7d"


def _row_to_job_response(row: dict[str, Any], action: str | None = None) -> JobResponse:
    """Build a `JobResponse` from a single LEFT-JOIN row.

    Step-1 B6: ``row`` may carry both ``jobs.*`` columns and ``enr_*``-prefixed
    ``job_enrichment`` columns. When the ``enr_*`` keys are absent (rows that
    came from :meth:`get_job_by_id` instead of the joined helper) the
    enrichment fields collapse to ``None`` — JobResponse already defaults
    them, so the call site path stays unchanged.
    """
    salary = None
    if row.get("salary_min") and row.get("salary_max"):
        salary = f"{int(row['salary_min'])}-{int(row['salary_max'])}"
    elif row.get("salary_min"):
        salary = str(int(row["salary_min"]))
    elif row.get("salary_max"):
        salary = str(int(row["salary_max"]))

    smin, smax, period, currency = _parse_enr_salary(row.get("enr_salary"))
    visa_enum = row.get("enr_visa_sponsorship")
    visa_bool: Optional[bool] = _VISA_TO_BOOL.get(visa_enum) if visa_enum else None

    return JobResponse(
        id=row.get("id", 0),
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", ""),
        salary=salary,
        # The caller's OWN score, from THEIR user_feed row — never
        # `jobs.match_score`.
        #
        # `_JOBS_ENRICHMENT_JOIN_COLS` starts with `j.*`, so the shared
        # catalog's `match_score` lands in this dict under that exact key,
        # while the per-user value arrives separately as `feed_score`. Reading
        # `match_score` here meant every logged-in caller was served the shared
        # column — which is user-derived (computed from whoever's profile ran
        # the last search) and last-writer-wins across ALL users. Measured in
        # production 2026-07-28: 100% of 3688 catalog rows carried a score, and
        # for 97% of one account's feed that shared value was EXACTLY that one
        # person's personal score.
        #
        # No `feed_score` means the row did not come from anyone's feed — the
        # anonymous catalog branch. 0 is then the honest answer ("not scored
        # for you"), never a stranger's number.
        match_score=int(row["feed_score"]) if row.get("feed_score") is not None else 0,
        source=row.get("source", ""),
        date_found=row.get("date_found", ""),
        apply_url=row.get("apply_url", ""),
        visa_flag=bool(row.get("visa_flag", 0)),
        # Product rule #31 — visa is a THREE-state fact, and `visa_flag` above
        # is a bool that cannot say "the ad never mentioned it". Computed at
        # read time from stored text (plus the LLM verdict when one exists),
        # so it needs no migration and can never go stale against the ad.
        visa_status=detect_visa_status(
            row.get("description", ""), row.get("title", ""),
            enrichment_value=row.get("visa_sponsorship"),
        ).value,
        experience_level=row.get("experience_level", ""),
        # Step-1.5 S1.1-F — surface the per-dim breakdown columns added by
        # migration 0011. The j.* projection in _JOBS_ENRICHMENT_JOIN_COLS
        # already carries them; without these reads JobResponse would keep
        # defaulting every dim to 0 (the bug Step 1's exit criteria missed).
        role=row.get("role", 0) or 0,
        skill=row.get("skill", 0) or 0,
        seniority_score=row.get("seniority_score", 0) or 0,
        experience=row.get("experience", 0) or 0,
        credentials=row.get("credentials", 0) or 0,
        location_score=row.get("location_score", 0) or 0,
        recency=row.get("recency", 0) or 0,
        semantic=row.get("semantic", 0) or 0,
        penalty=row.get("penalty", 0) or 0,
        action=action,
        bucket=_compute_bucket(row.get("date_found", "")),
        # Date-model fields (jobs table columns; Pillar 3 Batch 1).
        posted_at=row.get("posted_at"),
        first_seen_at=row.get("first_seen_at"),
        last_seen_at=row.get("last_seen_at"),
        date_confidence=row.get("date_confidence"),
        staleness_state=row.get("staleness_state"),
        # Enrichment fields (job_enrichment table; Pillar 2 Batch 2.5).
        title_canonical=row.get("enr_title_canonical"),
        seniority=(
            row.get("enr_seniority") if row.get("enr_seniority") and row.get("enr_seniority") != "unknown" else None
        ),
        employment_type=(
            row.get("enr_employment_type")
            if row.get("enr_employment_type") and row.get("enr_employment_type") != "unknown"
            else None
        ),
        workplace_type=(
            row.get("enr_workplace_type")
            if row.get("enr_workplace_type") and row.get("enr_workplace_type") != "unknown"
            else None
        ),
        visa_sponsorship=visa_bool,
        salary_min_gbp=smin,
        salary_max_gbp=smax,
        salary_period=period,
        salary_currency_original=currency,
        required_skills=_parse_json_list(row.get("enr_required_skills")),
        nice_to_have_skills=_parse_json_list(row.get("enr_preferred_skills")),
        industry=row.get("enr_category"),
        years_experience_min=row.get("enr_experience_min_years"),
        # Funnel->judge (LLM matcher) — per-user verdict from user_feed.
        # None-safe: shared-catalog rows (unauthenticated path) lack these keys.
        llm_fit_score=row.get("llm_fit_score"),
        llm_verdict=row.get("llm_verdict"),
        llm_reason=row.get("llm_reason"),
        # Application deadline — from jobs table (catalog-level, not per-user).
        deadline=row.get("deadline"),
        deadline_source=row.get("deadline_source"),
    )


def _profile_query_text(profile: Any) -> str:
    """Build the semantic-query string for the user's profile.

    Delegates to ``embeddings.profile_to_embedding_text`` — ONE definition of
    "what the candidate side means", shared by the hybrid read path and any
    future persisted candidate embedding.

    This used to build the string here from ``job_titles + cv.skills[:40] +
    summary``, and its docstring claimed LinkedIn/GitHub were "already merged
    into cv_data". That is true of the blob but NOT of ``cv.skills``, which
    only ever receives CV-derived skills — so the semantic engine searched on
    a CV-only, truncated view and ignored the LinkedIn/GitHub skills the
    profile pipeline works hardest to extract. Returns "" when there is
    nothing usable (caller then falls back to the top job's title).

    Kept as a thin wrapper (not deleted) because the BM25 leg and the
    fallback path both call it, and it is exercised directly by tests.
    """
    if profile is None:
        return ""
    try:
        from src.services.embeddings import (  # noqa: PLC0415 — lazy (rule #16)
            profile_to_embedding_text,
        )
    except Exception:  # noqa: BLE001 — semantic extra absent: degrade, never 500
        cv = getattr(profile, "cv_data", None)
        if cv is None:
            return ""
        parts = list(getattr(cv, "job_titles", []) or [])
        parts.extend((getattr(cv, "skills", []) or [])[:40])
        return " ".join(p for p in parts if p).strip()
    return profile_to_embedding_text(profile)


def _hybrid_reorder_rows(
    rows: list[dict[str, Any]],
    query_text: str,
    *,
    semantic_ids: Optional[list[int]] = None,
    rerank_fn: Optional[Callable[[list[int]], list[int]]] = None,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    """Pure engine-3 reorder core (offline-testable).

    Fuses three rankings via ``retrieve_for_user``:
      * keyword — the order ``rows`` already arrive in (engine 1's SQL score),
      * BM25 — ``bm25_rank(query_text, row_text)`` (pure, no heavy deps),
      * semantic — the injected ``semantic_ids`` (Chroma ANN, gathered by the
        caller; ``None`` / ``[]`` simply drops that leg),
    then applies the injected ``rerank_fn`` (cross-encoder) and maps the fused
    ids back onto the row dicts. No row is ever lost (belt-and-braces tail).
    """
    if not rows:
        return rows
    keyword_ids = [r["id"] for r in rows if r.get("id") is not None]
    if not keyword_ids:
        return rows

    from src.services.retrieval import bm25_rank, retrieve_for_user  # noqa: PLC0415

    docs = [
        (r["id"], f"{r.get('title', '')} {r.get('description', '')}")
        for r in rows
        if r.get("id") is not None
    ]

    def keyword_fn(_profile: Any, _limit: int) -> list[int]:
        return keyword_ids

    bm25_fn = None
    if query_text:

        def bm25_fn(_profile: Any, _limit: int) -> list[int]:  # noqa: F811 — conditional definition
            return [jid for jid, _score in bm25_rank(query_text, docs)]

    semantic_fn = None
    if semantic_ids:
        _sem = list(semantic_ids)

        def semantic_fn(_profile: Any, _limit: int) -> list[int]:  # noqa: F811
            return _sem

    fused_ids = retrieve_for_user(
        profile=None,
        k=len(keyword_ids),
        keyword_fn=keyword_fn,
        bm25_fn=bm25_fn,
        semantic_fn=semantic_fn,
        rerank_fn=rerank_fn,
        rrf_k=rrf_k,
    )

    by_id = {r["id"]: r for r in rows if r.get("id") is not None}
    reordered: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for jid in fused_ids:
        if jid in by_id and jid not in seen:
            reordered.append(by_id[jid])
            seen.add(jid)
    # Belt-and-braces — never lose a row the user would otherwise have seen.
    for r in rows:
        rid = r.get("id")
        if rid is not None and rid not in seen:
            reordered.append(r)
            seen.add(rid)
    return reordered


def _maybe_apply_hybrid_reorder(rows: list[dict[str, Any]], *, profile: Any = None) -> list[dict[str, Any]]:
    """Engine 3 — when ``?mode=hybrid`` is requested, fuse keyword + BM25 +
    semantic rankings via RRF, cross-encoder rerank the top survivors, and
    reorder ``rows`` accordingly (delegates to the tested ``_hybrid_reorder_rows``).

    Always degrades to keyword order on:
    - SEMANTIC_ENABLED is false (rule #18 — byte-identical to pre-Pillar-2)
    - the semantic stack (sentence_transformers / chromadb) isn't installed
    - the vector index is empty

    The semantic leg and the rerank are each guarded: if either fails, the
    BM25 + keyword fusion still applies (it has no heavy deps). Lazy-imports
    the heavy modules per CLAUDE.md rule #16.
    """
    try:
        from src.core.settings import (  # noqa: PLC0415 — lazy
            ENGINE3_ENABLED,
            SEMANTIC_ENABLED,
        )
    except Exception:
        return rows

    # Engine 3 switch (ENGINE3_ENABLED) OR the legacy SEMANTIC_ENABLED flag.
    if not (ENGINE3_ENABLED or SEMANTIC_ENABLED):
        return rows

    try:
        from src.services.pg_vector_index import PgVectorIndex  # noqa: PLC0415
        from src.services.retrieval import is_hybrid_available  # noqa: PLC0415 — lazy (rule #16)
    except Exception as e:
        logger.warning("hybrid mode requested but retrieval stack unavailable: %s", e)
        return rows

    try:
        vix = PgVectorIndex()
        count = vix.count()
    except Exception as e:
        logger.warning("hybrid mode requested but VectorIndex unavailable: %s", e)
        return rows

    if not is_hybrid_available(count):
        logger.warning(
            "hybrid mode requested but vector index is empty (count=%d); " "falling back to keyword",
            count,
        )
        return rows

    if not rows:
        return rows

    keyword_ids = [r["id"] for r in rows if r.get("id") is not None]

    # Stage B — semantic top-K via Chroma. Prefer a query vector built from the
    # CALLER'S PROFILE (CV titles/skills/summary + LinkedIn) so results rank by
    # similarity to THIS user, not to the top keyword hit. Fall back to the
    # top-scored job's title only when no profile is available. A failure here
    # is NOT fatal — engine 3 still has the BM25 leg, so we degrade rather than
    # bail to pure keyword.
    query_text = _profile_query_text(profile) if profile is not None else ""
    semantic_ids: list[int] = []
    try:
        from src.services.embeddings import encode_job  # noqa: PLC0415

        class _StubJob:
            def __init__(self, title: str, description: str = ""):
                self.title = title
                self.description = description

        if query_text:
            stub = _StubJob(query_text, "")
        else:
            head = rows[0]
            stub = _StubJob(head.get("title", ""), head.get("description", ""))
        query_vec = encode_job(stub, None)
        sem_pairs = vix.query(query_vec, k=min(500, max(len(keyword_ids), 50)))
        semantic_ids = [job_id for job_id, _dist in sem_pairs]
    except Exception as e:
        logger.warning("hybrid semantic leg failed: %s; using keyword + BM25 only", e)
        semantic_ids = []

    # Stage D — cross-encoder rerank wrapper. Only meaningful with a query; the
    # heavy model load is lazy + cached, and any failure degrades to fused order.
    rerank_fn = None
    if query_text:

        def rerank_fn(ids: list[int]) -> list[int]:  # noqa: F811 — conditional definition
            try:
                from src.services.retrieval import (  # noqa: PLC0415
                    _load_cross_encoder,
                    cross_encoder_rerank,
                )

                text_by_id = {
                    r["id"]: f"{r.get('title', '')} {r.get('description', '')}"
                    for r in rows
                    if r.get("id") is not None
                }
                candidates = [(jid, text_by_id.get(jid, "")) for jid in ids]
                reranked = cross_encoder_rerank(
                    query_text, candidates, encoder_factory=_load_cross_encoder
                )
                return [jid for jid, _score in reranked]
            except Exception as e:
                logger.warning("cross-encoder rerank failed; keeping fused order: %s", e)
                return ids

    # Stage C — fuse keyword + BM25 + semantic and rerank, via the tested core.
    return _hybrid_reorder_rows(rows, query_text, semantic_ids=semantic_ids, rerank_fn=rerank_fn)


@router.get("/jobs/export")
async def export_jobs(
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
) -> StreamingResponse:
    """Download the caller's own recent matches as CSV."""
    # Was `get_recent_jobs(...)` — the whole SHARED catalog. The route already
    # required a login, but it then handed that user every job in the system,
    # including ones never matched to them, carrying `jobs.match_score`: a
    # last-writer-wins column holding whoever searched most recently. So one
    # user's export contained another user's scores, and jobs that were never in
    # their feed. Reading the per-user feed makes the export mean what its name
    # promises — and matches the dashboard, which reads the same source.
    #
    # Kept as a comment, not a docstring: FastAPI publishes docstrings into the
    # OpenAPI description, and an internal post-mortem does not belong in the
    # public API contract (it also churns the generated frontend types).
    rows = await db.get_user_feed_jobs(user.id, days=7, min_score=0)
    output = io.StringIO()
    headers = [
        "job_title",
        "company",
        "location",
        "salary",
        "match_score",
        "apply_url",
        "source",
        "date_found",
        "visa_flag",
    ]
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        salary = None
        if row.get("salary_min") and row.get("salary_max"):
            salary = f"{int(row['salary_min'])}-{int(row['salary_max'])}"
        elif row.get("salary_min"):
            salary = str(int(row["salary_min"]))
        elif row.get("salary_max"):
            salary = str(int(row["salary_max"]))
        writer.writerow(
            {
                "job_title": row.get("title", ""),
                "company": row.get("company", ""),
                "location": row.get("location", ""),
                "salary": salary or "",
                # The caller's own feed score — same rule as the JSON
                # serializer above: never the shared `jobs.match_score`.
                "match_score": (
                    int(row["feed_score"]) if row.get("feed_score") is not None else 0
                ),
                "apply_url": row.get("apply_url", ""),
                "source": row.get("source", ""),
                "date_found": row.get("date_found", ""),
                "visa_flag": row.get("visa_flag", 0),
            }
        )
    output.seek(0)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"job360_export_{today}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    hours: Optional[int] = Query(None, ge=0),
    min_score: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    bucket: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    visa_only: Optional[bool] = Query(
        None,
        description=(
            'SPOTLIGHT, not a wall (product rule #31). True keeps every job '
            'and lifts confirmed sponsors to the top; it does NOT hide the '
            '58%% of the catalog whose visa position is simply unstated. '
            'Use visa_strict=true for the old hard-filter behaviour.'
        ),
    ),
    visa_strict: Optional[bool] = Query(
        None,
        description='Opt-in hard filter: show ONLY confirmed sponsors.',
    ),
    mode: Optional[str] = Query(None, description="'keyword' | 'hybrid' (Batch 2.7)"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI dependency-injection idiom
    user: Optional[CurrentUser] = Depends(optional_user),  # noqa: B008 — shared catalog; sitemap + unfurl bots read unauthenticated
) -> JobListResponse:
    # Step-1 B8 — wire ?mode=hybrid through services.retrieval. Falls back
    # to the keyword path silently when SEMANTIC_ENABLED is off, the vector
    # index is empty, or the semantic stack isn't installed.
    days = (hours // 24) + 1 if hours else 7
    # Multi-tenant: an authenticated user sees ONLY their own user_feed (the
    # isolated per-user view — John never sees Paul's jobs). Unauthenticated
    # callers (sitemap / unfurl bots) get the shared catalog. No fallback: an
    # empty feed shows the empty dashboard, never another user's jobs.
    # Step-1 B6: single LEFT JOIN avoids per-job enrichment lookups (N+1).
    # Fetch UNFILTERED, then apply min_score in Python, so we can report how many
    # rows the filter removed. Without that number the UI cannot say "N hidden",
    # and a filter that hides everything is indistinguishable from an empty feed —
    # measured 2026-07-28: 3,236 rows -> 406 visible at the old default of 20,
    # with nothing on screen explaining the missing 2,830.
    # No extra cost: this route already loads every row and paginates in Python.
    if user is not None:
        all_rows = await db.get_user_feed_jobs(user.id, days=days, min_score=0)
    else:
        all_rows = await db.get_recent_jobs_with_enrichment(days=days, min_score=0)

    total_unfiltered = len(all_rows)
    if min_score:
        all_rows = [r for r in all_rows if (r["match_score"] or 0) >= min_score]

    if mode == "hybrid":
        # Rank semantically against the caller's OWN profile (not the top job).
        hybrid_profile = None
        if user is not None:
            from src.services.profile.storage import load_profile  # noqa: PLC0415 — lazy

            hybrid_profile = load_profile(user.id)
        # WORKER THREAD (same reasoning as main.py's scoring hand-off): the
        # hybrid path is CPU-bound — a Chroma query plus a cross-encoder that
        # scores up to 50 (profile, job) PAIRS one by one. Measured in prod at
        # `Batches: 2/2 [00:14<00:00, 7.48s/it]`. Run inline it froze the whole
        # event loop for the duration, so ONE user opening the dashboard stalled
        # every other request in the process — including the 3-second
        # /api/search/{id}/status polls, which is how a slow reorder turned into
        # "Lost contact with the server while searching".
        all_rows = await asyncio.to_thread(
            _maybe_apply_hybrid_reorder, all_rows, profile=hybrid_profile
        )

    # Filter by hours cutoff
    if hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        filtered = []
        for row in all_rows:
            date_str = row.get("date_found", "")
            try:
                dt = datetime.fromisoformat(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    filtered.append(row)
            except (ValueError, TypeError):
                filtered.append(row)
        all_rows = filtered

    # Filter by source
    if source:
        all_rows = [r for r in all_rows if r.get("source", "") == source]

    # VISA — spotlight, not a wall (product rule #31, owner 2026-08-07).
    #
    # This used to be a hard filter on `visa_flag`. Measured 2026-08-07, that
    # hid ~58% of the catalog: visa status is genuinely UNSTATED in most ads,
    # and a bool cannot tell "the employer said no" from "the employer never
    # mentioned it". Filtering on it discarded every sponsor we merely failed
    # to detect. Now: keep everything, lift confirmed sponsors, and let the
    # per-card `visa_status` badge carry the fact.
    #
    # `visa_strict` remains for the user who explicitly wants only confirmed
    # sponsors — a deliberate choice, never the default.
    if visa_strict:
        all_rows = [
            r for r in all_rows
            if detect_visa_status(
                r.get("description", ""), r.get("title", ""),
                enrichment_value=r.get("visa_sponsorship"),
            ) is VisaStatus.SPONSORS
        ]
    elif visa_only:
        def _visa_rank(r: dict) -> int:
            st = detect_visa_status(
                r.get("description", ""), r.get("title", ""),
                enrichment_value=r.get("visa_sponsorship"),
            )
            # 0 sorts first. Confirmed refusals sink BELOW unknowns — an ad
            # that says "we cannot sponsor" is a dead end, while silence is
            # still a question worth asking.
            return {VisaStatus.SPONSORS: 0, VisaStatus.UNKNOWN: 1,
                    VisaStatus.NO_SPONSORSHIP: 2}[st]

        all_rows = sorted(all_rows, key=_visa_rank)

    # Filter by bucket
    if bucket:
        all_rows = [r for r in all_rows if _compute_bucket(r.get("date_found", "")) == bucket]

    # Pre-fetch the user's action map once (avoids N+1 round-trips).
    # Unauthenticated callers (sitemap, unfurl bots) get an empty map —
    # per-user fields (action) are simply null in the response.
    if user is not None:
        action_rows = await db.get_actions(user.id)
        action_map: dict[int, str] = {row["job_id"]: row["action"] for row in action_rows}
    else:
        action_map = {}

    if action is not None:
        all_rows = [r for r in all_rows if action_map.get(r["id"]) == action]

    total = len(all_rows)
    page = all_rows[offset : offset + limit]

    jobs = [_row_to_job_response(row, action_map.get(row["id"])) for row in page]

    filters_applied: dict[str, Any] = {}
    if hours is not None:
        filters_applied["hours"] = hours
    if min_score is not None:
        filters_applied["min_score"] = min_score
    if source:
        filters_applied["source"] = source
    if bucket:
        filters_applied["bucket"] = bucket
    if action:
        filters_applied["action"] = action
    if visa_only:
        filters_applied["visa_focus"] = visa_only
    if visa_strict:
        filters_applied["visa_strict"] = visa_strict

    return JobListResponse(
        jobs=jobs,
        total=total,
        total_unfiltered=total_unfiltered,
        filters_applied=filters_applied,
    )


@router.get("/jobs/{job_id}/duplicates")
async def get_job_duplicates(
    job_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI dependency-injection idiom
    user: Optional[CurrentUser] = Depends(optional_user),  # noqa: B008 — public: shared catalog
) -> dict[str, Any]:
    """Return alternate listings for the same job across sources (Option A: query-time grouping).

    Uses normalized_key() grouping: jobs with same normalized company + normalized title
    are considered duplicates. Public endpoint — same auth policy as GET /jobs/{id}.
    """
    # 1. Fetch the target job (use plain get_job_by_id — no enrichment needed here)
    row = await db.get_job_by_id(job_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    # 2. Get all jobs with same normalized_company + normalized_title (exclude self)
    duplicates = await db.get_duplicate_jobs(
        job_id,
        row["normalized_company"],
        row["normalized_title"],
    )
    return {"job_id": job_id, "duplicates": duplicates, "total": len(duplicates)}


def _row_to_scoring_job(row: dict[str, Any]) -> "Job":
    """Reconstruct a ``Job`` from a stored DB row for re-scoring.

    CRUCIAL: sets ``job.id`` from the row. The scorer's ``enrichment_lookup``
    is keyed on ``job.id``; omitting it makes every enrichment dim
    (seniority/salary/visa/workplace) silently score 0 (the dim-scoring-id
    bug). ``score()`` reads title/description/location/salary + the date
    fields, so we populate just those.
    """
    from src.models import Job  # noqa: PLC0415 — lazy (rule #16)

    job = Job(
        title=row.get("title", "") or "",
        company=row.get("company", "") or "",
        apply_url=row.get("apply_url", "") or "",
        source=row.get("source", "") or "",
        date_found=row.get("date_found", "") or "",
        location=row.get("location", "") or "",
        description=row.get("description", "") or "",
        salary_min=row.get("salary_min"),
        salary_max=row.get("salary_max"),
        posted_at=row.get("posted_at"),
        date_confidence=row.get("date_confidence") or "low",
    )
    job.id = row.get("id")
    return job


async def _personalize_dims(row: dict[str, Any], db: JobDatabase, user: CurrentUser) -> dict[str, Any]:
    """Rewrite the per-dimension breakdown on ``row`` to reflect ``user``'s
    own profile, returning a shallow copy with the dim columns overridden.

    Why this exists: the shared ``jobs`` row stores ONE dim breakdown per job
    (last-writer-wins across every user who matched it), but the detail-page
    8-D radar is inherently per-user. We re-score the job against the viewing
    user's profile — faithful because the exact ``description`` the scorer saw
    at search time is what got stored.

    The overall ``match_score`` is sourced from the user's stored
    ``user_feed.score`` (the dashboard card they clicked), falling back to the
    fresh recompute only when there is no feed row — i.e. a job the user never
    matched but reached by direct URL or the duplicates link. That fallback is
    exactly why we recompute instead of storing dims in the feed.

    Degrades to ``row`` unchanged when the user has no complete profile (so
    there is nothing to personalise against). Heavy scoring imports are kept
    local per CLAUDE.md rule #16 — they only load on an authenticated detail GET.
    """
    from src.core.settings import ENGINE2_ENABLED  # noqa: PLC0415
    from src.services.feed import FeedService  # noqa: PLC0415
    from src.services.job_enrichment import (  # noqa: PLC0415
        ENRICHMENT_ENABLED,
        load_enrichment,
    )
    from src.services.profile.keyword_generator import generate_search_config  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415
    from src.services.skill_matcher import JobScorer, detect_experience_level  # noqa: PLC0415

    profile = load_profile(user.id)
    if not profile or not profile.is_complete:
        return row  # nothing to personalise against — keep the shared row

    search_config = generate_search_config(profile)
    # Mirror run_search's scorer wiring exactly so a recompute against an
    # unchanged profile reproduces the stored feed score (CLAUDE.md rule #19).
    # Engine 2 switch (ENGINE2_ENABLED) OR the legacy ENRICHMENT_ENABLED flag.
    enrichment_on = ENGINE2_ENABLED or ENRICHMENT_ENABLED
    # N3 — this recompute scores exactly ONE job (row["id"]), so load only THAT
    # job's enrichment via the single-row load_enrichment(). The old
    # _build_enrichment_lookup(db._conn) deserialised the ENTIRE job_enrichment
    # table on every authenticated GET /jobs/{id} — a full-table scan that grows
    # with the 30-day catalog, to use one row. Build a 1-entry lookup so the
    # scorer's `enrichment_lookup` and `dims_active` below are unchanged.
    _this_enrichment = await load_enrichment(db._db, row["id"]) if enrichment_on else None
    enrichment_lookup_dict = {row["id"]: _this_enrichment} if _this_enrichment is not None else {}
    scorer = JobScorer(
        search_config,
        user_preferences=profile.preferences,
        enrichment_lookup=signal_backed_lookup(
            lambda j: enrichment_lookup_dict.get(getattr(j, "id", None))
        ),
    )

    # Reconstruct the Job for scoring — _row_to_scoring_job sets job.id so the
    # enrichment_lookup (keyed on id) actually hits and the dims contribute.
    job = _row_to_scoring_job(row)
    breakdown = scorer.score(job)

    feed_score = await FeedService(db._db).get_score(user.id, row["id"])

    out = dict(row)
    # Dim columns map ScoreBreakdown -> JobResponse exactly as run_search does
    # (main.py: title_score -> role, recency_score -> recency, *_score kept).
    out["role"] = breakdown.title_score
    out["skill"] = breakdown.skill_score
    out["location_score"] = breakdown.location_score
    out["recency"] = breakdown.recency_score
    out["seniority_score"] = breakdown.seniority_score
    # salary/visa/workplace were computed above and then DISCARDED — the engine
    # produced them every request and no one ever saw them. Surface them here
    # rather than persisting: they depend on THIS caller's preferences, so a
    # column on the shared `jobs` catalog would leak one user's salary target
    # into another user's row (rules #10/#17). Read-time recompute is the
    # correct home; this function already does it.
    out["salary_score"] = breakdown.salary_score
    out["visa_score"] = breakdown.visa_score
    out["workplace_score"] = breakdown.workplace_score
    # Explicit "did the enrichment dims actually run?" — never inferred from
    # zeros, because 0 is ambiguous (a real earned 0 vs never-measured). The
    # dims only populate when an enrichment row EXISTS for this job (rule #20:
    # both user_preferences AND a lookup hit are required).
    out["dims_active"] = enrichment_lookup_dict.get(row["id"]) is not None
    out["visa_flag"] = int(scorer.check_visa_flag(job))
    out["experience_level"] = detect_experience_level(job.title)
    # Overall: the stored feed score the user saw; recompute only as fallback.
    # Written under "feed_score" (not "match_score") because that's the key
    # _row_to_job_response actually reads for the personalized score — writing
    # "match_score" here would land on a key the serializer ignores, and the
    # response would silently fall back to 0.
    out["feed_score"] = feed_score if feed_score is not None else breakdown.match_score
    return out


def _user_skill_names(profile: Any) -> list[str]:
    """All of the user's skill names, deduped case-insensitively (CV + LinkedIn +
    GitHub-inferred + about-me-inferred). Empty when there's no profile."""
    cv = getattr(profile, "cv_data", None) if profile is not None else None
    if cv is None:
        return []
    names: list[str] = []
    for attr in ("skills", "linkedin_skills", "github_llm_skills", "about_me_inferred_skills"):
        names.extend(getattr(cv, attr, None) or [])
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = (n or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(n)
    return out


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008 — FastAPI dependency-injection idiom
    user: Optional[CurrentUser] = Depends(optional_user),  # noqa: B008 — shared catalog; generateMetadata reads unauthenticated
) -> JobResponse:
    # Step-1 B6: single LEFT JOIN — JobResponse surfaces enrichment fields.
    # Passing the user lets them open a job THEY applied to or acted on even
    # after it goes stale. Hiding a ghost from browsing is right; hiding a
    # person's own application from them reads as data loss (found 2026-08-03).
    row = await db.get_job_by_id_with_enrichment(
        job_id, user_id=user.id if user is not None else None
    )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    job_action = await db.get_action_for_job(job_id, user.id) if user is not None else None
    # Per-user radar: re-score the job against the logged-in user's profile so
    # the 8-D breakdown is theirs, not the shared last-writer-wins jobs row.
    if user is not None:
        row = await _personalize_dims(dict(row), db, user)
        # Surface THIS user's judge (E4) verdict so the detail page leads with the
        # same AI fit score the dashboard ranks by (the list read already joins
        # these; the single-job read didn't, so llm_* came back null here).
        row.update(await db.get_user_feed_verdict(user.id, job_id))
    resp = _row_to_job_response(row, job_action)

    # Skill Analysis: split the job's required skills into matched / missing /
    # transferable against the user's profile skills (read-time; empty when the job
    # has no enrichment-derived required_skills or the user has no profile).
    if user is not None and resp.required_skills:
        from src.services.profile.storage import load_profile  # noqa: PLC0415
        from src.services.skill_gap import compute_skill_gap  # noqa: PLC0415

        gap = compute_skill_gap(
            _user_skill_names(load_profile(user.id)), resp.required_skills
        )
        resp.matched_skills = gap["matched"]
        resp.missing_required = gap["missing"]
        resp.transferable_skills = gap["transferable"]
    return resp
