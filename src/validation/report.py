"""Generate validation reports — markdown for humans, JSON for benchmarking."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.validation.checker import SourceConfidence, ValidationResult


def generate_validation_report(
    results: list[ValidationResult],
    sources: list[SourceConfidence],
) -> str:
    """Generate a markdown validation report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(results)
    n_sources = len(sources)
    overall = _overall_confidence(sources)

    lines = [
        f"# Job360 Validation Report - {now}",
        "",
        f"## Overall Benchmark: {overall:.0%} confidence ({total} jobs checked, {n_sources} sources)",
        "",
    ]

    # Per-source table
    if sources:
        lines.append("## Per-Source Results")
        lines.append("")
        lines.append("| Source | Checked | URL | Title | Date | Description | Confidence |")
        lines.append("|--------|--------:|:---:|:-----:|:----:|:-----------:|:----------:|")
        for sc in sorted(sources, key=lambda s: s.confidence, reverse=True):
            url = f"{sc.url_score:.0%}"
            title = f"{sc.title_score:.0%}"
            date = f"{sc.date_score:.0%}" if sc.date_score >= 0 else "N/A"
            desc = f"{sc.desc_score:.0%}" if sc.desc_score >= 0 else "N/A"
            conf = f"{sc.confidence:.0%}"
            lines.append(
                f"| {sc.source} | {sc.jobs_checked} | {url} | {title} | {date} | {desc} | **{conf}** |"
            )
        lines.append("")

    # Issues found
    all_issues: list[str] = []
    for sc in sources:
        for issue in sc.issues:
            if issue and issue not in all_issues:
                all_issues.append(f"[{sc.source}] {issue}")

    if all_issues:
        lines.append("## Issues Found")
        lines.append("")
        for i, issue in enumerate(all_issues[:30], 1):  # Cap at 30 issues
            lines.append(f"{i}. {issue}")
        if len(all_issues) > 30:
            lines.append(f"... and {len(all_issues) - 30} more")
        lines.append("")

    # Per-job detail table (compact)
    if results:
        lines.append("## Job Details")
        lines.append("")
        lines.append("| Source | Score | Title | URL | Title Match | Date | Desc | Confidence |")
        lines.append("|--------|------:|-------|:---:|:-----------:|:----:|:----:|:----------:|")
        for r in sorted(results, key=lambda x: x.confidence, reverse=True):
            url_icon = {1.0: "OK", 0.5: "?", 0.0: "DEAD"}.get(r.url_alive, "?")
            title_m = f"{r.title_match:.0%}"
            date_m = f"{r.date_accurate:.0%}" if r.date_accurate >= 0 else "-"
            desc_m = f"{r.description_match:.0%}" if r.description_match >= 0 else "-"
            conf = f"{r.confidence:.0%}"
            lines.append(
                f"| {r.source} | {r.match_score} | {r.title[:35]} | {url_icon} | {title_m} | {date_m} | {desc_m} | {conf} |"
            )
        lines.append("")

    # Recommendations
    weak_sources = [sc for sc in sources if sc.confidence < 0.7]
    if weak_sources:
        lines.append("## Recommendations")
        lines.append("")
        for sc in weak_sources:
            if sc.url_score < 0.7:
                lines.append(f"- **{sc.source}**: URL validity low ({sc.url_score:.0%}) — check if source URLs are stable")
            if sc.title_score < 0.6:
                lines.append(f"- **{sc.source}**: Title mismatch ({sc.title_score:.0%}) — parser may be extracting wrong field")
            if 0 <= sc.date_score < 0.5:
                lines.append(f"- **{sc.source}**: Date inaccurate ({sc.date_score:.0%}) — check which date field is used")
            if 0 <= sc.desc_score < 0.4:
                lines.append(f"- **{sc.source}**: Description mismatch ({sc.desc_score:.0%}) — review HTML extraction")
        lines.append("")

    return "\n".join(lines)


def generate_validation_json(
    results: list[ValidationResult],
    sources: list[SourceConfidence],
) -> dict[str, Any]:
    """Generate machine-readable JSON validation metrics for benchmarking."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall_confidence": _overall_confidence(sources),
        "total_checked": len(results),
        "sources_validated": len(sources),
        "per_source": {
            sc.source: {
                "jobs_checked": sc.jobs_checked,
                "url_score": sc.url_score,
                "title_score": sc.title_score,
                "date_score": sc.date_score,
                "desc_score": sc.desc_score,
                "confidence": sc.confidence,
                "issue_count": len(sc.issues),
            }
            for sc in sources
        },
        "issues": [
            f"[{sc.source}] {issue}"
            for sc in sources
            for issue in sc.issues
            if issue
        ][:50],
        "per_job": [
            {
                "id": r.job_id,
                "source": r.source,
                "title": r.title[:60],
                "score": r.match_score,
                "url_alive": r.url_alive,
                "title_match": r.title_match,
                "date_accurate": r.date_accurate,
                "description_match": r.description_match,
                "confidence": r.confidence,
                "status_code": r.actual_status_code,
            }
            for r in results
        ],
    }


def _overall_confidence(sources: list[SourceConfidence]) -> float:
    """Weighted average confidence across validatable sources (excludes -1.0 / unvalidatable)."""
    if not sources:
        return 0.0
    validatable = [sc for sc in sources if sc.confidence >= 0]
    total_jobs = sum(sc.jobs_checked for sc in validatable)
    if total_jobs == 0:
        return 0.0
    weighted = sum(sc.confidence * sc.jobs_checked for sc in validatable)
    return round(weighted / total_jobs, 3)
