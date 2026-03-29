import argparse
import asyncio
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from src.config.settings import (
    REED_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY, JSEARCH_API_KEY,
    JOOBLE_API_KEY, SERPAPI_KEY, CAREERJET_AFFID, FINDWORK_API_KEY,
    DB_PATH, EXPORTS_DIR, REPORTS_DIR, REQUEST_TIMEOUT, MIN_MATCH_SCORE,
)
from src.utils.logger import setup_logging
from src.models import Job
from src.storage.database import JobDatabase
from src.storage.csv_export import export_to_csv
from src.filters.skill_matcher import detect_experience_level, salary_in_range, JobScorer, is_foreign_only
from src.filters.jd_parser import detect_job_type, parse_jd
from src.filters.deduplicator import deduplicate
from src.profile.storage import load_profile
from src.profile.keyword_generator import generate_search_config
from src.notifications.report_generator import generate_markdown_report
from src.notifications.base import get_configured_channels

from src.sources.reed import ReedSource
from src.sources.adzuna import AdzunaSource
from src.sources.jsearch import JSearchSource
from src.sources.arbeitnow import ArbeitnowSource
from src.sources.remoteok import RemoteOKSource
from src.sources.jobicy import JobicySource
from src.sources.himalayas import HimalayasSource
from src.sources.greenhouse import GreenhouseSource
from src.sources.lever import LeverSource
from src.sources.workable import WorkableSource
from src.sources.ashby import AshbySource
from src.sources.findajob import FindAJobSource
from src.sources.remotive import RemotiveSource
from src.sources.jooble import JoobleSource
from src.sources.linkedin import LinkedInSource
from src.sources.smartrecruiters import SmartRecruitersSource
from src.sources.pinpoint import PinpointSource
from src.sources.recruitee import RecruiteeSource
from src.sources.indeed import JobSpySource
from src.sources.workday import WorkdaySource
from src.sources.google_jobs import GoogleJobsSource
from src.sources.devitjobs import DevITJobsSource
from src.sources.landingjobs import LandingJobsSource
from src.sources.aijobs import AIJobsSource
from src.sources.themuse import TheMuseSource
from src.sources.hackernews import HackerNewsSource
from src.sources.careerjet import CareerjetSource
from src.sources.findwork import FindworkSource
from src.sources.nofluffjobs import NoFluffJobsSource
from src.sources.hn_jobs import HNJobsSource
from src.sources.yc_companies import YCCompaniesSource
from src.sources.jobs_ac_uk import JobsAcUkSource
from src.sources.nhs_jobs import NHSJobsSource
from src.sources.personio import PersonioSource
from src.sources.workanywhere import WorkAnywhereSource
from src.sources.weworkremotely import WeWorkRemotelySource
from src.sources.realworkfromanywhere import RealWorkFromAnywhereSource
from src.sources.biospace import BioSpaceSource
from src.sources.jobtensor import JobTensorSource
from src.sources.climatebase import ClimatebaseSource
from src.sources.eightykhours import EightyKHoursSource
from src.sources.bcs_jobs import BCSJobsSource
from src.sources.uni_jobs import UniJobsSource
from src.sources.successfactors import SuccessFactorsSource
from src.sources.aijobs_global import AIJobsGlobalSource
from src.sources.aijobs_ai import AIJobsAISource
from src.sources.nomis import NomisSource

logger = logging.getLogger("job360.main")

# Source name → class mapping for --source filter
SOURCE_REGISTRY = {
    "reed": ReedSource,
    "adzuna": AdzunaSource,
    "jsearch": JSearchSource,
    "arbeitnow": ArbeitnowSource,
    "remoteok": RemoteOKSource,
    "jobicy": JobicySource,
    "himalayas": HimalayasSource,
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "workable": WorkableSource,
    "ashby": AshbySource,
    "findajob": FindAJobSource,
    "remotive": RemotiveSource,
    "jooble": JoobleSource,
    "linkedin": LinkedInSource,
    "smartrecruiters": SmartRecruitersSource,
    "pinpoint": PinpointSource,
    "recruitee": RecruiteeSource,
    "indeed": JobSpySource,
    "glassdoor": JobSpySource,
    "workday": WorkdaySource,
    "google_jobs": GoogleJobsSource,
    "devitjobs": DevITJobsSource,
    "landingjobs": LandingJobsSource,
    "aijobs": AIJobsSource,
    "themuse": TheMuseSource,
    "hackernews": HackerNewsSource,
    "careerjet": CareerjetSource,
    "findwork": FindworkSource,
    "nofluffjobs": NoFluffJobsSource,
    # Phase 4: New free sources
    "hn_jobs": HNJobsSource,
    "yc_companies": YCCompaniesSource,
    "jobs_ac_uk": JobsAcUkSource,
    "nhs_jobs": NHSJobsSource,
    "personio": PersonioSource,
    "workanywhere": WorkAnywhereSource,
    "weworkremotely": WeWorkRemotelySource,
    "realworkfromanywhere": RealWorkFromAnywhereSource,
    "biospace": BioSpaceSource,
    "jobtensor": JobTensorSource,
    "climatebase": ClimatebaseSource,
    "eightykhours": EightyKHoursSource,
    "bcs_jobs": BCSJobsSource,
    "uni_jobs": UniJobsSource,
    "successfactors": SuccessFactorsSource,
    "aijobs_global": AIJobsGlobalSource,
    "aijobs_ai": AIJobsAISource,
    "nomis": NomisSource,
}


def _format_date(date_str: str) -> str:
    """Parse date_found into a short 'Posted: 28 Feb 2026' format."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return f"Posted: {dt.strftime('%d %b %Y')}"
        except (ValueError, AttributeError):
            continue
    # Fallback: try to extract a date-like substring
    if date_str and len(date_str) >= 10:
        return f"Posted: {date_str[:10]}"
    return "Posted: N/A"


def _build_sources(session: aiohttp.ClientSession, source_filter: str | None = None,
                    search_config=None) -> list:
    """Build source instances, optionally filtered to a single source."""
    sc = search_config  # short alias
    all_sources = [
        # Group A: Keyed APIs
        ReedSource(session, api_key=REED_API_KEY, search_config=sc),
        AdzunaSource(session, app_id=ADZUNA_APP_ID, app_key=ADZUNA_APP_KEY, search_config=sc),
        JSearchSource(session, api_key=JSEARCH_API_KEY, search_config=sc),
        # Group B: Free APIs
        ArbeitnowSource(session, search_config=sc),
        RemoteOKSource(session, search_config=sc),
        JobicySource(session, search_config=sc),
        HimalayasSource(session, search_config=sc),
        # Group C: ATS boards
        GreenhouseSource(session, search_config=sc),
        LeverSource(session, search_config=sc),
        WorkableSource(session, search_config=sc),
        AshbySource(session, search_config=sc),
        # Group D: Government
        FindAJobSource(session, search_config=sc),
        # Group E: New free APIs
        RemotiveSource(session, search_config=sc),
        JoobleSource(session, api_key=JOOBLE_API_KEY, search_config=sc),
        LinkedInSource(session, search_config=sc),
        # Group F: New ATS boards
        SmartRecruitersSource(session, search_config=sc),
        PinpointSource(session, search_config=sc),
        RecruiteeSource(session, search_config=sc),
        # Group G: Scraper-based
        JobSpySource(session, search_config=sc),
        # Group H: Workday ATS
        WorkdaySource(session, search_config=sc),
        # Group I: Real-time data sources
        GoogleJobsSource(session, api_key=SERPAPI_KEY, search_config=sc),
        DevITJobsSource(session, search_config=sc),
        LandingJobsSource(session, search_config=sc),
        # Group J: New free/keyed sources
        AIJobsSource(session, search_config=sc),
        TheMuseSource(session, search_config=sc),
        HackerNewsSource(session, search_config=sc),
        CareerjetSource(session, affid=CAREERJET_AFFID, search_config=sc),
        FindworkSource(session, api_key=FINDWORK_API_KEY, search_config=sc),
        NoFluffJobsSource(session, search_config=sc),
        # Group K: Phase 4 new free sources
        HNJobsSource(session, search_config=sc),
        YCCompaniesSource(session, search_config=sc),
        JobsAcUkSource(session, search_config=sc),
        NHSJobsSource(session, search_config=sc),
        PersonioSource(session, search_config=sc),
        WorkAnywhereSource(session, search_config=sc),
        WeWorkRemotelySource(session, search_config=sc),
        RealWorkFromAnywhereSource(session, search_config=sc),
        BioSpaceSource(session, search_config=sc),
        JobTensorSource(session, search_config=sc),
        ClimatebaseSource(session, search_config=sc),
        EightyKHoursSource(session, search_config=sc),
        BCSJobsSource(session, search_config=sc),
        UniJobsSource(session, search_config=sc),
        SuccessFactorsSource(session, search_config=sc),
        AIJobsGlobalSource(session, search_config=sc),
        AIJobsAISource(session, search_config=sc),
        NomisSource(session, search_config=sc),
    ]
    if source_filter:
        # Special case: glassdoor shares JobSpySource with indeed
        if source_filter == "glassdoor":
            source_filter = "indeed"
        return [s for s in all_sources if s.name == source_filter]
    return all_sources


async def run_search(
    db_path: str | None = None,
    source_filter: str | None = None,
    dry_run: bool = False,
    log_level: str | None = None,
    no_notify: bool = False,
    launch_dashboard: bool = False,
) -> dict:
    setup_logging(log_level)
    from src.diagnostics import PipelineDiagnostics
    diag = PipelineDiagnostics()

    logger.info("=" * 60)
    logger.info("Job360 - Starting job search run")
    if source_filter:
        logger.info(f"  Source filter: {source_filter}")
    if dry_run:
        logger.info("  Mode: DRY RUN (no DB writes, no notifications)")

    # Load user profile — CV is mandatory, no profile = no search
    diag.start_phase("profile_load")
    profile = load_profile()
    if not profile:
        logger.error("  No user profile found. Set up your profile to start searching.")
        logger.error("  Run: python -m src.cli setup-profile --cv path/to/cv.pdf")
    elif not profile.is_complete:
        logger.error("  Profile exists but is incomplete (no CV text, no job titles, no skills).")
        logger.error("  Run: python -m src.cli setup-profile --cv path/to/cv.pdf")
        logger.error("  Or re-run setup-profile and enter target job titles + skills at the prompts.")
    diag.end_phase("profile_load")
    if not profile or not profile.is_complete:
        return {"total_found": 0, "new_jobs": 0, "sources_queried": 0, "per_source": {}}

    search_config = generate_search_config(profile)
    scorer = JobScorer(search_config)
    logger.info("  Using keywords from user profile")
    logger.info("=" * 60)

    # Init database
    path = db_path or str(DB_PATH)
    db = JobDatabase(path)
    await db.init_db()

    try:
        # Auto-purge old jobs (>30 days)
        purged = await db.purge_old_jobs(days=30)
        if purged:
            logger.info(f"Purged {purged} jobs older than 30 days")

        # Create session
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Build sources
            sources = _build_sources(session, source_filter, search_config=search_config)

            if not sources:
                logger.error(f"No sources matched filter: {source_filter}")
                return {"total_found": 0, "new_jobs": 0, "sources_queried": 0, "per_source": {}}

            # Fetch from all sources concurrently
            diag.start_phase("source_fetch")
            all_jobs: list[Job] = []
            per_source: dict[str, int] = {}
            source_count = 0

            async def _fetch_source(source):
                try:
                    return await asyncio.wait_for(source.fetch_jobs(), timeout=60)
                except asyncio.TimeoutError:
                    logger.warning(f"Source {source.name} timed out after 60s")
                    return []
                except Exception as e:
                    logger.error(f"Source {source.name} failed: {e}")
                    return []

            results = await asyncio.gather(*[_fetch_source(s) for s in sources])

            for source, jobs in zip(sources, results):
                source_count += 1
                per_source[source.name] = len(jobs)
                all_jobs.extend(jobs)
                if jobs:
                    logger.info(f"  {source.name}: {len(jobs)} jobs")
                else:
                    logger.info(f"  {source.name}: 0 jobs")

            diag.end_phase("source_fetch")
            logger.info(f"Total raw jobs: {len(all_jobs)}")
            sys.stdout.flush()
            diag.record_funnel("raw_fetched", len(all_jobs))

            # Hard-remove foreign-only jobs (no UK/remote mention)
            diag.start_phase("foreign_filter")
            before_foreign = len(all_jobs)
            all_jobs = [j for j in all_jobs if not is_foreign_only(j.location)]
            removed = before_foreign - len(all_jobs)
            if removed:
                logger.info(f"Removed {removed} foreign-only jobs")
            diag.end_phase("foreign_filter")
            diag.record_funnel("after_foreign_filter", len(all_jobs))

            # Score all jobs using profile-based scorer + structured JD parsing
            cv_data = profile.cv_data if profile else None
            # Only primary + secondary for JD parsing (tertiary are inferred/supplementary
            # and would cause ~7x more regex calls with minimal match gain)
            _all_user_skills = (
                search_config.primary_skills
                + search_config.secondary_skills
            )

            # Compute job embeddings in batch (64 at a time, not one-by-one)
            diag.start_phase("embeddings")
            t_emb = time.time()
            try:
                from src.filters.embeddings import is_available as _emb_ok, encode_batch as _emb_batch
                from src.filters.hybrid_retriever import serialize_embedding as _emb_ser
                if _emb_ok():
                    texts = [f"{j.title} {j.description[:1500]}" for j in all_jobs]
                    vecs = _emb_batch(texts)
                    if vecs is not None:
                        for i, job in enumerate(all_jobs):
                            job.embedding = _emb_ser(vecs[i])
            except Exception:
                pass  # Embeddings are optional
            diag.end_phase("embeddings")
            logger.info(f"Embeddings computed ({len(all_jobs)} jobs, {time.time() - t_emb:.1f}s)")

            diag.start_phase("scoring")
            t_score = time.time()
            score_evolution: dict[int, dict] = {}
            for job in all_jobs:
                job.visa_flag = scorer.check_visa_flag(job)
                job.experience_level = detect_experience_level(job.title)
                job.job_type = detect_job_type(f"{job.title} {job.description}")
                # Full 8D scoring (replaces legacy score() — uses bd.total directly)
                try:
                    parsed_jd = parse_jd(
                        job.description, user_skills=_all_user_skills
                    ) if job.description else None
                    bd = scorer.score_detailed(job, parsed_jd=parsed_jd, cv_data=cv_data)
                    job.match_score = bd.total  # 8D score is the primary score
                    job.match_data = json.dumps({
                        "role": bd.role, "skill": bd.skill,
                        "seniority": bd.seniority, "experience": bd.experience,
                        "credentials": bd.credentials, "location": bd.location,
                        "recency": bd.recency, "semantic": bd.semantic,
                        "matched": bd.matched_skills,
                        "missing_required": bd.missing_required,
                        "missing_preferred": bd.missing_preferred,
                        "transferable": bd.transferable_skills,
                    })
                    # Backfill salary from JD when source didn't provide it
                    if parsed_jd and job.salary_min is None and parsed_jd.salary_min:
                        job.salary_min = parsed_jd.salary_min
                    if parsed_jd and job.salary_max is None and parsed_jd.salary_max:
                        job.salary_max = parsed_jd.salary_max
                except Exception:
                    # Fallback to legacy score only if detailed scoring fails
                    job.match_score = scorer.score(job)

            diag.end_phase("scoring")
            logger.info(f"Scoring complete ({len(all_jobs)} jobs, {time.time() - t_score:.1f}s)")
            diag.record_scores(all_jobs)
            diag.record_funnel("after_scoring", len(all_jobs))

            # Record initial scores for evolution tracking
            for job in all_jobs:
                score_evolution[id(job)] = {"score_initial": job.match_score}

            # Apply feedback adjustment from user liked/rejected signals
            diag.start_phase("feedback")
            _fb_liked = _fb_rejected = _fb_adjusted = _fb_total_adj = 0
            try:
                from src.filters.feedback import (
                    load_feedback_signals, build_preference_vector,
                    compute_feedback_adjustment,
                )
                from src.filters.hybrid_retriever import deserialize_embedding
                feedback_signals = await load_feedback_signals(db._conn)
                pref_vec = build_preference_vector(feedback_signals)
                _fb_liked = len(feedback_signals.get("liked_texts", []))
                _fb_rejected = len(feedback_signals.get("rejected_texts", []))
                if feedback_signals.get("liked_texts") or feedback_signals.get("rejected_texts"):
                    for job in all_jobs:
                        job_emb = deserialize_embedding(job.embedding) if job.embedding else None
                        adj = compute_feedback_adjustment(
                            f"{job.title} {job.description[:500]}",
                            feedback_signals, pref_vec, job_emb,
                        )
                        if adj != 0:
                            job.match_score = max(0, min(100, job.match_score + adj))
                            _fb_adjusted += 1
                            _fb_total_adj += adj
                    if _fb_adjusted:
                        logger.info(f"Feedback loop adjusted {_fb_adjusted} job scores")
            except Exception:
                pass  # Feedback is optional
            diag.end_phase("feedback")
            diag.record_feedback(_fb_liked, _fb_rejected, _fb_adjusted, _fb_total_adj)

            # Record scores after feedback for evolution tracking
            for job in all_jobs:
                evo = score_evolution.get(id(job))
                if evo:
                    evo["score_after_feedback"] = job.match_score

            # Cross-encoder reranking (top candidates only — expensive)
            diag.start_phase("reranking")
            _rr_count = 0
            _rr_scores: list[float] = []
            _rr_boosts: list[float] = []
            try:
                from src.filters.reranker import is_available as _rerank_ok, rerank, build_profile_text
                if _rerank_ok():
                    profile_text = build_profile_text(
                        search_config.job_titles,
                        search_config.primary_skills,
                        search_config.secondary_skills,
                    )
                    job_dicts = [
                        {"title": j.title, "description": j.description or "",
                         "match_score": j.match_score, "_job_ref": j}
                        for j in all_jobs
                    ]
                    job_dicts.sort(key=lambda d: d["match_score"], reverse=True)
                    reranked = rerank(profile_text, job_dicts, top_n=50)
                    _rr_count = min(50, len(reranked))
                    for rank, d in enumerate(reranked[:50]):
                        boost = max(0, 5 - rank // 10)
                        d["_job_ref"].match_score = min(100, d["_job_ref"].match_score + boost)
                        _rr_scores.append(d.get("rerank_score", 0))
                        _rr_boosts.append(boost)
                        # Persist rerank_score into match_data
                        try:
                            if d["_job_ref"].match_data:
                                _md = json.loads(d["_job_ref"].match_data)
                                _md["rerank_score"] = round(d.get("rerank_score", 0), 4)
                                d["_job_ref"].match_data = json.dumps(_md)
                        except Exception:
                            pass
                    logger.info("Cross-encoder reranked top-50 candidates")
            except Exception:
                pass  # Reranking is optional
            diag.end_phase("reranking")
            diag.record_rerank(
                _rr_count,
                sum(_rr_scores) / len(_rr_scores) if _rr_scores else 0.0,
                sum(_rr_boosts) / len(_rr_boosts) if _rr_boosts else 0.0,
            )

            # Record scores after reranking for evolution tracking
            for job in all_jobs:
                evo = score_evolution.get(id(job))
                if evo:
                    evo["score_after_rerank"] = job.match_score

            # LLM-enriched JD parsing (top candidates only — optional)
            diag.start_phase("llm_enrichment")
            # Snapshot scores before LLM for delta tracking
            _pre_llm_scores = {id(j): j.match_score for j in all_jobs}
            try:
                from src.llm.client import is_configured as _llm_ok
                if _llm_ok():
                    from src.llm.jd_enricher import enrich_top_jobs
                    t_llm = time.time()
                    enriched = await enrich_top_jobs(
                        all_jobs, scorer, cv_data, _all_user_skills, top_n=50
                    )
                    if enriched:
                        logger.info(
                            f"LLM-enriched {enriched} JDs ({time.time() - t_llm:.1f}s)"
                        )
            except Exception:
                pass  # LLM enrichment is optional
            diag.end_phase("llm_enrichment")

            # Collect LLM stats
            _llm_score_deltas = []
            for job in all_jobs:
                pre = _pre_llm_scores.get(id(job), job.match_score)
                if pre != job.match_score:
                    _llm_score_deltas.append(job.match_score - pre)
            try:
                from src.llm.cache import cache_stats as _cache_stats
                from src.llm.client import pool_status as _pool_status
                _cs = _cache_stats()
                _ps = _pool_status()
                diag.record_llm_stats(
                    cache_hits=_cs.get("hits", 0),
                    api_calls=_cs.get("misses", 0),
                    providers_used=_ps.get("configured", []),
                    score_deltas=_llm_score_deltas,
                    call_counts=_ps.get("call_counts", {}),
                    failures=_ps.get("failures", {}),
                )
            except Exception:
                pass

            # Record scores after LLM for evolution tracking
            for job in all_jobs:
                evo = score_evolution.get(id(job))
                if evo:
                    evo["score_after_llm"] = job.match_score

            # Per-source quality metrics (Phase 5B)
            _source_quality: dict[str, dict] = {}
            for job in all_jobs:
                src = job.source
                if src not in _source_quality:
                    _source_quality[src] = {"returned": 0, "above_threshold": 0}
                _source_quality[src]["returned"] += 1
                if job.match_score >= MIN_MATCH_SCORE:
                    _source_quality[src]["above_threshold"] += 1
            # Merge quality metrics into per_source
            for src, count in per_source.items():
                quality = _source_quality.get(src, {})
                per_source[src] = {
                    "fetched": count,
                    "after_foreign_filter": quality.get("returned", 0),
                    "above_threshold": quality.get("above_threshold", 0),
                }

            # Deduplicate
            diag.start_phase("dedup")
            t_dedup = time.time()
            _dedup_stats: dict = {}
            unique_jobs = deduplicate(all_jobs, stats_out=_dedup_stats)
            diag.end_phase("dedup")
            logger.info(f"After dedup: {len(unique_jobs)} unique jobs ({time.time() - t_dedup:.1f}s)")
            diag.record_dedup(
                before=len(all_jobs), after=len(unique_jobs),
                removed_by_key=_dedup_stats.get("removed_by_key", 0),
                removed_by_similarity=_dedup_stats.get("removed_by_similarity", 0),
            )

            # Filter by minimum score
            diag.start_phase("score_filter")
            unique_jobs = [j for j in unique_jobs if j.match_score >= MIN_MATCH_SCORE]
            diag.end_phase("score_filter")
            logger.info(f"After score filter (>={MIN_MATCH_SCORE}): {len(unique_jobs)} jobs")
            diag.record_funnel("after_score_filter", len(unique_jobs))

            # Per-company capping — prevent single company flooding results
            diag.start_phase("company_cap")
            from src.config.settings import MAX_JOBS_PER_COMPANY
            unique_jobs.sort(key=lambda j: j.match_score, reverse=True)
            company_counts: dict[str, int] = {}
            capped_jobs: list[Job] = []
            for job in unique_jobs:
                comp, _ = job.normalized_key()
                count = company_counts.get(comp, 0)
                if count < MAX_JOBS_PER_COMPANY:
                    capped_jobs.append(job)
                    company_counts[comp] = count + 1
            if len(capped_jobs) < len(unique_jobs):
                logger.info(f"Per-company cap ({MAX_JOBS_PER_COMPANY}): {len(unique_jobs)} -> {len(capped_jobs)} jobs")
            unique_jobs = capped_jobs
            diag.end_phase("company_cap")
            diag.record_funnel("after_company_cap", len(unique_jobs))

            if dry_run:
                # Dry run: show results without DB writes or notifications
                unique_jobs.sort(key=lambda j: (j.match_score, salary_in_range(j)), reverse=True)
                stats = {
                    "total_found": len(all_jobs),
                    "new_jobs": len(unique_jobs),
                    "sources_queried": source_count,
                    "per_source": per_source,
                }
                _print_bucketed_summary(unique_jobs, "DRY RUN")
                logger.info("Job360 dry run complete")
                return stats

            # Merge score evolution into match_data before DB store
            for job in unique_jobs:
                evo = score_evolution.get(id(job))
                if evo and job.match_data:
                    try:
                        _md = json.loads(job.match_data)
                        _md["score_evolution"] = evo
                        job.match_data = json.dumps(_md)
                    except Exception:
                        pass

            # Filter new jobs (not seen in DB)
            diag.start_phase("db_store")
            new_jobs: list[Job] = []
            for job in unique_jobs:
                if not await db.is_job_seen(job.normalized_key()):
                    await db.insert_job(job)
                    new_jobs.append(job)

            new_jobs.sort(key=lambda j: (j.match_score, salary_in_range(j)), reverse=True)
            logger.info(f"New jobs: {len(new_jobs)}")

            # Update per-source with stored counts
            for job in new_jobs:
                src = job.source
                if src in per_source and isinstance(per_source[src], dict):
                    per_source[src].setdefault("stored", 0)
                    per_source[src]["stored"] += 1

            diag.end_phase("db_store")
            diag.record_funnel("new_stored", len(new_jobs))

            # Record data quality and skill gaps from all scored jobs
            diag.record_data_quality(all_jobs)
            diag.record_skill_gaps(all_jobs)

            # Stats
            stats = {
                "total_found": len(all_jobs),
                "new_jobs": len(new_jobs),
                "sources_queried": source_count,
                "per_source": per_source,
            }

            # Generate outputs
            if new_jobs:
                # CSV
                EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                csv_path = str(EXPORTS_DIR / f"jobs_{ts}.csv")
                await asyncio.to_thread(export_to_csv, new_jobs, csv_path)
                logger.info(f"CSV exported: {csv_path}")

                # Markdown report
                REPORTS_DIR.mkdir(parents=True, exist_ok=True)
                md_report = generate_markdown_report(new_jobs, stats, diagnostics=diag)
                md_path = REPORTS_DIR / f"report_{ts}.md"
                md_path.write_text(md_report, encoding="utf-8")
                logger.info(f"Report saved: {md_path}")

                # Notifications via channel abstraction
                if not no_notify:
                    for channel in get_configured_channels():
                        try:
                            await channel.send(new_jobs, stats, csv_path=csv_path)
                        except Exception as e:
                            logger.error(f"{channel.name} notification failed: {e}")

                # Print time-bucketed summary to console
                _print_bucketed_summary(new_jobs, "Results")
            else:
                logger.info("No new jobs to report")
                logger.info("Job360: No new jobs found this run.")

            # Log run
            await db.log_run(stats)

        # Pipeline health summary (structured for automated analysis)
        logger.info("PIPELINE_HEALTH: %s", json.dumps({
            "total_fetched": stats.get("total_found", 0),
            "new_stored": stats.get("new_jobs", 0),
            "sources_active": stats.get("sources_queried", 0),
            "sources_with_jobs": sum(1 for v in stats.get("per_source", {}).values()
                                     if (v.get("stored", 0) if isinstance(v, dict) else v) > 0),
        }))
        # Full diagnostics (superset of PIPELINE_HEALTH)
        logger.info("PIPELINE_DIAGNOSTICS: %s", diag.to_json_line())
        logger.info("Job360 run complete")
    finally:
        await db.close()

    # Launch dashboard if requested
    if launch_dashboard:
        logger.info("Launching Streamlit dashboard...")
        subprocess.Popen([sys.executable, "-m", "streamlit", "run", "src/dashboard.py"])

    return stats


def _print_bucketed_summary(jobs: list, label: str = "Results"):
    """Print a time-bucketed summary of jobs to the console."""
    from src.utils.time_buckets import bucket_jobs, bucket_summary_counts, BUCKETS
    job_dicts = [
        {
            "title": j.title, "company": j.company, "location": j.location,
            "match_score": j.match_score, "visa_flag": j.visa_flag,
            "salary_min": j.salary_min, "salary_max": j.salary_max,
            "date_found": j.date_found, "apply_url": j.apply_url, "source": j.source,
        }
        for j in jobs
    ]
    bucketed = bucket_jobs(job_dicts, min_score=0)
    counts = bucket_summary_counts(bucketed)
    logger.info("=" * 60)
    logger.info(f"Job360 {label}: {len(jobs)} jobs found")
    logger.info(f"  24h: {counts['last_24h']} | 24-48h: {counts['24_48h']} | "
                f"2-3d: {counts['2_3d']} | 3-5d: {counts['3_5d']} | 5-7d: {counts['5_7d']}")
    logger.info("=" * 60)
    for idx in range(len(BUCKETS)):
        bucket_list = bucketed.get(idx, [])
        if bucket_list:
            label_name = BUCKETS[idx][0]
            # Use ASCII marker instead of emoji (Windows cp1252 can't encode Unicode emoji)
            marker = f"[{idx + 1}]"
            logger.info(f"  {marker} {label_name} ({len(bucket_list)} jobs):")
            for i, j in enumerate(bucket_list, 1):
                visa = " [VISA]" if j.get("visa_flag") else ""
                salary = ""
                if j.get("salary_min") and j.get("salary_max"):
                    salary = f" | {int(j['salary_min']):,}-{int(j['salary_max']):,}"
                posted = f" | {_format_date(j.get('date_found', ''))}"
                src = f" [{j.get('source', '')}]"
                logger.info(f"    {i}. [{j['match_score']}] {j['title']} @ {j['company']}{salary}{visa}{posted}{src}")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job360 Pipeline")
    parser.add_argument("--no-email", action="store_true", help="Skip notifications")
    parser.add_argument("--dashboard", action="store_true", help="Launch dashboard after run")
    args = parser.parse_args()
    try:
        asyncio.run(run_search(no_notify=args.no_email, launch_dashboard=args.dashboard))
    except Exception as exc:
        import traceback
        sys.stderr.write(f"\nFATAL: Pipeline crashed: {exc}\n")
        traceback.print_exc()
        sys.exit(1)
