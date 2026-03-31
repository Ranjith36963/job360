"""LLM-powered search intelligence — generates domain-aware search config.

Uses the multi-provider LLM pool to analyze a CV and generate:
- Domain-specific search queries that will actually find relevant jobs
- Negative keywords to filter out irrelevant domains
- Industry-specific terms that should boost relevance scoring
- Alternative job titles the seeker should be matched against

This is the key differentiator between generic keyword matching and
intelligent job search. One LLM call per profile setup — not per job.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("job360.profile.llm_search_intelligence")

SEARCH_INTELLIGENCE_PROMPT = """You are a job search expert. Analyze this CV and generate search intelligence for a UK job search engine.

The person's CV is below. Based on their experience, skills, and career trajectory, generate:

1. **search_queries**: 10 highly specific job search queries that would find RELEVANT jobs for this person on UK job boards. Each query should be specific to their domain — NOT generic like "Associate UK". Include location variants.

2. **alternative_titles**: 5-8 alternative job titles this person could realistically apply for, beyond what's on their CV. Think about lateral moves and natural career progressions.

3. **domain_keywords**: 10-15 domain-specific keywords that MUST appear in a relevant job posting. These are the words that distinguish a relevant job from an irrelevant one in this person's field.

4. **negative_keywords**: 10-15 keywords that indicate a job is NOT relevant. These are terms from completely different domains that the search engine should penalize.

5. **industry_terms**: 5-8 industry/sector terms to filter by (e.g., "financial services", "healthcare", "construction").

6. **professional_summary**: A 1-2 sentence description of what this person does, phrased as a job search intent (e.g., "Senior corporate M&A lawyer seeking in-house counsel or partnership roles in financial services or technology").

CV Text:
{cv_text}

Return ONLY valid JSON with the exact keys above. No explanation, no markdown."""


@dataclass
class SearchIntelligence:
    """LLM-generated search intelligence for a CV."""
    search_queries: list[str] = field(default_factory=list)
    alternative_titles: list[str] = field(default_factory=list)
    domain_keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    industry_terms: list[str] = field(default_factory=list)
    professional_summary: str = ""
    success: bool = True
    error: str = ""


def generate_search_intelligence(cv_text: str) -> SearchIntelligence:
    """Call LLM to generate domain-aware search intelligence from CV text."""
    from src.llm.client import is_configured, llm_complete, parse_json_response

    if not is_configured():
        logger.info("No LLM providers configured — skipping search intelligence")
        return SearchIntelligence(success=False, error="No LLM providers configured")

    if len(cv_text.strip()) < 100:
        return SearchIntelligence(success=False, error="CV text too short")

    prompt = SEARCH_INTELLIGENCE_PROMPT.format(cv_text=cv_text[:6000])

    try:
        raw = llm_complete(prompt, max_tokens=2000)
        if raw is None:
            return SearchIntelligence(success=False, error="LLM returned no response")

        data = parse_json_response(raw)
        result = SearchIntelligence(
            search_queries=data.get("search_queries", [])[:15],
            alternative_titles=data.get("alternative_titles", [])[:10],
            domain_keywords=data.get("domain_keywords", [])[:20],
            negative_keywords=data.get("negative_keywords", [])[:20],
            industry_terms=data.get("industry_terms", [])[:10],
            professional_summary=data.get("professional_summary", ""),
        )
        logger.info(
            f"LLM search intelligence: {len(result.search_queries)} queries, "
            f"{len(result.alternative_titles)} alt titles, "
            f"{len(result.domain_keywords)} domain kw, "
            f"{len(result.negative_keywords)} negative kw"
        )
        return result

    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM search intelligence as JSON")
        return SearchIntelligence(success=False, error="JSON parse error")
    except Exception as e:
        logger.warning(f"LLM search intelligence failed: {e}")
        return SearchIntelligence(success=False, error=str(e))


def enrich_search_config(config, intelligence: SearchIntelligence):
    """Merge LLM search intelligence into an existing SearchConfig.

    Non-destructive: only adds to existing config, never replaces.
    """
    if not intelligence.success:
        return config

    # Add alternative titles (deduplicated)
    existing_titles = {t.lower() for t in config.job_titles}
    for title in intelligence.alternative_titles:
        if title.lower() not in existing_titles:
            config.job_titles.append(title)
            existing_titles.add(title.lower())

    # Add domain keywords to relevance_keywords
    existing_rel = set(config.relevance_keywords)
    for kw in intelligence.domain_keywords:
        kw_lower = kw.lower()
        if kw_lower not in existing_rel:
            config.relevance_keywords.append(kw_lower)
            existing_rel.add(kw_lower)

    # Add domain keywords to core_domain_words (these get higher weight in scoring)
    for kw in intelligence.domain_keywords:
        config.core_domain_words.add(kw.lower())

    # Add negative keywords
    existing_neg = {n.lower() for n in config.negative_title_keywords}
    for neg in intelligence.negative_keywords:
        if neg.lower() not in existing_neg:
            config.negative_title_keywords.append(neg.lower())
            existing_neg.add(neg.lower())

    # Replace search queries with LLM-generated ones (they're much better)
    if intelligence.search_queries:
        # Keep first 5 regex-generated queries, add LLM queries
        existing_queries = {q.lower() for q in config.search_queries}
        for q in intelligence.search_queries:
            if q.lower() not in existing_queries:
                config.search_queries.append(q)
                existing_queries.add(q.lower())
        # Cap at 15
        config.search_queries = config.search_queries[:15]

    # Add industry terms
    for term in intelligence.industry_terms:
        term_lower = term.lower()
        if term_lower not in existing_rel:
            config.relevance_keywords.append(term_lower)
            existing_rel.add(term_lower)
        if not config.industries:
            config.industries = []
        if term not in config.industries:
            config.industries.append(term)

    # Use professional summary as about_me if not set
    if not config.about_me and intelligence.professional_summary:
        config.about_me = intelligence.professional_summary

    return config
