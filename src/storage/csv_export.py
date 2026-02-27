import csv
from src.models import Job


HEADERS = [
    "job_title", "company", "location", "salary",
    "match_score", "apply_url", "source", "date_found", "visa_flag",
]


def _format_salary(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"{int(job.salary_min)}-{int(job.salary_max)}"
    if job.salary_min:
        return str(int(job.salary_min))
    if job.salary_max:
        return str(int(job.salary_max))
    return ""


async def export_to_csv(jobs: list[Job], filepath: str) -> str:
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        for job in jobs:
            writer.writerow([
                job.title,
                job.company,
                job.location,
                _format_salary(job),
                job.match_score,
                job.apply_url,
                job.source,
                job.date_found,
                "Yes" if job.visa_flag else "No",
            ])
    return filepath
