import asyncio
import logging

import aiohttp

from src.models import Job
from src.config.settings import SLACK_WEBHOOK_URL

logger = logging.getLogger("job360.slack")


def is_slack_configured() -> bool:
    return bool(SLACK_WEBHOOK_URL)


def _format_salary(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"£{int(job.salary_min):,}-£{int(job.salary_max):,}"
    if job.salary_min:
        return f"£{int(job.salary_min):,}+"
    if job.salary_max:
        return f"Up to £{int(job.salary_max):,}"
    return "N/A"


def _build_payload(jobs: list[Job], stats: dict) -> dict:
    total = stats.get("total_found", 0)
    new = stats.get("new_jobs", 0)
    per_source = stats.get("per_source", {})
    source_summary = ", ".join(f"{k}: {v}" for k, v in per_source.items()) if per_source else "N/A"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔍 Job360: {new} new AI/ML jobs found"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Total scanned:* {total}"},
                {"type": "mrkdwn", "text": f"*New jobs:* {new}"},
                {"type": "mrkdwn", "text": f"*Sources:* {len(per_source)}"},
            ],
        },
        {"type": "divider"},
    ]

    sorted_jobs = sorted(jobs, key=lambda j: j.match_score, reverse=True)
    top_jobs = sorted_jobs[:10]

    for job in top_jobs:
        score = job.match_score
        emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
        visa = " 🛂" if job.visa_flag else ""
        salary = _format_salary(job)

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{emoji} *[{score}]* <{job.apply_url}|{job.title}>\n"
                    f"_{job.company}_ | {job.location} | {salary}{visa}"
                ),
            },
        })

    if len(sorted_jobs) > 10:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_...and {len(sorted_jobs) - 10} more jobs. Check email or dashboard for full list._"},
            ],
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"Sources: {source_summary}"},
        ],
    })

    return {"blocks": blocks}


async def send_slack(jobs: list[Job], stats: dict):
    if not is_slack_configured():
        logger.info("Slack not configured, skipping")
        return
    if not jobs:
        logger.info("No new jobs for Slack")
        return

    payload = _build_payload(jobs, stats)

    async with aiohttp.ClientSession() as session:
        async with session.post(SLACK_WEBHOOK_URL, json=payload) as resp:
            if resp.status == 200:
                logger.info(f"Slack notification sent ({len(jobs)} jobs)")
            else:
                body = await resp.text()
                logger.error(f"Slack webhook failed ({resp.status}): {body}")
