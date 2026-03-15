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


def generate_markdown_report(jobs: list[Job], stats: dict) -> str:
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
                 f"{counts['48_72h']} in 48-72h, {counts['3_7d']} in 3-7d")
    lines.append("")

    bucket_emojis = ["\U0001f534", "\U0001f7e0", "\U0001f7e1", "\U0001f535"]
    for idx in range(4):
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
    return "\n".join(lines)


def generate_html_report(jobs: list[Job], stats: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Bucket jobs
    job_dicts = _jobs_to_dicts(jobs)
    bucketed = bucket_jobs(job_dicts, min_score=0)
    counts = bucket_summary_counts(bucketed)

    per_source = stats.get("per_source", {})
    source_info = ", ".join(f"{k}: {v}" for k, v in per_source.items()) if per_source else "N/A"

    bucket_colors = ["#f44336", "#FF9800", "#FFC107", "#2196F3"]
    bucket_emojis_html = ["&#x1f534;", "&#x1f7e0;", "&#x1f7e1;", "&#x1f535;"]

    sections_html = ""
    for idx in range(4):
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
            sections_html += f"""<tr>
                <td style="color:{sc};font-weight:bold">{score}</td>
                <td>{j['title']}</td><td>{j['company']}</td>
                <td>{j.get('location', '')}</td><td>{salary}</td>
                <td>{visa}</td>
                <td><a href="{j['apply_url']}" style="color:#1a73e8">Apply</a></td>
            </tr>"""
        sections_html += "</table>"

    summary_line = (f"Found {counts['total']} jobs: {counts['last_24h']} in last 24h, "
                    f"{counts['24_48h']} in 24-48h, {counts['48_72h']} in 48-72h, "
                    f"{counts['3_7d']} in 3-7d")

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
