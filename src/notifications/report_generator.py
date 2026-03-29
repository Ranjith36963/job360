import html as html_mod
from datetime import datetime, timezone
from src.models import Job
from src.utils.time_buckets import (
    BUCKETS, bucket_jobs, bucket_summary_counts, format_relative_time, score_color_hex,
)


def _format_salary(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"{int(job.salary_min):,}-{int(job.salary_max):,}"
    if job.salary_min:
        return f"{int(job.salary_min):,}+"
    if job.salary_max:
        return f"Up to {int(job.salary_max):,}"
    return "N/A"


def _jobs_to_dicts(jobs: list[Job]) -> list[dict]:
    """Convert Job objects to dicts for bucket_jobs()."""
    return [
        {
            "title": j.title, "company": j.company, "location": j.location,
            "salary_min": j.salary_min, "salary_max": j.salary_max,
            "apply_url": j.apply_url, "source": j.source,
            "date_found": j.date_found, "match_score": j.match_score,
            "visa_flag": j.visa_flag, "description": j.description,
        }
        for j in jobs
    ]


def generate_markdown_report(jobs: list[Job], stats: dict, diagnostics=None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Job360 Report - {now}",
        "",
        "## Summary",
        f"- **Total found**: {stats.get('total_found', 0)}",
        f"- **New jobs**: {stats.get('new_jobs', 0)}",
    ]
    per_source = stats.get("per_source", {})
    if per_source:
        lines.append("- **Per source**: " + ", ".join(f"{k}: {v}" for k, v in per_source.items()))

    if not jobs:
        lines.append("")
        lines.append("No new jobs found this run.")
        return "\n".join(lines)

    # Bucket jobs
    job_dicts = _jobs_to_dicts(jobs)
    bucketed = bucket_jobs(job_dicts, min_score=0)
    counts = bucket_summary_counts(bucketed)
    lines.append(f"- **Breakdown**: {counts['last_24h']} in 24h, {counts['24_48h']} in 24-48h, "
                 f"{counts['2_3d']} in 2-3d, {counts['3_5d']} in 3-5d, {counts['5_7d']} in 5-7d")
    lines.append("")

    bucket_emojis = ["\U0001f534", "\U0001f7e0", "\U0001f7e1", "\U0001f7e2", "\U0001f535"]
    for idx in range(len(BUCKETS)):
        label = BUCKETS[idx][0]
        emoji = bucket_emojis[idx]
        bucket_list = bucketed.get(idx, [])
        lines.append(f"## {emoji} {label} ({len(bucket_list)} jobs)")
        lines.append("")
        if not bucket_list:
            lines.append("No jobs in this period.")
            lines.append("")
            continue
        lines.append("| # | Score | Title | Company | Location | Salary | Visa | Apply |")
        lines.append("|---|-------|-------|---------|----------|--------|------|-------|")
        for i, j in enumerate(bucket_list[:10], 1):
            visa = "Yes" if j.get("visa_flag") else ""
            salary = _format_salary(Job(
                title=j["title"], company=j["company"], apply_url=j["apply_url"],
                source=j["source"], date_found=j["date_found"],
                salary_min=j.get("salary_min"), salary_max=j.get("salary_max"),
            ))
            lines.append(
                f"| {i} | {j['match_score']} | {j['title']} | {j['company']} "
                f"| {j.get('location', '')} | {salary} | {visa} "
                f"| [Apply]({j['apply_url']}) |"
            )
        lines.append("")

    # === Diagnostic sections (when PipelineDiagnostics is provided) ===
    if diagnostics is not None:
        try:
            d = diagnostics.to_dict()
            lines.append("---")
            lines.append("")

            # 1. Pipeline Funnel
            funnel = d.get("funnel", [])
            if funnel:
                lines.append("## Pipeline Funnel")
                lines.append("")
                lines.append("| Stage | Count | % of Previous |")
                lines.append("|-------|------:|:-------------:|")
                prev = 0
                for stage, count in funnel:
                    pct = f"{count / prev * 100:.1f}%" if prev > 0 else "-"
                    lines.append(f"| {stage} | {count:,} | {pct} |")
                    prev = count
                lines.append("")

            # 2. Score Distribution
            hist = d.get("scores", {}).get("histogram", {})
            if hist and any(v > 0 for v in hist.values()):
                max_count = max(hist.values()) or 1
                lines.append("## Score Distribution")
                lines.append("")
                avg = d.get("scores", {}).get("avg_score", 0)
                total = d.get("scores", {}).get("total_scored", 0)
                lines.append(f"**{total:,} jobs scored** | Average: **{avg}**/100")
                lines.append("")
                lines.append("| Range | Count | Distribution |")
                lines.append("|------:|------:|:------------|")
                for label, count in hist.items():
                    bar = "\u2588" * max(1, count * 30 // max_count) if count > 0 else ""
                    lines.append(f"| {label} | {count} | {bar} |")
                lines.append("")

            # 3. Scoring Dimensions
            dims = d.get("dimensions", {})
            if dims:
                lines.append("## Scoring Dimensions")
                lines.append("")
                lines.append("| Dimension | Max Possible | Avg | Best | Zero Count | Zero % |")
                lines.append("|-----------|:-----------:|:---:|:----:|:----------:|:------:|")
                for dim, info in dims.items():
                    total_n = info.get("max", 0)  # max observed
                    max_p = info.get("max_possible", 0)
                    avg_v = info.get("avg", 0)
                    zc = info.get("zero_count", 0)
                    scored = d.get("scores", {}).get("total_scored", 1) or 1
                    zp = f"{zc / scored * 100:.1f}%"
                    lines.append(
                        f"| {dim.capitalize()} | {max_p} | {avg_v} | {total_n} | {zc} | {zp} |"
                    )
                lines.append("")

            # 4. Source Performance
            per_source = stats.get("per_source", {})
            source_rows = []
            for src, data in per_source.items():
                if isinstance(data, dict):
                    fetched = data.get("fetched", 0)
                    after_f = data.get("after_foreign_filter", 0)
                    above = data.get("above_threshold", 0)
                    stored = data.get("stored", 0)
                    conv = f"{stored / fetched * 100:.1f}%" if fetched > 0 else "0%"
                    source_rows.append((src, fetched, after_f, above, stored, conv))
            if source_rows:
                source_rows.sort(key=lambda r: r[4], reverse=True)  # Sort by stored
                lines.append("## Source Performance")
                lines.append("")
                lines.append("| Source | Fetched | After Foreign | Above Threshold | Stored | Conversion |")
                lines.append("|--------|--------:|--------------:|----------------:|-------:|-----------:|")
                for src, fetched, after_f, above, stored, conv in source_rows:
                    lines.append(
                        f"| {src} | {fetched} | {after_f} | {above} | {stored} | {conv} |"
                    )
                lines.append("")

            # 5. Deduplication
            dedup = d.get("dedup", {})
            if dedup.get("before", 0) > 0:
                removed = dedup["before"] - dedup["after"]
                pct = f"{removed / dedup['before'] * 100:.1f}%" if dedup["before"] else "0%"
                lines.append("## Deduplication")
                lines.append("")
                lines.append(f"- **Total removed**: {removed} ({pct})")
                lines.append(f"- By normalized key: {dedup.get('removed_by_key', 0)}")
                lines.append(f"- By description similarity: {dedup.get('removed_by_similarity', 0)}")
                lines.append("")

            # 6. LLM Enrichment Impact
            llm = d.get("llm", {})
            if llm.get("cache_hits", 0) or llm.get("api_calls", 0):
                lines.append("## LLM Enrichment")
                lines.append("")
                lines.append(f"- Cache hits: {llm.get('cache_hits', 0)}, API calls: {llm.get('api_calls', 0)}")
                providers = llm.get("providers_used", [])
                if providers:
                    lines.append(f"- Providers: {', '.join(providers)}")
                delta = llm.get("avg_score_delta", 0)
                sign = "+" if delta >= 0 else ""
                lines.append(f"- Avg score delta: {sign}{delta} points")
                cc = llm.get("call_counts", {})
                if cc:
                    lines.append(f"- Calls per provider: {', '.join(f'{k}: {v}' for k, v in cc.items())}")
                lines.append("")

            # 7. Data Quality
            dq = d.get("data_quality", {})
            if dq.get("total_jobs", 0) > 0:
                lines.append("## Data Quality")
                lines.append("")
                lines.append("| Metric | % |")
                lines.append("|--------|--:|")
                lines.append(f"| Has salary | {dq.get('pct_salary', 0)}% |")
                lines.append(f"| Has description (>50 chars) | {dq.get('pct_description', 0)}% |")
                lines.append(f"| Has location | {dq.get('pct_location', 0)}% |")
                lines.append(f"| Has visa flag | {dq.get('pct_visa', 0)}% |")
                lines.append("")

            # 8. Top Skill Gaps
            gaps = d.get("skill_gaps", [])
            if gaps:
                scored = d.get("scores", {}).get("total_scored", 1) or 1
                lines.append("## Top Skill Gaps (Most Common Missing Required)")
                lines.append("")
                lines.append("| Skill | Times Required | % of Jobs |")
                lines.append("|-------|---------------:|----------:|")
                for skill, count in gaps[:15]:
                    pct = f"{count / scored * 100:.1f}%"
                    lines.append(f"| {skill} | {count} | {pct} |")
                lines.append("")

            # 9. Timing Breakdown
            timings = d.get("timings", {})
            if timings:
                total_time = sum(timings.values())
                lines.append("## Timing Breakdown")
                lines.append("")
                lines.append("| Phase | Time (s) | % |")
                lines.append("|-------|:--------:|--:|")
                for phase, secs in sorted(timings.items(), key=lambda x: x[1], reverse=True):
                    pct = f"{secs / total_time * 100:.1f}%" if total_time > 0 else "0%"
                    lines.append(f"| {phase} | {secs:.1f} | {pct} |")
                lines.append(f"| **Total** | **{total_time:.1f}** | **100%** |")
                lines.append("")

            # 10. Feedback Loop
            fb = d.get("feedback", {})
            if fb.get("liked_count", 0) or fb.get("rejected_count", 0):
                lines.append("## Feedback Loop")
                lines.append("")
                lines.append(f"- Liked signals: {fb.get('liked_count', 0)}")
                lines.append(f"- Rejected signals: {fb.get('rejected_count', 0)}")
                lines.append(f"- Jobs adjusted: {fb.get('adjustments_made', 0)}")
                lines.append(f"- Total adjustment: {fb.get('total_adj', 0):+d} points")
                lines.append("")

            # 11. Reranker
            rr = d.get("reranker", {})
            if rr.get("reranked_count", 0) > 0:
                lines.append("## Reranker")
                lines.append("")
                lines.append(f"- Reranked: {rr.get('reranked_count', 0)} candidates")
                lines.append(f"- Avg rerank score: {rr.get('avg_rerank_score', 0):.3f}")
                lines.append(f"- Avg boost applied: {rr.get('avg_boost', 0):.1f} points")
                lines.append("")

        except Exception:
            lines.append("")
            lines.append("*Diagnostics generation failed — pipeline data may be incomplete.*")
            lines.append("")

    return "\n".join(lines)


def generate_html_report(jobs: list[Job], stats: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Bucket jobs
    job_dicts = _jobs_to_dicts(jobs)
    bucketed = bucket_jobs(job_dicts, min_score=0)
    counts = bucket_summary_counts(bucketed)

    per_source = stats.get("per_source", {})
    source_info = html_mod.escape(
        ", ".join(f"{k}: {v}" for k, v in per_source.items())
    ) if per_source else "N/A"

    bucket_colors = ["#f44336", "#FF9800", "#FFC107", "#4CAF50", "#2196F3"]
    bucket_emojis_html = ["&#x1f534;", "&#x1f7e0;", "&#x1f7e1;", "&#x1f7e2;", "&#x1f535;"]

    sections_html = ""
    for idx in range(len(BUCKETS)):
        label = BUCKETS[idx][0]
        emoji = bucket_emojis_html[idx]
        color = bucket_colors[idx]
        bucket_list = bucketed.get(idx, [])
        sections_html += f'<h2 style="color:{color}">{emoji} {label} ({len(bucket_list)} jobs)</h2>'
        if not bucket_list:
            sections_html += "<p><em>No jobs in this period.</em></p>"
            continue
        sections_html += """<table><tr><th>Score</th><th>Title</th><th>Company</th>
            <th>Location</th><th>Salary</th><th>Visa</th><th>Apply</th></tr>"""
        for j in bucket_list[:10]:
            score = j.get("match_score", 0)
            sc = score_color_hex(score)
            visa = '<span style="color:green">Yes</span>' if j.get("visa_flag") else ""
            salary = _format_salary(Job(
                title=j["title"], company=j["company"], apply_url=j["apply_url"],
                source=j["source"], date_found=j["date_found"],
                salary_min=j.get("salary_min"), salary_max=j.get("salary_max"),
            ))
            title_esc = html_mod.escape(j['title'])
            company_esc = html_mod.escape(j['company'])
            location_esc = html_mod.escape(j.get('location', ''))
            url_esc = html_mod.escape(j['apply_url'])
            sections_html += f"""<tr>
                <td style="color:{sc};font-weight:bold">{score}</td>
                <td>{title_esc}</td><td>{company_esc}</td>
                <td>{location_esc}</td><td>{salary}</td>
                <td>{visa}</td>
                <td><a href="{url_esc}" style="color:#1a73e8">Apply</a></td>
            </tr>"""
        sections_html += "</table>"

    summary_line = (f"Found {counts['total']} jobs: {counts['last_24h']} in last 24h, "
                    f"{counts['24_48h']} in 24-48h, {counts['2_3d']} in 2-3d, "
                    f"{counts['3_5d']} in 3-5d, {counts['5_7d']} in 5-7d")

    return f"""<html>
<head><style>
    body {{ font-family: Arial, sans-serif; margin: 20px; }}
    h1 {{ color: #1a73e8; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
    th {{ background: #1a73e8; color: white; padding: 10px; text-align: left; }}
    td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
    tr:hover {{ background: #f5f5f5; }}
    .stats {{ background: #f0f4ff; padding: 15px; border-radius: 8px; margin: 15px 0; }}
</style></head>
<body>
    <h1>Job360 Report - {now}</h1>
    <div class="stats">
        <strong>{summary_line}</strong><br>
        <strong>Total found:</strong> {stats.get('total_found', 0)} |
        <strong>New jobs:</strong> {stats.get('new_jobs', 0)} |
        <strong>Sources:</strong> {source_info}
    </div>
    {sections_html}
</body>
</html>"""
