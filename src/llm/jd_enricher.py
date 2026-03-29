"""LLM-powered job description enrichment for top-N candidates.

Enriches regex-parsed ``ParsedJD`` with LLM extraction.  Non-destructive:
LLM data supplements regex results, never replaces them.  Falls back to
regex-only parsing if LLM is unavailable or fails.

Only applied to top-N candidates (default 50) to avoid burning through
free-tier rate limits on low-scoring jobs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from src.llm import cache
from src.llm.client import allm_complete, is_configured, parse_json_response

logger = logging.getLogger("job360.llm.jd_enricher")

JD_EXTRACTION_PROMPT = """Extract structured information from this job description. Only extract what is EXPLICITLY stated — do not infer or guess.

Return a JSON object with these fields:
- required_skills: list of skills marked as required/essential/must-have
- preferred_skills: list of skills marked as nice-to-have/desirable/preferred
- experience_years: minimum years of experience required (integer or null)
- qualifications: list of degrees/certifications mentioned
- seniority: one of "entry", "mid", "senior", "lead", "executive", or "" if unclear
- salary_min: minimum salary in GBP annual (number or null)
- salary_max: maximum salary in GBP annual (number or null)

Job Description:
{description}

The candidate has these skills (identify which appear in the JD):
{user_skills}

Return ONLY valid JSON, no explanation."""


async def llm_parse_jd(
    description: str,
    user_skills: list[str] | None = None,
    use_cache: bool = True,
) -> Optional[dict]:
    """Extract structured data from a job description using LLM.

    Returns a dict with keys matching ``ParsedJD`` fields, or ``None`` on
    failure.  Results are cached by description hash to avoid redundant calls.
    """
    if not is_configured():
        return None

    if not description or len(description.strip()) < 30:
        return None

    truncated = description[:4000]
    skills_str = ", ".join((user_skills or [])[:30])

    # Check cache first.
    key = cache.cache_key("jd", truncated)
    if use_cache:
        cached = cache.get_cached(key)
        if cached is not None:
            return cached

    prompt = JD_EXTRACTION_PROMPT.format(
        description=truncated,
        user_skills=skills_str or "(none provided)",
    )

    try:
        raw = await allm_complete(prompt, max_tokens=1500)
        if raw is None:
            return None

        data = parse_json_response(raw)

        # Validate expected keys exist (even if empty).
        result = {
            "required_skills": data.get("required_skills", []),
            "preferred_skills": data.get("preferred_skills", []),
            "experience_years": data.get("experience_years"),
            "qualifications": data.get("qualifications", []),
            "seniority": data.get("seniority", ""),
            "salary_min": data.get("salary_min"),
            "salary_max": data.get("salary_max"),
        }

        if use_cache:
            cache.set_cached(key, result)
        return result

    except json.JSONDecodeError:
        logger.debug("LLM JD parse: invalid JSON response")
        return None
    except Exception as exc:
        logger.debug(f"LLM JD parse failed: {exc}")
        return None


def merge_llm_jd(regex_parsed, llm_data: dict):
    """Merge LLM extraction into a regex-parsed ``ParsedJD``.

    Non-destructive — only adds data the regex parser missed.  Returns the
    same (mutated) ``ParsedJD`` object.
    """
    # Add required skills not already found by regex.
    existing_req = {s.lower() for s in regex_parsed.required_skills}
    for skill in llm_data.get("required_skills", []):
        if skill.lower() not in existing_req:
            regex_parsed.required_skills.append(skill)
            existing_req.add(skill.lower())

    # Add preferred skills.
    existing_pref = {s.lower() for s in regex_parsed.preferred_skills}
    for skill in llm_data.get("preferred_skills", []):
        if skill.lower() not in existing_pref:
            regex_parsed.preferred_skills.append(skill)
            existing_pref.add(skill.lower())

    # Fill missing experience years.
    if regex_parsed.experience_years is None and llm_data.get("experience_years"):
        try:
            regex_parsed.experience_years = int(llm_data["experience_years"])
        except (ValueError, TypeError):
            pass

    # Add qualifications.
    existing_qual = {q.lower() for q in regex_parsed.qualifications}
    for qual in llm_data.get("qualifications", []):
        if qual.lower() not in existing_qual:
            regex_parsed.qualifications.append(qual)

    # Fill missing seniority signal.
    if not regex_parsed.seniority_signal and llm_data.get("seniority"):
        regex_parsed.seniority_signal = llm_data["seniority"]

    # Fill missing salary.
    if regex_parsed.salary_min is None and llm_data.get("salary_min"):
        try:
            regex_parsed.salary_min = float(llm_data["salary_min"])
        except (ValueError, TypeError):
            pass
    if regex_parsed.salary_max is None and llm_data.get("salary_max"):
        try:
            regex_parsed.salary_max = float(llm_data["salary_max"])
        except (ValueError, TypeError):
            pass

    return regex_parsed


async def enrich_top_jobs(
    jobs: list,
    scorer,
    cv_data,
    user_skills: list[str],
    top_n: int = 50,
) -> int:
    """LLM-enrich the top-N jobs and re-score them.

    Sorts *jobs* by ``match_score`` descending, LLM-parses the top *top_n*
    job descriptions, merges the results into the regex-parsed JD, and
    re-runs ``score_detailed()`` to update scores.

    Returns the number of jobs successfully enriched.
    """
    from src.filters.jd_parser import parse_jd

    if not is_configured():
        return 0

    sorted_jobs = sorted(jobs, key=lambda j: j.match_score, reverse=True)
    top_jobs = sorted_jobs[:top_n]

    semaphore = asyncio.Semaphore(10)
    enriched_count = 0

    async def _enrich_one(job):
        nonlocal enriched_count
        if not job.description:
            return
        async with semaphore:
            llm_data = await llm_parse_jd(job.description, user_skills)
        if llm_data is None:
            return

        # Re-parse with regex to get fresh baseline, then merge LLM data.
        parsed_jd = parse_jd(job.description, user_skills=user_skills)
        _before_req = set(s.lower() for s in parsed_jd.required_skills)
        merge_llm_jd(parsed_jd, llm_data)
        _after_req = set(s.lower() for s in parsed_jd.required_skills)
        _new_skills = _after_req - _before_req
        if _new_skills:
            logger.debug("LLM found new skills for '%s': %s", job.title[:30], _new_skills)

        # Re-score with enriched JD.
        import json as _json
        bd = scorer.score_detailed(job, parsed_jd=parsed_jd, cv_data=cv_data)
        job.match_score = bd.total
        job.match_data = _json.dumps({
            "role": bd.role, "skill": bd.skill, "seniority": bd.seniority,
            "experience": bd.experience, "credentials": bd.credentials,
            "location": bd.location, "recency": bd.recency,
            "semantic": bd.semantic,
            "matched": bd.matched_skills,
            "missing_required": bd.missing_required,
            "missing_preferred": bd.missing_preferred,
            "transferable": bd.transferable_skills,
        })
        enriched_count += 1

    await asyncio.gather(*[_enrich_one(job) for job in top_jobs])

    if enriched_count:
        from src.llm.client import pool_status
        status = pool_status()
        providers = ", ".join(status.get("configured", []))
        logger.info(
            f"LLM-enriched {enriched_count}/{len(top_jobs)} JDs via [{providers}]"
        )

    return enriched_count
