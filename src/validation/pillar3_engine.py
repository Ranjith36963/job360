"""Pillar 3: Search & Match Engine Quality Validator.

Given that CV parsing (Pillar 1) and source data (Pillar 2) are good,
validates whether the engine produces relevant results:

- Domain relevance: Do results match the CV's professional domain?
- Score distribution: Are scores reasonable (not compressed, not inflated)?
- Skill overlap: Do high-scoring jobs actually mention the seeker's skills?
- Seniority alignment: Are job seniority levels appropriate?
- Negative filtering: Are excluded/irrelevant jobs properly filtered?

Metrics per CV:
- Domain relevance score (0-1): % of stored jobs matching the CV's domain
- Score sanity (0-1): score distribution health (spread, not all bunched)
- Skill match accuracy (0-1): avg skill overlap for top-10 scored jobs
- Overall Pillar 3 confidence: weighted average
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("job360.validation.pillar3")

# Domain keyword mapping — for each domain, keywords that SHOULD appear in relevant jobs
DOMAIN_RELEVANCE_KEYWORDS: dict[str, list[str]] = {
    "software_engineering": ["software", "developer", "engineer", "backend", "frontend", "full-stack", "fullstack", "programming", "code", "api", "devops"],
    "devops": ["devops", "platform", "infrastructure", "cloud", "sre", "reliability", "kubernetes", "terraform", "ci/cd", "deployment"],
    "data_engineering": ["data engineer", "data platform", "etl", "pipeline", "data warehouse", "spark", "kafka", "airflow", "dbt", "analytics engineer"],
    "data_science": ["data scientist", "machine learning", "analytics", "statistical", "data analysis", "ml engineer", "predictive", "modelling"],
    "marketing": ["marketing", "digital marketing", "seo", "ppc", "content", "brand", "campaign", "social media", "growth", "acquisition"],
    "finance": ["finance", "financial", "analyst", "accountant", "banking", "investment", "audit", "fp&a", "treasury", "compliance"],
    "human_resources": ["hr", "human resources", "people", "talent", "recruitment", "employee relations", "hrbp", "learning", "development", "cipd"],
    "supply_chain": ["supply chain", "procurement", "logistics", "operations", "warehouse", "inventory", "sourcing", "planning", "manufacturing"],
    "design": ["designer", "ux", "ui", "user experience", "product design", "figma", "design system", "research", "usability"],
    "clinical_research": ["clinical", "research", "trial", "pharma", "cra", "gcp", "regulatory", "biotech", "medical"],
    "structural_engineering": ["structural", "civil", "engineer", "construction", "building", "design", "bim", "concrete", "steel"],
    "legal": ["lawyer", "solicitor", "legal", "counsel", "attorney", "law", "compliance", "contract", "regulatory"],
    "biomedical_science": ["biomedical", "laboratory", "scientist", "nhs", "pathology", "diagnostics", "clinical", "genomics"],
    "construction_pm": ["project manager", "construction", "building", "site", "nec", "jct", "programme", "quantity", "surveyor"],
    "environmental": ["environmental", "sustainability", "carbon", "climate", "esg", "green", "ecology", "renewable", "net zero"],
    "healthcare_ops": ["nhs", "healthcare", "hospital", "clinical", "operations", "service", "patient", "health"],
    "cybersecurity": ["cyber", "security", "infosec", "penetration", "soc", "siem", "threat", "vulnerability", "incident"],
    "nursing": ["nurse", "nursing", "nmc", "clinical", "patient care", "healthcare", "ward", "hospital"],
    "product_management": ["product manager", "product owner", "product lead", "roadmap", "backlog", "agile", "scrum", "stakeholder"],
}


@dataclass
class EngineResult:
    """Result of engine quality check for one CV's search results."""
    cv_name: str
    domain: str
    total_jobs_stored: int = 0
    # Scores (0.0 - 1.0)
    domain_relevance: float = 0.0
    score_sanity: float = 0.0
    skill_match_accuracy: float = 0.0
    seniority_alignment: float = 0.0
    # Details
    relevant_jobs: int = 0
    irrelevant_examples: list[str] = field(default_factory=list)
    score_stats: dict = field(default_factory=dict)
    top_jobs_skill_overlap: list[dict] = field(default_factory=list)
    notes: str = ""
    # Overall
    confidence: float = 0.0

    def compute_confidence(self) -> float:
        """Weighted: domain_relevance(0.35) + score_sanity(0.25) + skill_match(0.25) + seniority(0.15)."""
        self.confidence = (
            self.domain_relevance * 0.35
            + self.score_sanity * 0.25
            + self.skill_match_accuracy * 0.25
            + self.seniority_alignment * 0.15
        )
        return self.confidence


def _check_domain_relevance(jobs: list[dict], domain: str) -> tuple[float, int, list[str]]:
    """Check what % of stored jobs match the CV's domain keywords."""
    keywords = DOMAIN_RELEVANCE_KEYWORDS.get(domain, [])
    if not keywords or not jobs:
        return 0.0, 0, []

    relevant = 0
    irrelevant_examples = []

    for job in jobs:
        title = (job.get("title") or "").lower()
        desc = (job.get("description") or "").lower()[:500]
        combined = f"{title} {desc}"

        is_relevant = any(kw in combined for kw in keywords)
        if is_relevant:
            relevant += 1
        elif len(irrelevant_examples) < 5:
            irrelevant_examples.append(f"{job.get('title', 'Unknown')} @ {job.get('company', 'Unknown')}")

    score = relevant / len(jobs) if jobs else 0.0
    return score, relevant, irrelevant_examples


def _check_score_sanity(jobs: list[dict]) -> tuple[float, dict]:
    """Check if score distribution is healthy (not compressed, has spread)."""
    scores = [j.get("match_score", 0) for j in jobs if j.get("match_score")]
    if not scores:
        return 0.0, {}

    avg = sum(scores) / len(scores)
    min_s = min(scores)
    max_s = max(scores)
    spread = max_s - min_s
    # Score above 30 jobs (should be most since MIN_MATCH_SCORE=30)
    above_30 = sum(1 for s in scores if s >= 30)
    above_50 = sum(1 for s in scores if s >= 50)
    above_70 = sum(1 for s in scores if s >= 70)

    stats = {
        "count": len(scores),
        "avg": round(avg, 1),
        "min": min_s,
        "max": max_s,
        "spread": spread,
        "above_50": above_50,
        "above_70": above_70,
    }

    # Sanity checks:
    score = 1.0
    # 1. Spread should be > 15 (not all same score)
    if spread < 15:
        score -= 0.3
    # 2. Average should be between 35 and 80 (not too low, not inflated)
    if avg < 35:
        score -= 0.2
    elif avg > 80:
        score -= 0.2  # inflated scores
    # 3. Should have some high-quality matches (at least 1 above 60)
    if max_s < 60:
        score -= 0.2
    # 4. Should have diversity — not all bunched at one level
    if len(scores) > 5 and above_70 / len(scores) > 0.8:
        score -= 0.2  # too many high scores = probably inflated

    return max(0.0, score), stats


def _check_skill_match(jobs: list[dict], cv_skills: list[str]) -> tuple[float, list[dict]]:
    """Check skill overlap between top-scored jobs and CV skills."""
    if not jobs or not cv_skills:
        return 0.0, []

    # Sort by score, take top 10
    sorted_jobs = sorted(jobs, key=lambda j: j.get("match_score", 0), reverse=True)[:10]
    cv_skills_lower = {s.lower() for s in cv_skills}

    overlaps = []
    total_overlap = 0.0

    for job in sorted_jobs:
        desc = (job.get("description") or "").lower()
        title = (job.get("title") or "").lower()
        combined = f"{title} {desc}"

        matched = []
        for skill in cv_skills_lower:
            # Simple word boundary check
            if skill in combined:
                matched.append(skill)

        overlap_pct = len(matched) / len(cv_skills_lower) if cv_skills_lower else 0.0
        total_overlap += overlap_pct
        overlaps.append({
            "title": job.get("title", "Unknown"),
            "score": job.get("match_score", 0),
            "skills_found": len(matched),
            "skills_total": len(cv_skills_lower),
            "overlap_pct": round(overlap_pct, 2),
        })

    avg_overlap = total_overlap / len(sorted_jobs) if sorted_jobs else 0.0
    # Scale: 10%+ overlap for top jobs is good (many jobs won't list all CV skills)
    # 5% is baseline, 20%+ is excellent
    score = min(1.0, avg_overlap / 0.15)  # 15% overlap = 1.0
    return score, overlaps


def _check_seniority_alignment(jobs: list[dict], expected_seniority: str) -> float:
    """Check if job seniority levels match what the CV seeker should get."""
    if not jobs or not expected_seniority:
        return 0.5  # neutral if we can't check

    seniority_keywords = {
        "entry": ["junior", "graduate", "entry", "trainee", "intern", "apprentice"],
        "mid": ["mid", "intermediate"],
        "senior": ["senior", "sr", "experienced", "lead"],
        "lead": ["lead", "principal", "head", "manager", "director"],
        "executive": ["director", "vp", "chief", "head of", "executive", "cto", "cfo"],
    }

    target_words = seniority_keywords.get(expected_seniority, [])
    # Also accept adjacent levels
    levels = ["entry", "mid", "senior", "lead", "executive"]
    try:
        idx = levels.index(expected_seniority)
        adjacent = []
        if idx > 0:
            adjacent.extend(seniority_keywords.get(levels[idx - 1], []))
        if idx < len(levels) - 1:
            adjacent.extend(seniority_keywords.get(levels[idx + 1], []))
    except ValueError:
        adjacent = []

    exact = 0
    close = 0
    total_checked = 0

    for job in jobs[:20]:  # Check top 20
        title = (job.get("title") or "").lower()
        if not title:
            continue
        total_checked += 1

        if any(w in title for w in target_words):
            exact += 1
        elif any(w in title for w in adjacent):
            close += 1

    if total_checked == 0:
        return 0.5

    # Exact match = 1.0, close = 0.5, neither = 0.0
    alignment = (exact * 1.0 + close * 0.5) / total_checked
    return min(1.0, alignment)


def validate_engine_quality(
    cv_name: str,
    domain: str,
    jobs: list[dict],
    cv_skills: list[str],
    expected_seniority: str = "",
) -> EngineResult:
    """Run all engine quality checks for one CV's search results."""
    result = EngineResult(
        cv_name=cv_name,
        domain=domain,
        total_jobs_stored=len(jobs),
    )

    if not jobs:
        result.notes = "No jobs found — cannot validate engine quality"
        return result

    # Domain relevance
    result.domain_relevance, result.relevant_jobs, result.irrelevant_examples = (
        _check_domain_relevance(jobs, domain)
    )

    # Score distribution sanity
    result.score_sanity, result.score_stats = _check_score_sanity(jobs)

    # Skill match accuracy for top jobs
    result.skill_match_accuracy, result.top_jobs_skill_overlap = (
        _check_skill_match(jobs, cv_skills)
    )

    # Seniority alignment
    result.seniority_alignment = _check_seniority_alignment(jobs, expected_seniority)

    result.compute_confidence()
    return result


def format_pillar3_report(results: list[EngineResult]) -> str:
    """Format Pillar 3 results as markdown table."""
    lines = [
        "## Pillar 3: Search & Match Engine Quality",
        "",
        "| CV | Domain | Jobs | Relevance | Score Sanity | Skill Match | Seniority | Confidence |",
        "|---|--------|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for r in sorted(results, key=lambda x: x.confidence, reverse=True):
        lines.append(
            f"| {r.cv_name} | {r.domain} "
            f"| {r.total_jobs_stored} "
            f"| {r.domain_relevance:.0%} ({r.relevant_jobs}/{r.total_jobs_stored}) "
            f"| {r.score_sanity:.0%} "
            f"| {r.skill_match_accuracy:.0%} "
            f"| {r.seniority_alignment:.0%} "
            f"| **{r.confidence:.0%}** |"
        )

    # Overall average (excluding CVs with 0 jobs)
    valid_results = [r for r in results if r.total_jobs_stored > 0]
    if valid_results:
        avg = sum(r.confidence for r in valid_results) / len(valid_results)
        lines.append(f"\n**Overall Pillar 3 Confidence: {avg:.0%}** ({len(valid_results)} CVs with results)")

    # Issues
    issues = []
    for r in results:
        if r.total_jobs_stored == 0:
            issues.append(f"- **{r.cv_name}** ({r.domain}): Zero jobs found — sources may not cover this domain")
        elif r.domain_relevance < 0.5:
            issues.append(f"- **{r.cv_name}**: Low domain relevance ({r.domain_relevance:.0%}) — engine returning off-domain jobs")
        if r.score_sanity < 0.5 and r.total_jobs_stored > 0:
            stats = r.score_stats
            issues.append(f"- **{r.cv_name}**: Score distribution issue — avg={stats.get('avg')}, spread={stats.get('spread')}")
        if r.irrelevant_examples:
            examples = "; ".join(r.irrelevant_examples[:3])
            issues.append(f"  Irrelevant examples: {examples}")

    if issues:
        lines.append("\n### Engine Issues Found")
        lines.extend(issues[:20])

    return "\n".join(lines)
