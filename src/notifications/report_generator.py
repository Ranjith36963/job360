from datetime import datetime, timezone
from src.models import Job


def _format_salary(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"{int(job.salary_min):,}-{int(job.salary_max):,}"
    if job.salary_min:
        return f"{int(job.salary_min):,}+"
    if job.salary_max:
        return f"Up to {int(job.salary_max):,}"
    return "N/A"


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
    lines.append("")

    if not jobs:
        lines.append("No new jobs found this run.")
        return "\n".join(lines)

    sorted_jobs = sorted(jobs, key=lambda j: j.match_score, reverse=True)
    top_jobs = sorted_jobs[:20]

    lines.append("## Top Jobs")
    lines.append("")
    lines.append("| # | Score | Title | Company | Location | Salary | Visa | Apply |")
    lines.append("|---|-------|-------|---------|----------|--------|------|-------|")

    for i, job in enumerate(top_jobs, 1):
        visa = "Yes" if job.visa_flag else ""
        lines.append(
            f"| {i} | {job.match_score} | {job.title} | {job.company} "
            f"| {job.location} | {_format_salary(job)} | {visa} "
            f"| [Apply]({job.apply_url}) |"
        )
    lines.append("")
    return "\n".join(lines)


def generate_html_report(jobs: list[Job], stats: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sorted_jobs = sorted(jobs, key=lambda j: j.match_score, reverse=True)
    top_jobs = sorted_jobs[:20]

    rows = ""
    for job in top_jobs:
        visa = '<span style="color:green">Yes</span>' if job.visa_flag else ""
        score_color = "#4CAF50" if job.match_score >= 70 else "#FF9800" if job.match_score >= 50 else "#f44336"
        rows += f"""<tr>
            <td style="color:{score_color};font-weight:bold">{job.match_score}</td>
            <td>{job.title}</td>
            <td>{job.company}</td>
            <td>{job.location}</td>
            <td>{_format_salary(job)}</td>
            <td>{visa}</td>
            <td><a href="{job.apply_url}" style="color:#1a73e8">Apply</a></td>
        </tr>"""

    per_source = stats.get("per_source", {})
    source_info = ", ".join(f"{k}: {v}" for k, v in per_source.items()) if per_source else "N/A"

    return f"""<html>
<head><style>
    body {{ font-family: Arial, sans-serif; margin: 20px; }}
    h1 {{ color: #1a73e8; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th {{ background: #1a73e8; color: white; padding: 10px; text-align: left; }}
    td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
    tr:hover {{ background: #f5f5f5; }}
    .stats {{ background: #f0f4ff; padding: 15px; border-radius: 8px; margin: 15px 0; }}
</style></head>
<body>
    <h1>Job360 Report - {now}</h1>
    <div class="stats">
        <strong>Total found:</strong> {stats.get('total_found', 0)} |
        <strong>New jobs:</strong> {stats.get('new_jobs', 0)} |
        <strong>Sources:</strong> {source_info}
    </div>
    <table>
        <tr><th>Score</th><th>Title</th><th>Company</th><th>Location</th><th>Salary</th><th>Visa</th><th>Apply</th></tr>
        {rows}
    </table>
</body>
</html>"""
