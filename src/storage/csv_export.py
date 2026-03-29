import csv
import json
from src.models import Job


HEADERS = [
    "job_title", "company", "location", "salary",
    "match_score", "role", "skill", "seniority", "experience",
    "credentials", "location_score", "recency", "semantic", "penalty",
    "apply_url", "source", "date_found", "visa_flag",
    "matched_skills", "missing_required", "missing_preferred",
    "transferable_skills", "job_type", "experience_level",
]


def _format_salary(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"{int(job.salary_min)}-{int(job.salary_max)}"
    if job.salary_min:
        return str(int(job.salary_min))
    if job.salary_max:
        return str(int(job.salary_max))
    return ""


def _parse_match_data(job: Job) -> dict:
    """Extract score dimensions from match_data JSON."""
    if not job.match_data:
        return {}
    try:
        return json.loads(job.match_data)
    except (json.JSONDecodeError, TypeError):
        return {}


def export_to_csv(jobs: list[Job], filepath: str) -> str:
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        for job in jobs:
            md = _parse_match_data(job)
            writer.writerow([
                job.title,
                job.company,
                job.location,
                _format_salary(job),
                job.match_score,
                md.get("role", ""),
                md.get("skill", ""),
                md.get("seniority", ""),
                md.get("experience", ""),
                md.get("credentials", ""),
                md.get("location", ""),
                md.get("recency", ""),
                md.get("semantic", ""),
                md.get("penalty", 0) if md.get("penalty") else "",
                job.apply_url,
                job.source,
                job.date_found,
                "Yes" if job.visa_flag else "No",
                ";".join(md.get("matched", [])),
                ";".join(md.get("missing_required", [])),
                ";".join(md.get("missing_preferred", [])),
                ";".join(md.get("transferable", [])),
                getattr(job, "job_type", ""),
                getattr(job, "experience_level", ""),
            ])
    return filepath
