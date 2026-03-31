"""Pillar 1: CV Parsing Quality Validator.

Compares parser output against ground-truth annotations to measure
how accurately we extract skills, titles, education, certifications,
and seniority from real CVs.

Metrics:
- Skills recall: % of expected skills found by parser
- Titles recall: % of expected titles found by parser
- Education recall: % of expected degrees found
- Certification recall: % of expected certs found
- Seniority match: exact match (1.0) or close (0.5)
- Overall confidence: weighted average
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("job360.validation.pillar1")


@dataclass
class ParsingResult:
    """Result of parsing quality check for one CV."""
    cv_name: str
    domain: str
    # Recall scores (0.0 - 1.0)
    skills_recall: float = 0.0
    titles_recall: float = 0.0
    education_recall: float = 0.0
    certifications_recall: float = 0.0
    seniority_match: float = 0.0
    # Counts
    expected_skills: int = 0
    found_skills: int = 0
    expected_titles: int = 0
    found_titles: int = 0
    # Details
    missing_skills: list[str] = field(default_factory=list)
    extra_skills: list[str] = field(default_factory=list)
    missing_titles: list[str] = field(default_factory=list)
    missing_education: list[str] = field(default_factory=list)
    missing_certifications: list[str] = field(default_factory=list)
    seniority_expected: str = ""
    seniority_got: str = ""
    notes: str = ""
    # Overall
    confidence: float = 0.0

    def compute_confidence(self) -> float:
        """Weighted confidence: skills(0.35) + titles(0.25) + education(0.15) + certs(0.10) + seniority(0.15)."""
        self.confidence = (
            self.skills_recall * 0.35
            + self.titles_recall * 0.25
            + self.education_recall * 0.15
            + self.certifications_recall * 0.10
            + self.seniority_match * 0.15
        )
        return self.confidence


def _fuzzy_match(expected: str, candidates: list[str], threshold: float = 0.6) -> bool:
    """Check if expected string matches any candidate (case-insensitive, substring, abbreviation)."""
    exp_lower = expected.lower().strip()

    for candidate in candidates:
        cand_lower = candidate.lower().strip()

        # Exact match
        if exp_lower == cand_lower:
            return True

        # Substring match (either direction)
        if exp_lower in cand_lower or cand_lower in exp_lower:
            return True

        # Word overlap for multi-word terms
        exp_words = set(exp_lower.split())
        cand_words = set(cand_lower.split())
        if len(exp_words) > 1 and len(cand_words) > 1:
            overlap = len(exp_words & cand_words)
            min_len = min(len(exp_words), len(cand_words))
            if min_len > 0 and overlap / min_len >= threshold:
                return True

    return False


def _seniority_distance(expected: str, got: str) -> float:
    """Score seniority match: 1.0 = exact, 0.5 = adjacent, 0.0 = far."""
    levels = ["entry", "mid", "senior", "lead", "executive"]
    try:
        exp_idx = levels.index(expected.lower())
        got_idx = levels.index(got.lower())
    except ValueError:
        return 0.0

    diff = abs(exp_idx - got_idx)
    if diff == 0:
        return 1.0
    elif diff == 1:
        return 0.5
    else:
        return 0.0


def validate_cv_parsing(
    cv_name: str,
    parsed_skills: list[str],
    parsed_titles: list[str],
    parsed_education: list[str],
    parsed_certifications: list[str],
    parsed_seniority: str,
    ground_truth: dict,
) -> ParsingResult:
    """Compare parser output against ground truth for one CV."""
    gt = ground_truth.get(cv_name, {})
    if not gt:
        return ParsingResult(cv_name=cv_name, domain="unknown", notes="No ground truth found")

    result = ParsingResult(
        cv_name=cv_name,
        domain=gt.get("domain", "unknown"),
    )

    # Skills recall
    expected_skills = gt.get("expected_skills", [])
    result.expected_skills = len(expected_skills)
    found = 0
    for skill in expected_skills:
        if _fuzzy_match(skill, parsed_skills):
            found += 1
        else:
            result.missing_skills.append(skill)
    result.found_skills = found
    result.skills_recall = found / len(expected_skills) if expected_skills else 1.0

    # Extra skills (found by parser but not in ground truth — not necessarily wrong)
    gt_lower = {s.lower() for s in expected_skills}
    for skill in parsed_skills:
        if not any(skill.lower() in g or g in skill.lower() for g in gt_lower):
            result.extra_skills.append(skill)

    # Titles recall
    expected_titles = gt.get("expected_titles", [])
    result.expected_titles = len(expected_titles)
    found_titles = 0
    for title in expected_titles:
        if _fuzzy_match(title, parsed_titles):
            found_titles += 1
        else:
            result.missing_titles.append(title)
    result.found_titles = found_titles
    result.titles_recall = found_titles / len(expected_titles) if expected_titles else 1.0

    # Education recall
    expected_edu = gt.get("expected_education", [])
    found_edu = 0
    for edu in expected_edu:
        if _fuzzy_match(edu, parsed_education):
            found_edu += 1
        else:
            result.missing_education.append(edu)
    result.education_recall = found_edu / len(expected_edu) if expected_edu else 1.0

    # Certification recall
    expected_certs = gt.get("expected_certifications", [])
    found_certs = 0
    for cert in expected_certs:
        if _fuzzy_match(cert, parsed_certifications):
            found_certs += 1
        else:
            result.missing_certifications.append(cert)
    result.certifications_recall = found_certs / len(expected_certs) if expected_certs else 1.0

    # Seniority match
    expected_seniority = gt.get("expected_seniority", "")
    result.seniority_expected = expected_seniority
    result.seniority_got = parsed_seniority
    result.seniority_match = _seniority_distance(expected_seniority, parsed_seniority)

    result.compute_confidence()
    return result


def load_ground_truth(ground_truth_path: str | Path) -> dict:
    """Load ground truth annotations from JSON file."""
    path = Path(ground_truth_path)
    if not path.exists():
        logger.error(f"Ground truth file not found: {path}")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    # Remove metadata keys
    return {k: v for k, v in data.items() if not k.startswith("_")}


def format_pillar1_report(results: list[ParsingResult]) -> str:
    """Format Pillar 1 results as markdown table."""
    lines = [
        "## Pillar 1: CV Parsing Quality",
        "",
        "| CV | Domain | Skills | Titles | Education | Certs | Seniority | Confidence |",
        "|---|--------|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for r in sorted(results, key=lambda x: x.confidence, reverse=True):
        seniority_str = f"{r.seniority_got}" if r.seniority_match == 1.0 else f"{r.seniority_got} (exp: {r.seniority_expected})"
        lines.append(
            f"| {r.cv_name} | {r.domain} "
            f"| {r.skills_recall:.0%} ({r.found_skills}/{r.expected_skills}) "
            f"| {r.titles_recall:.0%} ({r.found_titles}/{r.expected_titles}) "
            f"| {r.education_recall:.0%} "
            f"| {r.certifications_recall:.0%} "
            f"| {seniority_str} "
            f"| **{r.confidence:.0%}** |"
        )

    # Overall average
    if results:
        avg = sum(r.confidence for r in results) / len(results)
        lines.append(f"\n**Overall Pillar 1 Confidence: {avg:.0%}**")

    # Issues found
    issues = []
    for r in results:
        if r.missing_skills:
            issues.append(f"- **{r.cv_name}**: Missing skills: {', '.join(r.missing_skills[:5])}")
        if r.missing_titles:
            issues.append(f"- **{r.cv_name}**: Missing titles: {', '.join(r.missing_titles)}")
        if r.seniority_match < 1.0:
            issues.append(f"- **{r.cv_name}**: Seniority mismatch — got '{r.seniority_got}', expected '{r.seniority_expected}'")

    if issues:
        lines.append("\n### Parsing Issues Found")
        lines.extend(issues[:20])

    return "\n".join(lines)
